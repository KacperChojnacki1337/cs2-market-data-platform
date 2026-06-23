import boto3
import json
import requests
import os
from collections import defaultdict
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

def get_steam_price(market_hash_name, median_7d=None, retries=5, backoff=2):
    encoded_name = requests.utils.quote(market_hash_name)
    url = f"https://steamcommunity.com/market/priceoverview/?appid=730&currency=1&market_hash_name={encoded_name}"
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and "lowest_price" in data:
                    price = float(data["lowest_price"].replace("$", "").replace(",", ""))

                    volume_str = data.get("volume", "0").replace(",", "")
                    try:
                        volume = int(volume_str)
                    except (ValueError, AttributeError):
                        volume = 0

                    flagged = volume == 0
                    if not flagged and median_7d is not None and median_7d > 0:
                        deviation = abs(price - median_7d) / median_7d
                        if deviation > 0.5:
                            flagged = True
                            print(f"PRICE_SPIKE | {market_hash_name} | price={price} | median_7d={median_7d:.2f} | deviation={deviation:.0%}")

                    return price, flagged
            elif response.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"STEAM_RATE_LIMIT | {market_hash_name} | attempt={attempt + 1}/{retries} | sleeping {wait}s")
                if attempt < retries - 1:
                    time.sleep(wait)
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
    request_id = context.aws_request_id if context else 'local'

    # Backfill mode: event payload may carry a 'date' field (YYYY-MM-DD).
    # Use noon UTC on that date so all BQ rows land in the correct day partition.
    backfill_date = (event or {}).get('date')
    if backfill_date:
        current_ts = f"{backfill_date}T12:00:00+00:00"
        print(f"BACKFILL_MODE | date={backfill_date} | request_id={request_id}")
    else:
        current_ts = datetime.now(timezone.utc).isoformat()
    run_date = current_ts[:10]

    # Batch mode: EventBridge passes batch_index + batch_size so each Lambda invocation
    # fetches prices for a different slice of the inventory. Each batch runs on a fresh
    # AWS IP, bypassing Steam's per-IP rate limit (~10 req/IP).
    # Without batch_index (e.g. manual invocation), all owned items are fetched in one go.
    batch_index = (event or {}).get('batch_index')  # None = all items
    batch_size  = int((event or {}).get('batch_size', 10))

    if batch_index is not None:
        batch_index = int(batch_index)
        print(f"INVOCATION_START | date={run_date} | batch_index={batch_index} | batch_size={batch_size} | request_id={request_id}")
    else:
        print(f"INVOCATION_START | date={run_date} | batch_mode=off | request_id={request_id}")

    print(f"Scanning DynamoDB: {DYNAMODB_TABLE}")

    items = inventory_table.scan().get('Items', [])

    if not items:
        print(f"INVOCATION_END | date={run_date} | status=no_items")
        return {'statusCode': 200, 'body': 'No items found.'}

    client = bigquery.Client(credentials=_GCP_CREDENTIALS, project=GCP_PROJECT_ID)

    # Double-fire detection only applies in non-batch mode (single daily invocation).
    # In batch mode, later batches legitimately see prices from earlier batches — not a double-fire.
    if batch_index is None:
        try:
            check = f"SELECT COUNT(*) as cnt FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.prices_history` WHERE DATE(timestamp) = '{run_date}'"
            rows = list(client.query(check).result())
            existing_price_rows = rows[0].cnt
            if existing_price_rows > 0:
                print(f"DOUBLE_FIRE_DETECTED | date={run_date} | existing_price_rows={existing_price_rows} | proceeding (silver layer deduplicates via ROW_NUMBER)")
            else:
                print(f"IDEMPOTENCY_OK | date={run_date} | no existing price rows")
        except Exception as e:
            print(f"Warning: double-fire check failed ({e}), proceeding.")

    assets_rows = []
    sales_rows = []
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

    # 2. Fetch existing sell asset_ids — sell events are immutable, skip re-inserts
    existing_sell_ids = set()
    try:
        query = f"SELECT DISTINCT asset_id FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.sales_history`"
        existing_sell_ids = {row.asset_id for row in client.query(query).result()}
        print(f"Found {len(existing_sell_ids)} existing sells, will skip re-inserts.")
    except Exception as e:
        print(f"Warning: could not fetch existing sell_ids ({e}), will insert all.")

    # 3. Fetch 7-day price medians for spike detection (one BQ query for all items)
    medians_7d = {}
    try:
        median_query = f"""
            SELECT item_id, APPROX_QUANTILES(price_usd, 2)[OFFSET(1)] as median_price
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.prices_history`
            WHERE DATE(timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
            GROUP BY item_id
        """
        medians_7d = {row.item_id: float(row.median_price) for row in client.query(median_query).result()}
        print(f"SPIKE_DETECTION | loaded 7-day medians for {len(medians_7d)} items")
    except Exception as e:
        print(f"Warning: could not fetch 7-day medians ({e}), spike detection disabled.")

    # 4. Fetch NBP rate once per invocation — skip if already recorded for this date
    existing_rate_for_date = False
    try:
        rate_check = f"SELECT COUNT(*) as cnt FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.exchange_rates` WHERE DATE(timestamp) = '{run_date}'"
        rate_rows = list(client.query(rate_check).result())
        existing_rate_for_date = rate_rows[0].cnt > 0
        if existing_rate_for_date:
            print(f"EXCHANGE_RATE_SKIP | date={run_date} | rate already recorded for today")
    except Exception as e:
        print(f"Warning: could not check existing exchange rate ({e}), will fetch and insert.")

    if not existing_rate_for_date:
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

    # Compute net quantity per item_id from DynamoDB scan (buys minus sells).
    # Only items with net_quantity > 0 are still in the portfolio — prices are only needed for those.
    net_quantity: dict = defaultdict(int)
    for item in items:
        qty = int(item.get('quantity', 1))
        if item.get('event_type', 'buy') == 'buy':
            net_quantity[item['item_id']] += qty
        elif item.get('event_type') == 'sell':
            net_quantity[item['item_id']] -= qty

    # Phase 1: Build assets and sales rows — process ALL items regardless of batch.
    # Idempotency checks ensure duplicates are never re-inserted.
    buy_items = []
    for item in items:
        item_id = item['item_id']
        asset_id = item.get('asset_id')
        event_type = item.get('event_type', 'buy')

        if event_type == 'buy':
            if asset_id not in existing_asset_ids:
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
            # Only collect for price fetch if still owned (net_quantity > 0).
            if net_quantity[item_id] > 0:
                buy_items.append(item)

        elif event_type == 'sell':
            if asset_id not in existing_sell_ids:
                sales_rows.append({
                    "asset_id": asset_id,
                    "item_id": item_id,
                    "sell_price": float(item.get('sell_price', 0)),
                    "sell_currency": item.get('sell_currency', 'PLN'),
                    "sell_date": item.get('sell_date'),
                    "sell_channel": item.get('sell_channel', 'Unknown'),
                    "category": item.get('category', 'Skin'),
                    "quantity": int(item.get('quantity', 1)),
                    "timestamp": current_ts
                })

    # Phase 2: Fetch Steam prices — only for this batch's slice of owned buy items.
    # Sort alphabetically by item_id so each batch is deterministic (DynamoDB scan order varies).
    # Deduplicate by item_id: multiple buy events for the same skin (e.g. bought twice)
    # still only need one price fetch — prices_history is keyed by item_id, not asset_id.
    buy_items_sorted = sorted(buy_items, key=lambda x: x['item_id'])
    seen_item_ids: set = set()
    buy_items_deduped = []
    for item in buy_items_sorted:
        if item['item_id'] not in seen_item_ids:
            seen_item_ids.add(item['item_id'])
            buy_items_deduped.append(item)

    if batch_index is not None:
        start = batch_index * batch_size
        end   = start + batch_size
        items_for_prices = buy_items_deduped[start:end]
        print(f"BATCH_PRICE_FETCH | batch_index={batch_index} | items_in_batch={len(items_for_prices)} | range={start}-{end - 1} | total_owned_unique_items={len(buy_items_deduped)}")
    else:
        items_for_prices = buy_items_deduped

    for item in items_for_prices:
        item_id = item['item_id']
        result = get_steam_price(item_id, median_7d=medians_7d.get(item_id))
        time.sleep(1.0)
        if result is not None:
            price_usd, price_flagged = result
            prices_rows.append({
                "item_id": item_id,
                "price_usd": price_usd,
                "price_flagged": price_flagged,
                "timestamp": current_ts
            })
            if price_flagged:
                print(f"PRICE_FLAGGED | {item_id} | price_usd={price_usd}")
        else:
            print(f"Could not fetch price for {item_id}, skipping price row.")

    # 5. Write to BigQuery
    results = {}

    if assets_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history"
        errors = client.insert_rows_json(table_id, assets_rows)
        results['assets'] = "success" if not errors else f"errors: {errors}"
        print(f"assets_history: {results['assets']}")

    if sales_rows:
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.sales_history"
        errors = client.insert_rows_json(table_id, sales_rows)
        results['sales'] = "success" if not errors else f"errors: {errors}"
        print(f"sales_history: {results['sales']}")

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
        "sales_written": len(sales_rows),
        "prices_written": len(prices_rows),
        "exchange_rates_written": len(exchange_rate_rows),
        "results": results
    }
    print(f"INVOCATION_END | date={run_date} | batch_index={batch_index} | assets_written={len(assets_rows)} | sales_written={len(sales_rows)} | prices_written={len(prices_rows)} | exchange_rates_written={len(exchange_rate_rows)}")

    return {
        'statusCode': 200,
        'body': json.dumps(summary)
    }