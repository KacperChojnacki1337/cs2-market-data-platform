#!/usr/bin/env python3
"""Seed the development bronze layer (steam_raw_dev) with curated fixtures.

The dev BigQuery datasets are created empty by Terraform, which makes CI dbt tests
vacuous (they pass over zero rows) and blocks local preview of the marts. This
loads a small, synthetic, deterministic fixture set that deliberately exercises
every transformation path:

  - multi-lot item + a partial sale        -> FIFO unit matching, realized PnL
  - a free drop (buy_price = 0)            -> worth_to_sell drop handling, tax view
  - a bulk sticker (quantity > 1)          -> FIFO explosion at scale
  - an item with NO Skinport listing       -> coefficient fallback (no gaps)
  - a flagged price row                    -> int_latest_prices excludes it
  - varied 7-day volume                    -> LOW / MEDIUM / HIGH liquidity tiers
  - prices across several dates            -> fct_portfolio_history snapshots

Idempotent: each table is loaded with WRITE_TRUNCATE (full replace), so re-running
resets dev to a known state. Loads (not streaming inserts) are immediately
queryable — dbt can build straight after.

Usage:
    # credentials via env (CI writes the key to a file and points this at it)
    GOOGLE_APPLICATION_CREDENTIALS=/path/key.json python scripts/seed_dev.py
    # or explicitly
    python scripts/seed_dev.py --key C:/path/key.json
"""
import argparse
import os
import sys

from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT = "steam-tracker-portfolio"
DATASET = "steam_raw_dev"

TS = lambda d: f"{d}T12:00:00Z"
PRICE_DATES = ["2026-07-01", "2026-07-05", "2026-07-07"]

# --- assets_history: buys (one drop, one multi-lot item, a bulk sticker) ---------
ASSETS = [
    # AK-47 Redline: TWO lots of the same item_id -> FIFO test
    dict(asset_id="dev-ak-lot1", item_id="AK-47 | Redline (Field-Tested)", buy_date="2026-05-01",
         buy_price=100.0, buy_currency="PLN", quantity=1, category="Skin", purchase_channel="CSFloat"),
    dict(asset_id="dev-ak-lot2", item_id="AK-47 | Redline (Field-Tested)", buy_date="2026-06-01",
         buy_price=120.0, buy_currency="PLN", quantity=1, category="Skin", purchase_channel="CSFloat"),
    # Karambit: high value, illiquid winner -> concentration + TAKE PROFIT (illiquid)
    dict(asset_id="dev-karambit", item_id="Karambit | Doppler (Factory New)", buy_date="2026-04-10",
         buy_price=1500.0, buy_currency="PLN", quantity=1, category="Knife", purchase_channel="Steam"),
    # Gloves
    dict(asset_id="dev-gloves", item_id="Sport Gloves | Vice (Field-Tested)", buy_date="2026-03-15",
         buy_price=800.0, buy_currency="PLN", quantity=1, category="Gloves", purchase_channel="Steam"),
    # Bulk sticker
    dict(asset_id="dev-sticker-bulk", item_id="Sticker | ZywOo | Katowice 2019", buy_date="2026-05-20",
         buy_price=0.10, buy_currency="PLN", quantity=20, category="Sticker", purchase_channel="Steam"),
    # Free drop (buy_price 0)
    dict(asset_id="dev-drop-case", item_id="Kilowatt Case", buy_date="2026-06-25",
         buy_price=0.0, buy_currency="PLN", quantity=5, category="Case", purchase_channel="Drop"),
    # Item with NO Skinport listing -> coefficient fallback
    dict(asset_id="dev-slab-noskinport", item_id="Sticker Slab | FURIA (Holo) | Paris 2023", buy_date="2026-05-15",
         buy_price=20.0, buy_currency="PLN", quantity=1, category="Other", purchase_channel="Steam"),
]

# --- sales_history: partial sale of the multi-lot AK (consumes oldest = lot1) ----
SALES = [
    dict(asset_id="dev-sale-ak", item_id="AK-47 | Redline (Field-Tested)", sell_price=150.0,
         sell_currency="PLN", sell_date="2026-06-15", sell_channel="CSFloat", category="Skin", quantity=1),
]

# --- prices_history: per item across dates + one flagged row --------------------
# base Steam price in USD; slight series so momentum has signal (AK dips from peak)
PRICE_SERIES = {
    "AK-47 | Redline (Field-Tested)":            [66.0, 70.0, 68.0],   # dipped from peak -> past_peak
    "Karambit | Doppler (Factory New)":          [680.0, 700.0, 700.0],
    "Sport Gloves | Vice (Field-Tested)":        [250.0, 252.0, 250.0],
    "Sticker | ZywOo | Katowice 2019":           [0.06, 0.06, 0.07],
    "Kilowatt Case":                             [1.4, 1.5, 1.5],
    "Sticker Slab | FURIA (Holo) | Paris 2023":  [5.5, 5.2, 5.0],
}


def build_prices():
    rows = []
    for item, series in PRICE_SERIES.items():
        for d, px in zip(PRICE_DATES, series):
            rows.append(dict(item_id=item, price_usd=px, price_flagged=False, timestamp=TS(d)))
    # one flagged anomaly for AK (latest date) — int_latest_prices must exclude it
    rows.append(dict(item_id="AK-47 | Redline (Field-Tested)", price_usd=999.0,
                     price_flagged=True, timestamp=f"{PRICE_DATES[-1]}T13:00:00Z"))
    return rows


VOLUME = {  # 7-day volume -> liquidity tier: <5 LOW, <50 MEDIUM, else HIGH
    "AK-47 | Redline (Field-Tested)":            120,   # HIGH
    "Karambit | Doppler (Factory New)":          2,     # LOW
    "Sport Gloves | Vice (Field-Tested)":        3,     # LOW
    "Sticker | ZywOo | Katowice 2019":           30,    # MEDIUM
    "Kilowatt Case":                             500,   # HIGH
    "Sticker Slab | FURIA (Holo) | Paris 2023":  1,     # LOW
}

# Skinport prices in PLN — deliberately MISSING the FURIA slab (fallback test)
SKINPORT = {
    "AK-47 | Redline (Field-Tested)":            250.0,
    "Karambit | Doppler (Factory New)":          2600.0,
    "Sport Gloves | Vice (Field-Tested)":        900.0,
    "Sticker | ZywOo | Katowice 2019":           0.20,
    "Kilowatt Case":                             5.0,
}

EXCHANGE = []
for d in PRICE_DATES:
    EXCHANGE.append(dict(from_currency="USD", to_currency="PLN", rate=3.75, source="NBP", timestamp=TS(d)))
    EXCHANGE.append(dict(from_currency="EUR", to_currency="PLN", rate=4.30, source="NBP", timestamp=TS(d)))


def build_all():
    prices = build_prices()
    volume = [dict(item_id=k, volume_7d=v, timestamp=TS(PRICE_DATES[-1])) for k, v in VOLUME.items()]
    skinport = [dict(item_id=k, skinport_price_pln=v, timestamp=TS(PRICE_DATES[-1])) for k, v in SKINPORT.items()]
    assets = [dict(**a, last_updated=TS(a["buy_date"])) for a in ASSETS]
    sales = [dict(**s, timestamp=TS(s["sell_date"])) for s in SALES]
    return {
        "assets_history": assets,
        "sales_history": sales,
        "prices_history": prices,
        "volume_history": volume,
        "skinport_prices_history": skinport,
        "exchange_rates": EXCHANGE,
    }


def main():
    ap = argparse.ArgumentParser(description="Seed steam_raw_dev with fixtures")
    ap.add_argument("--key", help="Path to GCP service-account JSON (else GOOGLE_APPLICATION_CREDENTIALS / ADC)")
    args = ap.parse_args()

    key = args.key or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    creds = service_account.Credentials.from_service_account_file(key) if key else None
    client = bigquery.Client(project=PROJECT, credentials=creds)

    tables = build_all()
    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)

    for table, rows in tables.items():
        ref = f"{PROJECT}.{DATASET}.{table}"
        job = client.load_table_from_json(rows, ref, job_config=job_config)
        job.result()  # wait
        print(f"  loaded {len(rows):>3} rows -> {ref}")

    total = sum(len(r) for r in tables.values())
    print(f"Done. {total} fixture rows across {len(tables)} tables in {DATASET}.")


if __name__ == "__main__":
    sys.exit(main())