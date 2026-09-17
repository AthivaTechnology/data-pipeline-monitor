"""Read-only HTTP API Lambda for the dashboard.

GET /status              -> summary counts + all pipeline items
GET /status/{name}       -> one pipeline's full detail
GET /lineage             -> lineage discovery summary (counts, last run)
GET /lineage/{name}      -> one pipeline's lineage graph (nodes + edges)
GET /resources           -> deduped catalog of every discovered AWS resource
GET /resources/{arn+}    -> one resource's detail + upstream/downstream

This Lambda never touches AWS APIs directly - it only reads what the monitor
Lambda and the lineage Lambda already computed and stored in DynamoDB (the
same table for both - see lineage_store.py), keeping the dashboard fast and
decoupled from Step Functions/S3 throttling.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from .lineage_store import get_all_pipeline_lineage, get_lineage_summary, get_pipeline_lineage
from .status_store import get_all_statuses, get_pipeline_status

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("pipeline_freshness_monitor.api_handler")

TABLE_NAME = os.environ.get("STATUS_TABLE_NAME", "")

_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

_SUMMARY_KEYS = ["fresh", "delayed", "failed", "stale", "running", "never_run", "unknown"]


def _response(status_code: int, body: dict):
    # Every response - success or error - goes through this, so CORS headers
    # are always present. A response that skips this (e.g. an exception
    # escaping lambda_handler entirely) would come back to the browser with
    # no CORS header at all, which shows up as a misleading "CORS error"
    # instead of the real failure - that's exactly what the try/except below
    # prevents.
    return {"statusCode": status_code, "headers": _HEADERS, "body": json.dumps(body, default=str)}


def _summarize(items):
    counts = {key: 0 for key in _SUMMARY_KEYS}
    for item in items:
        status = item.get("execution_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _status_list() -> dict:
    items = get_all_statuses(TABLE_NAME)
    return _response(200, {
        "total_pipelines": len(items),
        "summary": _summarize(items),
        "pipelines": items,
    })


def _status_detail(name: str) -> dict:
    item = get_pipeline_status(TABLE_NAME, name)
    if item is None:
        return _response(404, {"error": f"pipeline {name!r} not found"})
    return _response(200, item)


def _lineage_summary() -> dict:
    summary = get_lineage_summary(TABLE_NAME)
    if summary is None:
        return _response(200, {
            "discovery_has_run": False,
            "total_pipelines_scanned": 0,
            "total_resources": 0,
            "total_relationships": 0,
            "last_discovery_at": None,
        })
    body = {k: v for k, v in summary.items() if k != "pipeline_name"}
    body["discovery_has_run"] = True
    return _response(200, body)


def _lineage_detail(name: str) -> dict:
    item = get_pipeline_lineage(TABLE_NAME, name)
    if item is None:
        return _response(404, {"error": f"no lineage data yet for pipeline {name!r}"})
    body = {k: v for k, v in item.items() if k != "pipeline_name"}
    body["pipeline_name"] = item.get("source_pipeline_name", name)
    return _response(200, body)


def _resources_list() -> dict:
    items = get_all_pipeline_lineage(TABLE_NAME)
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        pipeline_name = item.get("source_pipeline_name", "")
        for node in item.get("nodes", []):
            rid = node["resource_id"]
            entry = by_id.setdefault(rid, {**node, "pipelines": []})
            if pipeline_name and pipeline_name not in entry["pipelines"]:
                entry["pipelines"].append(pipeline_name)
    resources = sorted(by_id.values(), key=lambda r: (r["resource_type"], r["display_name"]))
    return _response(200, {"total": len(resources), "resources": resources})


def _resource_detail(resource_id: str) -> dict:
    items = get_all_pipeline_lineage(TABLE_NAME)
    node = None
    pipelines: List[str] = []
    upstream: List[dict] = []
    downstream: List[dict] = []

    for item in items:
        pipeline_name = item.get("source_pipeline_name", "")
        nodes_by_id = {n["resource_id"]: n for n in item.get("nodes", [])}
        if resource_id not in nodes_by_id:
            continue
        if node is None:
            node = nodes_by_id[resource_id]
        if pipeline_name and pipeline_name not in pipelines:
            pipelines.append(pipeline_name)

        for edge in item.get("edges", []):
            if edge["target_id"] == resource_id:
                src = nodes_by_id.get(edge["source_id"])
                upstream.append({
                    "resource_id": edge["source_id"],
                    "display_name": src["display_name"] if src else edge["source_id"],
                    "resource_type": src["resource_type"] if src else None,
                    "relationship_source": edge["relationship_source"],
                    "confidence": edge["confidence"],
                })
            if edge["source_id"] == resource_id:
                tgt = nodes_by_id.get(edge["target_id"])
                downstream.append({
                    "resource_id": edge["target_id"],
                    "display_name": tgt["display_name"] if tgt else edge["target_id"],
                    "resource_type": tgt["resource_type"] if tgt else None,
                    "relationship_source": edge["relationship_source"],
                    "confidence": edge["confidence"],
                })

    if node is None:
        return _response(404, {"error": f"resource {resource_id!r} not found"})

    return _response(200, {**node, "pipelines": pipelines, "upstream": upstream, "downstream": downstream})


def _handle(event) -> dict:
    route_key = event.get("routeKey", "")
    path_params = event.get("pathParameters") or {}

    if route_key == "GET /status/{name}" or (not route_key and path_params.get("name")):
        return _status_detail(path_params["name"])
    if route_key == "GET /status" or not route_key:
        return _status_list()
    if route_key == "GET /lineage/{name}":
        return _lineage_detail(path_params["name"])
    if route_key == "GET /lineage":
        return _lineage_summary()
    if route_key == "GET /resources/{arn+}":
        return _resource_detail(path_params["arn"])
    if route_key == "GET /resources":
        return _resources_list()

    return _response(404, {"error": f"no route for {route_key!r}"})


def lambda_handler(event, context):
    if not TABLE_NAME:
        return _response(500, {"error": "server misconfigured: STATUS_TABLE_NAME is not set"})

    try:
        return _handle(event)
    except Exception as exc:
        logger.exception("unexpected error handling request")
        return _response(500, {"error": "internal server error", "message": str(exc)})
