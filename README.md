# CS2 Skin Portfolio Tracker — Data Engineering Pipeline

A production-grade data engineering pipeline that tracks CS2 skin inventory, fetches real-time market prices from Steam and Skinport, and calculates portfolio value with unrealized and realized PnL in USD and PLN — across a dbt medallion architecture on AWS + GCP.

**[dbt Docs — lineage, model descriptions, test results](https://kacperchojnacki1337.github.io/cs2-market-data-platform/)**

---

## Architecture

```
DynamoDB (source of truth — OLTP)
    │
Producer Lambda — two-pass price fetching (daily, EventBridge)
    ├─ Pass 1 (07:00 UTC): 20 concurrent Lambdas × 5 items each
    │     └─ daily date-seeded shuffle rotates item→IP (bypasses Steam per-IP limit)
    ├─ Pass 2 (07:30 UTC): 1 Lambda smart retry — only items missing a valid price
    └─ writes: assets_history, sales_history, prices_history, volume_history, exchange_rates
Skinport Lambda (07:00 UTC) — second price source → skinport_prices_history
    │
BigQuery — medallion architecture
    ├─ steam_raw      (bronze — raw Lambda inserts)
    ├─ steam_staging  (silver — typed views + FIFO matching via dbt)
    └─ steam_marts    (gold — business tables via dbt)
    ↑
dbt Pipeline (08:00 UTC via GitHub Actions; Airflow in Phase 2 — issue #70)
    └─ freshness check → dbt run → dbt test → dbt docs → GitHub Pages
    ↓
Looker Studio (dashboard over steam_marts — issue #63; build guide: docs/looker_studio_guide.md)
```

The Producer Lambda also exposes single-responsibility **modes** (`sync_inventory`,
`exchange_rates`, `batch_prices`) that the Airflow DAG will invoke as separate tasks,
eliminating the race-condition duplicates the legacy all-in-one mode produces in bronze.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Source of Truth | AWS DynamoDB | Schemaless, serverless, PITR enabled |
| Compute | AWS Lambda | Event-driven, zero idle cost, direct BQ write |
| Data Warehouse | BigQuery (EU) | Serverless, columnar, native dbt, GDPR-compliant |
| Transformations | dbt | Version-controlled SQL, lineage, automated testing |
| Orchestration | EventBridge → Airflow (Phase 2) | Daily schedule; Airflow adds dependency-graph scheduling |
| IaC | Terraform | AWS + GCP resources + budget alerts as code |
| CI/CD | GitHub Actions | dbt CI on dev fixtures + prod run + Lambda unit tests |
| Secrets | AWS SSM Parameter Store | Encrypted GCP service account key |
| Price sources | Steam Market, Skinport | Two independent markets; cross-checked |
| FX Rates | NBP API | Free Polish National Bank USD/PLN + EUR/PLN |
| Docs | GitHub Pages | Auto-published dbt lineage on every prod run |

---

## dbt Models

```
bronze (steam_raw) → silver (steam_staging) → gold (steam_marts)
```

### Staging (steam_staging — views)

| Model | Description |
|---|---|
| `stg_assets` | Buy events — type casts, uppercase currency |
| `stg_sales` | Sell events — type casts, renames timestamp → sold_at |
| `stg_prices` | Steam prices — casts price, passes through `price_flagged` |
| `stg_exchange_rates` | NBP USD/PLN + EUR/PLN rates |
| `stg_volume` | Steam 7-day trade volume |
| `stg_skinport_prices` | Skinport market prices (PLN) |

### Intermediate (steam_staging — views)

| Model | Description |
|---|---|
| `int_latest_prices` | Latest valid Steam price per item (excludes flagged) |
| `int_latest_exchange_rate` | Latest USD/PLN and EUR/PLN rate |
| `int_latest_skinport_prices` | Latest Skinport price per item |
| `int_latest_volume` | Latest 7-day volume per item |
| `int_fifo_units` | **FIFO** buy↔sale unit matching — explodes lots and sales into units, matches oldest-first |

### Marts (steam_marts — tables)

| Model | Description |
|---|---|
| `dim_assets` | Asset dimension — deduplicated buy events, surrogate key |
| `fct_portfolio` | Unrealized PnL per **held lot** (FIFO `remaining_qty`); Steam / CSFloat / Skinport valuations + liquidity |
| `fct_portfolio_history` | Daily portfolio snapshots — FIFO time-aware holdings |
| `fct_realized_pnl` | Realized PnL on closed positions, grain = (sale × buy lot) via FIFO |
| `worth_to_sell` | Sell-signal list: held positions with net Skinport profit ≥ 25% and ≥ 50 PLN |
| `rpt_portfolio_summary` | Single-row KPI + ratio metrics layer for Looker Studio |

### Key metrics

```sql
-- fct_portfolio (per held lot; quantity = FIFO remaining_qty)
current_value_pln       = price_usd × usd_pln_rate × quantity
net_value_steam_pln     = current_value_pln × 0.85                 -- after 15% Steam fee
net_value_skinport_pln  = skinport_price_pln × quantity × 0.92     -- after 8% Skinport fee
real_cash_value_pln     = current_value_pln × real_cash_coeff      -- CSFloat, per-category coeff
pnl_total_pln           = (price_usd × rate − buy_price_pln) × quantity

-- fct_realized_pnl (grain = sale × buy lot; fee from sell_channel)
fee_pct             = {Steam: 15, CSFloat: 2, Skinport: 8, Unknown: 0}
net_sell_price_pln  = gross_sell_price_pln × (1 − fee_pct/100)
realized_pnl_pln    = (net_sell_price_pln − buy_price_pln) × units_sold_from_lot
holding_period_days = sell_date − buy_date
```

`real_cash_coeff` per category: Knife 0.83, Gloves 0.80, Skin/Case 0.74, Agent 0.60,
Sticker 0.49, Other 0.65. Skinport price falls back to a coefficient estimate when an
item is unlisted, so there are no gaps.

---

## FIFO sale matching

Sales link to buy lots by **First-In-First-Out unit matching** (`int_fifo_units`), not by
`item_id`. Buy lots and sales are exploded into individual units, numbered per item by
date, and matched unit-to-unit — oldest lot consumed first. A sale that spans multiple
lots splits across them, each priced by its own buy cost. This fixes the double-counted
PnL and dropped held units that item-level matching caused for multi-lot items. No
DynamoDB/Lambda change — the minimal sell event is sufficient.

---

## Data Quality

- `price_flagged = TRUE` if a Steam price deviates **> 50% from its 7-day median**.
  Volume = 0 alone does **not** flag — rare items can have zero weekly sales yet a valid
  price (issue #82).
- Flagged prices are stored in bronze but excluded from `int_latest_prices`.
- `fct_portfolio` shows NULL for items with no valid price.
- Guards (dbt tests): oversell (`sold ≤ bought` per item), `holding_period_days ≥ 0`,
  `quantity ≥ 1`, currency = PLN, sale `item_id` → `dim_assets` relationship.

---

## Setup

### Prerequisites

- AWS CLI configured (`aws configure`)
- Terraform >= 1.5
- GCP project with BigQuery API enabled + service account JSON key
- Python 3.11

### 1. Deploy infrastructure

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # fill in your values
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### 2. Store the GCP service account key in SSM

```bash
aws ssm put-parameter --name "/steam-tracker/gcp-key" --type "SecureString" \
  --value "$(cat your-gcp-key.json)" --region eu-central-1
```

### 3. GitHub Actions secret

Add `GCP_SA_KEY` (contents of the GCP service account JSON) in repo Settings → Secrets → Actions.

### 4. GitHub Pages

Repo Settings → Pages → Source: Deploy from branch → Branch: `gh-pages`.

### 5. Add inventory to DynamoDB

```bash
# Buy event
aws dynamodb put-item --table-name steam_inventory_metadata --item '{
  "asset_id":         {"S": "UNIQUE-UUID"},
  "item_id":          {"S": "AWP | Printstream (Well-Worn)"},
  "event_type":       {"S": "buy"},
  "buy_price":        {"N": "164.81"},
  "buy_currency":     {"S": "PLN"},
  "buy_date":         {"S": "2026-02-06"},
  "category":         {"S": "Skin"},
  "purchase_channel": {"S": "CSFloat"},
  "quantity":         {"N": "1"},
  "updated_at":       {"S": "2026-02-06T00:00:00Z"}
}'

# Sell event (sell_price always in PLN; sell_channel drives the fee)
aws dynamodb put-item --table-name steam_inventory_metadata --item '{
  "asset_id":     {"S": "UNIQUE-UUID"},
  "item_id":      {"S": "AWP | Printstream (Well-Worn)"},
  "event_type":   {"S": "sell"},
  "sell_price":   {"N": "180.00"},
  "sell_currency":{"S": "PLN"},
  "sell_date":    {"S": "2026-03-01"},
  "sell_channel": {"S": "CSFloat"},
  "updated_at":   {"S": "2026-03-01T00:00:00Z"}
}'
```

### 6. Backfill missed days

```bash
python scripts/backfill.py --date 2026-06-15
python scripts/backfill.py --start-date 2026-06-13 --end-date 2026-06-15
```

---

## Local dbt development

The dev BigQuery datasets (`*_dev`) are separate from prod. Seed them once with curated
fixtures so the marts build with representative data (and CI tests are meaningful):

```bash
python scripts/seed_dev.py --key /path/to/gcp-key.json   # fills steam_raw_dev
cd dbt/steam_tracker
dbt deps
dbt run  --target dev
dbt test --target dev
```

CI reseeds `steam_raw_dev` before every dbt-ci run, so PR checks run against real fixtures
rather than empty tables.

---

## Security

- Secrets in AWS SSM Parameter Store (SecureString) — never committed
- GCP service account key written to `/tmp` only in GitHub Actions
- IAM: least-privilege — Lambda has DynamoDB read + SSM read only
- DynamoDB PITR enabled
- GCP key rotation: 90-day cycle (manual)
- Budget alerts: AWS $5/month (Lambda + DynamoDB), GCP 25 PLN/month

---

## Roadmap

| Issue | Feature | Status |
|---|---|---|
| [#70](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/70) | Airflow orchestrator (replaces EventBridge + GH Actions cron) | In Progress |
| [#63](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/63) | Looker Studio dashboard | In Progress — see `docs/looker_studio_guide.md` |

---

## License

MIT