import json
import os
import requests
import time
from datetime import datetime, timezone
from google.cloud import bigquery
from google.oauth2 import service_account
import boto3

# --- Configuration ---
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
BQ_DATASET_RAW = os.environ.get('BQ_DATASET_RAW')
GCP_KEY_PARAM = os.environ.get('GCP_KEY_PARAM')

# --- AWS Clients ---
ssm = boto3.client('ssm')

def _load_gcp_credentials():
    parameter = ssm.get_parameter(Name=GCP_KEY_PARAM, WithDecryption=True)
    credentials_json = json.loads(parameter['Parameter']['Value'])
    return service_account.Credentials.from_service_account_info(credentials_json)

_GCP_CREDENTIALS = _load_gcp_credentials()

def fetch_skinport_prices(owned_item_ids, retries=5, backoff=2):
    """
    Fetch Skinport market prices for owned items.
    Returns dict of {item_id: price_pln} for successfully matched items.
    """
    url = "https://api.skinport.com/v1/items?app_id=730&currency=PLN"
    # Skinport requires Brotli — it returns 406 Not Acceptable for gzip/deflate.
    # The `brotli` package is bundled in the shared Lambda layer so requests/urllib3
    # can transparently decompress the `br` response before response.json().
    headers = {"Accept-Encoding": "br"}
    matched_prices = {}
    unmatched_names = []

    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    # Index by market_hash_name for O(1) lookup
                    skinport_items = {item.get("market_hash_name"): item for item in data}

                    # Match owned items to Skinport listings
                    for item_id in owned_item_ids:
                        if item_id in skinport_items:
                            skinport_item = skinport_items[item_id]
                            try:
                                price_pln = float(skinport_item.get("suggested_price", 0))
                                if price_pln > 0:
                                    matched_prices[item_id] = price_pln
                            except (ValueError, TypeError):
                                pass
                        else:
                            unmatched_names.append(item_id)

                    if unmatched_names:
                        unmatched_rate = len(unmatched_names) / len(owned_item_ids) * 100
                        print(f"SKINPORT_ITEM_MISMATCH | unmatched={len(unmatched_names)}/{len(owned_item_ids)} | rate={unmatched_rate:.1f}%")

                    print(f"SKINPORT_FETCH_SUCCESS | matched={len(matched_prices)} | unmatched={len(unmatched_names)}")
                    return matched_prices
            elif response.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"SKINPORT_RATE_LIMIT | attempt={attempt + 1}/{retries} | sleeping {wait}s")
                if attempt < retries - 1:
                    time.sleep(wait)
        except Exception as e:
            print(f"Skinport attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)

    print(f"SKINPORT_FETCH_FAILED | all {retries} attempts failed")
    return None

def lambda_handler(event, context):
    request_id = context.aws_request_id if context else 'local'

    # Backfill mode: accept optional 'date' field for testing
    backfill_date = (event or {}).get('date')
    if backfill_date:
        current_ts = f"{backfill_date}T12:00:00+00:00"
        print(f"BACKFILL_MODE | date={backfill_date} | request_id={request_id}")
    else:
        current_ts = datetime.now(timezone.utc).isoformat()
    run_date = current_ts[:10]

    print(f"INVOCATION_START | date={run_date} | request_id={request_id}")

    client = bigquery.Client(credentials=_GCP_CREDENTIALS, project=GCP_PROJECT_ID)

    # 1. Fetch owned item_ids from dim_assets (must have been populated by steam_price_producer first)
    owned_item_ids = []
    try:
        query = f"SELECT DISTINCT item_id FROM `{GCP_PROJECT_ID}.steam_marts.dim_assets`"
        owned_item_ids = [row.item_id for row in client.query(query).result()]
        print(f"OWNED_ITEMS_LOADED | count={len(owned_item_ids)}")
    except Exception as e:
        print(f"ERROR_LOADING_OWNED_ITEMS | {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({"status": "error", "reason": f"Could not load owned items: {e}"})
        }

    if not owned_item_ids:
        print(f"INVOCATION_END | date={run_date} | status=no_owned_items")
        return {'statusCode': 200, 'body': json.dumps({"status": "success", "prices_written": 0})}

    # 2. Fetch Skinport prices
    skinport_prices = fetch_skinport_prices(owned_item_ids)

    if skinport_prices is None:
        print(f"INVOCATION_END | date={run_date} | status=skinport_fetch_failed")
        return {
            'statusCode': 500,
            'body': json.dumps({"status": "error", "reason": "Skinport fetch failed after all retries"})
        }

    # 3. Build rows for BigQuery
    skinport_rows = []
    for item_id, price_pln in skinport_prices.items():
        skinport_rows.append({
            "item_id": item_id,
            "skinport_price_pln": price_pln,
            "timestamp": current_ts
        })

    # 4. Write to BigQuery
    results = {}

    if skinport_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.skinport_prices_history"
        errors = client.insert_rows_json(table_id, skinport_rows)
        results['skinport_prices'] = "success" if not errors else f"errors: {errors}"
        print(f"skinport_prices_history: {results['skinport_prices']}")

    summary = {
        "status": "success",
        "prices_written": len(skinport_rows),
        "results": results
    }

    print(f"INVOCATION_END | date={run_date} | prices_written={len(skinport_rows)}")

    return {
        'statusCode': 200,
        'body': json.dumps(summary)
    }