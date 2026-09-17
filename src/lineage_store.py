"""DynamoDB persistence for pipeline lineage - reuses the SAME table as
status_store.py (pipeline-freshness-status), not a second table.

The table has only a partition key (`pipeline_name`, no sort key - see
template.yaml). Lineage items reuse that same attribute with a namespaced
value instead of a real pipeline name:

  pipeline_name = "LINEAGE#PIPELINE#<name>"  -> that pipeline's full graph
  pipeline_name = "LINEAGE#SUMMARY"          -> one rolling aggregate item

This is safe because AWS Step Functions state machine names can never
contain "#" (only letters, digits, "-", "_" are legal), so a namespaced
value can never collide with a real pipeline_name. No schema change to the
table, no new GSI, no second table - see status_store.py's get_all_statuses
for the matching filter that keeps these items out of the Pipeline
Monitor's existing /status response.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

LINEAGE_PIPELINE_PREFIX = "LINEAGE#PIPELINE#"
LINEAGE_SUMMARY_KEY = "LINEAGE#SUMMARY"

_table_cache = {}


def _table(table_name: str):
    if table_name not in _table_cache:
        _table_cache[table_name] = boto3.resource("dynamodb").Table(table_name)
    return _table_cache[table_name]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def put_pipeline_lineage(table_name: str, item: Dict[str, Any]) -> None:
    """`item` must already have pipeline_name set to
    lineage_key_for_pipeline(name) - callers build the full item (see
    lineage_handler.py) so this stays a thin, single-purpose write, same
    shape as status_store.put_pipeline_status.
    """
    _table(table_name).put_item(Item=item)


def get_pipeline_lineage(table_name: str, pipeline_name: str) -> Optional[Dict[str, Any]]:
    key = lineage_key_for_pipeline(pipeline_name)
    response = _table(table_name).get_item(Key={"pipeline_name": key})
    return response.get("Item")


def get_all_pipeline_lineage(table_name: str) -> List[Dict[str, Any]]:
    """Every pipeline's lineage item. A Scan is unavoidable here (DynamoDB
    can't Query "every key starting with X" without a sort key or a GSI -
    neither exists on this table, see the module docstring), but it's the
    exact same access pattern status_store.get_all_statuses() already uses
    for the same table, at the same small scale - not a new cost profile.
    """
    response = _table(table_name).scan(
        FilterExpression="begins_with(pipeline_name, :prefix)",
        ExpressionAttributeValues={":prefix": LINEAGE_PIPELINE_PREFIX},
    )
    return response.get("Items", [])


def put_lineage_summary(table_name: str, item: Dict[str, Any]) -> None:
    _table(table_name).put_item(Item=item)


def get_lineage_summary(table_name: str) -> Optional[Dict[str, Any]]:
    response = _table(table_name).get_item(Key={"pipeline_name": LINEAGE_SUMMARY_KEY})
    return response.get("Item")


def lineage_key_for_pipeline(pipeline_name: str) -> str:
    return f"{LINEAGE_PIPELINE_PREFIX}{pipeline_name}"
