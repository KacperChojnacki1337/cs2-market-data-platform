import pytest
from unittest.mock import patch, MagicMock
import skinport_lambda


def _skinport_response(items):
    """Mock Skinport API response with given items list."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = items
    return resp


def _bq_client_mock(owned_item_ids=None):
    """Mock BigQuery client for testing."""
    client = MagicMock()

    def _query_side_effect(sql):
        result = MagicMock()
        if "SELECT DISTINCT item_id" in sql and "dim_assets" in sql:
            result.result.return_value = [MagicMock(item_id=i) for i in (owned_item_ids or [])]
        else:
            result.result.return_value = []
        return result

    client.query.side_effect = _query_side_effect
    client.insert_rows_json.return_value = []
    return client


# ---------------------------------------------------------------------------
# Test 1 — Skinport API success: items matched and prices extracted
# ---------------------------------------------------------------------------

def test_skinport_fetch_success():
    owned_items = ["AWP | Dragon Lore", "M4A1-S | Printstream"]
    skinport_data = [
        {"market_hash_name": "AWP | Dragon Lore", "suggested_price": 1500.50},
        {"market_hash_name": "M4A1-S | Printstream", "suggested_price": 250.00},
        {"market_hash_name": "AK-47 | Neon Rider", "suggested_price": 800.00},  # Not owned
    ]

    with patch("skinport_lambda.requests.get", return_value=_skinport_response(skinport_data)):
        result = skinport_lambda.fetch_skinport_prices(owned_items)

    assert result is not None
    assert len(result) == 2
    assert result["AWP | Dragon Lore"] == 1500.50
    assert result["M4A1-S | Printstream"] == 250.00


# ---------------------------------------------------------------------------
# Test 2 — Item mismatch: owned item not on Skinport
# ---------------------------------------------------------------------------

def test_skinport_item_mismatch():
    owned_items = ["AWP | Dragon Lore", "M4A1-S | Printstream", "Rare Unknown Item"]
    skinport_data = [
        {"market_hash_name": "AWP | Dragon Lore", "suggested_price": 1500.50},
        {"market_hash_name": "M4A1-S | Printstream", "suggested_price": 250.00},
    ]

    with patch("skinport_lambda.requests.get", return_value=_skinport_response(skinport_data)):
        result = skinport_lambda.fetch_skinport_prices(owned_items)

    assert result is not None
    assert len(result) == 2  # Only matched items
    assert "Rare Unknown Item" not in result


# ---------------------------------------------------------------------------
# Test 3 — Skinport API failure (429) with retry
# ---------------------------------------------------------------------------

def test_skinport_429_retries():
    owned_items = ["AWP | Dragon Lore"]
    mock_429 = MagicMock()
    mock_429.status_code = 429

    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = [
        {"market_hash_name": "AWP | Dragon Lore", "suggested_price": 1500.50}
    ]

    with patch("skinport_lambda.requests.get", side_effect=[mock_429, mock_200]):
        result = skinport_lambda.fetch_skinport_prices(owned_items, retries=2, backoff=0)

    assert result is not None
    assert len(result) == 1
    assert result["AWP | Dragon Lore"] == 1500.50


# ---------------------------------------------------------------------------
# Test 4 — Skinport API failure: all retries exhausted
# ---------------------------------------------------------------------------

def test_skinport_all_retries_failed():
    owned_items = ["AWP | Dragon Lore"]
    mock_500 = MagicMock()
    mock_500.status_code = 500

    with patch("skinport_lambda.requests.get", return_value=mock_500):
        result = skinport_lambda.fetch_skinport_prices(owned_items, retries=2, backoff=0)

    assert result is None


# ---------------------------------------------------------------------------
# Test 4b — median_price is preferred over suggested_price when present
# ---------------------------------------------------------------------------

def test_skinport_prefers_median_over_suggested_price():
    owned_items = ["AWP | Dragon Lore"]
    skinport_data = [
        {"market_hash_name": "AWP | Dragon Lore", "median_price": 1200.00, "suggested_price": 1500.50},
    ]

    with patch("skinport_lambda.requests.get", return_value=_skinport_response(skinport_data)):
        result = skinport_lambda.fetch_skinport_prices(owned_items)

    assert result["AWP | Dragon Lore"] == 1200.00


# ---------------------------------------------------------------------------
# Test 4c — null median_price (no current listings) falls back to suggested_price
# ---------------------------------------------------------------------------

def test_skinport_null_median_falls_back_to_suggested_price():
    owned_items = ["AWP | Dragon Lore"]
    skinport_data = [
        {"market_hash_name": "AWP | Dragon Lore", "median_price": None, "suggested_price": 1500.50},
    ]

    with patch("skinport_lambda.requests.get", return_value=_skinport_response(skinport_data)):
        result = skinport_lambda.fetch_skinport_prices(owned_items)

    assert result["AWP | Dragon Lore"] == 1500.50


# ---------------------------------------------------------------------------
# Test 5 — Lambda handler: no owned items in dim_assets
# ---------------------------------------------------------------------------

def test_lambda_handler_no_owned_items():
    bq = _bq_client_mock(owned_item_ids=[])

    with patch("skinport_lambda.bigquery.Client", return_value=bq):
        response = skinport_lambda.lambda_handler({}, None)

    assert response["statusCode"] == 200
    body = response["body"]
    assert '"status": "success"' in body
    assert '"prices_written": 0' in body


# ---------------------------------------------------------------------------
# Test 6 — Lambda handler: full flow with items matched and written
# ---------------------------------------------------------------------------

def test_lambda_handler_full_flow():
    owned_items = ["AWP | Dragon Lore"]
    skinport_data = [
        {"market_hash_name": "AWP | Dragon Lore", "suggested_price": 1500.50}
    ]

    bq = _bq_client_mock(owned_item_ids=owned_items)
    captured_rows = []

    def _capture_insert(table_id, rows):
        captured_rows.extend([(table_id, r) for r in rows])
        return []

    bq.insert_rows_json.side_effect = _capture_insert

    with patch("skinport_lambda.bigquery.Client", return_value=bq):
        with patch("skinport_lambda.requests.get", return_value=_skinport_response(skinport_data)):
            response = skinport_lambda.lambda_handler({}, None)

    assert response["statusCode"] == 200
    body_str = response["body"]
    assert '"status": "success"' in body_str
    assert '"prices_written": 1' in body_str

    # Verify BigQuery insert was called
    assert len(captured_rows) == 1
    table_id, row = captured_rows[0]
    assert "skinport_prices_history" in table_id
    assert row["item_id"] == "AWP | Dragon Lore"
    assert row["skinport_price_pln"] == 1500.50


# ---------------------------------------------------------------------------
# Test 7 — Lambda handler raises (not a caught 500) when the Skinport fetch
# fails after all retries, so CloudWatch records a failed invocation and the
# skinport_errors alarm can fire.
# ---------------------------------------------------------------------------

def test_lambda_handler_raises_on_skinport_fetch_failure():
    owned_items = ["AWP | Dragon Lore"]
    bq = _bq_client_mock(owned_item_ids=owned_items)
    mock_500 = MagicMock()
    mock_500.status_code = 500

    with patch("skinport_lambda.bigquery.Client", return_value=bq):
        with patch("skinport_lambda.requests.get", return_value=mock_500):
            with pytest.raises(RuntimeError, match="Skinport fetch failed"):
                skinport_lambda.lambda_handler({}, None)


# ---------------------------------------------------------------------------
# Test 8 — Lambda handler raises when the dim_assets query itself fails
# ---------------------------------------------------------------------------

def test_lambda_handler_raises_on_owned_items_query_failure():
    bq = MagicMock()
    bq.query.side_effect = RuntimeError("BQ unavailable")

    with patch("skinport_lambda.bigquery.Client", return_value=bq):
        with pytest.raises(RuntimeError, match="BQ unavailable"):
            skinport_lambda.lambda_handler({}, None)