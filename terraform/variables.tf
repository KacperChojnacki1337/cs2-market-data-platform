variable "aws_region" {
  description = "AWS region for Lambda and DynamoDB"
  type        = string
  default     = "eu-central-1"
}

variable "gcp_region" {
  description = "GCP region for BigQuery"
  type        = string
  default     = "europe-west3"
}

variable "gcp_project_id" {
  description = "Your unique Project ID from the GCP console"
  type        = string
}

variable "gcp_credentials_file" {
  description = "Path to the JSON file with the service account key"
  type        = string
}

variable "alert_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}

variable "billing_account_id" {
  description = "GCP Billing Account ID (format: XXXXXX-XXXXXX-XXXXXX)"
  type        = string
}

variable "gcp_project_number" {
  description = "GCP Project Number (numeric, found in GCP Console > Home > Project info)"
  type        = string
}

variable "price_batch_count" {
  description = "Number of EventBridge-triggered price batches (5 items each). Increase when inventory exceeds batch_count * 5."
  type        = number
  default     = 20
}