from unittest.mock import patch, MagicMock
from decimal import Decimal
import producer_lambda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _steam_response(price: str, volume: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "success": True,
        "lowest_price": f"${price}",
        "volume": volume,
        "median_price": f"${price}",
    }
    return resp


def _bq_client_mock(existing_asset_ids=None, existing_sell_ids=None, existing_price_rows=0, existing_exchange_rate_rows=0, missing_item_ids=None):
    client = MagicMock()

    def _query_side_effect(sql):
        result = MagicMock()
        if "NOT IN" in sql and "assets_history" in sql:
            result.result.return_value = [MagicMock(item_id=i) for i in (missing_item_ids or [])]
        elif "assets_history" in sql and "SELECT DISTINCT" in sql:
            result.result.return_value = [MagicMock(asset_id=a) for a in (existing_asset_ids or [])]
        elif "sales_history" in sql and "SELECT DISTINCT" in sql:
            result.result.return_value = [MagicMock(asset_id=s) for s in (existing_sell_ids or [])]
        elif "prices_history" in sql and "COUNT" in sql:
            result.result.return_value = [MagicMock(cnt=existing_price_rows)]
        elif "exchange_rates" in sql and "COUNT" in sql:
            result.result.return_value = [MagicMock(cnt=existing_exchange_rate_rows)]
        elif "APPROX_QUANTILES" in sql:
            result.result.return_value = []
        else:
            result.result.return_value = []
        return result

    client.query.side_effect = _query_side_effect
    client.insert_rows_json.return_value = []
    return client


def _make_buy_item(asset_id="uuid-buy-1", item_id="AWP | Test"):
    return {
        "asset_id": asset_id,
        "item_id": item_id,
        "event_type": "buy",
        "buy_price": Decimal("100.00"),
        "buy_currency": "PLN",
        "buy_date": "2026-01-01",
        "quantity": Decimal("1"),
        "category": "Skin",
        "purchase_channel": "CSFloat",
    }


def _make_sell_item(asset_id="uuid-sell-1", item_id="AWP | Test", sell_channel="Steam"):
    return {
        "asset_id": asset_id,
        "item_id": item_id,
        "event_type": "sell",
        "sell_price": Decimal("150.00"),
        "sell_currency": "PLN",
        "sell_date": "2026-06-01",
        "sell_channel": sell_channel,
        "quantity": Decimal("1"),
        "category": "Skin",
    }


# ---------------------------------------------------------------------------
# Test 1 — NBP fallback: weekend/holiday 404 → /last/1/
# ---------------------------------------------------------------------------

def test_nbp_weekend_fallback():
    mock_404 = MagicMock()
    mock_404.status_code = 404

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = {"rates": [{"mid": 3.92}]}

    with patch("producer_lambda.requests.get", side_effect=[mock_404, mock_200]) as mock_get:
        rate = producer_lambda.get_nbp_rate("USD")

    assert rate == 3.92
    second_url = mock_get.call_args_list[1][0][0]
    assert "/last/1/" in second_url


# ---------------------------------------------------------------------------
# Test 2 — Steam: volume == 0 does NOT flag price (decoupled per #82) —
# zero-volume rare items can still have a legitimate, stable price.
# ---------------------------------------------------------------------------

def test_steam_zero_volume_not_flagged():
    with patch("producer_lambda.requests.get", return_value=_steam_response("100.00", "0")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=100.0)

    assert result is not None
    price, volume, flagged = result
    assert price == 100.0
    assert volume == 0
    assert flagged is False


# ---------------------------------------------------------------------------
# Test 3 — Steam: price > 50% above 7-day median → price_flagged = True
# ---------------------------------------------------------------------------

def test_steam_spike_above_threshold_flagged():
    with patch("producer_lambda.requests.get", return_value=_steam_response("200.00", "50")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=100.0)

    price, volume, flagged = result
    assert price == 200.0
    assert flagged is True


# ---------------------------------------------------------------------------
# Test 4 — Steam: 49% deviation is within threshold → price_flagged = False
# ---------------------------------------------------------------------------

def test_steam_below_threshold_not_flagged():
    with patch("producer_lambda.requests.get", return_value=_steam_response("149.00", "200")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=100.0)

    _, _, flagged = result
    assert flagged is False


# ---------------------------------------------------------------------------
# Test 5 — Steam: no median provided → never flagged, regardless of volume
# ---------------------------------------------------------------------------

def test_steam_no_median_never_flagged():
    with patch("producer_lambda.requests.get", return_value=_steam_response("999.00", "0")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=None)

    _, _, flagged = result
    assert flagged is False


# ---------------------------------------------------------------------------
# Test 6 — Buy idempotency: existing asset_id → no re-insert
# ---------------------------------------------------------------------------

def test_buy_idempotency_skips_existing_asset():
    item = _make_buy_item(asset_id="uuid-already-in-bq")
    bq = _bq_client_mock(existing_asset_ids=["uuid-already-in-bq"])

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(100.0, 100, False)):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    producer_lambda.lambda_handler({}, None)

    insert_calls = [str(c) for c in bq.insert_rows_json.call_args_list]
    assert not any("assets_history" in c for c in insert_calls)


# ---------------------------------------------------------------------------
# Test 7 — Missing event_type defaults to buy (backwards compatibility)
# ---------------------------------------------------------------------------

def test_missing_event_type_defaults_to_buy():
    item = _make_buy_item()
    del item["event_type"]

    bq = _bq_client_mock()

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(100.0, 100, False)):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    producer_lambda.lambda_handler({}, None)

    insert_calls = [str(c) for c in bq.insert_rows_json.call_args_list]
    assert any("assets_history" in c for c in insert_calls)


# ---------------------------------------------------------------------------
# Test 8 — Steam API failure (returns None) → price row skipped, handler continues
# ---------------------------------------------------------------------------

def test_steam_api_failure_skips_price_row():
    item = _make_buy_item()
    bq = _bq_client_mock()

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=None):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    result = producer_lambda.lambda_handler({}, None)

    import json as _json
    body = _json.loads(result["body"])
    assert body["prices_written"] == 0
    assert body["status"] == "success"


# ---------------------------------------------------------------------------
# Test 9 — Backfill mode: event with 'date' key uses that date, not today
# ---------------------------------------------------------------------------

def test_backfill_mode_uses_event_date():
    item = _make_buy_item()
    bq = _bq_client_mock()

    captured_rows = []

    def _capture_insert(table_id, rows):
        captured_rows.extend([(table_id, r) for r in rows])
        return []

    bq.insert_rows_json.side_effect = _capture_insert

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(100.0, 100, False)):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    producer_lambda.lambda_handler({"date": "2026-01-15"}, None)

    for _table, row in captured_rows:
        ts = row.get("timestamp") or row.get("last_updated")
        assert ts.startswith("2026-01-15"), (
            f"Expected timestamp to start with '2026-01-15', got {ts!r}"
        )


# ---------------------------------------------------------------------------
# Test 10 — Exchange rate idempotency: rate already exists for today → skip insert
# ---------------------------------------------------------------------------

def test_exchange_rate_idempotency_skips_existing():
    item = _make_buy_item()
    bq = _bq_client_mock(existing_exchange_rate_rows=1)

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(100.0, 100, False)):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95) as mock_nbp:
                    producer_lambda.lambda_handler({}, None)

    insert_calls = [str(c) for c in bq.insert_rows_json.call_args_list]
    assert not any("exchange_rates" in c for c in insert_calls)
    mock_nbp.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9b — EUR/PLN exchange rate fetched alongside USD/PLN
# ---------------------------------------------------------------------------

def test_eur_pln_exchange_rate_fetched():
    item = _make_buy_item()
    bq = _bq_client_mock(existing_exchange_rate_rows=0)

    captured_rows = []

    def _capture_insert(table_id, rows):
        captured_rows.extend([(table_id, r) for r in rows])
        return []

    bq.insert_rows_json.side_effect = _capture_insert

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(100.0, 100, False)):
                with patch("producer_lambda.get_nbp_rate", side_effect=[3.95, 4.05]) as mock_nbp:
                    producer_lambda.lambda_handler({}, None)

    # Verify both USD and EUR rates were fetched
    assert mock_nbp.call_count == 2
    mock_nbp.assert_any_call("USD")
    mock_nbp.assert_any_call("EUR")

    # Verify both rates written to BigQuery
    exchange_rate_rows = [r for table, r in captured_rows if table.endswith("exchange_rates")]
    assert len(exchange_rate_rows) == 2
    assert {"USD", "EUR"} == {r["from_currency"] for r in exchange_rate_rows}
    assert all(r["to_currency"] == "PLN" for r in exchange_rate_rows)
    assert exchange_rate_rows[0]["rate"] == 3.95  # USD
    assert exchange_rate_rows[1]["rate"] == 4.05  # EUR


# ---------------------------------------------------------------------------
# Test 11 — Sell event: sell_channel written to sales row, not purchase_channel
# ---------------------------------------------------------------------------

def test_sell_event_writes_sell_channel():
    sell_item = _make_sell_item(sell_channel="CSFloat")
    bq = _bq_client_mock()

    captured_rows = []

    def _capture_insert(table_id, rows):
        captured_rows.extend([(table_id, r) for r in rows])
        return []

    bq.insert_rows_json.side_effect = _capture_insert

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [sell_item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                producer_lambda.lambda_handler({}, None)

    sales_rows = [row for table, row in captured_rows if "sales_history" in table]
    assert len(sales_rows) == 1
    assert sales_rows[0]["sell_channel"] == "CSFloat"
    assert "purchase_channel" not in sales_rows[0]


# ---------------------------------------------------------------------------
# Test 12 — Steam 429: retries with backoff, succeeds on second attempt
# ---------------------------------------------------------------------------

def test_steam_429_retries_and_succeeds():
    mock_429 = MagicMock()
    mock_429.status_code = 429

    with patch("producer_lambda.requests.get", side_effect=[mock_429, _steam_response("50.00", "100")]):
        with patch("producer_lambda.time.sleep") as mock_sleep:
            result = producer_lambda.get_steam_price("AWP | Test")

    assert result is not None
    price, volume, flagged = result
    assert price == 50.0
    assert volume == 100
    assert flagged is False
    mock_sleep.assert_called_once_with(10)


# ---------------------------------------------------------------------------
# Test 13 — Steam 429: exhausts all retries, returns None
# ---------------------------------------------------------------------------

def test_steam_429_exhausts_retries_returns_none():
    mock_429 = MagicMock()
    mock_429.status_code = 429

    with patch("producer_lambda.requests.get", return_value=mock_429):
        with patch("producer_lambda.time.sleep"):
            result = producer_lambda.get_steam_price("AWP | Test")

    assert result is None


# ---------------------------------------------------------------------------
# Test 15 — Batch mode: only this batch's items get price fetches
# ---------------------------------------------------------------------------

def test_batch_mode_fetches_only_batch_items():
    # 5 buy items — after dedup + daily shuffle (seeded by run_date), batch_index=1, batch_size=2
    # fetches exactly 2 items from the shuffled list (which 2 depends on the date seed).
    import json as _json, random as _random, datetime as _dt
    items = [
        _make_buy_item(asset_id=f"uuid-{n}", item_id=f"{c} | Skin")
        for n, c in enumerate(["C", "A", "E", "B", "D"])
    ]
    bq = _bq_client_mock()

    fetched_names = []

    def _capture_steam(name, **kwargs):
        fetched_names.append(name)
        return (10.0, 100, False)

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": items}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", side_effect=_capture_steam):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    result = producer_lambda.lambda_handler({"batch_index": 1, "batch_size": 2}, None)

    body = _json.loads(result["body"])
    assert body["prices_written"] == 2
    # Exactly 2 items fetched; must be a subset of the 5 valid item_ids
    all_names = {f"{c} | Skin" for c in ["A", "B", "C", "D", "E"]}
    assert len(fetched_names) == 2
    assert all(name in all_names for name in fetched_names)


# ---------------------------------------------------------------------------
# Test 16 — Batch mode: assets written for ALL items even when batch_index limits prices
# ---------------------------------------------------------------------------

def test_batch_mode_assets_written_for_all_items():
    items = [
        _make_buy_item(asset_id=f"uuid-{i}", item_id=f"Item{i} | Skin")
        for i in range(5)
    ]
    bq = _bq_client_mock()

    captured_rows = []

    def _capture_insert(table_id, rows):
        captured_rows.extend([(table_id, r) for r in rows])
        return []

    bq.insert_rows_json.side_effect = _capture_insert

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": items}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(10.0, 100, False)):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    # batch_index=0 fetches only items 0-1, but ALL 5 assets should be written
                    producer_lambda.lambda_handler({"batch_index": 0, "batch_size": 2}, None)

    asset_rows = [row for table, row in captured_rows if "assets_history" in table]
    price_rows = [row for table, row in captured_rows if "prices_history" in table]
    assert len(asset_rows) == 5, f"Expected 5 assets written, got {len(asset_rows)}"
    assert len(price_rows) == 2, f"Expected 2 prices written (batch slice), got {len(price_rows)}"


# ---------------------------------------------------------------------------
# Test 17 — net_quantity: sold items excluded from price fetch
# ---------------------------------------------------------------------------

def test_sold_items_excluded_from_price_fetch():
    buy_item  = _make_buy_item(asset_id="uuid-buy-sold", item_id="AWP | Sold Skin")
    sell_item = _make_sell_item(asset_id="uuid-sell-1",   item_id="AWP | Sold Skin")
    owned_item = _make_buy_item(asset_id="uuid-buy-keep", item_id="AK-47 | Kept Skin")

    bq = _bq_client_mock()
    fetched_names = []

    def _capture_steam(name, **kwargs):
        fetched_names.append(name)
        return (10.0, 100, False)

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [buy_item, sell_item, owned_item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", side_effect=_capture_steam):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    producer_lambda.lambda_handler({}, None)

    # AWP | Sold Skin: net_quantity = 1 buy - 1 sell = 0 → no price fetch
    # AK-47 | Kept Skin: net_quantity = 1 buy → price fetched
    assert "AWP | Sold Skin" not in fetched_names
    assert "AK-47 | Kept Skin" in fetched_names


# ---------------------------------------------------------------------------
# Test 18 — retry_missing: fetches only items without a valid price today, no assets/sales written
# ---------------------------------------------------------------------------

def test_retry_missing_fetches_only_missing_items():
    items = [
        _make_buy_item(asset_id=f"uuid-{i}", item_id=f"Item{i} | Skin")
        for i in range(4)
    ]
    # BQ reports Item1 and Item3 as missing (no valid price today)
    bq = _bq_client_mock(
        existing_exchange_rate_rows=1,
        missing_item_ids=["Item1 | Skin", "Item3 | Skin"],
    )

    fetched_names = []
    captured_rows = []

    def _capture_steam(name, **kwargs):
        fetched_names.append(name)
        return (10.0, 100, False)

    def _capture_insert(table_id, rows):
        captured_rows.extend([(table_id, r) for r in rows])
        return []

    bq.insert_rows_json.side_effect = _capture_insert

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": items}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", side_effect=_capture_steam):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    import json as _json
                    result = producer_lambda.lambda_handler({"retry_missing": True}, None)

    body = _json.loads(result["body"])
    # Only the 2 missing items fetched (order may vary due to daily shuffle)
    assert set(fetched_names) == {"Item1 | Skin", "Item3 | Skin"}
    assert body["prices_written"] == 2
    # No assets or sales written in retry_missing mode
    assert not any("assets_history" in t for t, _ in captured_rows)
    assert not any("sales_history" in t for t, _ in captured_rows)


# ---------------------------------------------------------------------------
# Test 19 — Steam volume (#69): handler writes volume_7d to volume_history
# ---------------------------------------------------------------------------

def test_volume_history_written():
    item = _make_buy_item()
    bq = _bq_client_mock()

    captured_rows = []

    def _capture_insert(table_id, rows):
        captured_rows.extend([(table_id, r) for r in rows])
        return []

    bq.insert_rows_json.side_effect = _capture_insert

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(100.0, 42, False)):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95):
                    result = producer_lambda.lambda_handler({}, None)

    import json as _json
    body = _json.loads(result["body"])
    assert body["volumes_written"] == 1

    volume_rows = [row for table, row in captured_rows if "volume_history" in table]
    assert len(volume_rows) == 1
    assert volume_rows[0]["item_id"] == "AWP | Test"
    assert volume_rows[0]["volume_7d"] == 42