"""DynamoDB persistence for computed pipeline status.

One item per pipeline, PK=pipeline_name, always fully overwritten on each
monitor run - the freshness engine already guarantees a collector failure
maps to UNKNOWN rather than a false "healthy" reading (see freshness.py), so
this layer doesn't need special-case merge/skip logic to stay honest.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

_table_cache = {}


def _table(table_name: str):
    if table_name not in _table_cache:
        _table_cache[table_name] = boto3.resource("dynamodb").Table(table_name)
    return _table_cache[table_name]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def put_pipeline_status(table_name: str, item: Dict[str, Any]) -> None:
    _table(table_name).put_item(Item=item)


def get_all_statuses(table_name: str) -> List[Dict[str, Any]]:
    # Scan is fine at this scale (a handful of registered pipelines); revisit
    # with a Query/GSI only if the registry grows into the hundreds.
    response = _table(table_name).scan()
    return response.get("Items", [])


def get_pipeline_status(table_name: str, pipeline_name: str) -> Optional[Dict[str, Any]]:
    response = _table(table_name).get_item(Key={"pipeline_name": pipeline_name})
    return response.get("Item")
