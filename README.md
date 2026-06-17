# CS2 Skin Portfolio Tracker — Data Engineering Pipeline

A production-grade data engineering pipeline that tracks CS2 skin inventory, fetches real-time market prices from Steam, and calculates portfolio value with unrealized and realized PnL in both USD and PLN.

**[dbt Docs — lineage, model descriptions, test results](https://kacperchojnacki1337.github.io/cs2-market-data-platform/)**

---

## Architecture

```
DynamoDB (source of truth — OLTP)
    ↓
Producer Lambda (daily 07:00 UTC via EventBridge)
    ├─ Scans buy + sell events from DynamoDB
    ├─ Fetches Steam Market prices (with data quality validation)
    ├─ Fetches USD/PLN rate from NBP API
    └─ Writes directly to BigQuery steam_raw
         ├─ assets_history
         ├─ sales_history
         ├─ prices_history
         └─ exchange_rates
    ↓
BigQuery — Medallion Architecture
    ├─ steam_raw      (bronze — raw inserts from Lambda)
    ├─ steam_staging  (silver — typed views via dbt)
    └─ steam_marts    (gold — business tables via dbt)
    ↑
dbt Pipeline (daily 08:00 UTC via GitHub Actions)
    └─ Data freshness check → dbt run → dbt test → dbt docs → GitHub Pages
    ↓
Looker Studio (dashboard over steam_marts)
```

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Source of Truth | AWS DynamoDB | Schemaless, serverless, PITR enabled |
| Compute | AWS Lambda | Event-driven, zero idle cost, direct BQ write |
| Data Warehouse | BigQuery (EU) | Serverless, columnar, native dbt, GDPR-compliant |
| Transformations | dbt | Version-controlled SQL, lineage, automated testing |
| IaC | Terraform | Full infra as code — AWS + GCP resources + budget alerts |
| CI/CD | GitHub Actions | dbt on push to main + daily schedule + lambda unit tests |
| Secrets | AWS SSM Parameter Store | Encrypted GCP service account key |
| FX Rates | NBP API | Free Polish National Bank rates |
| Docs | GitHub Pages | Auto-published dbt lineage + model docs on every prod run |

---

## dbt Models

### Medallion Architecture

```
bronze (steam_raw) → silver (steam_staging) → gold (steam_marts)
```

### Staging (steam_staging — views)

| Model | Description |
|---|---|
| `stg_assets` | Buy events — type casts, uppercase currency |
| `stg_sales` | Sell events — type casts, renames timestamp → sold_at |
| `stg_prices` | Steam prices — casts price, passes through price_flagged |
| `stg_exchange_rates` | NBP rates — renames source → rate_source |

### Intermediate (steam_staging — views)

| Model | Description |
|---|---|
| `int_latest_prices` | Latest valid Steam price per item (excludes flagged prices) |
| `int_latest_exchange_rate` | Latest USD/PLN rate |

### Marts (steam_marts — tables)

| Model | Description |
|---|---|
| `dim_assets` | Asset dimension — deduplicated buy events, surrogate key |
| `fct_portfolio` | Current unrealized PnL per active position (excludes sold items) |
| `fct_portfolio_history` | Daily portfolio value snapshots — enables time-series charts |
| `fct_realized_pnl` | Realized PnL on closed positions (sold items) |

### Key metrics

```sql
-- fct_portfolio
current_value_pln  = price_usd × usd_pln_rate × quantity
pnl_per_unit_pln   = (price_usd × usd_pln_rate) - buy_price_pln
pnl_total_pln      = pnl_per_unit_pln × quantity
pnl_pct            = (pnl_per_unit_pln / buy_price_pln) × 100

-- fct_realized_pnl
realized_pnl_pln   = sell_price_pln - buy_price_pln
realized_pnl_pct   = (realized_pnl_pln / buy_price_pln) × 100
holding_period_days = sell_date - buy_date
```

---

## Data Quality

Steam prices are validated before insert:
- `price_flagged = TRUE` if volume = 0 or price deviates > 50% from 7-day median
- Flagged prices are stored in bronze but excluded from `int_latest_prices`
- `fct_portfolio` shows NULL current value for items with no valid price

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
cp terraform.tfvars.example terraform.tfvars  # fill in your values
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

### 2. Store GCP service account key in SSM

```bash
aws ssm put-parameter \
  --name "/steam-tracker/gcp-key" \
  --type "SecureString" \
  --value "$(cat your-gcp-key.json)"
```

### 3. Add GitHub Actions secret

Add `GCP_SA_KEY` (contents of GCP service account JSON) in repo Settings → Secrets → Actions.

### 4. Enable GitHub Pages

Repo Settings → Pages → Source: Deploy from branch → Branch: `gh-pages`.

### 5. Add inventory to DynamoDB

```bash
# Buy event
aws dynamodb put-item \
  --table-name steam_inventory_metadata \
  --item '{
    "asset_id": {"S": "UNIQUE-UUID"},
    "item_id": {"S": "AWP | Printstream (Well-Worn)"},
    "event_type": {"S": "buy"},
    "buy_price": {"N": "164.81"},
    "buy_currency": {"S": "PLN"},
    "buy_date": {"S": "2026-02-06"},
    "category": {"S": "Skin"},
    "purchase_channel": {"S": "CSFloat"},
    "quantity": {"N": "1"},
    "updated_at": {"S": "2026-02-06T00:00:00Z"}
  }'
```

### 6. Backfill missed days

```bash
# Single day
python scripts/backfill.py --date 2026-06-15

# Range (end-date defaults to yesterday)
python scripts/backfill.py --start-date 2026-06-13 --end-date 2026-06-15
```

---

## Security

- Secrets in AWS SSM Parameter Store (SecureString) — never committed
- GCP service account key written to `/tmp` only in GitHub Actions
- IAM: least-privilege — Lambda has DynamoDB read + SSM read only
- DynamoDB PITR enabled
- GCP key rotation: 90-day cycle (manual)
- Budget alerts: AWS $5/month (Lambda + DynamoDB), GCP 25 PLN/month

---

## License

MIT