"""Read-only HTTP API Lambda for the dashboard.

GET /status         -> summary counts + all pipeline items
GET /status/{name}   -> one pipeline's full detail

This Lambda never touches AWS APIs directly - it only reads what the monitor
Lambda already computed and stored in DynamoDB, keeping the dashboard fast
and decoupled from Step Functions/S3 throttling.
"""
from __future__ import annotations

import json
import logging
import os

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


def _handle(event):
    path_params = event.get("pathParameters") or {}
    pipeline_name = path_params.get("name")

    if pipeline_name:
        item = get_pipeline_status(TABLE_NAME, pipeline_name)
        if item is None:
            return _response(404, {"error": f"pipeline {pipeline_name!r} not found"})
        return _response(200, item)

    items = get_all_statuses(TABLE_NAME)
    return _response(
        200,
        {
            "total_pipelines": len(items),
            "summary": _summarize(items),
            "pipelines": items,
        },
    )


def lambda_handler(event, context):
    if not TABLE_NAME:
        return _response(500, {"error": "server misconfigured: STATUS_TABLE_NAME is not set"})

    try:
        return _handle(event)
    except Exception as exc:
        logger.exception("unexpected error handling request")
        return _response(500, {"error": "internal server error", "message": str(exc)})
