import boto3
import json
import random
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


# ---------------------------------------------------------------------------
# External API helpers
# ---------------------------------------------------------------------------

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

                    flagged = False
                    if median_7d is not None and median_7d > 0:
                        deviation = abs(price - median_7d) / median_7d
                        if deviation > 0.5:
                            flagged = True
                            print(f"PRICE_SPIKE | {market_hash_name} | price={price} | median_7d={median_7d:.2f} | deviation={deviation:.0%}")

                    return price, volume, flagged
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_run_context(event, context):
    """Returns (request_id, run_date, current_ts). Handles backfill via event['date']."""
    request_id = context.aws_request_id if context else 'local'
    backfill_date = (event or {}).get('date')
    if backfill_date:
        current_ts = f"{backfill_date}T12:00:00+00:00"
        print(f"BACKFILL_MODE | date={backfill_date} | request_id={request_id}")
    else:
        current_ts = datetime.now(timezone.utc).isoformat()
    run_date = current_ts[:10]
    return request_id, run_date, current_ts


def _get_bq_client():
    return bigquery.Client(credentials=_GCP_CREDENTIALS, project=GCP_PROJECT_ID)


def _scan_dynamodb():
    return inventory_table.scan().get('Items', [])


def _compute_net_quantity(items):
    """Returns {item_id: net_quantity} — buys increment, sells decrement."""
    net_quantity = defaultdict(int)
    for item in items:
        qty = int(item.get('quantity', 1))
        if item.get('event_type', 'buy') == 'buy':
            net_quantity[item['item_id']] += qty
        elif item.get('event_type') == 'sell':
            net_quantity[item['item_id']] -= qty
    return net_quantity


def _deduped_owned_items(items, net_quantity, run_date):
    """
    Returns date-seeded-shuffled, deduplicated list of actively owned buy items.

    Daily shuffle (seeded by date) rotates which items land on which batch/IP across days,
    preventing the same items from consistently hitting rate-limited AWS IPs.
    """
    owned = [
        item for item in items
        if item.get('event_type', 'buy') == 'buy' and net_quantity[item['item_id']] > 0
    ]
    owned_sorted = sorted(owned, key=lambda x: x['item_id'])
    seen: set = set()
    deduped = []
    for item in owned_sorted:
        if item['item_id'] not in seen:
            seen.add(item['item_id'])
            deduped.append(item)
    random.Random(run_date).shuffle(deduped)
    return deduped


def _fetch_7d_medians(client, run_date):
    """Returns {item_id: median_price_usd} for spike detection. Empty dict on failure."""
    try:
        query = f"""
            SELECT item_id, APPROX_QUANTILES(price_usd, 2)[OFFSET(1)] AS median_price
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.prices_history`
            WHERE DATE(timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
            GROUP BY item_id
        """
        medians = {row.item_id: float(row.median_price) for row in client.query(query).result()}
        print(f"SPIKE_DETECTION | loaded 7-day medians for {len(medians)} items")
        return medians
    except Exception as e:
        print(f"Warning: could not fetch 7-day medians ({e}), spike detection disabled.")
        return {}


def _write_prices_and_volumes(client, items_for_prices, medians_7d, current_ts):
    """Fetches Steam prices for the given items and writes prices + volumes to BQ."""
    prices_rows = []
    volume_rows = []

    for item in items_for_prices:
        item_id = item['item_id']
        result = get_steam_price(item_id, median_7d=medians_7d.get(item_id))
        time.sleep(1.0)
        if result is not None:
            price_usd, volume, price_flagged = result
            prices_rows.append({
                "item_id": item_id,
                "price_usd": price_usd,
                "price_flagged": price_flagged,
                "timestamp": current_ts,
            })
            volume_rows.append({
                "item_id": item_id,
                "volume_7d": volume,
                "timestamp": current_ts,
            })
            if price_flagged:
                print(f"PRICE_FLAGGED | {item_id} | price_usd={price_usd}")
        else:
            print(f"Could not fetch price for {item_id}, skipping price row.")

    results = {}
    if prices_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.prices_history", prices_rows)
        results['prices'] = "success" if not errors else f"errors: {errors}"
        print(f"prices_history: {results['prices']}")

    if volume_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.volume_history", volume_rows)
        results['volumes'] = "success" if not errors else f"errors: {errors}"
        print(f"volume_history: {results['volumes']}")

    return prices_rows, volume_rows, results


# ---------------------------------------------------------------------------
# Mode handlers — single-responsibility, designed for Airflow task fan-out
# ---------------------------------------------------------------------------

def _handle_sync_inventory(event, context):
    """
    Write DynamoDB buy/sell events to BigQuery exactly once per day.

    Invoked by the Airflow sync_inventory task (single invocation, no fan-out race).
    Idempotent: skips assets/sales already present in BigQuery.
    """
    request_id, run_date, current_ts = _parse_run_context(event, context)
    print(f"INVOCATION_START | date={run_date} | mode=sync_inventory | request_id={request_id}")

    items = _scan_dynamodb()
    if not items:
        print(f"INVOCATION_END | date={run_date} | mode=sync_inventory | status=no_items")
        return {'statusCode': 200, 'body': json.dumps({"status": "success", "assets_written": 0, "sales_written": 0})}

    client = _get_bq_client()

    try:
        query = f"SELECT DISTINCT asset_id FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history`"
        existing_asset_ids = {row.asset_id for row in client.query(query).result()}
        print(f"Found {len(existing_asset_ids)} existing assets, will skip re-inserts.")
    except Exception as e:
        print(f"Warning: could not fetch existing asset_ids ({e}), will insert all.")
        existing_asset_ids = set()

    try:
        query = f"SELECT DISTINCT asset_id FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.sales_history`"
        existing_sell_ids = {row.asset_id for row in client.query(query).result()}
        print(f"Found {len(existing_sell_ids)} existing sells, will skip re-inserts.")
    except Exception as e:
        print(f"Warning: could not fetch existing sell_ids ({e}), will insert all.")
        existing_sell_ids = set()

    assets_rows = []
    sales_rows = []

    for item in items:
        item_id    = item['item_id']
        asset_id   = item.get('asset_id')
        event_type = item.get('event_type', 'buy')

        if event_type == 'buy' and asset_id not in existing_asset_ids:
            assets_rows.append({
                "asset_id":         asset_id,
                "item_id":          item_id,
                "buy_date":         item.get('buy_date'),
                "buy_price":        float(item.get('buy_price', 0)),
                "buy_currency":     item.get('buy_currency', 'PLN'),
                "quantity":         int(item.get('quantity', 1)),
                "category":         item.get('category', 'Skin'),
                "purchase_channel": item.get('purchase_channel', 'Unknown'),
                "last_updated":     current_ts,
            })
        elif event_type == 'sell' and asset_id not in existing_sell_ids:
            sales_rows.append({
                "asset_id":      asset_id,
                "item_id":       item_id,
                "sell_price":    float(item.get('sell_price', 0)),
                "sell_currency": item.get('sell_currency', 'PLN'),
                "sell_date":     item.get('sell_date'),
                "sell_channel":  item.get('sell_channel', 'Unknown'),
                "category":      item.get('category', 'Skin'),
                "quantity":      int(item.get('quantity', 1)),
                "timestamp":     current_ts,
            })

    results = {}
    if assets_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history", assets_rows)
        results['assets'] = "success" if not errors else f"errors: {errors}"
        print(f"assets_history: {results['assets']}")

    if sales_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.sales_history", sales_rows)
        results['sales'] = "success" if not errors else f"errors: {errors}"
        print(f"sales_history: {results['sales']}")

    print(f"INVOCATION_END | date={run_date} | mode=sync_inventory | assets_written={len(assets_rows)} | sales_written={len(sales_rows)}")
    return {
        'statusCode': 200,
        'body': json.dumps({"status": "success", "assets_written": len(assets_rows), "sales_written": len(sales_rows), "results": results}),
    }


def _handle_exchange_rates(event, context):
    """
    Fetch USD/PLN and EUR/PLN from NBP and write to BigQuery exactly once per day.

    Invoked by the Airflow fetch_exchange_rate task (single invocation).
    Idempotent: skips write if a rate for today already exists.
    """
    request_id, run_date, current_ts = _parse_run_context(event, context)
    print(f"INVOCATION_START | date={run_date} | mode=exchange_rates | request_id={request_id}")

    client = _get_bq_client()

    try:
        rate_check = f"SELECT COUNT(*) AS cnt FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.exchange_rates` WHERE DATE(timestamp) = '{run_date}'"
        existing = list(client.query(rate_check).result())[0].cnt > 0
        if existing:
            print(f"EXCHANGE_RATE_SKIP | date={run_date} | rate already recorded for today")
            print(f"INVOCATION_END | date={run_date} | mode=exchange_rates | exchange_rates_written=0")
            return {'statusCode': 200, 'body': json.dumps({"status": "success", "exchange_rates_written": 0})}
    except Exception as e:
        print(f"Warning: could not check existing exchange rate ({e}), will fetch and insert.")

    exchange_rate_rows = []
    for currency in ['USD', 'EUR']:
        rate = get_nbp_rate(currency)
        if rate is not None:
            exchange_rate_rows.append({
                "from_currency": currency,
                "to_currency":   "PLN",
                "rate":          rate,
                "source":        "NBP",
                "timestamp":     current_ts,
            })
            print(f"NBP {currency}/PLN rate: {rate}")
        else:
            print(f"Could not fetch NBP {currency}/PLN rate, skipping.")

    if exchange_rate_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.exchange_rates", exchange_rate_rows)
        if errors:
            print(f"exchange_rates write errors: {errors}")

    print(f"INVOCATION_END | date={run_date} | mode=exchange_rates | exchange_rates_written={len(exchange_rate_rows)}")
    return {
        'statusCode': 200,
        'body': json.dumps({"status": "success", "exchange_rates_written": len(exchange_rate_rows)}),
    }


def _handle_batch_prices(event, context):
    """
    Fetch Steam prices for one batch slice of the owned inventory.

    Invoked by 20 parallel Airflow tasks (batch_index 0–19, batch_size 5).
    Each Lambda invocation runs on a fresh AWS IP, bypassing Steam's per-IP rate limit.
    Daily shuffle (date-seeded) rotates which items land on which IP.
    """
    request_id, run_date, current_ts = _parse_run_context(event, context)
    batch_index = int((event or {}).get('batch_index', 0))
    batch_size  = int((event or {}).get('batch_size', 5))
    print(f"INVOCATION_START | date={run_date} | mode=batch_prices | batch_index={batch_index} | batch_size={batch_size} | request_id={request_id}")

    items = _scan_dynamodb()
    if not items:
        print(f"INVOCATION_END | date={run_date} | mode=batch_prices | status=no_items")
        return {'statusCode': 200, 'body': json.dumps({"status": "success", "prices_written": 0, "volumes_written": 0})}

    client     = _get_bq_client()
    medians_7d = _fetch_7d_medians(client, run_date)

    net_quantity = _compute_net_quantity(items)
    owned_items  = _deduped_owned_items(items, net_quantity, run_date)

    start = batch_index * batch_size
    end   = start + batch_size
    batch_items = owned_items[start:end]
    print(f"BATCH_PRICE_FETCH | batch_index={batch_index} | items_in_batch={len(batch_items)} | range={start}-{end - 1} | total_owned_unique_items={len(owned_items)}")

    prices_rows, volume_rows, results = _write_prices_and_volumes(client, batch_items, medians_7d, current_ts)

    print(f"INVOCATION_END | date={run_date} | mode=batch_prices | batch_index={batch_index} | prices_written={len(prices_rows)} | volumes_written={len(volume_rows)}")
    return {
        'statusCode': 200,
        'body': json.dumps({"status": "success", "prices_written": len(prices_rows), "volumes_written": len(volume_rows), "results": results}),
    }


# ---------------------------------------------------------------------------
# Legacy all-in-one handler (backward compatibility: no mode / backfill / retry_missing)
# ---------------------------------------------------------------------------

def _handle_legacy(event, context):
    request_id, run_date, current_ts = _parse_run_context(event, context)

    batch_index   = (event or {}).get('batch_index')
    batch_size    = int((event or {}).get('batch_size', 10))
    retry_missing = bool((event or {}).get('retry_missing', False))

    if retry_missing:
        print(f"INVOCATION_START | date={run_date} | mode=retry_missing | request_id={request_id}")
    elif batch_index is not None:
        batch_index = int(batch_index)
        print(f"INVOCATION_START | date={run_date} | batch_index={batch_index} | batch_size={batch_size} | request_id={request_id}")
    else:
        print(f"INVOCATION_START | date={run_date} | batch_mode=off | request_id={request_id}")

    print(f"Scanning DynamoDB: {DYNAMODB_TABLE}")
    items = _scan_dynamodb()

    if not items:
        print(f"INVOCATION_END | date={run_date} | status=no_items")
        return {'statusCode': 200, 'body': 'No items found.'}

    client = _get_bq_client()

    # Double-fire detection only applies in non-batch, non-retry mode.
    # In batch mode, later batches legitimately see prices from earlier batches — not a double-fire.
    # In retry_missing mode, prices from the 07:00 run are expected to exist.
    if batch_index is None and not retry_missing:
        try:
            check = f"SELECT COUNT(*) AS cnt FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.prices_history` WHERE DATE(timestamp) = '{run_date}'"
            rows = list(client.query(check).result())
            existing_price_rows = rows[0].cnt
            if existing_price_rows > 0:
                print(f"DOUBLE_FIRE_DETECTED | date={run_date} | existing_price_rows={existing_price_rows} | proceeding (silver layer deduplicates via ROW_NUMBER)")
            else:
                print(f"IDEMPOTENCY_OK | date={run_date} | no existing price rows")
        except Exception as e:
            print(f"Warning: double-fire check failed ({e}), proceeding.")

    assets_rows = []
    sales_rows  = []

    existing_asset_ids = set()
    existing_sell_ids  = set()
    if not retry_missing:
        try:
            query = f"SELECT DISTINCT asset_id FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history`"
            existing_asset_ids = {row.asset_id for row in client.query(query).result()}
            print(f"Found {len(existing_asset_ids)} existing assets, will skip re-inserts.")
        except Exception as e:
            print(f"Warning: could not fetch existing asset_ids ({e}), will insert all.")

        try:
            query = f"SELECT DISTINCT asset_id FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.sales_history`"
            existing_sell_ids = {row.asset_id for row in client.query(query).result()}
            print(f"Found {len(existing_sell_ids)} existing sells, will skip re-inserts.")
        except Exception as e:
            print(f"Warning: could not fetch existing sell_ids ({e}), will insert all.")

    medians_7d = _fetch_7d_medians(client, run_date)

    exchange_rate_rows = []
    existing_rate_for_date = False
    try:
        rate_check = f"SELECT COUNT(*) AS cnt FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.exchange_rates` WHERE DATE(timestamp) = '{run_date}'"
        rate_rows = list(client.query(rate_check).result())
        existing_rate_for_date = rate_rows[0].cnt > 0
        if existing_rate_for_date:
            print(f"EXCHANGE_RATE_SKIP | date={run_date} | rate already recorded for today")
    except Exception as e:
        print(f"Warning: could not check existing exchange rate ({e}), will fetch and insert.")

    if not existing_rate_for_date:
        for currency in ['USD', 'EUR']:
            rate = get_nbp_rate(currency)
            if rate is not None:
                exchange_rate_rows.append({
                    "from_currency": currency,
                    "to_currency":   "PLN",
                    "rate":          rate,
                    "source":        "NBP",
                    "timestamp":     current_ts,
                })
                print(f"NBP {currency}/PLN rate: {rate}")
            else:
                print(f"Could not fetch NBP {currency}/PLN rate, skipping.")

    net_quantity = _compute_net_quantity(items)

    for item in items:
        item_id    = item['item_id']
        asset_id   = item.get('asset_id')
        event_type = item.get('event_type', 'buy')

        if event_type == 'buy':
            if not retry_missing and asset_id not in existing_asset_ids:
                assets_rows.append({
                    "asset_id":         asset_id,
                    "item_id":          item_id,
                    "buy_date":         item.get('buy_date'),
                    "buy_price":        float(item.get('buy_price', 0)),
                    "buy_currency":     item.get('buy_currency', 'PLN'),
                    "quantity":         int(item.get('quantity', 1)),
                    "category":         item.get('category', 'Skin'),
                    "purchase_channel": item.get('purchase_channel', 'Unknown'),
                    "last_updated":     current_ts,
                })
        elif event_type == 'sell':
            if not retry_missing and asset_id not in existing_sell_ids:
                sales_rows.append({
                    "asset_id":      asset_id,
                    "item_id":       item_id,
                    "sell_price":    float(item.get('sell_price', 0)),
                    "sell_currency": item.get('sell_currency', 'PLN'),
                    "sell_date":     item.get('sell_date'),
                    "sell_channel":  item.get('sell_channel', 'Unknown'),
                    "category":      item.get('category', 'Skin'),
                    "quantity":      int(item.get('quantity', 1)),
                    "timestamp":     current_ts,
                })

    buy_items_deduped = _deduped_owned_items(items, net_quantity, run_date)

    if retry_missing:
        try:
            missing_query = f"""
                SELECT DISTINCT a.item_id
                FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history` a
                WHERE a.item_id NOT IN (
                    SELECT item_id FROM `{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.prices_history`
                    WHERE DATE(timestamp) = '{run_date}' AND price_flagged = FALSE
                )
            """
            missing_item_ids = {row.item_id for row in client.query(missing_query).result()}
            print(f"RETRY_MISSING | found {len(missing_item_ids)} items without valid price today")
        except Exception as e:
            print(f"Warning: could not fetch missing item_ids ({e}), aborting retry.")
            return {'statusCode': 200, 'body': json.dumps({"status": "retry_aborted", "reason": str(e)})}

        if not missing_item_ids:
            print(f"RETRY_MISSING | all items already have prices, nothing to do")
            return {'statusCode': 200, 'body': json.dumps({"status": "success", "prices_written": 0})}

        items_for_prices = [item for item in buy_items_deduped if item['item_id'] in missing_item_ids]
    elif batch_index is not None:
        start = batch_index * batch_size
        end   = start + batch_size
        items_for_prices = buy_items_deduped[start:end]
        print(f"BATCH_PRICE_FETCH | batch_index={batch_index} | items_in_batch={len(items_for_prices)} | range={start}-{end - 1} | total_owned_unique_items={len(buy_items_deduped)}")
    else:
        items_for_prices = buy_items_deduped

    prices_rows, volume_rows, price_results = _write_prices_and_volumes(client, items_for_prices, medians_7d, current_ts)

    results = {**price_results}

    if assets_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.assets_history", assets_rows)
        results['assets'] = "success" if not errors else f"errors: {errors}"
        print(f"assets_history: {results['assets']}")

    if sales_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.sales_history", sales_rows)
        results['sales'] = "success" if not errors else f"errors: {errors}"
        print(f"sales_history: {results['sales']}")

    if exchange_rate_rows:
        errors = client.insert_rows_json(f"{GCP_PROJECT_ID}.{BQ_DATASET_RAW}.exchange_rates", exchange_rate_rows)
        results['exchange_rates'] = "success" if not errors else f"errors: {errors}"
        print(f"exchange_rates: {results['exchange_rates']}")

    summary = {
        "status":                 "success",
        "assets_written":         len(assets_rows),
        "sales_written":          len(sales_rows),
        "prices_written":         len(prices_rows),
        "volumes_written":        len(volume_rows),
        "exchange_rates_written": len(exchange_rate_rows),
        "results":                results,
    }
    print(f"INVOCATION_END | date={run_date} | batch_index={batch_index} | assets_written={len(assets_rows)} | sales_written={len(sales_rows)} | prices_written={len(prices_rows)} | volumes_written={len(volume_rows)} | exchange_rates_written={len(exchange_rate_rows)}")

    return {'statusCode': 200, 'body': json.dumps(summary)}


# ---------------------------------------------------------------------------
# Entry point — dispatches by mode for Airflow single-responsibility tasks,
# falls through to legacy all-in-one handler for manual/backfill/retry invocations.
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    mode = (event or {}).get('mode')
    if mode == 'sync_inventory':
        return _handle_sync_inventory(event, context)
    if mode == 'exchange_rates':
        return _handle_exchange_rates(event, context)
    if mode == 'batch_prices':
        return _handle_batch_prices(event, context)
    return _handle_legacy(event, context)
