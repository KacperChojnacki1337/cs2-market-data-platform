# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**CS2 Skin Portfolio Tracker** is a production-grade data engineering pipeline that tracks Counter-Strike 2 skin inventory, fetches real-time market prices from Steam, and calculates portfolio value with unrealized PnL in both USD and PLN. It demonstrates a modern, event-driven data architecture spanning AWS (DynamoDB, Lambda), Redpanda (event streaming), and GCP BigQuery (data warehouse).

Core flow:
- Track CS2 skin inventory stored in AWS DynamoDB
- Fetch live prices from Steam Market API and USD/PLN exchange rates from Poland's National Bank (NBP)
- Stream events through Redpanda Serverless (Kafka-compatible)
- Load data into BigQuery and transform via dbt
- Calculate portfolio metrics: current value, unrealized PnL, PnL percentage

## Architecture Overview

```
DynamoDB (source of truth)
    ↓
Producer Lambda (daily 07:00 UTC via EventBridge)
    ├─ Scans inventory
    ├─ Fetches Steam prices
    ├─ Fetches NBP exchange rates
    └─ Publishes 3 Redpanda topics
         ├─ db-inventory-events
         ├─ market-price-events
         └─ exchange-rate-events
    ↓
Redpanda Serverless
    ↓
Consumer Lambda (triggered per topic)
    └─ Routes events to BigQuery by topic
    ↓
BigQuery (medallion architecture)
    ├─ steam_raw (bronze layer)
    │  ├─ assets_history
    │  ├─ prices_history
    │  └─ exchange_rates
    └─ steam_marts (gold layer)
       ├─ stg_assets, stg_prices, stg_exchange_rates (staging views)
       ├─ int_latest_prices, int_latest_exchange_rate (intermediate views)
       ├─ dim_assets (dimension table - deduplicated with surrogate key)
       └─ fct_portfolio (fact table - PnL calculations)
    ↑
dbt Pipeline (daily 08:00 UTC via GitHub Actions, + manual pushes to dbt/**)
```

### Key Design Decisions

| Component | Choice | Why |
|-----------|--------|-----|
| Source of Truth | DynamoDB | Schemaless, serverless, PITR enabled for audit safety |
| Event Broker | Redpanda Serverless | Kafka-compatible, zero ops, decouples ingestion from storage |
| Compute | AWS Lambda | Event-driven, zero idle cost: producer (scheduled) + consumer (event-triggered) |
| Data Warehouse | BigQuery (EU region) | Serverless, columnar, native dbt, GDPR-compliant location |
| Transformations | dbt | Version-controlled SQL, lineage, automated testing, surrogate keys in marts |
| IaC | Terraform | Complete infrastructure definition — DynamoDB, Lambda, IAM, BigQuery datasets |
| CI/CD | GitHub Actions | dbt runs on push to main (if `dbt/**` changed) + daily 08:00 UTC schedule |
| Secrets | AWS SSM Parameter Store | Encrypted parameters for Redpanda + GCP service account key |
| Exchange Rates | NBP API | Free Polish National Bank rates, fetched once per producer invocation |

## Project Structure

```
cs2-skin-vault/
├── .github/workflows/dbt.yml       # GitHub Actions pipeline
├── dbt/steam_tracker/
│   ├── dbt_project.yml
│   ├── packages.yml                # Uses dbt_utils v1.3.0
│   └── models/
│       ├── staging/                # Type casting, no business logic
│       │   ├── sources.yml         # Defines steam_raw sources
│       │   ├── stg_assets.sql
│       │   ├── stg_prices.sql
│       │   └── stg_exchange_rates.sql
│       ├── intermediate/           # Reusable logic, not exposed
│       │   ├── int_latest_prices.sql
│       │   └── int_latest_exchange_rate.sql
│       └── marts/                  # Business-facing tables
│           ├── schema.yml          # dbt tests & descriptions
│           ├── dim_assets.sql      # Deduplicated, surrogate key
│           └── fct_portfolio.sql   # PnL + FX calculations
├── lambda/
│   ├── producer/
│   │   ├── producer_lambda.py      # Scan DynamoDB, fetch prices, publish events
│   │   ├── requirements.txt
│   │   └── layer/                  # Lambda layer (zipped by Terraform)
│   └── consumer/
│       └── consumer_lambda.py      # Route Redpanda events to BigQuery tables
├── terraform/
│   ├── provider.tf
│   ├── main.tf                     # Full infrastructure definition
│   ├── variables.tf
│   ├── terraform.tfvars            # Git-ignored
│   └── .terraform.lock.hcl
└── scripts/seed_dim_assets.py
```

## Development Commands

### Prerequisites
- AWS CLI configured (`aws configure`)
- Terraform >= 1.5
- GCP project with BigQuery API enabled + service account JSON key
- Redpanda Serverless account
- Python 3.11
- `pip install dbt-bigquery`

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

dbt deps                          # Install packages
dbt run                           # Run all models
dbt run -s dim_assets             # Run specific model
dbt run -s path:models/staging    # Run a layer
dbt test
dbt test -s fct_portfolio
dbt docs generate && dbt docs serve
```

### GitHub Actions (CI/CD)

The dbt pipeline triggers automatically:
1. On push to `main` if any files in `dbt/**` changed
2. Daily at 08:00 UTC
3. Manual trigger via `workflow_dispatch`

Steps: `dbt deps` → `dbt run` → `dbt test` → `dbt docs generate`

Required secret: `GCP_SA_KEY` (GCP service account JSON contents)

### Manual Lambda Invocation

```bash
aws lambda invoke \
  --function-name steam_price_producer \
  --region eu-central-1 \
  response.json

aws logs tail /aws/lambda/steam_price_producer --follow
```

### Adding Inventory Items to DynamoDB

```bash
aws dynamodb put-item \
  --table-name steam_inventory_metadata \
  --item '{
    "asset_id": {"S": "UNIQUE-UUID"},
    "item_id": {"S": "AWP | Printstream (Well-Worn)"},
    "buy_price": {"N": "164.81"},
    "buy_currency": {"S": "PLN"},
    "buy_date": {"S": "2026-02-06"},
    "category": {"S": "Skin"},
    "purchase_channel": {"S": "CSFloat"},
    "quantity": {"N": "1"},
    "updated_at": {"S": "2026-02-06T00:00:00Z"}
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

**Staging** (views, no business logic):
- `stg_assets` — type casts, uppercases `buy_currency`
- `stg_prices` — casts price and timestamp
- `stg_exchange_rates` — renames `source` → `rate_source` (avoids SQL reserved word)

**Intermediate** (views, reusable logic):
- `int_latest_prices` — latest Steam price per `item_id` using `ROW_NUMBER()`
- `int_latest_exchange_rate` — latest USD/PLN rate

**Marts** (materialized tables):
- `dim_assets` — deduplicated asset dimension, surrogate key via `dbt_utils.generate_surrogate_key(['asset_id'])`
- `fct_portfolio` — PnL calculations:
  - `current_value_usd = price_usd × quantity`
  - `current_value_pln = price_usd × usd_pln_rate × quantity`
  - `pnl_per_unit_pln = (price_usd × usd_pln_rate) - buy_price_pln`
  - `pnl_total_pln = pnl_per_unit_pln × quantity`
  - `pnl_pct = (pnl_per_unit_pln / buy_price_pln) × 100`

### Event Schema (all JSON with `timestamp`)

**db-inventory-events**: `asset_id`, `item_id`, `buy_date`, `buy_price`, `buy_currency`, `quantity`, `category`, `purchase_channel`

**market-price-events**: `item_id`, `price_usd`

**exchange-rate-events**: `from_currency`, `to_currency`, `rate`, `source`

## Critical Implementation Details

### Producer Lambda (`lambda/producer/producer_lambda.py`)

- Trigger: EventBridge daily 07:00 UTC (1 hour before dbt run)
- Timeout: 60s / Memory: 256 MB
- Fetches NBP rate **once per invocation** (not per item)
- Retries with exponential backoff: 3 attempts, 2s base for Steam + NBP calls
- Redpanda credentials fetched from SSM and **cached at module level**
- NBP fallback: tries `/today/` first; falls back to `/last/1/` on weekends/holidays

### Consumer Lambda (`lambda/consumer/consumer_lambda.py`)

- Trigger: Redpanda event source mappings (3 topics)
- Timeout: 30s / Memory: 256 MB
- Decodes Base64 payloads from Redpanda
- Routes by topic to the corresponding BigQuery table via `insert_rows_json()`
- GCP service account key fetched from SSM and **cached at module level**

### Lambda Layer

Shared Python layer for both Lambdas: `google-cloud-bigquery`, `google-auth`, `confluent-kafka`, `requests`. Built and zipped by Terraform.

### CloudWatch Alarms

Four alarms → SNS email:
- Producer errors (any), producer duration > 48s (80% of timeout)
- Consumer errors (any), consumer duration > 24s (80% of timeout)

## Security Model

- **DynamoDB**: PITR enabled
- **Secrets**: SSM Parameter Store (`SecureString`)
- **IAM**: Least-privilege per Lambda
  - Producer: DynamoDB read + SSM read (Redpanda creds)
  - Consumer: SSM read (GCP key) only
- **GCP key**: Never committed; GitHub Actions writes to `/tmp` only
- **Redpanda**: SASL_SSL with SCRAM-SHA-256

## Environment Variables (Terraform-managed)

**Producer**: `DYNAMODB_TABLE`, `RP_BOOTSTRAP_PARAM`, `RP_USER_PARAM`, `RP_PASS_PARAM`

**Consumer**: `GCP_PROJECT_ID`, `BQ_DATASET`, `GCP_KEY_PARAM`
