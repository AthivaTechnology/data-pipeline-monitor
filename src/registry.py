"""Loads the manually-curated pipeline registry from config/registry.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import List

import yaml

from .models import OutputConfig, PipelineConfig, ScheduleConfig

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "registry.yaml"


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> List[PipelineConfig]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

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
                grace_period_minutes=entry["grace_period_minutes"],
                output=output,
            )
        )
    return pipelines


def load_monitored_registry(path: Path = DEFAULT_REGISTRY_PATH) -> List[PipelineConfig]:
    return [p for p in load_registry(path) if p.monitoring_enabled]
