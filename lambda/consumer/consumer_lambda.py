import boto3
import json
import os
import hashlib
import base64
from google.cloud import bigquery
from google.oauth2 import service_account

# --- Configuration ---
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
BQ_DATASET = os.environ.get('BQ_DATASET')
GCP_KEY_PARAM = os.environ.get('GCP_KEY_PARAM')

ssm = boto3.client('ssm')

def get_gcp_credentials():
    """Retrieves GCP Service Account JSON from AWS SSM."""
    parameter = ssm.get_parameter(Name=GCP_KEY_PARAM, WithDecryption=True)
    credentials_json = json.loads(parameter['Parameter']['Value'])
    return service_account.Credentials.from_service_account_info(credentials_json)

def generate_asset_id(item_id, buy_date):
    """Generates the surrogate key to match BigQuery schema."""
    unique_str = f"{item_id}_{buy_date}"
    return hashlib.md5(unique_str.encode()).hexdigest()

def lambda_handler(event, context):
    credentials = get_gcp_credentials()
    client = bigquery.Client(credentials=credentials, project=GCP_PROJECT_ID)
    
    inventory_rows = []
    price_rows = []

    # Iterate through records received from Redpanda
    for topic_partition, records in event['records'].items():
        topic = topic_partition.split('-')[0]
        
        for record in records:
            # Decode payload from Base64
            raw_payload = base64.b64decode(record['value']).decode('utf-8')
            payload = json.loads(raw_payload)
            
            if 'db-inventory-events' in topic:
                # Add surrogate key if not present in the message
                if 'asset_id' not in payload:
                    payload['asset_id'] = generate_asset_id(
                        payload['item_id'], 
                        payload.get('buy_date', 'unknown')
                    )
                # Ensure 'last_updated' matches BQ schema (last_updated vs timestamp)
                payload['last_updated'] = payload.get('timestamp')
                # Remove extra fields not in BQ schema if necessary
                inventory_rows.append(payload)
                
            elif 'market-price-events' in topic:
                price_rows.append(payload)

    # Load data to BigQuery
    results = {}
    if inventory_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.assets_history"
        # We filter keys to match only what BQ table expects
        errors = client.insert_rows_json(table_id, inventory_rows)
        results['inventory'] = "Success" if not errors else f"Errors: {errors}"

    if price_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.prices_history"
        errors = client.insert_rows_json(table_id, price_rows)
        results['prices'] = "Success" if not errors else f"Errors: {errors}"

    print(f"📊 Consumer Summary: {results}")
    return results