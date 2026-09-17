"""Lineage discovery orchestration - discovers AWS resource lineage for
every Step Function in the account, and persists it to the SAME DynamoDB
table the freshness monitor uses (see lineage_store.py for why that's safe).

NOT a Lambda entrypoint. `run_lineage_discovery()` is called directly by
handler.py's lambda_handler when it's invoked in lineage mode (see the
mode-dispatch branch there) - there is no separate LineageFunction, no
separate IAM role, no separate schedule. One Lambda (MonitorFunction) with
two EventBridge schedules: the existing rate(15 minutes) one (no Input,
normal freshness-monitoring mode) and a new rate(1 day) one that passes
{"mode": "lineage"}. A pipeline's *structure* - which Lambda it calls, what
it writes to - changes far less often than its execution status, so
recomputing lineage every 15 minutes alongside freshness would be pure
waste; the daily schedule is what keeps this cheap, not a separate function.

Reuses handler.py's exact discovery primitives (list_all_state_machines,
describe_state_machine_details, detect_trigger) rather than
re-implementing discovery - this is not a second discovery system, just a
second code path invoked from the same Lambda.

Read-only against AWS. Writes only to its own lineage items in the shared
table - never touches a pipeline's status item.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, Set

from .discovery import describe_state_machine_details, list_all_state_machines
from .lineage_scanner import build_pipeline_graph
from .lineage_store import (
    LINEAGE_SUMMARY_KEY,
    lineage_key_for_pipeline,
    put_lineage_summary,
    put_pipeline_lineage,
)
from .registry import load_excluded_names
from .trigger_scanner import detect_trigger_detail

logger = logging.getLogger("pipeline_freshness_monitor.lineage_handler")

# Same env vars MonitorFunction's own module reads - correct as independent
# reads (not values threaded in as parameters) because this always runs
# inside that same Lambda's environment, whichever mode invoked it.
TABLE_NAME = os.environ.get("STATUS_TABLE_NAME", "")
DISCOVERY_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _iso(dt) -> str:
    return dt.isoformat() if dt is not None else None


def _process_machine(machine: dict, now: datetime) -> dict:
    name, arn = machine["name"], machine["arn"]
    details = describe_state_machine_details(arn, DISCOVERY_REGION)
    definition = details.get("definition") if details else None
    trigger_detail = detect_trigger_detail(arn, DISCOVERY_REGION)

    graph = build_pipeline_graph(name, arn, DISCOVERY_REGION, definition, trigger_detail)

    item = {
        "pipeline_name": lineage_key_for_pipeline(name),
        "source_pipeline_name": name,
        "state_machine_arn": arn,
        "region": DISCOVERY_REGION,
        "nodes": [asdict(n) for n in graph.nodes],
        "edges": [asdict(e) for e in graph.edges],
        "discovered_at": _iso(now),
    }
    return item


def run_lineage_discovery() -> dict:
    """Called by handler.py's lambda_handler in lineage mode - not invoked
    directly by AWS. The `if not TABLE_NAME` guard is defense in depth: the
    caller already checked this before dispatching, but this function stays
    correct and independently callable/testable even if that ever changes.
    """
    if not TABLE_NAME:
        raise RuntimeError("STATUS_TABLE_NAME environment variable is not set")

    now = datetime.now(timezone.utc)
    excluded_names = load_excluded_names()
    machines = list_all_state_machines(DISCOVERY_REGION)

    logger.info("lineage run starting machines=%d", len(machines))

    results = {"total": 0, "succeeded": 0, "failed": 0}
    all_resource_ids: Set[str] = set()
    total_edges = 0

    for machine in machines:
        name = machine["name"]
        if name in excluded_names:
            continue
        results["total"] += 1
        try:
            item = _process_machine(machine, now)
            put_pipeline_lineage(TABLE_NAME, item)
            all_resource_ids.update(n["resource_id"] for n in item["nodes"])
            total_edges += len(item["edges"])
            results["succeeded"] += 1
        except Exception:
            logger.exception("unexpected error building lineage for pipeline=%s", name)
            results["failed"] += 1

    summary = {
        "pipeline_name": LINEAGE_SUMMARY_KEY,
        "total_pipelines_scanned": results["total"],
        "total_resources": len(all_resource_ids),
        "total_relationships": total_edges,
        "last_discovery_at": _iso(now),
    }
    try:
        put_lineage_summary(TABLE_NAME, summary)
    except Exception:
        logger.exception("failed to write lineage summary - per-pipeline items above are unaffected")

    logger.info("lineage run finished %s", results)
    return results
