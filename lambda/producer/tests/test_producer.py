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


def _bq_client_mock(existing_asset_ids=None, existing_sell_ids=None, existing_price_rows=0, existing_exchange_rate_rows=0):
    client = MagicMock()

    def _query_side_effect(sql):
        result = MagicMock()
        if "assets_history" in sql and "SELECT DISTINCT" in sql:
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
# Test 2 — Steam: volume == 0 → price_flagged = True
# ---------------------------------------------------------------------------

def test_steam_zero_volume_flagged():
    with patch("producer_lambda.requests.get", return_value=_steam_response("100.00", "0")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=100.0)

    assert result is not None
    price, flagged = result
    assert price == 100.0
    assert flagged is True


# ---------------------------------------------------------------------------
# Test 3 — Steam: price > 50% above 7-day median → price_flagged = True
# ---------------------------------------------------------------------------

def test_steam_spike_above_threshold_flagged():
    with patch("producer_lambda.requests.get", return_value=_steam_response("200.00", "50")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=100.0)

    price, flagged = result
    assert price == 200.0
    assert flagged is True


# ---------------------------------------------------------------------------
# Test 4 — Steam: 49% deviation is within threshold → price_flagged = False
# ---------------------------------------------------------------------------

def test_steam_below_threshold_not_flagged():
    with patch("producer_lambda.requests.get", return_value=_steam_response("149.00", "200")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=100.0)

    _, flagged = result
    assert flagged is False


# ---------------------------------------------------------------------------
# Test 5 — Steam: no median provided → only volume check applies
# ---------------------------------------------------------------------------

def test_steam_no_median_only_volume_check():
    with patch("producer_lambda.requests.get", return_value=_steam_response("999.00", "10")):
        result = producer_lambda.get_steam_price("AWP | Test", median_7d=None)

    _, flagged = result
    assert flagged is False


# ---------------------------------------------------------------------------
# Test 6 — Buy idempotency: existing asset_id → no re-insert
# ---------------------------------------------------------------------------

def test_buy_idempotency_skips_existing_asset():
    item = _make_buy_item(asset_id="uuid-already-in-bq")
    bq = _bq_client_mock(existing_asset_ids=["uuid-already-in-bq"])

    with patch.object(producer_lambda.inventory_table, "scan", return_value={"Items": [item]}):
        with patch("producer_lambda.bigquery.Client", return_value=bq):
            with patch("producer_lambda.get_steam_price", return_value=(100.0, False)):
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
            with patch("producer_lambda.get_steam_price", return_value=(100.0, False)):
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
            with patch("producer_lambda.get_steam_price", return_value=(100.0, False)):
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
            with patch("producer_lambda.get_steam_price", return_value=(100.0, False)):
                with patch("producer_lambda.get_nbp_rate", return_value=3.95) as mock_nbp:
                    producer_lambda.lambda_handler({}, None)

    insert_calls = [str(c) for c in bq.insert_rows_json.call_args_list]
    assert not any("exchange_rates" in c for c in insert_calls)
    mock_nbp.assert_not_called()


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