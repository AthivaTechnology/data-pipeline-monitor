"""DynamoDB persistence for the Application Dependency pilot - reuses the
SAME table as status_store.py and lineage_store.py, not a new table.

Mirrors lineage_store.py's namespacing approach with two more prefixes:

  pipeline_name = "APP#<application_id>"                     -> one summary item
  pipeline_name = "APPRESOURCE#<application_id>#<resource_id>" -> one resource item

Safe for the same reason lineage_store.py's prefixes are: a real
pipeline_name (a Step Functions state machine name) can never contain "#",
so these namespaced values can never collide with one. No schema change,
no new GSI, no second table.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import boto3

APPLICATION_PREFIX = "APP#"
APPLICATION_RESOURCE_PREFIX = "APPRESOURCE#"

_table_cache = {}


def _table(table_name: str):
    if table_name not in _table_cache:
        _table_cache[table_name] = boto3.resource("dynamodb").Table(table_name)
    return _table_cache[table_name]


def put_application_summary(table_name: str, item: Dict[str, Any]) -> None:
    """`item` must already have pipeline_name set to
    application_key_for(application_id) - callers build the full item (see
    application_handler.py).
    """
    _table(table_name).put_item(Item=item)


def get_application_summary(table_name: str, application_id: str) -> Optional[Dict[str, Any]]:
    key = application_key_for(application_id)
    response = _table(table_name).get_item(Key={"pipeline_name": key})
    return response.get("Item")


def put_application_resource(table_name: str, item: Dict[str, Any]) -> None:
    """`item` must already have pipeline_name set to
    application_resource_key_for(application_id, resource_id).
    """
    _table(table_name).put_item(Item=item)


def get_application_resource(table_name: str, application_id: str, resource_id: str) -> Optional[Dict[str, Any]]:
    key = application_resource_key_for(application_id, resource_id)
    response = _table(table_name).get_item(Key={"pipeline_name": key})
    return response.get("Item")


def get_all_application_resources(table_name: str, application_id: str) -> List[Dict[str, Any]]:
    """Every resource item for one application. Same Scan-with-begins_with
    pattern as lineage_store.get_all_pipeline_lineage, at the same small
    scale (~35 items for this pilot's one application).
    """
    prefix = f"{APPLICATION_RESOURCE_PREFIX}{application_id}#"
    response = _table(table_name).scan(
        FilterExpression="begins_with(pipeline_name, :prefix)",
        ExpressionAttributeValues={":prefix": prefix},
    )
    return response.get("Items", [])


def application_key_for(application_id: str) -> str:
    return f"{APPLICATION_PREFIX}{application_id}"


def application_resource_key_for(application_id: str, resource_id: str) -> str:
    return f"{APPLICATION_RESOURCE_PREFIX}{application_id}#{resource_id}"
