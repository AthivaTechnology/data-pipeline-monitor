"""Lambda entrypoint: discover every Step Function in the account, then
collect -> evaluate -> persist for each one, in a single unified pass.

Account-wide discovery (list_all_state_machines) is the single source of
truth for *which* pipelines exist and get processed - never registry.yaml.
registry.yaml is consulted per-ARN purely as optional metadata (a trusted
schedule, an output location to check, owner/alerting, or an exclusion) -
see registry.py. Iterating the AWS-side listing exactly once, with a single
lookup into that metadata, is what guarantees every state machine is
processed exactly once: there is no second loop that could double-process
or disagree with the first.

Every pipeline gets a DynamoDB write on every run, no matter what happens -
including when something goes wrong in a way collect_pipeline_state/
evaluate_data_freshness didn't already turn into a clean UNKNOWN. Without
that guarantee, an unexpected bug would leave the dashboard silently
showing a stale (possibly "healthy") status forever, which is exactly the
failure mode this tool exists to catch elsewhere.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional

from .collector import collect_pipeline_state
from .data_freshness import DataFreshnessStatus, evaluate_data_freshness
from .discovery import describe_state_machine_details, list_all_state_machines
from .freshness import evaluate_freshness
from .models import ExecutionStatus, PipelineConfig, ScheduleConfig
from .registry import load_excluded_names, load_registry
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


def _synthetic_pipeline(name: str, arn: str, region: str) -> PipelineConfig:
    """Stand-in PipelineConfig for a state machine with no enabled
    registry.yaml entry. Every field here is an honest placeholder, never a
    guess: no schedule/output/owner is invented, so evaluate_freshness/
    evaluate_data_freshness correctly report UNKNOWN/NOT_CONFIGURED instead
    of a fabricated status.
    """
    return PipelineConfig(
        name=name,
        state_machine_arn=arn,
        region=region,
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


def _build_item(pipeline, source, state, exec_result, data_result, now) -> dict:
    return {
        "pipeline_name": pipeline.name,
        "environment": pipeline.environment,
        "region": pipeline.region,
        "state_machine_arn": pipeline.state_machine_arn,
        "owner": pipeline.owner,
        "contact": pipeline.contact,
        "alerting_enabled": pipeline.alerting_enabled,
        "review_status": pipeline.review_status,
        # "registry" (an enabled config/registry.yaml entry matched this
        # ARN) or "discovered" (no such entry) - the two branches _process_
        # machine can take, always one or the other, never both/neither.
        "source": source,
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
        # Populated below for every pipeline from the account-wide listing
        # (created_at) and, for discovered ones only, from a describe call
        # already being made for resource/trigger detection anyway
        # (state_machine_status) - see _process_machine.
        "created_at": None,
        "state_machine_status": None,
    }


def _build_error_item(pipeline, source, error: str, now) -> dict:
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
        "source": source,
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
        "created_at": None,
        "state_machine_status": None,
    }


def _registry_lookup() -> Dict[str, PipelineConfig]:
    """ARN -> PipelineConfig for every *enabled* registry entry. A disabled
    entry (monitoring_enabled=False) is deliberately left out of this map -
    handled separately as `disabled_arns` in lambda_handler - so it is
    skipped entirely, the same as before this module had a single loop: a
    registry pipeline with monitoring turned off must not appear on the
    dashboard at all, not fall back to being shown as auto-discovered.
    """
    return {p.state_machine_arn: p for p in load_registry() if p.monitoring_enabled}


def _disabled_registry_arns() -> set:
    return {p.state_machine_arn for p in load_registry() if not p.monitoring_enabled}


def _process_machine(machine: dict, registry_entry: Optional[PipelineConfig], now) -> dict:
    """Collect, evaluate, and build one DynamoDB item for one state machine.
    May raise - AWS/logic failures are the caller's job to catch and turn
    into an error item via _build_error_item.
    """
    arn = machine["arn"]
    region = DISCOVERY_REGION

    if registry_entry is not None:
        pipeline = registry_entry
        source = "registry"
    else:
        pipeline = _synthetic_pipeline(machine["name"], arn, region)
        source = "discovered"

    state = collect_pipeline_state(pipeline)
    exec_result = evaluate_freshness(state, now)

    if pipeline.output is not None:
        last_modified, fetch_error = get_last_modified(pipeline.output, pipeline.region)
        data_result = evaluate_data_freshness(pipeline, last_modified, fetch_error, now)
    else:
        data_result = evaluate_data_freshness(pipeline, None, None, now)

    item = _build_item(pipeline, source, state, exec_result, data_result, now)
    item["created_at"] = _iso(machine.get("creation_date"))

    if source == "discovered":
        # Resource/trigger detection only runs for pipelines that don't
        # already have a trusted, human-set schedule and output location -
        # a registry entry's evaluate_freshness/evaluate_data_freshness
        # results above are authoritative and are never second-guessed by
        # what an automated scan of the definition JSON turns up.
        details = describe_state_machine_details(arn, region)
        definition = details.get("definition") if details else None
        resources = scan_definition_json(definition) if definition else []
        trigger_label = detect_trigger(arn, region)

        item["discovery_reason"] = "Auto-discovered via states:ListStateMachines; not present in config/registry.yaml"
        item["detected_trigger"] = trigger_label
        item["detected_resources"] = resources
        item["state_machine_status"] = details.get("status") if details else None

        # Refine the one UNKNOWN case evaluate_freshness can reach for this
        # synthetic config (schedule.type="custom" with no interval, after a
        # SUCCEEDED execution) into a message tied to what trigger_scanner
        # found, instead of the generic "schedule configuration is
        # incomplete" wording meant for a mis-set registry entry.
        if item["execution_status"] == "unknown" and item["last_execution_status"] == ExecutionStatus.SUCCEEDED.value:
            item["execution_reason"] = (
                f"No schedule is registered for this pipeline yet ({trigger_label}). "
                "Execution health is tracked either way - a schedule enables freshness tracking too."
            )

        # Kept short and generic on purpose - the actual resource list lives
        # in detected_resources for the pipeline detail page to render, not
        # stuffed into this sentence (a table cell showing 5+ resource names
        # inline is what made rows unreadably tall before).
        if item["data_status"] == DataFreshnessStatus.NOT_CONFIGURED.value:
            if resources:
                item["data_status"] = DataFreshnessStatus.SOURCE_DETECTED_UNAVAILABLE.value
                item["data_reason"] = (
                    "Output source detected, but the exact data location could not be determined automatically."
                )
            else:
                item["data_status"] = DataFreshnessStatus.SOURCE_NOT_DETECTED.value
                item["data_reason"] = (
                    "No supported output source was identified. Execution health is still available, "
                    "but data freshness cannot be calculated."
                )

    return item


def lambda_handler(event, context):
    if not TABLE_NAME:
        raise RuntimeError("STATUS_TABLE_NAME environment variable is not set")

    now = datetime.now(timezone.utc)
    registry_by_arn = _registry_lookup()
    disabled_arns = _disabled_registry_arns()
    excluded_names = load_excluded_names()
    machines = list_all_state_machines(DISCOVERY_REGION)

    logger.info(
        "monitor run starting machines=%d registry_entries=%d excluded=%d",
        len(machines), len(registry_by_arn), len(excluded_names),
    )

    results = {"total": 0, "succeeded": 0, "failed": 0, "from_registry": 0, "from_discovery": 0}

    for machine in machines:
        name = machine["name"]
        arn = machine["arn"]
        if name in excluded_names or arn in disabled_arns:
            continue

        registry_entry = registry_by_arn.get(arn)
        results["total"] += 1

        try:
            item = _process_machine(machine, registry_entry, now)
            put_pipeline_status(TABLE_NAME, item)
            logger.info(
                "pipeline=%s source=%s execution_status=%s data_status=%s",
                name, item["source"], item["execution_status"], item["data_status"],
            )
            results["succeeded"] += 1
            results["from_registry" if registry_entry is not None else "from_discovery"] += 1
        except Exception as exc:
            logger.exception("unexpected error processing pipeline=%s", name)
            pipeline = registry_entry if registry_entry is not None else _synthetic_pipeline(name, arn, DISCOVERY_REGION)
            source = "registry" if registry_entry is not None else "discovered"
            try:
                put_pipeline_status(TABLE_NAME, _build_error_item(pipeline, source, str(exc), now))
            except Exception:
                logger.exception(
                    "also failed to write error status for pipeline=%s - dashboard will show stale data for it",
                    name,
                )
            results["failed"] += 1

    logger.info("monitor run finished %s", results)
    return results
