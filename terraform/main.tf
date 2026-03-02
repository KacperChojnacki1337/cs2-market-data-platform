# ==========================================
# 1. AWS: DynamoDB - Inventory (Source of Truth)
# ==========================================
resource "aws_dynamodb_table" "inventory_metadata" {
  name         = "steam_inventory_metadata"
  billing_mode = "PAY_PER_REQUEST"
  
  hash_key  = "item_id"
  range_key = "buy_date"

  attribute {
    name = "item_id"
    type = "S"
  }

  attribute {
    name = "buy_date"
    type = "S"
  }

  tags = {
    Environment = "Dev"
    Project     = "steam-tracker"
  }
}

# ==========================================
# 2. GCP: BigQuery - Data Warehouse
# ==========================================

resource "google_bigquery_dataset" "steam_dataset" {
  dataset_id                  = "steam_analytics"
  friendly_name               = "Steam Price Analytics"
  description                 = "Storage for facts and dimensions regarding Steam prices"
  location                    = "EU"
  delete_contents_on_destroy  = false
}

# --- FACT TABLE: Market Price History ---
resource "google_bigquery_table" "fact_price_history" {
  dataset_id = google_bigquery_dataset.steam_dataset.dataset_id
  table_id   = "fact_price_history"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp" 
  }

  schema = <<EOF
[
  {"name": "item_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "price_usd", "type": "FLOAT", "mode": "NULLABLE"},
  {"name": "timestamp", "type": "TIMESTAMP", "mode": "REQUIRED"}
]
EOF
}

# --- DIMENSION TABLE: User Assets (Surrogate Key) ---
resource "google_bigquery_table" "dim_assets" {
  dataset_id = google_bigquery_dataset.steam_dataset.dataset_id
  table_id   = "dim_assets"
  deletion_protection = false

  schema = <<EOF
[
  {
    "name": "asset_id",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Surrogate Key (Hash of item_id + buy_date)"
  },
  {
    "name": "item_id",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Natural Key (Item Name)"
  },
  {
    "name": "buy_date",
    "type": "STRING",
    "mode": "REQUIRED"
  },
  {
    "name": "buy_price",
    "type": "FLOAT",
    "mode": "NULLABLE"
  },
  {
    "name": "buy_currency",
    "type": "STRING",
    "mode": "NULLABLE"
  },
  {
    "name": "quantity",
    "type": "INTEGER",
    "mode": "NULLABLE"
  },
  {
    "name": "category",
    "type": "STRING",
    "mode": "NULLABLE"
  },
  {
    "name": "purchase_channel",
    "type": "STRING",
    "mode": "NULLABLE"
  },
  {
    "name": "last_updated",
    "type": "TIMESTAMP",
    "mode": "NULLABLE"
  }
]
EOF
}

# ==========================================
# 3. IAM: Security and Permissions
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
        Action = "ssm:GetParameter"
        Effect = "Allow"
        Resource = "arn:aws:ssm:eu-central-1:*:parameter/steam-tracker/gcp-key"
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
  filename         = data.archive_file.lambda_code_zip.output_path
  function_name    = "steam_price_producer"
  role             = aws_iam_role.lambda_exec_role.arn
  handler          = "producer_lambda.lambda_handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 256

  layers = [aws_lambda_layer_version.python_libs.arn]

  # Zmienne środowiskowe - ułatwiają rozwój kodu
  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.inventory_metadata.name
      GCP_PROJECT_ID = "steam-tracker-portfolio"
      BQ_DATASET     = google_bigquery_dataset.steam_dataset.dataset_id
      GCP_KEY_PARAM  = "/steam-tracker/gcp-key"
    }
  }

  source_code_hash = data.archive_file.lambda_code_zip.output_base64sha256
}