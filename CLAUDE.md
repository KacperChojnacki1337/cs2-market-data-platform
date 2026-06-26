# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

All code, comments, commit messages, PR descriptions, issue titles, and documentation must be written in **English**. No exceptions.

## Project Overview

**CS2 Skin Portfolio Tracker** is a production-grade data engineering pipeline that tracks Counter-Strike 2 skin inventory, fetches real-time market prices from Steam, and calculates portfolio value with both unrealized and realized PnL in USD and PLN. It demonstrates a modern data architecture spanning AWS (DynamoDB, Lambda) and GCP (BigQuery, Looker Studio) with dbt transformations.

Core flow:
- Track CS2 skin inventory (buy and sell events) stored in AWS DynamoDB
- Fetch live prices from Steam Market API and USD/PLN exchange rates from Poland's National Bank (NBP)
- Producer Lambda writes directly to BigQuery (no message broker)
- Transform via dbt with full medallion architecture (bronze/silver/gold)
- Calculate portfolio metrics: current value, unrealized PnL, realized PnL, PnL percentage
- Visualize in Looker Studio

## Architecture Overview

```
DynamoDB (source of truth â€” OLTP)
    â†“
Producer Lambda — two-pass price fetching (07:00 + 07:30 UTC via EventBridge)
    â”śâ”€ Pass 1 (07:00): 20 concurrent Lambdas Ã— 5 items each — different IPs bypass Steam rate limit
    â”‚   â”śâ”€ Daily shuffle (seeded by date) rotates item-to-batch assignment — no item stuck on bad IP
    â”‚   â”śâ”€ Scans DynamoDB inventory (buy + sell events)
    â”‚   â”śâ”€ Fetches Steam prices with 429 retry backoff (5 attempts, 10/20/30/40s waits)
    â”‚   â”śâ”€ Fetches NBP exchange rates (once, idempotent)
    â”‚   â””â”€ Writes to BigQuery: assets_history, sales_history, prices_history, exchange_rates
    â””â”€ Pass 2 (07:30): 1 Lambda smart retry — queries BQ for items without valid price today, fetches only those
    â†”
BigQuery (medallion architecture)
    â”śâ”€ steam_raw        (bronze â€” raw inserts from Lambda)
    â”śâ”€ steam_staging    (silver â€” type casts, views via dbt)
    â””â”€ steam_marts      (gold â€” business tables via dbt)
    â†’
dbt Pipeline (daily 08:00 UTC via GitHub Actions, + manual pushes to dbt/**)
    â””â”€ Pre-run data freshness check (abort if today’s data missing)
    â†”
Looker Studio (dashboard over steam_marts — planned, issue #63)
```

### Key Design Decisions

| Component | Choice | Why |
|-----------|--------|-----|
| Source of Truth | DynamoDB | Schemaless, serverless, PITR enabled â€” OLTP layer |
| Compute | AWS Lambda | Event-driven, zero idle cost, single producer writes directly to BQ |
| Data Warehouse | BigQuery (EU region) | Serverless, columnar, native dbt, GDPR-compliant |
| Transformations | dbt | Version-controlled SQL, lineage, automated testing, surrogate keys |
| Medallion | 3 BQ datasets | bronze/silver/gold aligned with dbt layers (staging/intermediate/marts) |
| IaC | Terraform | Complete infra â€” DynamoDB, Lambda, IAM, BigQuery datasets + budget alerts |
| CI/CD | GitHub Actions | dbt runs on push to main (if `dbt/**` changed) + daily 08:00 UTC schedule |
| Secrets | AWS SSM Parameter Store | Encrypted GCP service account key |
| Exchange Rates | NBP API | Free Polish National Bank rates, fetched once per invocation |
| Visualization | Looker Studio | Free, native GCP, no extra infra |
| No message broker | Direct BQ write | Pipeline is scheduled (not real-time), Kafka added complexity without value |
| Steam rate limiting | 20 concurrent Lambdas × 5 items + daily shuffle | Each Lambda gets fresh AWS IP; shuffle prevents same items hitting same IP daily |
| Smart retry | Single Lambda at 07:30 with `retry_missing=true` | Queries BQ for missing prices, fetches only those on fresh IP — no wasted Steam requests |

## Planned Features (GitHub Issues)

| Issue | Feature | Status |
|-------|---------|--------|
| [#61](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/61) | Real cash value coefficient per category in `fct_portfolio` | Planned |
| [#62](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/62) | Azure Data Factory as cross-cloud pipeline orchestrator | Planned |
| [#63](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/63) | Looker Studio dashboard | Planned |

## Project Structure

```
cs2-market-data-platform/
â”śâ”€â”€ .github/workflows/dbt.yml           # GitHub Actions pipeline (with freshness check)
â”śâ”€â”€ dbt/steam_tracker/
â”‚   â”śâ”€â”€ dbt_project.yml                 # Layer â†’ dataset routing via +schema
â”‚   â”śâ”€â”€ packages.yml                    # Uses dbt_utils v1.3.0
â”‚   â”śâ”€â”€ macros/
â”‚   â”‚   â””â”€â”€ generate_schema_name.sql   # Routes staging/intermediate â†’ steam_staging, marts â†’ steam_marts
â”‚   â””â”€â”€ models/
â”‚       â”śâ”€â”€ staging/                    # â†’ steam_staging dataset (views)
â”‚       â”‚   â”śâ”€â”€ sources.yml
â”‚       â”‚   â”śâ”€â”€ stg_assets.sql
â”‚       â”‚   â”śâ”€â”€ stg_sales.sql
â”‚       â”‚   â”śâ”€â”€ stg_prices.sql
â”‚       â”‚   â””â”€â”€ stg_exchange_rates.sql
â”‚       â”śâ”€â”€ intermediate/               # â†’ steam_staging dataset (views)
â”‚       â”‚   â”śâ”€â”€ int_latest_prices.sql
â”‚       â”‚   â””â”€â”€ int_latest_exchange_rate.sql
â”‚       â””â”€â”€ marts/                      # â†’ steam_marts dataset (tables)
â”‚           â”śâ”€â”€ schema.yml
â”‚           â”śâ”€â”€ dim_assets.sql
â”‚           â”śâ”€â”€ fct_portfolio.sql       # Unrealized PnL
â”‚           â””â”€â”€ fct_realized_pnl.sql   # Realized PnL (closed positions)
â”śâ”€â”€ lambda/
â”‚   â””â”€â”€ producer/
â”‚       â”śâ”€â”€ producer_lambda.py          # Scan DynamoDB, validate prices, write to BigQuery
â”‚       â”śâ”€â”€ requirements.txt
â”‚       â”śâ”€â”€ layer/                      # Lambda layer (zipped by Terraform)
â”‚       â””â”€â”€ tests/
â”‚           â””â”€â”€ test_producer.py        # pytest unit tests (moto + unittest.mock)
â”śâ”€â”€ terraform/
â”‚   â”śâ”€â”€ provider.tf
â”‚   â”śâ”€â”€ main.tf                         # DynamoDB, Lambda, IAM, 3 BQ datasets, budget alert
â”‚   â”śâ”€â”€ variables.tf
â”‚   â”śâ”€â”€ terraform.tfvars                # Git-ignored
â”‚   â””â”€â”€ .terraform.lock.hcl
â””â”€â”€ scripts/
    â”śâ”€â”€ seed_dim_assets.py
    â””â”€â”€ backfill.py                     # Manual backfill for a specific date
```

## Development Commands

### Prerequisites
- AWS CLI configured (`aws configure`)
- Terraform >= 1.5
- GCP project with BigQuery API enabled + service account JSON key
- Python 3.11
- `pip install dbt-bigquery pytest moto boto3`

### Terraform

```bash
cd terraform

terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
terraform destroy  # WARNING: irreversible
```

### dbt

All commands run from `dbt/steam_tracker/`:

```bash
cd dbt/steam_tracker

dbt deps
dbt run
dbt run -s dim_assets
dbt run -s path:models/staging
dbt test
dbt test -s fct_portfolio
dbt docs generate && dbt docs serve
```

### Lambda â€” Unit Tests

```bash
cd lambda/producer
pytest tests/ -v
```

### Lambda â€” Manual Invocation

```bash
aws lambda invoke \
  --function-name steam_price_producer \
  --region eu-central-1 \
  response.json

aws logs tail /aws/lambda/steam_price_producer --follow
```

### Backfill

```bash
# Single day
python scripts/backfill.py --date 2026-01-15

# Range (inclusive, --end-date defaults to yesterday)
python scripts/backfill.py --start-date 2026-01-13 --end-date 2026-01-15
python scripts/backfill.py --start-date 2026-01-13
```

### GitHub Actions (CI/CD)

The dbt pipeline triggers automatically:
1. On push to `main` if any files in `dbt/**` changed
2. Daily at 08:00 UTC
3. Manual trigger via `workflow_dispatch`

Steps: freshness check â†’ `dbt deps` â†’ `dbt run` â†’ `dbt test` â†’ `dbt docs generate`

Required secret: `GCP_SA_KEY` (GCP service account JSON contents)

### Adding Inventory Items to DynamoDB

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

# Sell event
aws dynamodb put-item \
  --table-name steam_inventory_metadata \
  --item '{
    "asset_id": {"S": "UNIQUE-UUID"},
    "item_id": {"S": "AWP | Printstream (Well-Worn)"},
    "event_type": {"S": "sell"},
    "sell_price": {"N": "180.00"},
    "sell_currency": {"S": "PLN"},
    "sell_date": {"S": "2026-03-01"},
    "sell_channel": {"S": "CSFloat"},
    "updated_at": {"S": "2026-03-01T00:00:00Z"}
  }'
# sell_channel values: "Steam" (15% fee), "CSFloat" (2%), "Skinport" (5%), "Unknown" (0%)
# sell_price must always be in PLN (convert manually if platform pays in USD)
```

### Storing GCP Service Account Key in SSM

```bash
aws ssm put-parameter \
  --name "/steam-tracker/gcp-key" \
  --type "SecureString" \
  --value "<JSON contents>"
```

## Data Models

### Medallion Architecture

**Bronze** â€” `steam_raw` dataset (raw inserts from Lambda):
- `assets_history` â€” buy events from DynamoDB
- `sales_history` â€” sell events from DynamoDB
- `prices_history` â€” Steam market prices (with `price_flagged` column for data quality)
- `exchange_rates` â€” NBP USD/PLN rates

All tables partitioned by `DATE(timestamp)`.

**Silver** â€” `steam_staging` dataset (views, dbt staging + intermediate):
- `stg_assets` â€” type casts, uppercases `buy_currency`
- `stg_sales` â€” type casts, uppercases `sell_currency`, adds `sell_channel` with `COALESCE(..., ‘Unknown’)`, renames `timestamp` â†’ `sold_at`
- `stg_prices` â€” casts price, renames `timestamp` â†’ `fetched_at`; `price_flagged` column planned in Issue #8
- `stg_exchange_rates` â€” renames `source` â†’ `rate_source`, `timestamp` â†’ `fetched_at`
- `int_latest_prices` â€” latest Steam price per `item_id` using `ROW_NUMBER()`
- `int_latest_exchange_rate` â€” latest USD/PLN rate

**Gold** â€” `steam_marts` dataset (materialized tables):
- `dim_assets` â€” deduplicated buy dimension, surrogate key via `dbt_utils.generate_surrogate_key(['asset_id'])`
- `fct_portfolio` â€” current unrealized PnL per asset (recreated daily):
  - `current_value_usd = price_usd Ă— quantity`
  - `current_value_pln = price_usd Ă— usd_pln_rate Ă— quantity`
  - `pnl_per_unit_pln = (price_usd Ă— usd_pln_rate) - buy_price_pln`
  - `pnl_total_pln = pnl_per_unit_pln Ă— quantity`
  - `pnl_pct = (pnl_per_unit_pln / buy_price_pln) Ă— 100`
  - `net_value_steam_usd/pln = current_value Ă— 0.85` (estimated value after Steam 15% fee)
  - `net_pnl_steam_pln = net_value_steam_pln - buy_price_pln`
  - `net_pnl_pct_steam = (net_pnl_steam_pln / buy_price_pln) Ă— 100`
  - `real_cash_coeff` — per-category CSFloat coefficient from dbt seed (issue #61)
  - `real_cash_value_pln = current_value_pln × real_cash_coeff` (real money if sold on CSFloat)
  - `real_cash_pnl_pln = real_cash_value_pln - buy_price_pln`
  - `real_cash_pnl_pct = (real_cash_pnl_pln / buy_price_pln) × 100`
  - Coefficients: Knife=0.83, Gloves=0.80, Skin/Case=0.74, Agent=0.60, Sticker=0.49, Other=0.65
- `fct_portfolio_history` â€” daily portfolio value snapshots (incremental, partitioned by `snapshot_date`):
  - Joins `stg_prices` Ă— `stg_assets` Ă— `stg_exchange_rates` by date
  - `portfolio_value_usd/pln`, `total_cost_pln`, `unrealized_pnl_pln/pct`, `active_positions`
  - Enables time-series charts in Looker Studio; sold positions excluded via time-aware LEFT JOIN anti-join (`sell_date <= snapshot_date`)
- `fct_realized_pnl` â€” realized PnL on closed positions:
  - Joins `dim_assets` Ă— `stg_sales` by `item_id`
  - `fee_pct` derived from `sell_channel`: Steam=15%, CSFloat=2%, Skinport=5%, Unknown=0%
  - `gross_sell_price_pln` â€” sell price before fee
  - `fee_amount_pln = gross_sell_price_pln Ă— fee_pct / 100`
  - `net_sell_price_pln = gross_sell_price_pln Ă— (1 - fee_pct / 100)`
  - `realized_pnl_pln = net_sell_price_pln - buy_price_pln`
  - `realized_pnl_pct = (realized_pnl_pln / buy_price_pln) Ă— 100`
  - `holding_period_days = sell_date - buy_date`

### dbt Schema Routing (`generate_schema_name` macro)

```
staging/     â†’ steam_staging
intermediate/ â†’ steam_staging
marts/       â†’ steam_marts
```

## Critical Implementation Details

### Producer Lambda (`lambda/producer/producer_lambda.py`)

- Trigger: EventBridge daily 07:00 UTC (1 hour before dbt run)
- Timeout: 300s / Memory: 256 MB
- **Event routing**: `event_type` field routes items â€” `buy` â†’ `assets_history` + Steam price fetch, `sell` â†’ `sales_history` (no price fetch for sold items). Missing `event_type` defaults to `buy` for backwards compatibility
- **Event-driven assets insert**: queries `SELECT DISTINCT asset_id FROM assets_history` before the loop â€” only inserts buy events not yet present in BigQuery (buy events are immutable, no re-inserts needed)
- **Event-driven sales insert**: queries `SELECT DISTINCT asset_id FROM sales_history` before the loop â€” skips re-inserts of already recorded sell events
- **Idempotency**: data quality guaranteed by `ROW_NUMBER()` deduplication in the silver layer (`int_latest_prices`, `int_latest_exchange_rate`); `assets_history` and `sales_history` protected by event-driven insert checks. EventBridge double-fire detected via structured CloudWatch log (`DOUBLE_FIRE_DETECTED | date=... | existing_price_rows=...`) â€” non-blocking by design so legitimate re-runs are never blocked
- **Backfill mode**: accepts optional `date` parameter in event payload to write data for a specific past date
- Fetches NBP rate **once per invocation** (not per item)
- Retries with exponential backoff: 3 attempts, 2s base for Steam + NBP calls
- GCP service account key fetched from SSM and **cached at module level**
- NBP fallback: tries `/today/` first; falls back to `/last/1/` on weekends/holidays
- Writes directly to BigQuery via `insert_rows_json()` (no message broker)

### Steam Data Quality

Prices are validated before insert:
- **Flagging condition**: price is marked `price_flagged = TRUE` if Steam returns 0 recent sales or price deviates > 50% from 7-day median
- Flagged prices are **stored but excluded** from `int_latest_prices` (not silently dropped)
- `fct_portfolio` will show `NULL` current value for items with no valid price rather than stale/wrong data

### Lambda Unit Tests (`lambda/producer/tests/test_producer.py`)

Coverage (9 tests):
1. NBP fallback — weekend/holiday 404 → `/last/1/` endpoint used
2. Steam price zero volume → `price_flagged = True`
3. Steam price spike > 50% deviation from 7-day median → `price_flagged = True`
4. Steam price within threshold (49% deviation) → `price_flagged = False`
5. Normal price, no median → `price_flagged = False` (only volume check)
6. Buy event idempotency — existing `asset_id` in BQ → row skipped, no re-insert
7. Missing `event_type` defaults to buy (backwards compatibility with old DynamoDB items)
8. `get_steam_price` returns None (API failure) → price row skipped, handler continues
9. Backfill mode — `event[“date”]` → all BQ row timestamps use that date

HTTP calls mocked via `unittest.mock`. Module-level SSM + GCP credential init patched in `conftest.py` before import.

### Lambda Layer

Shared Python layer: `google-cloud-bigquery`, `google-auth`, `requests`. Built and zipped by Terraform.

### BigQuery Table Partitioning

All `steam_raw` tables partitioned by `DATE(timestamp)`:
- Reduces scan cost on historical queries
- Enables partition-based idempotency check (`WHERE DATE(timestamp) = CURRENT_DATE()`)

### CloudWatch Alarms

Four alarms â†’ SNS email:
- Producer errors (any)
- Producer duration > 240s (80% of timeout)
- Data freshness: custom metric if today's BQ partition is empty at 07:30 UTC

### Data Freshness Check (GitHub Actions)

Before `dbt run`, a Python step queries BigQuery:
```sql
SELECT COUNT(*) FROM steam_raw.prices_history
WHERE DATE(timestamp) = CURRENT_DATE()
```
If count = 0 â†’ workflow fails with alert, dbt does not run on stale data.

### Secret Rotation

GCP service account key must be rotated every 90 days. **Next rotation due: 2026-09-15** (key created 2026-06-17).

**Full rotation checklist:**

1. **Generate new key** — GCP Console → IAM & Admin → Service Accounts → `terraform-deployer@steam-tracker-portfolio.iam.gserviceaccount.com` → Keys → Add Key → JSON → Download

2. **Update SSM** (Lambda reads from here at runtime):
   ```bash
   aws ssm put-parameter \
     --name "/steam-tracker/gcp-key" \
     --type "SecureString" \
     --value "$(cat new-key.json)" \
     --overwrite \
     --region eu-central-1
   ```

3. **Update GitHub Actions secret** — repo Settings → Secrets → Actions → `GCP_SA_KEY` → Update with contents of `new-key.json`

4. **Verify Lambda works** with new key:
   ```bash
   aws lambda invoke \
     --function-name steam_price_producer \
     --region eu-central-1 \
     response.json && cat response.json
   ```
   Check CloudWatch logs for `INVOCATION_END` — if it appears, new key is valid.

5. **Delete old key** — GCP Console → IAM & Admin → Service Accounts → Keys → Delete the old key ID

6. **Update next rotation date** in this file (`CLAUDE.md`) — set 90 days from today.

## Security Model

- **DynamoDB**: PITR enabled
- **Secrets**: SSM Parameter Store (`SecureString`)
- **IAM**: Least-privilege per Lambda
  - Producer: DynamoDB read + SSM read (GCP key)
- **GCP key**: Never committed; GitHub Actions writes to `/tmp` only
- **Key rotation**: 90-day cycle (manual)

## Environment Variables (Terraform-managed)

**Producer**: `DYNAMODB_TABLE`, `GCP_PROJECT_ID`, `BQ_DATASET_RAW`, `GCP_KEY_PARAM`

## Budget Alerts

**GCP** — Terraform-managed `google_billing_budget`: email notification (via `google_monitoring_notification_channel`) at 25%, 50%, 100% of 25 PLN/month on the project. Requires `Billing Account Costs Manager` role on the billing account and `Monitoring Notification Channel Editor` role on the project for the service account.

**AWS** — Terraform-managed `aws_budgets_budget`: email notification at 25%, 50%, 100% of $5/month covering Lambda + DynamoDB.

## Environments

| Environment | Branch | BigQuery datasets | Lambda |
|-------------|--------|-------------------|--------|
| Production | `main` | `steam_raw`, `steam_staging`, `steam_marts` | `steam_price_producer` |
| Development | `develop` / `feature/*` | `steam_raw_dev`, `steam_staging_dev`, `steam_marts_dev` | shared (no dev Lambda) |

### Branch Strategy

```
feature/* â†’ develop â†’ main
```

- `main` â€” production, protected (PR required)
- `develop` â€” integration branch, CI runs dbt in dev environment
- `feature/*` â€” individual changes, PR to develop

### dbt Targets

- `dev` (default locally) â€” writes to `*_dev` BigQuery datasets
- `prod` â€” writes to production datasets, used only by GitHub Actions on `main`

```bash
dbt run --target dev   # local development
dbt run --target prod  # production (GitHub Actions only)
```

### GitHub Actions Environments

- **PR to `main` or `develop`** â€” runs `dbt run --target dev` + `dbt test` against `*_dev` datasets
- **Push to `main`** â€” runs `dbt run --target prod` + `dbt test` + `dbt docs generate`

Dev datasets cost $0 at this scale â€” identical small data split across separate BigQuery datasets.


