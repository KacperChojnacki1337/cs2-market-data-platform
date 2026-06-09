import boto3
import json
import requests
import os
from datetime import datetime, timezone
from google.cloud import bigquery
from google.oauth2 import service_account
import time

# --- Configuration ---
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE')
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
BQ_DATASET_RAW = os.environ.get('BQ_DATASET_RAW')
GCP_KEY_PARAM  = os.environ.get('GCP_KEY_PARAM')

# --- AWS Clients ---
ssm = boto3.client('ssm')
dynamodb = boto3.resource('dynamodb')
inventory_table = dynamodb.Table(DYNAMODB_TABLE)

def _load_gcp_credentials():
    parameter = ssm.get_parameter(Name=GCP_KEY_PARAM, WithDecryption=True)
    credentials_json = json.loads(parameter['Parameter']['Value'])
    return service_account.Credentials.from_service_account_info(credentials_json)

_GCP_CREDENTIALS = _load_gcp_credentials()

def get_steam_price(market_hash_name, retries=3, backoff=2):
    encoded_name = requests.utils.quote(market_hash_name)
    url = f"https://steamcommunity.com/market/priceoverview/?appid=730&currency=1&market_hash_name={encoded_name}"
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "lowest_price" in data:
                    price_str = data["lowest_price"].replace("$", "").replace(",", "")
                    return float(price_str)
        except Exception as e:
            print(f"Attempt {attempt + 1}/{retries} failed for {market_hash_name}: {e}")
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    print(f"All {retries} attempts failed for {market_hash_name}, skipping.")
    return None

def get_nbp_rate(currency='USD', retries=3, backoff=2):
    url = f"https://api.nbp.pl/api/exchangerates/rates/a/{currency.lower()}/today/?format=json"
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return float(data['rates'][0]['mid'])
            elif response.status_code == 404:
                # NBP returns 404 on weekends/holidays — fallback to last available rate
                url_last = f"https://api.nbp.pl/api/exchangerates/rates/a/{currency.lower()}/last/1/?format=json"
                response = requests.get(url_last, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    return float(data['rates'][0]['mid'])
        except Exception as e:
            print(f"Attempt {attempt + 1}/{retries} failed for NBP rate {currency}: {e}")
            if attempt < retries - 1:
                time.sleep(backoff ** attempt)
    print(f"All {retries} attempts failed for NBP rate {currency}, skipping.")
    return None

def lambda_handler(event, context):
    print(f"Scanning DynamoDB: {DYNAMODB_TABLE}")
    items = inventory_table.scan().get('Items', [])

    if not items:
        return {'statusCode': 200, 'body': 'No items found.'}

    client = bigquery.Client(credentials=_GCP_CREDENTIALS, project=GCP_PROJECT_ID)
    current_ts = datetime.now(timezone.utc).isoformat()

    assets_rows = []
    prices_rows = []
    exchange_rate_rows = []

    # 1. Fetch existing asset_ids — buy events are immutable, skip re-inserts
    existing_asset_ids = set()
    try:
        query = f"SELECT DISTINCT asset_id FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history`"
        existing_asset_ids = {row.asset_id for row in client.query(query).result()}
        print(f"Found {len(existing_asset_ids)} existing assets, will skip re-inserts.")
    except Exception as e:
        print(f"Warning: could not fetch existing asset_ids ({e}), will insert all.")

    # 2. Fetch NBP rate once per invocation
    usd_pln_rate = get_nbp_rate('USD')
    if usd_pln_rate is not None:
        exchange_rate_rows.append({
            "from_currency": "USD",
            "to_currency": "PLN",
            "rate": usd_pln_rate,
            "source": "NBP",
            "timestamp": current_ts
        })
        print(f"NBP USD/PLN rate: {usd_pln_rate}")
    else:
        print("Could not fetch NBP rate, skipping exchange rate row.")

    for item in items:
        item_id = item['item_id']
        asset_id = item.get('asset_id')

        # 3. Build assets row — only new buy events not yet in BigQuery
        if item.get('event_type', 'buy') == 'buy' and asset_id not in existing_asset_ids:
            assets_rows.append({
                "asset_id": asset_id,
                "item_id": item_id,
                "buy_date": item.get('buy_date'),
                "buy_price": float(item.get('buy_price', 0)),
                "buy_currency": item.get('buy_currency', 'PLN'),
                "quantity": int(item.get('quantity', 1)),
                "category": item.get('category', 'Skin'),
                "purchase_channel": item.get('purchase_channel', 'Unknown'),
                "last_updated": current_ts
            })

        # 4. Fetch Steam price
        market_price = get_steam_price(item_id)
        if market_price is not None:
            prices_rows.append({
                "item_id": item_id,
                "price_usd": market_price,
                "timestamp": current_ts
            })
        else:
            print(f"Could not fetch price for {item_id}, skipping price row.")

    # 5. Write to BigQuery
    results = {}

    if assets_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history"
        errors = client.insert_rows_json(table_id, assets_rows)
        results['assets'] = "success" if not errors else f"errors: {errors}"
        print(f"assets_history: {results['assets']}")

    if prices_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.prices_history"
        errors = client.insert_rows_json(table_id, prices_rows)
        results['prices'] = "success" if not errors else f"errors: {errors}"
        print(f"prices_history: {results['prices']}")

    if exchange_rate_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.exchange_rates"
        errors = client.insert_rows_json(table_id, exchange_rate_rows)
        results['exchange_rates'] = "success" if not errors else f"errors: {errors}"
        print(f"exchange_rates: {results['exchange_rates']}")

    summary = {
        "status": "success",
        "assets_written": len(assets_rows),
        "prices_written": len(prices_rows),
        "exchange_rates_written": len(exchange_rate_rows),
        "results": results
    }
    print(f"Summary: {summary}")

    return {
        'statusCode': 200,
        'body': json.dumps(summary)
    }
