#!/usr/bin/env python3
"""
Backfill the steam_price_producer Lambda for one date or a range of dates.

Usage:
    # Single day
    python scripts/backfill.py --date 2026-06-15

    # Range (inclusive on both ends)
    python scripts/backfill.py --start-date 2026-06-13 --end-date 2026-06-15

    # From a date to yesterday (--end-date defaults to yesterday)
    python scripts/backfill.py --start-date 2026-06-13
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta

import boto3

LAMBDA_NAME = "steam_price_producer"
AWS_REGION = "eu-central-1"


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")


def invoke_lambda_for_date(client, target_date: date) -> bool:
    date_str = target_date.isoformat()
    payload = json.dumps({"date": date_str}).encode()

    print(f"[{date_str}] Invoking Lambda...")
    try:
        response = client.invoke(
            FunctionName=LAMBDA_NAME,
            InvocationType="RequestResponse",
            Payload=payload,
        )
    except Exception as exc:
        print(f"[{date_str}] AWS invoke failed: {exc}")
        return False

    status_code = response["StatusCode"]
    result = json.loads(response["Payload"].read())

    if status_code != 200 or result.get("statusCode") != 200:
        print(f"[{date_str}] Lambda returned error (HTTP {status_code}): {result}")
        return False

    body = json.loads(result["body"])
    print(
        f"[{date_str}] OK — assets={body['assets_written']} "
        f"prices={body['prices_written']} "
        f"exchange_rates={body['exchange_rates_written']}"
    )
    return True


def main():
    parser = argparse.ArgumentParser(description="Backfill steam pipeline for past dates.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", type=parse_date, help="Single date (YYYY-MM-DD)")
    group.add_argument("--start-date", type=parse_date, dest="start_date", help="Range start (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=parse_date, dest="end_date", help="Range end inclusive (YYYY-MM-DD, defaults to yesterday)")
    args = parser.parse_args()

    today = date.today()

    if args.date:
        dates = [args.date]
    else:
        end = args.end_date or (today - timedelta(days=1))
        if args.start_date > end:
            print(f"Error: --start-date {args.start_date} is after --end-date {end}")
            sys.exit(1)
        dates = []
        current = args.start_date
        while current <= end:
            dates.append(current)
            current += timedelta(days=1)

    # Reject future dates
    future = [d for d in dates if d >= today]
    if future:
        print(f"Error: backfill dates must be in the past. Future dates given: {[d.isoformat() for d in future]}")
        sys.exit(1)

    print(f"Backfilling {len(dates)} day(s): {dates[0]} → {dates[-1]}")
    print()

    client = boto3.client("lambda", region_name=AWS_REGION)

    succeeded = []
    failed = []

    for target_date in dates:
        ok = invoke_lambda_for_date(client, target_date)
        (succeeded if ok else failed).append(target_date.isoformat())

    print()
    print(f"Done. Succeeded: {len(succeeded)}, Failed: {len(failed)}")
    if failed:
        print(f"Failed dates: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()