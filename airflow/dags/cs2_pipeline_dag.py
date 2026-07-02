"""
cs2_daily_pipeline DAG

Replaces EventBridge (Lambda scheduling) and GitHub Actions dbt cron.
Keeps GH Actions only for PR/push CI (dbt run on dbt/** changes).

Task flow:
  [sync_inventory, fetch_exchange_rate]   ← parallel, single-run tasks (no fan-out race)
                    ↓
  fetch_prices_batch (20× dynamic mapping, batch_size=5, fresh AWS IP per invocation)
                    ↓
  retry_missing     ← queries BQ for items without a valid price today, fetches only those
                    ↓
  run_dbt           ← dbt deps + seed + run --target prod + test (replaces GH Actions cron)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import boto3
from airflow.decorators import dag, task
from airflow.operators.bash import BashOperator

_AWS_REGION       = os.environ.get("LAMBDA_REGION", "eu-central-1")
_LAMBDA_NAME      = os.environ.get("LAMBDA_FUNCTION_NAME", "steam_price_producer")
_DBT_PROJECT_DIR  = os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/dbt/steam_tracker")
_DBT_PROFILES_DIR = os.environ.get("DBT_PROFILES_DIR", "/home/airflow/.dbt")

_DEFAULT_ARGS = {
    "owner": "cs2-pipeline",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}


def _invoke_lambda(payload: dict) -> dict:
    """Invoke the producer Lambda synchronously and raise on any failure."""
    client = boto3.client("lambda", region_name=_AWS_REGION)
    response = client.invoke(
        FunctionName=_LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )
    if response["StatusCode"] != 200:
        raise RuntimeError(f"Lambda HTTP error: StatusCode={response['StatusCode']} payload={payload}")
    body = json.loads(response["Payload"].read())
    if body.get("statusCode", 200) != 200:
        raise RuntimeError(f"Lambda returned error: {body} | original payload={payload}")
    return json.loads(body["body"])


@dag(
    dag_id="cs2_daily_pipeline",
    description="CS2 price collection → dbt (replaces EventBridge + GH Actions cron)",
    schedule="0 7 * * *",
    start_date=datetime(2026, 7, 1),
    catchup=False,
    max_active_runs=1,
    default_args=_DEFAULT_ARGS,
    tags=["cs2", "steam", "bigquery"],
)
def cs2_daily_pipeline():

    @task(task_id="sync_inventory")
    def sync_inventory(run_date: str) -> dict:
        """
        Write new DynamoDB buy/sell events to BigQuery — runs exactly once.

        Replaces the assets/sales insert that previously ran in all 20 parallel
        EventBridge Lambdas, causing race-condition duplicates in BigQuery.
        """
        return _invoke_lambda({"mode": "sync_inventory", "date": run_date})

    @task(task_id="fetch_exchange_rate")
    def fetch_exchange_rate(run_date: str) -> dict:
        """
        Fetch USD/PLN and EUR/PLN from NBP, write to BigQuery — runs exactly once.

        Replaces the exchange-rate fetch that previously ran in all 20 parallel
        EventBridge Lambdas, causing 2–4 duplicate rows per day in exchange_rates.
        """
        return _invoke_lambda({"mode": "exchange_rates", "date": run_date})

    @task(task_id="fetch_prices_batch")
    def fetch_prices_batch(batch_index: int, run_date: str) -> dict:
        """
        Fetch Steam prices for one batch slice of the owned inventory.

        Dynamic task mapping creates 20 parallel instances (batch_index 0–19,
        batch_size 5). Each Lambda invocation runs on a fresh AWS IP, bypassing
        Steam's per-IP rate limit. Daily date-seed shuffles item→IP assignment.
        """
        return _invoke_lambda({
            "mode":        "batch_prices",
            "batch_index": batch_index,
            "batch_size":  5,
            "date":        run_date,
        })

    @task(task_id="retry_missing")
    def retry_missing(run_date: str) -> dict:
        """
        Fetch prices for items missed in the main fan-out (Steam rate-limited).

        Queries BigQuery for item_ids without a valid (unflagged) price today,
        then fetches only those on a single fresh AWS IP.
        """
        return _invoke_lambda({"retry_missing": True, "date": run_date})

    run_date = "{{ ds }}"

    inv_task = sync_inventory(run_date)
    xr_task  = fetch_exchange_rate(run_date)

    # 20 parallel Lambda invocations via dynamic task mapping (Airflow 2.3+).
    # Each task instance appears separately in the Airflow UI with its own logs.
    batch_tasks = fetch_prices_batch.partial(run_date=run_date).expand(
        batch_index=list(range(20))
    )

    retry_task = retry_missing(run_date)

    dbt_task = BashOperator(
        task_id="run_dbt",
        bash_command=(
            f"cd {_DBT_PROJECT_DIR}"
            f" && dbt deps --profiles-dir {_DBT_PROFILES_DIR}"
            f" && dbt seed --target prod --profiles-dir {_DBT_PROFILES_DIR}"
            f" && dbt run --target prod --profiles-dir {_DBT_PROFILES_DIR}"
            f" && dbt test --target prod --profiles-dir {_DBT_PROFILES_DIR}"
        ),
        execution_timeout=timedelta(minutes=30),
    )

    # sync_inventory and fetch_exchange_rate run in parallel (no data dependency).
    # Both must succeed before any of the 20 price batches start.
    [inv_task, xr_task] >> batch_tasks >> retry_task >> dbt_task


cs2_daily_pipeline()