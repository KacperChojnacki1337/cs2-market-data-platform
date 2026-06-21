"""
Bulk imports buy events from inventory.csv into DynamoDB.

Usage:
    python scripts/bulk_import.py           # dry run (preview only)
    python scripts/bulk_import.py --import  # actual insert
"""

import argparse
import csv
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import boto3

CSV_PATH = Path(__file__).parent / "inventory.csv"
TABLE_NAME = "steam_inventory_metadata"
REGION = "eu-central-1"

REQUIRED_FIELDS = ["item_id", "buy_price", "buy_currency", "buy_date", "quantity", "purchase_channel", "category"]


def validate_row(row: dict, line: int) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in row or row[field].strip() == "":
            errors.append(f"line {line}: missing '{field}'")

    if row.get("buy_date"):
        try:
            datetime.strptime(row["buy_date"].strip(), "%Y-%m-%d")
        except ValueError:
            errors.append(f"line {line}: buy_date '{row['buy_date']}' is not YYYY-MM-DD")

    if row.get("buy_price") is not None:
        try:
            Decimal(row["buy_price"].strip())
        except InvalidOperation:
            errors.append(f"line {line}: buy_price '{row['buy_price']}' is not a number")

    if row.get("quantity"):
        try:
            q = int(row["quantity"].strip())
            if q < 1:
                errors.append(f"line {line}: quantity must be >= 1")
        except ValueError:
            errors.append(f"line {line}: quantity '{row['quantity']}' is not an integer")

    return errors


def build_item(row: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "asset_id": str(uuid.uuid4()),
        "item_id": row["item_id"].strip(),
        "event_type": "buy",
        "buy_price": Decimal(row["buy_price"].strip()),
        "buy_currency": row["buy_currency"].strip().upper(),
        "buy_date": row["buy_date"].strip(),
        "quantity": int(row["quantity"].strip()),
        "purchase_channel": row["purchase_channel"].strip(),
        "category": row["category"].strip(),
        "updated_at": now,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--import", dest="do_import", action="store_true", help="Actually write to DynamoDB")
    args = parser.parse_args()

    rows = []
    all_errors = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            errors = validate_row(row, i)
            if errors:
                all_errors.extend(errors)
            else:
                rows.append(row)

    if all_errors:
        print("Validation errors — fix before importing:\n")
        for e in all_errors:
            print(f"  {e}")
        sys.exit(1)

    print(f"Validated {len(rows)} rows OK.\n")

    total_cost = sum(Decimal(r["buy_price"]) * int(r["quantity"]) for r in rows)
    by_category = {}
    for r in rows:
        cat = r["category"]
        by_category[cat] = by_category.get(cat, 0) + 1
    by_channel = {}
    for r in rows:
        ch = r["purchase_channel"]
        by_channel[ch] = by_channel.get(ch, 0) + 1

    print("Summary:")
    print(f"  Items:       {len(rows)}")
    print(f"  Total cost:  {total_cost} PLN")
    print(f"  Categories:  {by_category}")
    print(f"  Channels:    {by_channel}")

    if not args.do_import:
        print("\nDRY RUN — no data written. Run with --import to insert.")
        return

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)

    inserted = 0
    failed = 0
    with table.batch_writer() as batch:
        for row in rows:
            try:
                batch.put_item(Item=build_item(row))
                inserted += 1
            except Exception as e:
                print(f"  ERROR inserting {row['item_id']}: {e}")
                failed += 1

    print(f"\nDone. Inserted: {inserted} | Failed: {failed}")
    if failed == 0:
        print("All items imported successfully.")


if __name__ == "__main__":
    main()