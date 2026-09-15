"""Freshness engine: turns a PipelineExecutionState into one FreshnessStatus.

Pure logic only - no AWS calls, no wall-clock reads (`now` is always passed
in). This is what makes it independently unit-testable and keeps "did the
pipeline run" (this module) cleanly separate from "is the output data
actually fresh" (Phase 4, data-level checks).

Decision order (first match wins):
  1. collector_error set                          -> UNKNOWN
  2. no executions ever                            -> NEVER_RUN
  3. latest execution RUNNING                      -> RUNNING
  4. latest execution FAILED/TIMED_OUT/ABORTED     -> FAILED
  5. latest execution SUCCEEDED, elapsed <= interval                        -> FRESH
     interval < elapsed <= interval + grace_period                          -> DELAYED
     elapsed > interval + grace_period                                     -> STALE
     (schedule config missing/invalid, e.g. custom with no interval)        -> UNKNOWN
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from .models import ExecutionStatus, ExecutionSummary, PipelineExecutionState, ScheduleConfig

_INTERVAL_MINUTES_BY_TYPE = {
    "hourly": 60,
    "daily": 60 * 24,
}


class FreshnessStatus(str, Enum):
    FRESH = "fresh"
    DELAYED = "delayed"
    FAILED = "failed"
    STALE = "stale"
    RUNNING = "running"
    NEVER_RUN = "never_run"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FreshnessResult:
    pipeline_name: str
    status: FreshnessStatus
    reason: str
    computed_at: datetime
    last_execution: Optional[ExecutionSummary]
    last_successful_execution: Optional[ExecutionSummary]
    expected_next_run: Optional[datetime] = None
    stale_deadline: Optional[datetime] = None


def expected_interval(schedule: ScheduleConfig) -> Optional[timedelta]:
    """Public so data_freshness.py can apply the same schedule to S3 checks."""
    if schedule.type in _INTERVAL_MINUTES_BY_TYPE:
        return timedelta(minutes=_INTERVAL_MINUTES_BY_TYPE[schedule.type])
    if schedule.type == "custom" and schedule.interval_minutes:
        return timedelta(minutes=schedule.interval_minutes)
    return None


def classify_elapsed(elapsed: timedelta, interval: timedelta, grace: timedelta) -> FreshnessStatus:
    """Shared FRESH/DELAYED/STALE threshold logic, reused by execution and data freshness."""
    if elapsed <= interval:
        return FreshnessStatus.FRESH
    if elapsed <= interval + grace:
        return FreshnessStatus.DELAYED
    return FreshnessStatus.STALE


def evaluate_freshness(state: PipelineExecutionState, now: datetime) -> FreshnessResult:
    pipeline = state.pipeline

    if state.collector_error is not None:
        return FreshnessResult(
            pipeline_name=pipeline.name,
            status=FreshnessStatus.UNKNOWN,
            reason=f"monitor failed to collect execution data: {state.collector_error}",
            computed_at=now,
            last_execution=None,
            last_successful_execution=None,
        )

    if state.latest_execution is None:
        return FreshnessResult(
            pipeline_name=pipeline.name,
            status=FreshnessStatus.NEVER_RUN,
            reason="no executions found for this pipeline",
            computed_at=now,
            last_execution=None,
            last_successful_execution=None,
        )

    latest = state.latest_execution

    if latest.status == ExecutionStatus.RUNNING:
        return FreshnessResult(
            pipeline_name=pipeline.name,
            status=FreshnessStatus.RUNNING,
            reason=f"pipeline is currently executing (started {latest.start_date.isoformat()})",
            computed_at=now,
            last_execution=latest,
            last_successful_execution=state.latest_successful_execution,
        )

    if latest.status in (ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT, ExecutionStatus.ABORTED):
        detail = latest.error or "no error detail available"
        return FreshnessResult(
            pipeline_name=pipeline.name,
            status=FreshnessStatus.FAILED,
            reason=(
                f"most recent execution ended in {latest.status.value} "
                f"at {latest.stop_date.isoformat() if latest.stop_date else 'unknown time'}: {detail}"
            ),
            computed_at=now,
            last_execution=latest,
            last_successful_execution=state.latest_successful_execution,
        )

    # latest.status == SUCCEEDED beyond this point
    interval = expected_interval(pipeline.schedule)
    if interval is None:
        return FreshnessResult(
            pipeline_name=pipeline.name,
            status=FreshnessStatus.UNKNOWN,
            reason=(
                f"pipeline schedule configuration is incomplete or invalid "
                f"(type={pipeline.schedule.type!r}); cannot compute freshness"
            ),
            computed_at=now,
            last_execution=latest,
            last_successful_execution=state.latest_successful_execution,
        )

    if pipeline.grace_period_minutes is None:
        return FreshnessResult(
            pipeline_name=pipeline.name,
            status=FreshnessStatus.UNKNOWN,
            reason=(
                "grace period is not yet configured for this pipeline "
                "(schedule is verified but too irregular for the current default rule); "
                "cannot compute freshness"
            ),
            computed_at=now,
            last_execution=latest,
            last_successful_execution=state.latest_successful_execution,
        )

    reference_time = latest.stop_date or latest.start_date
    elapsed = now - reference_time
    grace = timedelta(minutes=pipeline.grace_period_minutes)
    expected_next_run = reference_time + interval
    stale_deadline = expected_next_run + grace

    status = classify_elapsed(elapsed, interval, grace)
    if status == FreshnessStatus.FRESH:
        reason = f"last success at {reference_time.isoformat()}, within expected interval"
    elif status == FreshnessStatus.DELAYED:
        reason = (
            f"last success at {reference_time.isoformat()} is past the expected interval "
            f"but still within the {pipeline.grace_period_minutes}-minute grace period"
        )
    else:
        reason = (
            f"last success at {reference_time.isoformat()} is past the expected interval "
            f"and past the {pipeline.grace_period_minutes}-minute grace period"
        )

    return FreshnessResult(
        pipeline_name=pipeline.name,
        status=status,
        reason=reason,
        computed_at=now,
        last_execution=latest,
        last_successful_execution=state.latest_successful_execution,
        expected_next_run=expected_next_run,
        stale_deadline=stale_deadline,
    )
