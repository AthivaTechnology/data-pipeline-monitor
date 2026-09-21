"""DynamoDB persistence for computed pipeline status.

One item per pipeline, PK=pipeline_name, always fully overwritten on each
monitor run - the freshness engine already guarantees a collector failure
maps to UNKNOWN rather than a false "healthy" reading (see freshness.py), so
this layer doesn't need special-case merge/skip logic to stay honest.

This table also holds lineage items (see lineage_store.py) and Application
Dependency items (see application_store.py), which reuse the same
pipeline_name attribute with a "LINEAGE#...", "APP#...", or "APPRESOURCE#..."
prefix that can never collide with a real pipeline name. get_all_statuses()
below filters all three out explicitly - without that filter, one of these
items would show up in the Pipeline Monitor's /status response as if it
were a broken pipeline (blank owner, UNKNOWN health, no execution history -
exactly the "not useful" symptom this filter exists to prevent).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

_LINEAGE_PREFIX = "LINEAGE#"
_APPLICATION_PREFIX = "APP#"
_APPLICATION_RESOURCE_PREFIX = "APPRESOURCE#"
# APPRESOURCE# is checked separately from APP# - "APPRESOURCE#..." does NOT
# begin with the literal string "APP#" (its 4th character is "R", not "#"),
# so both prefixes must be excluded explicitly, not just the shorter one.
_NON_PIPELINE_PREFIXES = (_LINEAGE_PREFIX, _APPLICATION_PREFIX, _APPLICATION_RESOURCE_PREFIX)

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
    # Scan is fine at this scale (a few dozen pipelines); revisit with a
    # Query/GSI only if discovery grows into the hundreds. The filter
    # excludes lineage and application-dependency items sharing this table -
    # see module docstring.
    response = _table(table_name).scan(
        FilterExpression=" AND ".join(f"NOT begins_with(pipeline_name, :p{i})" for i in range(len(_NON_PIPELINE_PREFIXES))),
        ExpressionAttributeValues={f":p{i}": prefix for i, prefix in enumerate(_NON_PIPELINE_PREFIXES)},
    )
    return response.get("Items", [])


def get_pipeline_status(table_name: str, pipeline_name: str) -> Optional[Dict[str, Any]]:
    # Same boundary get_all_statuses() enforces for the list endpoint - a
    # caller passing a LINEAGE#/APP#/APPRESOURCE#-prefixed value (e.g.
    # straight from a URL path parameter) must get "not found", never one of
    # those items disguised as a pipeline status. No real pipeline_name can
    # start with any of these prefixes (AWS forbids "#" in state machine
    # names), so this can never reject a legitimate lookup.
    if pipeline_name.startswith(_NON_PIPELINE_PREFIXES):
        return None
    response = _table(table_name).get_item(Key={"pipeline_name": pipeline_name})
    return response.get("Item")
