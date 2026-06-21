"""
Fills empty buy_price cells in inventory.csv with current Steam market price (converted to PLN).
Skips rows that already have a buy_price.
Non-marketable items (medals, coins, etc.) are left with buy_price=0.

Usage:
    python scripts/enrich_prices.py
"""

import csv
import time
import urllib.parse
from pathlib import Path

import requests

CSV_PATH = Path(__file__).parent / "inventory.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://steamcommunity.com/market/",
}


def get_nbp_rate() -> float:
    for url in [
        "https://api.nbp.pl/api/exchangerates/rates/a/usd/today/?format=json",
        "https://api.nbp.pl/api/exchangerates/rates/a/usd/last/1/?format=json",
    ]:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()["rates"][0]["mid"]
    raise RuntimeError("Could not fetch NBP rate")


def get_steam_price(item_name: str) -> float | None:
    encoded = urllib.parse.quote(item_name)
    url = (
        f"https://steamcommunity.com/market/priceoverview/"
        f"?appid=730&currency=1&market_hash_name={encoded}"
    )
    for attempt in range(3):
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"    Rate limited. Waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("success"):
            return None
        raw = data.get("lowest_price") or data.get("median_price", "")
        raw = raw.replace("$", "").replace(",", "").strip()
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def safe_print(text: str) -> None:
    print(text.encode("ascii", errors="replace").decode("ascii"))


def main():
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    to_enrich = [r for r in rows if not r["buy_price"].strip()]
    if not to_enrich:
        print("All rows already have buy_price. Nothing to do.")
        return

    print("Fetching NBP USD/PLN rate...")
    usd_pln = get_nbp_rate()
    print(f"  1 USD = {usd_pln} PLN")
    print(f"Enriching {len(to_enrich)} rows...\n")

    filled = 0
    not_found = 0
    for i, row in enumerate(rows):
        if row["buy_price"].strip():
            continue

        name = row["item_id"]
        price_usd = get_steam_price(name)
        time.sleep(1.5)

        if price_usd is None:
            safe_print(f"  [no price] {name} -- setting 0")
            row["buy_price"] = "0"
            row["buy_currency"] = "PLN"
            not_found += 1
        else:
            price_pln = round(price_usd * usd_pln, 2)
            safe_print(f"  {name}: ${price_usd} -> {price_pln} PLN")
            row["buy_price"] = str(price_pln)
            row["buy_currency"] = "PLN"
            filled += 1

        # Write after every row so progress is never lost on crash
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    print(f"\nDone. Filled: {filled} | No market price (set to 0): {not_found}")
    print(f"Review {CSV_PATH} and correct any prices before running bulk_import.py")


if __name__ == "__main__":
    main()