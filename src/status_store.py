"""DynamoDB persistence for computed pipeline status.

One item per pipeline, PK=pipeline_name, always fully overwritten on each
monitor run - the freshness engine already guarantees a collector failure
maps to UNKNOWN rather than a false "healthy" reading (see freshness.py), so
this layer doesn't need special-case merge/skip logic to stay honest.

This table also holds lineage items (see lineage_store.py), which reuse the
same pipeline_name attribute with a "LINEAGE#..." prefix that can never
collide with a real pipeline name. get_all_statuses() below filters those
out explicitly - without that filter, a lineage item would show up in the
Pipeline Monitor's /status response as if it were a broken pipeline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

_LINEAGE_PREFIX = "LINEAGE#"

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
    # excludes lineage items sharing this table - see module docstring.
    response = _table(table_name).scan(
        FilterExpression="NOT begins_with(pipeline_name, :prefix)",
        ExpressionAttributeValues={":prefix": _LINEAGE_PREFIX},
    )
    return response.get("Items", [])


def get_pipeline_status(table_name: str, pipeline_name: str) -> Optional[Dict[str, Any]]:
    # Same boundary get_all_statuses() enforces for the list endpoint - a
    # caller passing a LINEAGE#-prefixed value (e.g. straight from a URL
    # path parameter) must get "not found", never a lineage item disguised
    # as a pipeline status. No real pipeline_name can start with this
    # prefix (AWS forbids "#" in state machine names), so this can never
    # reject a legitimate lookup.
    if pipeline_name.startswith(_LINEAGE_PREFIX):
        return None
    response = _table(table_name).get_item(Key={"pipeline_name": pipeline_name})
    return response.get("Item")
