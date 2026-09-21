"""Read-only detection of what triggers a state machine, for pipelines that
have no registry entry (and therefore no manually-verified schedule).

Checks two independent trigger mechanisms, in order:
  1. Classic EventBridge Rules (events:list_rule_names_by_target / describe_rule)
  2. EventBridge Scheduler (scheduler:list_schedules / get_schedule)

Never raises: any AWS failure or "nothing found" collapses to a single
honest "Trigger not identified" label rather than an exception, so one
lookup failure can't take down the discovery run for a whole pipeline.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("pipeline_freshness_monitor.trigger_scanner")

_NOT_IDENTIFIED = "Trigger not identified"

_events_client_cache: Dict[str, "boto3.client"] = {}
_scheduler_client_cache: Dict[str, "boto3.client"] = {}


def _events_client(region: str):
    if region not in _events_client_cache:
        _events_client_cache[region] = boto3.client(
            "events", region_name=region, config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})
        )
    return _events_client_cache[region]


def _scheduler_client(region: str):
    if region not in _scheduler_client_cache:
        _scheduler_client_cache[region] = boto3.client(
            "scheduler", region_name=region, config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})
        )
    return _scheduler_client_cache[region]


def _check_eventbridge_rules(state_machine_arn: str, region: str) -> Optional[dict]:
    client = _events_client(region)
    try:
        rule_names = []
        paginator = client.get_paginator("list_rule_names_by_target")
        for page in paginator.paginate(TargetArn=state_machine_arn):
            rule_names.extend(page.get("RuleNames", []))
    except (ClientError, BotoCoreError) as exc:
        logger.warning("list_rule_names_by_target failed for %s: %s", state_machine_arn, exc)
        return None

    for rule_name in rule_names:
        try:
            rule = client.describe_rule(Name=rule_name)
        except (ClientError, BotoCoreError) as exc:
            logger.warning("describe_rule failed for %s: %s", rule_name, exc)
            continue

        if rule.get("State") != "ENABLED":
            continue

        schedule_expr = rule.get("ScheduleExpression")
        if schedule_expr:
            return {
                "kind": "eventbridge", "name": rule_name,
                "resource_id": rule.get("Arn") or f"eventbridge-rule:{rule_name}",
                "label": f"Schedule detected: {schedule_expr} (EventBridge rule {rule_name})",
                "schedule_expression": schedule_expr,
            }
        if rule.get("EventPattern"):
            return {
                "kind": "eventbridge", "name": rule_name,
                "resource_id": rule.get("Arn") or f"eventbridge-rule:{rule_name}",
                "label": f"Event triggered (EventBridge rule {rule_name})",
            }

    return None


def _check_eventbridge_scheduler(state_machine_arn: str, region: str) -> Optional[dict]:
    client = _scheduler_client(region)
    try:
        schedule_names = []
        paginator = client.get_paginator("list_schedules")
        for page in paginator.paginate():
            for entry in page.get("Schedules", []):
                schedule_names.append(entry["Name"])
    except (ClientError, BotoCoreError) as exc:
        logger.warning("list_schedules failed region=%s: %s", region, exc)
        return None

    for name in schedule_names:
        try:
            detail = client.get_schedule(Name=name)
        except (ClientError, BotoCoreError) as exc:
            logger.warning("get_schedule failed for %s: %s", name, exc)
            continue

        target_arn = (detail.get("Target") or {}).get("Arn")
        if target_arn != state_machine_arn:
            continue
        if detail.get("State") != "ENABLED":
            continue

        expr = detail.get("ScheduleExpression")
        if expr:
            return {
                "kind": "eventbridge", "name": name,
                "resource_id": detail.get("Arn") or f"eventbridge-schedule:{name}",
                "label": f"Schedule detected: {expr} (EventBridge Scheduler {name})",
                "schedule_expression": expr,
            }

    return None


def detect_trigger_detail(state_machine_arn: str, region: str) -> Optional[dict]:
    """Returns {"kind", "name", "resource_id", "label"} for the first
    enabled trigger found, or None if none was identified - never raises,
    never guesses. `detect_trigger()` below is a thin, backward-compatible
    wrapper over this for callers that only need the display label.
    """
    detail = _check_eventbridge_rules(state_machine_arn, region)
    if detail:
        return detail
    return _check_eventbridge_scheduler(state_machine_arn, region)


def detect_trigger(state_machine_arn: str, region: str) -> str:
    """Returns a single human-readable label - never raises, never guesses."""
    detail = detect_trigger_detail(state_machine_arn, region)
    return detail["label"] if detail else _NOT_IDENTIFIED
