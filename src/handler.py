"""Lambda entrypoint: collect -> evaluate -> persist, for every monitored pipeline.

Triggered on a schedule by EventBridge. Every pipeline gets a DynamoDB write
on every run, no matter what happens - including when something goes wrong
in a way collect_pipeline_state/evaluate_data_freshness didn't already turn
into a clean UNKNOWN. Without that guarantee, an unexpected bug would leave
the dashboard silently showing a stale (possibly "healthy") status forever,
which is exactly the failure mode this tool exists to catch elsewhere.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .collector import collect_pipeline_state
from .data_freshness import DataFreshnessStatus, evaluate_data_freshness
from .discovery import get_definition, list_all_state_machines
from .freshness import FreshnessStatus, evaluate_freshness
from .models import ExecutionStatus, PipelineConfig, ScheduleConfig
from .registry import load_excluded_names, load_registry, load_monitored_registry
from .resource_scanner import scan_definition_json
from .s3_checker import get_last_modified
from .status_store import put_pipeline_status
from .trigger_scanner import detect_trigger

# NOTE: logging.basicConfig() is a no-op here - AWS Lambda's Python runtime
# always attaches a root handler before our code runs, and basicConfig()
# silently does nothing when the root logger already has one. Setting the
# level directly is what actually makes INFO logs reach CloudWatch.
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("pipeline_freshness_monitor.handler")

TABLE_NAME = os.environ.get("STATUS_TABLE_NAME", "")
# Discovery scope: every state machine in this single region. Matches every
# registered pipeline today (all us-east-1) - if this account ever spans
# multiple regions, discovery would need to loop over them explicitly rather
# than guess, same principle as everything else in this module.
DISCOVERY_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _decimal(value) -> Optional[Decimal]:
    # DynamoDB's boto3 resource API rejects native float - route every
    # numeric duration through this so put_item never raises on it.
    return Decimal(str(value)) if value is not None else None


def _execution_dict(execution) -> dict:
    return {
        "status": execution.status.value,
        "start_date": _iso(execution.start_date),
        "stop_date": _iso(execution.stop_date),
        "duration_seconds": _decimal(execution.duration_seconds),
        # Only the enriched entry (collector.py's _enrich_with_error_detail,
        # applied to the single latest execution) will ever have these set -
        # other history entries were never describe_execution'd to keep this
        # to one extra AWS call per pipeline, not five.
        "error": execution.error,
        "cause": execution.cause,
    }


def _build_item(pipeline, state, exec_result, data_result, now) -> dict:
    return {
        "pipeline_name": pipeline.name,
        "environment": pipeline.environment,
        "region": pipeline.region,
        "state_machine_arn": pipeline.state_machine_arn,
        "owner": pipeline.owner,
        "contact": pipeline.contact,
        "alerting_enabled": pipeline.alerting_enabled,
        "review_status": pipeline.review_status,
        "schedule_type": pipeline.schedule.type,
        "schedule_cron_utc": pipeline.schedule.cron_utc,
        "schedule_interval_minutes": pipeline.schedule.interval_minutes,
        "grace_period_minutes": pipeline.grace_period_minutes,
        "execution_status": exec_result.status.value,
        "execution_reason": exec_result.reason,
        "last_execution_status": exec_result.last_execution.status.value if exec_result.last_execution else None,
        "last_execution_at": _iso(exec_result.last_execution.start_date) if exec_result.last_execution else None,
        "last_execution_duration_seconds": (
            _decimal(exec_result.last_execution.duration_seconds) if exec_result.last_execution else None
        ),
        "last_successful_execution_at": (
            _iso(exec_result.last_successful_execution.stop_date)
            if exec_result.last_successful_execution
            else None
        ),
        "expected_next_run": _iso(exec_result.expected_next_run),
        "stale_deadline": _iso(exec_result.stale_deadline),
        "recent_executions": [_execution_dict(e) for e in state.recent_executions],
        "data_status": data_result.status.value,
        "data_reason": data_result.reason,
        "data_checked_location": data_result.checked_location,
        "data_last_modified": _iso(data_result.last_modified),
        "last_checked_at": _iso(now),
    }


def _build_error_item(pipeline, error: str, now) -> dict:
    """Used when something breaks in a way not already handled as a clean
    collector_error/UNKNOWN path. Always overwrites last_checked_at so the
    dashboard can never mistake "we haven't looked recently" for "healthy".
    """
    return {
        "pipeline_name": pipeline.name,
        "environment": pipeline.environment,
        "region": pipeline.region,
        "state_machine_arn": pipeline.state_machine_arn,
        "owner": pipeline.owner,
        "contact": pipeline.contact,
        "alerting_enabled": pipeline.alerting_enabled,
        "review_status": pipeline.review_status,
        "schedule_type": pipeline.schedule.type,
        "schedule_cron_utc": pipeline.schedule.cron_utc,
        "schedule_interval_minutes": pipeline.schedule.interval_minutes,
        "grace_period_minutes": pipeline.grace_period_minutes,
        "execution_status": "unknown",
        "execution_reason": f"unexpected monitor error while processing this pipeline: {error}",
        "last_execution_status": None,
        "last_execution_at": None,
        "last_execution_duration_seconds": None,
        "last_successful_execution_at": None,
        "expected_next_run": None,
        "stale_deadline": None,
        "recent_executions": [],
        "data_status": "unknown",
        "data_reason": "not evaluated because the monitor failed before reaching the data check",
        "data_checked_location": None,
        "data_last_modified": None,
        "last_checked_at": _iso(now),
    }


def _build_discovered_item(name: str, arn: str, region: str, now) -> dict:
    """Builds a DynamoDB item for a state machine that has no config/registry.yaml
    entry. Reuses the exact same collect -> evaluate -> _build_item pipeline as
    a registered pipeline (a synthetic PipelineConfig with no known
    schedule/output stands in for the registry entry), then patches on
    discovery-only fields and replaces the two "missing config" messages that
    pipeline produces with ones grounded in what was actually detected via
    resource_scanner/trigger_scanner - never a bare "not configured".
    """
    synthetic = PipelineConfig(
        name=name,
        state_machine_arn=arn,
        region=region,
        # Honest placeholder, not a guess - environment is a registry concept
        # this pipeline has never been given.
        environment="unregistered",
        monitoring_enabled=True,
        alerting_enabled=False,
        owner=None,
        schedule=ScheduleConfig(type="custom", cron_utc=None, interval_minutes=None),
        grace_period_minutes=None,
        output=None,
        review_status="needs_review",
        contact=None,
    )

    state = collect_pipeline_state(synthetic)
    exec_result = evaluate_freshness(state, now)
    data_result = evaluate_data_freshness(synthetic, None, None, now)
    item = _build_item(synthetic, state, exec_result, data_result, now)

    definition = get_definition(arn, region)
    resources = scan_definition_json(definition) if definition else []
    trigger_label = detect_trigger(arn, region)

    item["source"] = "discovered"
    item["discovery_reason"] = "Auto-discovered via states:ListStateMachines; not present in config/registry.yaml"
    item["detected_trigger"] = trigger_label
    item["detected_resources"] = resources

    # Refine the one UNKNOWN case evaluate_freshness can reach for this
    # synthetic config (schedule.type="custom" with no interval, after a
    # SUCCEEDED execution) into a message tied to what trigger_scanner found,
    # instead of the generic "schedule configuration is incomplete" wording
    # meant for a mis-set registry entry.
    if item["execution_status"] == "unknown" and item["last_execution_status"] == ExecutionStatus.SUCCEEDED.value:
        item["execution_reason"] = (
            f"No schedule is registered for this auto-discovered pipeline ({trigger_label}). "
            "Add it to config/registry.yaml with a schedule to enable freshness tracking."
        )

    if item["data_status"] == DataFreshnessStatus.NOT_CONFIGURED.value:
        if resources:
            item["data_status"] = DataFreshnessStatus.SOURCE_DETECTED_UNAVAILABLE.value
            item["data_reason"] = (
                f"Possible output resource(s) referenced in the definition ({', '.join(resources)}), "
                "but none could be automatically resolved to a specific, checkable location. "
                "Add an output location to config/registry.yaml to enable freshness tracking."
            )
        else:
            item["data_status"] = DataFreshnessStatus.SOURCE_NOT_DETECTED.value
            item["data_reason"] = (
                "No S3, Lambda, DynamoDB, Athena, Glue, Redshift, or RDS resource references were found "
                "in this state machine's definition. Freshness cannot be tracked without a registered output location."
            )

    return item


def _run_discovery_phase(now) -> dict:
    """Finds every state machine not already covered by config/registry.yaml
    and writes a lightweight status item for each. Runs after the registry
    loop and is fully independent of it: a failure here never affects the 10
    registry-driven pipelines, and a failure on one discovered pipeline never
    stops the others (same per-item try/except pattern as the registry loop).
    """
    results = {"discovered": 0, "failed": 0}

    known_arns = {p.state_machine_arn for p in load_registry()}
    excluded_names = load_excluded_names()

    machines = list_all_state_machines(DISCOVERY_REGION)
    logger.info("discovery phase found %d state machines total in %s", len(machines), DISCOVERY_REGION)

    for machine in machines:
        name = machine["name"]
        arn = machine["arn"]
        if arn in known_arns or name in excluded_names:
            continue
        try:
            item = _build_discovered_item(name, arn, DISCOVERY_REGION, now)
            put_pipeline_status(TABLE_NAME, item)
            logger.info(
                "discovered pipeline=%s execution_status=%s data_status=%s",
                name,
                item["execution_status"],
                item["data_status"],
            )
            results["discovered"] += 1
        except Exception:
            logger.exception("unexpected error processing discovered pipeline=%s", name)
            results["failed"] += 1

    logger.info("discovery phase finished %s", results)
    return results


def lambda_handler(event, context):
    if not TABLE_NAME:
        raise RuntimeError("STATUS_TABLE_NAME environment variable is not set")

    now = datetime.now(timezone.utc)
    pipelines = load_monitored_registry()
    logger.info("monitor run starting pipelines=%d", len(pipelines))

    results = {"succeeded": 0, "failed": 0}

    for pipeline in pipelines:
        try:
            state = collect_pipeline_state(pipeline)
            exec_result = evaluate_freshness(state, now)

            if pipeline.output is not None:
                last_modified, fetch_error = get_last_modified(pipeline.output, pipeline.region)
                data_result = evaluate_data_freshness(pipeline, last_modified, fetch_error, now)
            else:
                data_result = evaluate_data_freshness(pipeline, None, None, now)

            item = _build_item(pipeline, state, exec_result, data_result, now)
            put_pipeline_status(TABLE_NAME, item)

            logger.info(
                "pipeline=%s execution_status=%s data_status=%s",
                pipeline.name,
                exec_result.status.value,
                data_result.status.value,
            )
            results["succeeded"] += 1
        except Exception as exc:
            logger.exception("unexpected error processing pipeline=%s", pipeline.name)
            try:
                put_pipeline_status(TABLE_NAME, _build_error_item(pipeline, str(exc), now))
            except Exception:
                logger.exception(
                    "also failed to write error status for pipeline=%s - dashboard will show stale data for it",
                    pipeline.name,
                )
            results["failed"] += 1

    logger.info("monitor run finished (registry) %s", results)

    try:
        results["discovery"] = _run_discovery_phase(now)
    except Exception:
        logger.exception("discovery phase failed entirely - registry-driven pipelines above are unaffected")
        results["discovery"] = {"discovered": 0, "failed": 0, "error": "discovery phase failed"}

    return results
