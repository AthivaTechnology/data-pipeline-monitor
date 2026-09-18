"""Application Dependency discovery orchestration - the pilot's daily scan,
scoped to exactly the applications listed in config/applications.yaml (one
entry, `data-exporter`, at pilot time - see application_registry.py).

NOT a Lambda entrypoint, same shape as lineage_handler.py:
run_application_discovery() is called directly by handler.py's
lambda_handler when invoked in "applications" mode - no separate Lambda, no
separate role, no separate schedule.

Read-only against AWS. Writes only to its own APP#/APPRESOURCE# items in
the shared table - never touches a pipeline's status item or a pipeline's
own LINEAGE# items.
"""
from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List

from .application_registry import ApplicationConfig, load_applications
from .application_scanner import build_application_graph
from .application_store import (
    application_key_for,
    application_resource_key_for,
    put_application_resource,
    put_application_summary,
)

logger = logging.getLogger("pipeline_freshness_monitor.application_handler")

TABLE_NAME = os.environ.get("STATUS_TABLE_NAME", "")
DISCOVERY_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _iso(dt) -> str:
    return dt.isoformat() if dt is not None else None


def _process_application(app: ApplicationConfig, now: datetime) -> int:
    """Builds and persists one application's resource graph. Returns the
    number of resources written. Raises on unexpected failure - the caller
    (run_application_discovery) is what isolates one application's failure
    from another's, same division of responsibility as lineage_handler.py.
    """
    graph = build_application_graph(app, DISCOVERY_REGION)

    upstream: Dict[str, List[dict]] = defaultdict(list)
    downstream: Dict[str, List[dict]] = defaultdict(list)
    for edge in graph.edges:
        downstream[edge.source_id].append({
            "resource_id": edge.target_id,
            "relationship_type": edge.relationship_type,
            "evidence_type": "direct",
            "relationship_source": edge.relationship_source,
            "discovered_at": _iso(now),
        })
        upstream[edge.target_id].append({
            "resource_id": edge.source_id,
            "relationship_type": edge.relationship_type,
            "evidence_type": "direct",
            "relationship_source": edge.relationship_source,
            "discovered_at": _iso(now),
        })

    for node in graph.nodes:
        item = {
            "pipeline_name": application_resource_key_for(app.id, node.resource_id),
            "application_id": app.id,
            "resource_id": node.resource_id,
            "resource_type": node.resource_type,
            "display_name": node.display_name,
            "region": node.region,
            "upstream": upstream.get(node.resource_id, []),
            "downstream": downstream.get(node.resource_id, []),
            "discovered_at": _iso(now),
        }
        put_application_resource(TABLE_NAME, item)

    resource_type_counts = dict(Counter(n.resource_type for n in graph.nodes))
    summary = {
        "pipeline_name": application_key_for(app.id),
        "application_id": app.id,
        "display_name": app.display_name,
        "stack_name": app.stack_name,
        "resource_type_counts": resource_type_counts,
        "total_resources": len(graph.nodes),
        "total_relationships": len(graph.edges),
        "last_discovered_at": _iso(now),
    }
    put_application_summary(TABLE_NAME, summary)

    return len(graph.nodes)


def run_application_discovery() -> dict:
    """Called by handler.py's lambda_handler in "applications" mode - not
    invoked directly by AWS. Same defense-in-depth TABLE_NAME guard as
    run_lineage_discovery().
    """
    if not TABLE_NAME:
        raise RuntimeError("STATUS_TABLE_NAME environment variable is not set")

    now = datetime.now(timezone.utc)
    applications = load_applications()

    logger.info("application discovery run starting applications=%d", len(applications))

    results = {"total": 0, "succeeded": 0, "failed": 0}
    for app in applications:
        results["total"] += 1
        try:
            resource_count = _process_application(app, now)
            logger.info("application=%s resources=%d", app.id, resource_count)
            results["succeeded"] += 1
        except Exception:
            logger.exception("unexpected error building application graph for application=%s", app.id)
            results["failed"] += 1

    logger.info("application discovery run finished %s", results)
    return results
