# ==========================================
# 1. AWS: DynamoDB - Inventory (Source of Truth)
# ==========================================
resource "aws_dynamodb_table" "inventory_metadata" {
  name         = "steam_inventory_metadata"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "asset_id"

  attribute {
    name = "asset_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Environment = "Dev"
    Project     = "steam-tracker"
  }
}

# ==========================================
# 2. GCP: BigQuery - Medallion Architecture
# ==========================================

locals {
  sa_email = jsondecode(file(var.gcp_credentials_file)).client_email
}

# --- Bronze Layer: Raw Data Ingestion ---
resource "google_bigquery_dataset" "raw_dataset" {
  dataset_id                 = "steam_raw"
  friendly_name              = "Steam Raw Data"
  description                = "Bronze Layer: Raw ingestion from AWS and Steam API"
  location                   = "EU"
  delete_contents_on_destroy = false
}

# --- Silver Layer: Staging & Intermediate (dbt views) ---
resource "google_bigquery_dataset" "staging_dataset" {
  dataset_id                 = "steam_staging"
  friendly_name              = "Steam Staging"
  description                = "Silver Layer: Typed views and intermediate models via dbt"
  location                   = "EU"
  delete_contents_on_destroy = false
}

# --- Gold Layer: Analytics Ready Data ---
resource "google_bigquery_dataset" "marts_dataset" {
  dataset_id                 = "steam_marts"
  friendly_name              = "Steam Analytics Marts"
  description                = "Gold Layer: Cleaned and modeled Star Schema (Kimball)"
  location                   = "EU"
  delete_contents_on_destroy = false
}

# --- Dev Datasets (mirror of prod, for local dbt development) ---
resource "google_bigquery_dataset" "raw_dataset_dev" {
  dataset_id                 = "steam_raw_dev"
  friendly_name              = "Steam Raw Data (Dev)"
  description                = "Bronze Layer Dev"
  location                   = "EU"
  delete_contents_on_destroy = true
}

resource "google_bigquery_dataset" "staging_dataset_dev" {
  dataset_id                 = "steam_staging_dev"
  friendly_name              = "Steam Staging (Dev)"
  description                = "Silver Layer Dev"
  location                   = "EU"
  delete_contents_on_destroy = true
}

resource "google_bigquery_dataset" "marts_dataset_dev" {
  dataset_id                 = "steam_marts_dev"
  friendly_name              = "Steam Analytics Marts (Dev)"
  description                = "Gold Layer Dev"
  location                   = "EU"
  delete_contents_on_destroy = true
}

# --- IAM: Service Account access to new datasets ---
resource "google_bigquery_dataset_iam_member" "staging_sa_editor" {
  dataset_id = google_bigquery_dataset.staging_dataset.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${local.sa_email}"
}

resource "google_bigquery_dataset_iam_member" "raw_dev_sa_editor" {
  dataset_id = google_bigquery_dataset.raw_dataset_dev.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${local.sa_email}"
}

resource "google_bigquery_dataset_iam_member" "staging_dev_sa_editor" {
  dataset_id = google_bigquery_dataset.staging_dataset_dev.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${local.sa_email}"
}

resource "google_bigquery_dataset_iam_member" "marts_dev_sa_editor" {
  dataset_id = google_bigquery_dataset.marts_dataset_dev.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${local.sa_email}"
}

# --- Raw Table: Assets History (Bronze) ---
resource "google_bigquery_table" "raw_assets" {
  dataset_id          = google_bigquery_dataset.raw_dataset.dataset_id
  table_id            = "assets_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "last_updated"
  }

  schema = <<EOF
[
  {"name": "asset_id",         "type": "STRING",    "mode": "REQUIRED", "description": "Source Key (DynamoDB UUID)"},
  {"name": "item_id",          "type": "STRING",    "mode": "REQUIRED", "description": "Natural Key - skin market name"},
  {"name": "buy_date",         "type": "DATE",      "mode": "NULLABLE"},
  {"name": "buy_price",        "type": "FLOAT",     "mode": "NULLABLE"},
  {"name": "buy_currency",     "type": "STRING",    "mode": "NULLABLE"},
  {"name": "quantity",         "type": "INTEGER",   "mode": "NULLABLE"},
  {"name": "category",         "type": "STRING",    "mode": "NULLABLE"},
  {"name": "purchase_channel", "type": "STRING",    "mode": "NULLABLE"},
  {"name": "last_updated",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

# --- Raw Table: Price History (Bronze) ---
resource "google_bigquery_table" "raw_prices" {
  dataset_id          = google_bigquery_dataset.raw_dataset.dataset_id
  table_id            = "prices_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "item_id",       "type": "STRING",    "mode": "REQUIRED"},
  {"name": "price_usd",     "type": "FLOAT",     "mode": "NULLABLE"},
  {"name": "price_flagged", "type": "BOOLEAN",   "mode": "NULLABLE", "description": "TRUE if price is anomalous: zero volume or >50% deviation from 7-day median"},
  {"name": "timestamp",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

# --- Raw Table: Exchange Rates (Bronze) ---
resource "google_bigquery_table" "raw_exchange_rates" {
  dataset_id          = google_bigquery_dataset.raw_dataset.dataset_id
  table_id            = "exchange_rates"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "from_currency", "type": "STRING",    "mode": "REQUIRED"},
  {"name": "to_currency",   "type": "STRING",    "mode": "REQUIRED"},
  {"name": "rate",          "type": "FLOAT",     "mode": "REQUIRED"},
  {"name": "source",        "type": "STRING",    "mode": "NULLABLE"},
  {"name": "timestamp",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

# --- Raw Table: Sales History (Bronze) ---
resource "google_bigquery_table" "raw_sales" {
  dataset_id          = google_bigquery_dataset.raw_dataset.dataset_id
  table_id            = "sales_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "asset_id",      "type": "STRING",    "mode": "REQUIRED", "description": "Source Key (DynamoDB UUID)"},
  {"name": "item_id",       "type": "STRING",    "mode": "REQUIRED", "description": "Natural Key - skin market name"},
  {"name": "sell_price",    "type": "FLOAT",     "mode": "NULLABLE"},
  {"name": "sell_currency", "type": "STRING",    "mode": "NULLABLE"},
  {"name": "sell_date",     "type": "DATE",      "mode": "NULLABLE"},
  {"name": "sell_channel",  "type": "STRING",    "mode": "NULLABLE", "description": "Platform used for sale (Steam, CSFloat, Skinport) — drives fee_pct in dbt"},
  {"name": "category",      "type": "STRING",    "mode": "NULLABLE"},
  {"name": "quantity",      "type": "INTEGER",   "mode": "NULLABLE"},
  {"name": "timestamp",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

# --- Dev Tables: mirrors of prod schema, empty (for CI dbt validation) ---
resource "google_bigquery_table" "dev_assets" {
  dataset_id          = google_bigquery_dataset.raw_dataset_dev.dataset_id
  table_id            = "assets_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "last_updated"
  }

  schema = <<EOF
[
  {"name": "asset_id",         "type": "STRING",    "mode": "REQUIRED", "description": "Source Key (DynamoDB UUID)"},
  {"name": "item_id",          "type": "STRING",    "mode": "REQUIRED", "description": "Natural Key - skin market name"},
  {"name": "buy_date",         "type": "DATE",      "mode": "NULLABLE"},
  {"name": "buy_price",        "type": "FLOAT",     "mode": "NULLABLE"},
  {"name": "buy_currency",     "type": "STRING",    "mode": "NULLABLE"},
  {"name": "quantity",         "type": "INTEGER",   "mode": "NULLABLE"},
  {"name": "category",         "type": "STRING",    "mode": "NULLABLE"},
  {"name": "purchase_channel", "type": "STRING",    "mode": "NULLABLE"},
  {"name": "last_updated",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

resource "google_bigquery_table" "dev_prices" {
  dataset_id          = google_bigquery_dataset.raw_dataset_dev.dataset_id
  table_id            = "prices_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "item_id",       "type": "STRING",    "mode": "REQUIRED"},
  {"name": "price_usd",     "type": "FLOAT",     "mode": "NULLABLE"},
  {"name": "price_flagged", "type": "BOOLEAN",   "mode": "NULLABLE", "description": "TRUE if price is anomalous: zero volume or >50% deviation from 7-day median"},
  {"name": "timestamp",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

resource "google_bigquery_table" "dev_exchange_rates" {
  dataset_id          = google_bigquery_dataset.raw_dataset_dev.dataset_id
  table_id            = "exchange_rates"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "from_currency", "type": "STRING",    "mode": "REQUIRED"},
  {"name": "to_currency",   "type": "STRING",    "mode": "REQUIRED"},
  {"name": "rate",          "type": "FLOAT",     "mode": "REQUIRED"},
  {"name": "source",        "type": "STRING",    "mode": "NULLABLE"},
  {"name": "timestamp",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

resource "google_bigquery_table" "dev_sales" {
  dataset_id          = google_bigquery_dataset.raw_dataset_dev.dataset_id
  table_id            = "sales_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "asset_id",      "type": "STRING",    "mode": "REQUIRED", "description": "Source Key (DynamoDB UUID)"},
  {"name": "item_id",       "type": "STRING",    "mode": "REQUIRED", "description": "Natural Key - skin market name"},
  {"name": "sell_price",    "type": "FLOAT",     "mode": "NULLABLE"},
  {"name": "sell_currency", "type": "STRING",    "mode": "NULLABLE"},
  {"name": "sell_date",     "type": "DATE",      "mode": "NULLABLE"},
  {"name": "sell_channel",  "type": "STRING",    "mode": "NULLABLE", "description": "Platform used for sale (Steam, CSFloat, Skinport) — drives fee_pct in dbt"},
  {"name": "category",      "type": "STRING",    "mode": "NULLABLE"},
  {"name": "quantity",      "type": "INTEGER",   "mode": "NULLABLE"},
  {"name": "timestamp",     "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

# --- Raw Table: Skinport Prices (Bronze) ---
# Second price source for portfolio analysis (Skinport API data)
resource "google_bigquery_table" "raw_skinport_prices" {
  dataset_id          = google_bigquery_dataset.raw_dataset.dataset_id
  table_id            = "skinport_prices_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "item_id",             "type": "STRING",    "mode": "REQUIRED", "description": "Market hash name (must match Steam item_id for joining)"},
  {"name": "skinport_price_pln",  "type": "FLOAT",     "mode": "NULLABLE", "description": "Skinport marketplace price in PLN"},
  {"name": "timestamp",           "type": "TIMESTAMP", "mode": "REQUIRED", "description": "When Lambda fetched this price"}
]
EOF
}

# --- Dev Table: Skinport Prices (Bronze Dev) ---
resource "google_bigquery_table" "dev_skinport_prices" {
  dataset_id          = google_bigquery_dataset.raw_dataset_dev.dataset_id
  table_id            = "skinport_prices_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = <<EOF
[
  {"name": "item_id",             "type": "STRING",    "mode": "REQUIRED", "description": "Market hash name (must match Steam item_id for joining)"},
  {"name": "skinport_price_pln",  "type": "FLOAT",     "mode": "NULLABLE", "description": "Skinport marketplace price in PLN"},
  {"name": "timestamp",           "type": "TIMESTAMP", "mode": "REQUIRED", "description": "When Lambda fetched this price"}
]
EOF
}

# ==========================================
# 3. IAM: Producer Lambda Permissions
# ==========================================

resource "aws_iam_role" "lambda_exec_role" {
  name = "steam_tracker_lambda_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "steam_tracker_lambda_policy"
  role = aws_iam_role.lambda_exec_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "dynamodb:Scan",
          "dynamodb:Query",
          "dynamodb:GetItem"
        ]
        Effect   = "Allow"
        Resource = aws_dynamodb_table.inventory_metadata.arn
      },
      {
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Effect = "Allow"
        Resource = [
          "arn:aws:ssm:eu-central-1:*:parameter/steam-tracker/gcp-key",
          "arn:aws:ssm:eu-central-1:*:parameter/steam-tracker/*"
        ]
      },
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Effect   = "Allow"
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# ==========================================
# 4. Lambda: Packaging and Deployment
# ==========================================

data "archive_file" "lambda_code_zip" {
  type        = "zip"
  source_file = "../lambda/producer/producer_lambda.py"
  output_path = "producer_lambda.zip"
}

data "archive_file" "lambda_layer_zip" {
  type        = "zip"
  source_dir  = "../lambda/producer/layer"
  output_path = "lambda_layer.zip"
}

resource "aws_lambda_layer_version" "python_libs" {
  filename            = data.archive_file.lambda_layer_zip.output_path
  layer_name          = "steam_tracker_libs"
  compatible_runtimes = ["python3.11"]
}

resource "aws_lambda_function" "steam_producer" {
  filename      = data.archive_file.lambda_code_zip.output_path
  function_name = "steam_price_producer"
  role          = aws_iam_role.lambda_exec_role.arn
  handler       = "producer_lambda.lambda_handler"
  runtime       = "python3.11"
  timeout       = 600
  memory_size   = 256

  layers = [aws_lambda_layer_version.python_libs.arn]

  source_code_hash = data.archive_file.lambda_code_zip.output_base64sha256

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.inventory_metadata.name
      GCP_PROJECT_ID = var.gcp_project_id
      BQ_DATASET_RAW = "steam_raw"
      GCP_KEY_PARAM  = "/steam-tracker/gcp-key"
    }
  }
}

# ==========================================
# 5. EventBridge: Batched Price Fetch (20 invocations × 5 items each, + retry at 07:30)
# ==========================================

# All 20 rules fire at exactly 07:00 UTC — Lambda scales to 20 concurrent instances,
# each on a different host/IP, bypassing Steam's per-IP rate limit (~10 req/IP).
# Smaller batches (5 items) reduce the chance of hitting the rate limit per instance.
# A second identical set fires at 07:30 UTC to catch any items missed due to rate limiting.
# Items are sorted alphabetically inside Lambda so the same item always hits the same batch.
# To support more items: increase price_batch_count in terraform.tfvars (5 items per batch).

# --- First run: 07:00 UTC ---
resource "aws_cloudwatch_event_rule" "producer_batch" {
  count               = var.price_batch_count
  name                = "steam-producer-price-batch-${count.index}"
  description         = "Price batch ${count.index} — items ${count.index * 5}-${count.index * 5 + 4} alphabetically"
  schedule_expression = "cron(0 7 * * ? *)"
}

resource "aws_cloudwatch_event_target" "producer_batch" {
  count = var.price_batch_count
  rule  = aws_cloudwatch_event_rule.producer_batch[count.index].name
  arn   = aws_lambda_function.steam_producer.arn
  input = jsonencode({
    batch_index = count.index
    batch_size  = 5
  })
}

resource "aws_lambda_permission" "allow_eventbridge_batch" {
  count         = var.price_batch_count
  statement_id  = "AllowEventBridgeBatch${count.index}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.steam_producer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.producer_batch[count.index].arn
}

# --- Smart retry: 07:30 UTC — fetches only items without a valid price from the 07:00 run ---
# Single invocation: Lambda queries BQ for missing item_ids, fetches only those.
# Avoids re-fetching items that already have prices, minimising unnecessary Steam requests.
resource "aws_cloudwatch_event_rule" "producer_retry_missing" {
  name                = "steam-producer-retry-missing"
  description         = "Smart retry at 07:30 UTC — fetches only items without a valid price today"
  schedule_expression = "cron(30 7 * * ? *)"
}

resource "aws_cloudwatch_event_target" "producer_retry_missing" {
  rule  = aws_cloudwatch_event_rule.producer_retry_missing.name
  arn   = aws_lambda_function.steam_producer.arn
  input = jsonencode({ retry_missing = true })
}

resource "aws_lambda_permission" "allow_eventbridge_retry_missing" {
  statement_id  = "AllowEventBridgeRetryMissing"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.steam_producer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.producer_retry_missing.arn
}

# ==========================================
# 6. CloudWatch: Alarms + SNS Notifications
# ==========================================

resource "aws_sns_topic" "alerts" {
  name = "steam-tracker-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# --- Producer Lambda: Errors ---
resource "aws_cloudwatch_metric_alarm" "producer_errors" {
  alarm_name          = "steam-producer-errors"
  alarm_description   = "Producer Lambda is throwing errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions = {
    FunctionName = aws_lambda_function.steam_producer.function_name
  }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# ==========================================
# 7. Budget Alerts — GCP and AWS
# ==========================================

# --- GCP: Email notification channel ---
resource "google_monitoring_notification_channel" "email" {
  display_name = "Steam Tracker Email"
  type         = "email"
  project      = var.gcp_project_id
  labels = {
    email_address = var.alert_email
  }
}

# --- GCP: Monthly budget $5 with alerts at 25%, 50%, 100% ---
resource "google_billing_budget" "monthly_budget" {
  billing_account = var.billing_account_id
  display_name    = "Steam Tracker Monthly Budget"

  budget_filter {
    projects = ["projects/${var.gcp_project_number}"]
  }

  amount {
    specified_amount {
      currency_code = "PLN"
      units         = "25"
    }
  }

  threshold_rules {
    threshold_percent = 0.25
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    monitoring_notification_channels = [
      google_monitoring_notification_channel.email.id
    ]
    disable_default_iam_recipients = false
  }
}

# --- AWS: Monthly budget $5 covering Lambda + DynamoDB ---
resource "aws_budgets_budget" "monthly_budget" {
  name         = "steam-tracker-monthly-budget"
  budget_type  = "COST"
  limit_amount = "5"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["AWS Lambda", "Amazon DynamoDB"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 25
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}

# --- Producer Lambda: Duration (timeout risk) ---
resource "aws_cloudwatch_metric_alarm" "producer_duration" {
  alarm_name          = "steam-producer-duration"
  alarm_description   = "Producer Lambda duration exceeds 80% of timeout (480s of 600s)"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions = {
    FunctionName = aws_lambda_function.steam_producer.function_name
  }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 480000
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

