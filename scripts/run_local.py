"""Manual local runner for Phase 2: collects real execution state for every
enabled pipeline in the registry and prints it. Read-only against AWS.

Usage (PowerShell):
    $env:AWS_PROFILE = "haripriya.p"
    py scripts/run_local.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

from src.collector import collect_pipeline_state
from src.data_freshness import evaluate_data_freshness
from src.freshness import evaluate_freshness
from src.registry import load_monitored_registry
from src.s3_checker import get_last_modified

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    pipelines = load_monitored_registry()
    print(f"Loaded {len(pipelines)} monitored pipeline(s) from registry.\n")
    now = datetime.now(timezone.utc)

    for pipeline in pipelines:
        state = collect_pipeline_state(pipeline)
        result = evaluate_freshness(state, now)

        print(f"--- {pipeline.name} ({pipeline.environment}/{pipeline.region}) ---")
        print(f"  STATUS: {result.status.value.upper()}")
        print(f"  Reason: {result.reason}")

        if state.latest_execution is not None:
            le = state.latest_execution
            print(f"  Latest execution:    {le.status.value} at {le.start_date} (name={le.name})")
        if state.latest_successful_execution is not None:
            ls = state.latest_successful_execution
            print(f"  Latest success:      {ls.start_date} (duration={ls.duration_seconds}s)")
        if result.expected_next_run:
            print(f"  Expected next run:   {result.expected_next_run}")
        if result.stale_deadline:
            print(f"  Stale deadline:      {result.stale_deadline}")

        if pipeline.output is not None:
            last_modified, fetch_error = get_last_modified(pipeline.output, pipeline.region)
            data_result = evaluate_data_freshness(pipeline, last_modified, fetch_error, now)
            print(f"  DATA STATUS: {data_result.status.value.upper()} - {data_result.reason}")
        else:
            print("  DATA STATUS: not_configured - no output registered")
        print()


if __name__ == "__main__":
    main()
