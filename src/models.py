"""Normalized data models for the pipeline freshness monitor.

Kept free of boto3/AWS types on purpose: the collector (AWS-facing) produces
these, and the freshness engine (Phase 3) consumes them without needing to
know anything about Step Functions APIs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class ExecutionStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    ABORTED = "ABORTED"


@dataclass(frozen=True)
class ScheduleConfig:
    type: str  # "hourly" | "daily" | "custom"
    cron_utc: Optional[str]  # None when unverified/unknown
    interval_minutes: Optional[int] = None  # required only when type == "custom"


@dataclass(frozen=True)
class OutputConfig:
    type: str  # "s3" (only type supported in the MVP)
    bucket: str
    key: str


@dataclass(frozen=True)
class PipelineConfig:
    name: str
    state_machine_arn: str
    region: str
    environment: str
    monitoring_enabled: bool  # should the monitor collect/evaluate this pipeline at all
    alerting_enabled: bool  # should Slack alerts (Phase 6) ever fire for this pipeline
    owner: str
    schedule: ScheduleConfig
    grace_period_minutes: int
    output: Optional[OutputConfig]


@dataclass(frozen=True)
class ExecutionSummary:
    execution_arn: str
    name: str
    status: ExecutionStatus
    start_date: datetime
    stop_date: Optional[datetime]
    error: Optional[str] = None
    cause: Optional[str] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.stop_date is None:
            return None
        return (self.stop_date - self.start_date).total_seconds()


@dataclass(frozen=True)
class PipelineExecutionState:
    """Raw, normalized execution facts for one pipeline - no freshness judgment yet."""

    pipeline: PipelineConfig
    latest_execution: Optional[ExecutionSummary]
    latest_successful_execution: Optional[ExecutionSummary]
    collector_error: Optional[str] = None  # set when AWS calls failed for this pipeline
    recent_executions: List[ExecutionSummary] = field(default_factory=list)
