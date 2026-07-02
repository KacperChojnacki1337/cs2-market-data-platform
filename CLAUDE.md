# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

All code, comments, commit messages, PR descriptions, issue titles, and documentation must be written in **English**. No exceptions.

## Project Overview

**CS2 Skin Portfolio Tracker** — production-grade data engineering pipeline tracking Counter-Strike 2 skin inventory with real-time Steam market prices, PnL calculation in USD and PLN, and dbt-based medallion architecture across AWS (DynamoDB, Lambda) and GCP (BigQuery, Looker Studio).

Core flow: DynamoDB (inventory) → Producer Lambda (prices + exchange rates) → BigQuery bronze → dbt (silver/gold) → Looker Studio.

## Architecture Overview

```
DynamoDB (source of truth — OLTP)
    |
Producer Lambda — two-pass price fetching
    |-- Pass 1 (07:00 UTC): 20 concurrent Lambdas x 5 items each
    |   |-- Daily shuffle (date-seeded) rotates item-to-batch — no item stuck on bad IP
    |   |-- Scans DynamoDB, fetches Steam prices + NBP exchange rates
    |   +-- Writes: assets_history, sales_history, prices_history, volume_history, exchange_rates
    +-- Pass 2 (07:30 UTC): 1 Lambda smart retry — only items without valid price today
    |
BigQuery (medallion architecture)
    |-- steam_raw        (bronze — raw Lambda inserts)
    |-- steam_staging    (silver — dbt views)
    +-- steam_marts      (gold — dbt materialized tables)
    ^
dbt Pipeline (08:00 UTC via GitHub Actions; Airflow Phase 2+ will replace GH Actions cron)
    +-- Pre-run freshness check (abort if today's prices_history is empty)
    |
Looker Studio (dashboard over steam_marts — issue #63, planned)
```

### Key Design Decisions

| Component | Choice | Why |
|-----------|--------|-----|
| Source of Truth | DynamoDB | Schemaless, serverless, PITR enabled — OLTP layer |
| Compute | AWS Lambda | Event-driven, zero idle cost, direct BQ write |
| Data Warehouse | BigQuery (EU) | Serverless, columnar, native dbt, GDPR-compliant |
| Transformations | dbt | Version-controlled SQL, lineage, automated testing |
| IaC | Terraform | DynamoDB, Lambda, IAM, BigQuery datasets + budget alerts |
| CI/CD | GitHub Actions | dbt on push to main (`dbt/**`) + daily 08:00 UTC cron |
| Secrets | AWS SSM Parameter Store | Encrypted GCP service account key |
| Exchange Rates | NBP API | Free Polish National Bank rates |
| No message broker | Direct BQ write | Scheduled pipeline, Kafka adds complexity without value |
| Steam rate limiting | 20 Lambdas × 5 items + daily shuffle | Fresh AWS IP per Lambda bypasses per-IP limit |
| Smart retry | Single Lambda at 07:30 with `retry_missing=true` | Queries BQ for missing prices, fetches only those |
| Airflow orchestrator | Oracle Cloud ARM A1 VM (Phase 2) | Replaces EventBridge + GH Actions cron; dependency-graph-based scheduling |

## Planned Features (GitHub Issues)

| Issue | Feature | Status |
|-------|---------|--------|
| [#61](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/61) | Real cash value coefficient per category in `fct_portfolio` | Done |
| [#67](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/67) | Skinport as second price source | Done |
| [#68](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/68) | EUR/PLN exchange rate from NBP | Done |
| [#69](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/69) | Steam volume history + liquidity risk flag | Done |
| [#82](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/82) | Decouple price_flagged from zero volume | Done |
| [#83](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/83) | Skinport fee 5%→8% + net Skinport columns | Done |
| [#70](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/70) | Airflow orchestrator (Phase 1: DAG skeleton done; Phase 2: Oracle VM deploy; Phase 3: cutover) | In Progress |
| [#63](https://github.com/KacperChojnacki1337/cs2-market-data-platform/issues/63) | Looker Studio dashboard | Planned |

## Project Structure

```
cs2-market-data-platform/
|-- .github/workflows/dbt.yml           # GH Actions: freshness check + dbt run/test/docs
|-- airflow/
|   |-- dags/cs2_pipeline_dag.py        # Airflow DAG (replaces EventBridge + GH Actions cron)
|   |-- docker-compose.yml              # LocalExecutor + Postgres (Oracle Cloud ARM A1)
|   |-- Dockerfile                      # Airflow 2.10.2 + dbt-bigquery + providers-amazon
|   +-- .env.example
|-- dbt/steam_tracker/
|   |-- dbt_project.yml                 # Layer -> dataset routing via +schema
|   |-- packages.yml                    # dbt_utils v1.3.0
|   |-- macros/generate_schema_name.sql # staging/intermediate -> steam_staging, marts -> steam_marts
|   +-- models/
|       |-- staging/                    # -> steam_staging (views)
|       |-- intermediate/               # -> steam_staging (views)
|       +-- marts/                      # -> steam_marts (tables)
|-- lambda/
|   +-- producer/
|       |-- producer_lambda.py          # Mode dispatch: sync_inventory / exchange_rates / batch_prices / legacy
|       +-- tests/test_producer.py      # 24 pytest unit tests (unittest.mock, no moto)
|-- terraform/
|   +-- main.tf                         # DynamoDB, Lambda, IAM, 3 BQ datasets, budget alerts
+-- scripts/
    +-- backfill.py                     # Manual backfill for a specific date
```

## Development Commands

### Terraform

```bash
cd terraform
terraform fmt -check && terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

### dbt

```bash
cd dbt/steam_tracker
dbt deps
dbt run --target dev          # local: writes to *_dev datasets
dbt run --target prod         # GH Actions only
dbt test
dbt docs generate && dbt docs serve
```

### Lambda — Tests

```bash
cd lambda/producer
pytest tests/ -v
```

### Lambda — Manual Invocation

```bash
# Legacy mode (all-in-one, backward compat)
aws lambda invoke --function-name steam_price_producer --region eu-central-1 response.json

# New Airflow modes (single-responsibility)
aws lambda invoke --function-name steam_price_producer --region eu-central-1 \
  --payload '{"mode":"sync_inventory"}' response.json

aws lambda invoke --function-name steam_price_producer --region eu-central-1 \
  --payload '{"mode":"exchange_rates"}' response.json

aws lambda invoke --function-name steam_price_producer --region eu-central-1 \
  --payload '{"mode":"batch_prices","batch_index":0,"batch_size":5}' response.json

aws logs tail /aws/lambda/steam_price_producer --follow
```

### Backfill

```bash
python scripts/backfill.py --date 2026-01-15
python scripts/backfill.py --start-date 2026-01-13 --end-date 2026-01-15
```

### Adding Inventory Items to DynamoDB

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

# Sell event
aws dynamodb put-item --table-name steam_inventory_metadata --item '{
  "asset_id":   {"S": "UNIQUE-UUID"},
  "item_id":    {"S": "AWP | Printstream (Well-Worn)"},
  "event_type": {"S": "sell"},
  "sell_price": {"N": "180.00"},
  "sell_currency": {"S": "PLN"},
  "sell_date":  {"S": "2026-03-01"},
  "sell_channel": {"S": "CSFloat"},
  "updated_at": {"S": "2026-03-01T00:00:00Z"}
}'
# sell_channel values: "Steam" (15% fee), "CSFloat" (2%), "Skinport" (8%), "Unknown" (0%)
# sell_price must always be in PLN
```

## Data Models

### Medallion Architecture

**Bronze** — `steam_raw`: `assets_history`, `sales_history`, `prices_history`, `volume_history`, `exchange_rates`. All partitioned by `DATE(timestamp)`.

**Silver** — `steam_staging` (dbt views): `stg_assets`, `stg_sales`, `stg_prices`, `stg_exchange_rates`, `int_latest_prices` (ROW_NUMBER dedup), `int_latest_exchange_rate`.

**Gold** — `steam_marts` (dbt tables, full rebuild on every run):
- `dim_assets` — deduplicated buy dimension, surrogate key via `dbt_utils.generate_surrogate_key`
- `fct_portfolio` — unrealized PnL per active position. Key columns:
  - Steam: `current_value_pln`, `pnl_total_pln`, `pnl_pct`, `net_value_steam_pln` (×0.85)
  - CSFloat: `real_cash_value_pln` (×`real_cash_coeff`), `real_cash_pnl_pln/pct`
  - Skinport: `skinport_price_pln` (gross), `net_value_skinport_pln` (×0.92), `net_skinport_pnl_pln/pct`
  - Liquidity: `volume_7d`, `liquidity_risk` (LOW/MEDIUM/HIGH), `coeff_accuracy`
  - real_cash_coeff per category: Knife=0.83, Gloves=0.80, Skin/Case=0.74, Agent=0.60, Sticker=0.49, Other=0.65
- `fct_portfolio_history` — daily snapshots (incremental, partitioned by `snapshot_date`); sold positions excluded via time-aware anti-join
- `fct_realized_pnl` — closed positions; fee from `sell_channel` (Steam=15%, CSFloat=2%, Skinport=8%, Unknown=0%); `net_sell_price_pln`, `realized_pnl_pln/pct`, `holding_period_days`

### Steam Data Quality

`price_flagged = TRUE` if price deviates >50% from 7-day median. Volume=0 alone does NOT flag (rare items can have 0 weekly sales with a valid price — #82). Flagged prices are stored but excluded from `int_latest_prices`. `fct_portfolio` shows NULL for items with no valid price.

### Lambda Mode Dispatch (`producer_lambda.py`)

`lambda_handler` reads `event.get('mode')` and routes:
- `mode=sync_inventory` — DynamoDB scan → assets/sales to BQ (1×, no race condition)
- `mode=exchange_rates` — NBP USD/PLN + EUR/PLN → BQ (1×, idempotent)
- `mode=batch_prices` — Steam prices + volume for batch slice (20× parallel fan-out)
- no mode → legacy all-in-one (EventBridge, backfill, retry_missing — backward compat)

Airflow DAG invokes the first 3 modes as single-responsibility tasks. EventBridge continues using legacy mode until Phase 3 cutover.

## Critical Implementation Details

- **Lambda timeout**: 300s / Memory: 256 MB
- **GCP key**: fetched from SSM (`/steam-tracker/gcp-key`) and cached at module level
- **NBP fallback**: `/today/` → 404 on weekends/holidays → `/last/1/`
- **Steam retry**: 5 attempts, 10/20/30/40s backoff on 429
- **Idempotency**: assets/sales protected by `SELECT DISTINCT asset_id` pre-check; prices/exchange_rates deduped by `ROW_NUMBER()` in silver; `DOUBLE_FIRE_DETECTED` log for non-batch re-runs (non-blocking)
- **Backfill**: `event["date"]` → all BQ timestamps use `{date}T12:00:00+00:00`
- **Lambda layer**: `google-cloud-bigquery`, `google-auth`, `requests`, `brotli` (Skinport API requires `Accept-Encoding: br`)
- **CloudWatch alarms**: errors, duration >240s, data freshness at 07:30 UTC
- **GH Actions freshness check**: `COUNT(*) FROM prices_history WHERE DATE(timestamp) = CURRENT_DATE()` — aborts dbt if 0

## Secret Rotation

GCP service account key — **Next rotation due: 2026-09-15** (key created 2026-06-17).

1. GCP Console → IAM → Service Accounts → `terraform-deployer@steam-tracker-portfolio.iam.gserviceaccount.com` → Keys → Add Key → JSON
2. `aws ssm put-parameter --name "/steam-tracker/gcp-key" --type SecureString --value "$(cat new-key.json)" --overwrite --region eu-central-1`
3. GitHub repo Settings → Secrets → `GCP_SA_KEY` → update
4. Verify: `aws lambda invoke --function-name steam_price_producer --region eu-central-1 response.json` → check CloudWatch for `INVOCATION_END`
5. Delete old key in GCP Console
6. Update rotation date in this file (90 days from today)

## Security Model

- DynamoDB: PITR enabled
- Secrets: SSM `SecureString`; GCP key never committed
- IAM: least-privilege — Producer: DynamoDB read + SSM read
- Key rotation: 90-day cycle (manual)

## Environment Variables (Terraform-managed)

**Producer Lambda**: `DYNAMODB_TABLE`, `GCP_PROJECT_ID`, `BQ_DATASET_RAW`, `GCP_KEY_PARAM`

**Airflow container** (see `airflow/.env.example`): `LAMBDA_FUNCTION_NAME`, `LAMBDA_REGION`, `DBT_PROJECT_DIR`, `DBT_PROFILES_DIR`, `GOOGLE_APPLICATION_CREDENTIALS`, AWS credentials

## Budget Alerts

- **GCP**: Terraform `google_billing_budget` — 25/50/100% of 25 PLN/month
- **AWS**: Terraform `aws_budgets_budget` — 25/50/100% of $5/month (Lambda + DynamoDB)

## Environments

| Environment | Branch | BigQuery datasets | Lambda |
|-------------|--------|-------------------|--------|
| Production | `main` | `steam_raw`, `steam_staging`, `steam_marts` | `steam_price_producer` |
| Development | `develop` / `feature/*` | `steam_raw_dev`, `steam_staging_dev`, `steam_marts_dev` | shared (no dev Lambda) |

### Branch Strategy

```
feature/* -> develop -> main
```

- `main` — production, protected (PR required)
- `develop` — integration, CI runs dbt in dev environment
- `feature/*` — individual changes, PR to develop

### GitHub Actions Triggers

- **PR to `main` or `develop`** — `dbt run --target dev` + `dbt test` against `*_dev`
- **Push to `main`** — `dbt run --target prod` + `dbt test` + `dbt docs generate`
- **Daily 08:00 UTC** — prod dbt run (will be replaced by Airflow `run_dbt` task in Phase 3)
