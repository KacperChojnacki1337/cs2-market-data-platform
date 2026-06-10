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
DynamoDB (source of truth — OLTP)
    ↓
Producer Lambda (daily 07:00 UTC via EventBridge)
    ├─ Scans inventory (buy + sell events)
    ├─ Fetches Steam prices (with data quality validation)
    ├─ Fetches NBP exchange rates
    └─ Writes directly to BigQuery steam_raw
         ├─ assets_history
         ├─ sales_history
         ├─ prices_history
         └─ exchange_rates
    ↓
BigQuery (medallion architecture)
    ├─ steam_raw        (bronze — raw inserts from Lambda)
    ├─ steam_staging    (silver — type casts, views via dbt)
    └─ steam_marts      (gold — business tables via dbt)
    ↑
dbt Pipeline (daily 08:00 UTC via GitHub Actions, + manual pushes to dbt/**)
    └─ Pre-run data freshness check (abort if today's data missing)
    ↓
Looker Studio (dashboard over steam_marts)
```

### Key Design Decisions

| Component | Choice | Why |
|-----------|--------|-----|
| Source of Truth | DynamoDB | Schemaless, serverless, PITR enabled — OLTP layer |
| Compute | AWS Lambda | Event-driven, zero idle cost, single producer writes directly to BQ |
| Data Warehouse | BigQuery (EU region) | Serverless, columnar, native dbt, GDPR-compliant |
| Transformations | dbt | Version-controlled SQL, lineage, automated testing, surrogate keys |
| Medallion | 3 BQ datasets | bronze/silver/gold aligned with dbt layers (staging/intermediate/marts) |
| IaC | Terraform | Complete infra — DynamoDB, Lambda, IAM, BigQuery datasets + budget alerts |
| CI/CD | GitHub Actions | dbt runs on push to main (if `dbt/**` changed) + daily 08:00 UTC schedule |
| Secrets | AWS SSM Parameter Store | Encrypted GCP service account key |
| Exchange Rates | NBP API | Free Polish National Bank rates, fetched once per invocation |
| Visualization | Looker Studio | Free, native GCP, no extra infra |
| No message broker | Direct BQ write | Pipeline is scheduled (not real-time), Kafka added complexity without value |

## Project Structure

```
cs2-market-data-platform/
├── .github/workflows/dbt.yml           # GitHub Actions pipeline (with freshness check)
├── dbt/steam_tracker/
│   ├── dbt_project.yml                 # Layer → dataset routing via +schema
│   ├── packages.yml                    # Uses dbt_utils v1.3.0
│   ├── macros/
│   │   └── generate_schema_name.sql   # Routes staging/intermediate → steam_staging, marts → steam_marts
│   └── models/
│       ├── staging/                    # → steam_staging dataset (views)
│       │   ├── sources.yml
│       │   ├── stg_assets.sql
│       │   ├── stg_sales.sql
│       │   ├── stg_prices.sql
│       │   └── stg_exchange_rates.sql
│       ├── intermediate/               # → steam_staging dataset (views)
│       │   ├── int_latest_prices.sql
│       │   └── int_latest_exchange_rate.sql
│       └── marts/                      # → steam_marts dataset (tables)
│           ├── schema.yml
│           ├── dim_assets.sql
│           ├── fct_portfolio.sql       # Unrealized PnL
│           └── fct_realized_pnl.sql   # Realized PnL (closed positions)
├── lambda/
│   └── producer/
│       ├── producer_lambda.py          # Scan DynamoDB, validate prices, write to BigQuery
│       ├── requirements.txt
│       ├── layer/                      # Lambda layer (zipped by Terraform)
│       └── tests/
│           └── test_producer.py        # pytest unit tests (moto + unittest.mock)
├── terraform/
│   ├── provider.tf
│   ├── main.tf                         # DynamoDB, Lambda, IAM, 3 BQ datasets, budget alert
│   ├── variables.tf
│   ├── terraform.tfvars                # Git-ignored
│   └── .terraform.lock.hcl
└── scripts/
    ├── seed_dim_assets.py
    └── backfill.py                     # Manual backfill for a specific date
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

### Lambda — Unit Tests

```bash
cd lambda/producer
pytest tests/ -v
```

### Lambda — Manual Invocation

```bash
aws lambda invoke \
  --function-name steam_price_producer \
  --region eu-central-1 \
  response.json

aws logs tail /aws/lambda/steam_price_producer --follow
```

### Backfill

```bash
python scripts/backfill.py --date 2026-01-15
```

### GitHub Actions (CI/CD)

The dbt pipeline triggers automatically:
1. On push to `main` if any files in `dbt/**` changed
2. Daily at 08:00 UTC
3. Manual trigger via `workflow_dispatch`

Steps: freshness check → `dbt deps` → `dbt run` → `dbt test` → `dbt docs generate`

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
    "updated_at": {"S": "2026-03-01T00:00:00Z"}
  }'
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

**Bronze** — `steam_raw` dataset (raw inserts from Lambda):
- `assets_history` — buy events from DynamoDB
- `sales_history` — sell events from DynamoDB
- `prices_history` — Steam market prices (with `price_flagged` column for data quality)
- `exchange_rates` — NBP USD/PLN rates

All tables partitioned by `DATE(timestamp)`.

**Silver** — `steam_staging` dataset (views, dbt staging + intermediate):
- `stg_assets` — type casts, uppercases `buy_currency`
- `stg_sales` — type casts, uppercases `sell_currency` *(planned — Issue #6)*
- `stg_prices` — casts price, renames `timestamp` → `fetched_at`; `price_flagged` column planned in Issue #8
- `stg_exchange_rates` — renames `source` → `rate_source`, `timestamp` → `fetched_at`
- `int_latest_prices` — latest Steam price per `item_id` using `ROW_NUMBER()`
- `int_latest_exchange_rate` — latest USD/PLN rate

**Gold** — `steam_marts` dataset (materialized tables):
- `dim_assets` — deduplicated buy dimension, surrogate key via `dbt_utils.generate_surrogate_key(['asset_id'])`
- `fct_portfolio` — current unrealized PnL per asset (recreated daily):
  - `current_value_usd = price_usd × quantity`
  - `current_value_pln = price_usd × usd_pln_rate × quantity`
  - `pnl_per_unit_pln = (price_usd × usd_pln_rate) - buy_price_pln`
  - `pnl_total_pln = pnl_per_unit_pln × quantity`
  - `pnl_pct = (pnl_per_unit_pln / buy_price_pln) × 100`
- `fct_portfolio_history` — daily portfolio value snapshots (incremental, partitioned by `snapshot_date`):
  - Joins `stg_prices` × `stg_assets` × `stg_exchange_rates` by date
  - `portfolio_value_usd/pln`, `total_cost_pln`, `unrealized_pnl_pln/pct`, `active_positions`
  - Enables time-series charts in Looker Studio; sold position exclusion added after Issue #6
- `fct_realized_pnl` — realized PnL on closed positions *(planned — Issue #6)*:
  - Joins buy events with matching sell events by `asset_id`
  - `realized_pnl_pln = sell_price_pln - buy_price_pln`
  - `holding_period_days = sell_date - buy_date`

### dbt Schema Routing (`generate_schema_name` macro)

```
staging/     → steam_staging
intermediate/ → steam_staging
marts/       → steam_marts
```

## Critical Implementation Details

### Producer Lambda (`lambda/producer/producer_lambda.py`)

- Trigger: EventBridge daily 07:00 UTC (1 hour before dbt run)
- Timeout: 60s / Memory: 256 MB
- **Event-driven assets insert**: queries `SELECT DISTINCT asset_id FROM assets_history` before the loop — only inserts buy events not yet present in BigQuery (buy events are immutable, no re-inserts needed)
- **Idempotency**: checks if today's date already exists in `steam_raw.prices_history` before writing — skips insert if data already present (prevents EventBridge double-fire duplicates) *(planned — Issue #7)*
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

Coverage:
- NBP fallback logic (weekends/holidays → `/last/1/`)
- Price spike detection (> 50% deviation flagged)
- Idempotency check (no duplicate insert when date exists)
- PnL calculation correctness
- DynamoDB scan mocked via `moto`
- Steam/NBP HTTP calls mocked via `unittest.mock`

### Lambda Layer

Shared Python layer: `google-cloud-bigquery`, `google-auth`, `requests`. Built and zipped by Terraform.

### BigQuery Table Partitioning

All `steam_raw` tables partitioned by `DATE(timestamp)`:
- Reduces scan cost on historical queries
- Enables partition-based idempotency check (`WHERE DATE(timestamp) = CURRENT_DATE()`)

### CloudWatch Alarms

Four alarms → SNS email:
- Producer errors (any)
- Producer duration > 48s (80% of timeout)
- Data freshness: custom metric if today's BQ partition is empty at 07:30 UTC

### Data Freshness Check (GitHub Actions)

Before `dbt run`, a Python step queries BigQuery:
```sql
SELECT COUNT(*) FROM steam_raw.prices_history
WHERE DATE(timestamp) = CURRENT_DATE()
```
If count = 0 → workflow fails with alert, dbt does not run on stale data.

### Secret Rotation

GCP service account key stored in SSM (`/steam-tracker/gcp-key`) should be rotated every 90 days. Rotation process: generate new key in GCP IAM → update SSM parameter → verify Lambda invocation succeeds → delete old key.

## Security Model

- **DynamoDB**: PITR enabled
- **Secrets**: SSM Parameter Store (`SecureString`)
- **IAM**: Least-privilege per Lambda
  - Producer: DynamoDB read + SSM read (GCP key)
- **GCP key**: Never committed; GitHub Actions writes to `/tmp` only
- **Key rotation**: 90-day cycle (manual)

## Environment Variables (Terraform-managed)

**Producer**: `DYNAMODB_TABLE`, `GCP_PROJECT_ID`, `BQ_DATASET_RAW`, `GCP_KEY_PARAM`

## BigQuery Budget Alert

Terraform-managed budget alert: email notification when GCP spend exceeds $5/month on the project.

## Environments

| Environment | Branch | BigQuery datasets | Lambda |
|-------------|--------|-------------------|--------|
| Production | `main` | `steam_raw`, `steam_staging`, `steam_marts` | `steam_price_producer` |
| Development | `develop` / `feature/*` | `steam_raw_dev`, `steam_staging_dev`, `steam_marts_dev` | shared (no dev Lambda) |

### Branch Strategy

```
feature/* → develop → main
```

- `main` — production, protected (PR required)
- `develop` — integration branch, CI runs dbt in dev environment
- `feature/*` — individual changes, PR to develop

### dbt Targets

- `dev` (default locally) — writes to `*_dev` BigQuery datasets
- `prod` — writes to production datasets, used only by GitHub Actions on `main`

```bash
dbt run --target dev   # local development
dbt run --target prod  # production (GitHub Actions only)
```

### GitHub Actions Environments

- **PR to `main` or `develop`** — runs `dbt run --target dev` + `dbt test` against `*_dev` datasets
- **Push to `main`** — runs `dbt run --target prod` + `dbt test` + `dbt docs generate`

Dev datasets cost $0 at this scale — identical small data split across separate BigQuery datasets.
