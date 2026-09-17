"""Loads the optional, manually-curated pipeline registry from
config/registry.yaml.

Discovery (see discovery.py/handler.py) is the single source of truth for
*which* state machines are monitored - every one in the account, always.
This file is consulted per-ARN purely as an optional metadata overlay
(trusted schedule, output location, owner, alerting, exclusions) on top of
that. It is never required to exist: a missing file behaves exactly like an
empty one, so the monitor still runs from discovery alone.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Set

import yaml

from .models import OutputConfig, PipelineConfig, ScheduleConfig

logger = logging.getLogger("pipeline_freshness_monitor.registry")

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "registry.yaml"


def _load_raw(path: Path) -> dict:
    """A missing file is expected and silent (see module docstring - this
    file is optional). Malformed YAML is not expected, but must degrade the
    same way rather than raise: both handler.py's and lineage_handler.py's
    lambda_handler call load_registry()/load_excluded_names() before their
    per-pipeline try/except blocks even start, so an uncaught error here
    would crash the entire run - every pipeline, not just a mis-configured
    row - the exact single point of failure this project's design otherwise
    goes out of its way to avoid. Logged as a warning (unlike the silent
    missing-file case) because a syntax error in a file someone deliberately
    edited is worth knowing about, even though the monitor keeps running.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError:
        logger.warning("config/registry.yaml is not valid YAML - proceeding as if it were empty", exc_info=True)
        return {}


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> List[PipelineConfig]:
    raw = _load_raw(path)

    pipelines = []
    for entry in raw.get("pipelines", []):
        schedule_raw = entry["schedule"]
        schedule = ScheduleConfig(
            type=schedule_raw["type"],
            cron_utc=schedule_raw.get("cron_utc"),
            interval_minutes=schedule_raw.get("interval_minutes"),
        )

        output_raw = entry.get("output")
        output = (
            OutputConfig(type=output_raw["type"], bucket=output_raw["bucket"], key=output_raw["key"])
            if output_raw
            else None
        )

        pipelines.append(
            PipelineConfig(
                name=entry["name"],
                state_machine_arn=entry["state_machine_arn"],
                region=entry["region"],
                environment=entry["environment"],
                monitoring_enabled=entry.get("monitoring_enabled", True),
                # Default to False: a newly-registered pipeline should never
                # start alerting until someone deliberately opts it in.
                alerting_enabled=entry.get("alerting_enabled", False),
                owner=entry.get("owner"),  # None (not a "TBD" string) when unknown
                schedule=schedule,
                # None (not a required key) when a schedule is verified but too
                # irregular/uncommon (monthly, bounded-hours, annual) for the
                # current grace-period rule to apply - see registry.yaml comments.
                grace_period_minutes=entry.get("grace_period_minutes"),
                output=output,
                review_status=entry.get("review_status", "confirmed"),
                contact=entry.get("contact"),  # None (not a placeholder string) when unassigned
            )
        )
    return pipelines


def load_monitored_registry(path: Path = DEFAULT_REGISTRY_PATH) -> List[PipelineConfig]:
    return [p for p in load_registry(path) if p.monitoring_enabled]


def load_excluded_names(path: Path = DEFAULT_REGISTRY_PATH) -> Set[str]:
    """State machine names listed under a top-level `excluded:` key in
    registry.yaml are skipped entirely (no AWS calls, no DynamoDB item
    written for them) - e.g. AWS's own internal automation stacks, or
    throwaway console experiments that aren't real pipelines. Absent/empty
    by default - the default behavior is to surface everything found, not
    to hide it.
    """
    return set(_load_raw(path).get("excluded") or [])
