"""
Fetches CS2 inventory from Steam API and writes item_id + quantity to inventory.csv.
Buy price, buy date, and purchase channel must be filled in manually.

Usage:
    python scripts/fetch_steam_inventory.py
"""

import csv
import time
from pathlib import Path

import requests

STEAM_ID = "76561198362681858"
APP_ID = 730
CONTEXT_ID = 2
OUTPUT = Path(__file__).parent / "inventory.csv"

ALREADY_IN_DYNAMO = {
    "AWP | Printstream (Well-Worn)",
    "Little Kev | The Professionals",
}

def type_to_category(steam_type: str) -> str:
    t = steam_type.lower()
    if "sticker" in t or "graffiti" in t or "patch" in t or "slab" in t:
        return "Sticker"
    if "gloves" in t:
        return "Gloves"
    if "knife" in t:
        return "Knife"
    if "agent" in t:
        return "Agent"
    if "container" in t or "case" in t or "capsule" in t or "package" in t:
        return "Case"
    if "collectible" in t or "music kit" in t or "charm" in t or "key" in t or "pin" in t:
        return "Other"
    return "Skin"


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://steamcommunity.com/",
}


def fetch_inventory(steam_id: str) -> dict:
    base_url = f"https://steamcommunity.com/inventory/{steam_id}/{APP_ID}/{CONTEXT_ID}"
    all_assets: list[dict] = []
    all_descriptions: list[dict] = []
    last_assetid = None

    while True:
        params = {"l": "english", "count": 75}
        if last_assetid:
            params["start_assetid"] = last_assetid

        for attempt in range(5):
            resp = requests.get(base_url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
            break
        resp.raise_for_status()

        data = resp.json()
        if not data.get("success"):
            raise RuntimeError("Steam returned success=false — inventory may be private")

        all_assets.extend(data.get("assets", []))
        all_descriptions.extend(data.get("descriptions", []))

        if data.get("more_items"):
            last_assetid = data.get("last_assetid")
            print(f"  Fetched {len(all_assets)} items so far, loading next page...")
            time.sleep(4)
        else:
            break

    return {"assets": all_assets, "descriptions": all_descriptions}


def parse_items(data: dict) -> list[dict]:
    descriptions = {
        (d["classid"], d["instanceid"]): d
        for d in data.get("descriptions", [])
    }

    aggregated: dict[str, dict] = {}
    for asset in data.get("assets", []):
        key = (asset["classid"], asset["instanceid"])
        desc = descriptions.get(key, {})
        name = desc.get("market_hash_name", "Unknown")
        raw_type = desc.get("type", "")
        category = type_to_category(raw_type)

        if name not in aggregated:
            aggregated[name] = {"item_id": name, "quantity": 0, "category": category}
        aggregated[name]["quantity"] += int(asset.get("amount", 1))

    return sorted(aggregated.values(), key=lambda x: x["item_id"])


def main():
    print(f"Fetching CS2 inventory for Steam ID {STEAM_ID}...")
    data = fetch_inventory(STEAM_ID)
    items = parse_items(data)

    skipped = [i for i in items if i["item_id"] in ALREADY_IN_DYNAMO]
    new_items = [i for i in items if i["item_id"] not in ALREADY_IN_DYNAMO]

    print(f"Found {len(items)} unique items ({len(skipped)} already in DynamoDB, {len(new_items)} new).")
    if skipped:
        print(f"Skipped: {[i['item_id'] for i in skipped]}")

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["item_id", "buy_price", "buy_currency", "buy_date", "quantity", "purchase_channel", "category"],
        )
        writer.writeheader()
        for item in new_items:
            writer.writerow({
                "item_id": item["item_id"],
                "buy_price": "",
                "buy_currency": "PLN",
                "buy_date": "",
                "quantity": item["quantity"],
                "purchase_channel": "",
                "category": item["category"],
            })

    print(f"Written {len(new_items)} items to {OUTPUT}")
    print("Next: fill in buy_price, buy_date, purchase_channel, then run bulk_import.py")


if __name__ == "__main__":
    main()