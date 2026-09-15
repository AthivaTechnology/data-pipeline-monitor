"""Data-level freshness: is the pipeline's *output* actually fresh?

Deliberately separate from freshness.py (execution freshness). A pipeline can
report SUCCEEDED and still write stale/empty data, and this module is what
catches that - it never looks at Step Functions state, only at the
registered output location. Optional: pipelines without a configured
`output` get NOT_CONFIGURED, never a guessed status.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from .freshness import classify_elapsed, expected_interval
from .models import PipelineConfig


class DataFreshnessStatus(str, Enum):
    FRESH = "fresh"
    DELAYED = "delayed"
    STALE = "stale"
    UNKNOWN = "unknown"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class DataFreshnessResult:
    pipeline_name: str
    status: DataFreshnessStatus
    reason: str
    checked_location: Optional[str]
    last_modified: Optional[datetime]


def evaluate_data_freshness(
    pipeline: PipelineConfig,
    last_modified: Optional[datetime],
    fetch_error: Optional[str],
    now: datetime,
) -> DataFreshnessResult:
    if pipeline.output is None:
        return DataFreshnessResult(
            pipeline_name=pipeline.name,
            status=DataFreshnessStatus.NOT_CONFIGURED,
            reason="no output location registered for this pipeline",
            checked_location=None,
            last_modified=None,
        )

    location = f"s3://{pipeline.output.bucket}/{pipeline.output.key}"

    if fetch_error is not None:
        return DataFreshnessResult(
            pipeline_name=pipeline.name,
            status=DataFreshnessStatus.UNKNOWN,
            reason=f"could not check {location}: {fetch_error}",
            checked_location=location,
            last_modified=None,
        )

    interval = expected_interval(pipeline.schedule)
    if interval is None:
        return DataFreshnessResult(
            pipeline_name=pipeline.name,
            status=DataFreshnessStatus.UNKNOWN,
            reason=(
                f"pipeline schedule configuration is incomplete or invalid "
                f"(type={pipeline.schedule.type!r}); cannot compute data freshness"
            ),
            checked_location=location,
            last_modified=last_modified,
        )

    grace = timedelta(minutes=pipeline.grace_period_minutes)
    elapsed = now - last_modified
    status = classify_elapsed(elapsed, interval, grace)
    reason = f"{location} last modified {last_modified.isoformat()} ({status.value})"

    return DataFreshnessResult(
        pipeline_name=pipeline.name,
        status=DataFreshnessStatus(status.value),
        reason=reason,
        checked_location=location,
        last_modified=last_modified,
    )
