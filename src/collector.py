"""Read-only Step Functions collector.

Fetches recent executions for a registered pipeline and normalizes them into
PipelineExecutionState. Makes no mutating AWS calls (list_executions /
describe_execution only). A failure collecting one pipeline is captured on
that pipeline's result rather than raised, so one bad AWS call never takes
down the whole monitoring run and never gets mistaken for "pipeline is
healthy" - the caller must check `collector_error` before trusting a status.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from .models import ExecutionStatus, ExecutionSummary, PipelineConfig, PipelineExecutionState

logger = logging.getLogger("pipeline_freshness_monitor.collector")

# Bound how many executions we'll page through looking for the latest success.
# Protects the monitor's own runtime/cost against a pipeline with thousands of
# failed executions and no recent success.
MAX_EXECUTIONS_TO_SCAN = 100

# How many of the most recent executions to keep for the dashboard's detail
# view. Sourced from the same paginated list_executions calls above - this
# does not add any new AWS API calls, it just keeps more of what we already
# fetch (previously discarded everything but the single latest execution).
RECENT_EXECUTIONS_LIMIT = 5

_TERMINAL_ERROR_STATUSES = {
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMED_OUT,
    ExecutionStatus.ABORTED,
}

_client_cache: Dict[str, "boto3.client"] = {}


def _get_client(region: str):
    if region not in _client_cache:
        _client_cache[region] = boto3.client(
            "stepfunctions",
            region_name=region,
            config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"}),
        )
    return _client_cache[region]


def _to_execution_summary(execution: dict) -> ExecutionSummary:
    return ExecutionSummary(
        execution_arn=execution["executionArn"],
        name=execution["name"],
        status=ExecutionStatus(execution["status"]),
        start_date=execution["startDate"],
        stop_date=execution.get("stopDate"),
    )


def _enrich_with_error_detail(client, summary: ExecutionSummary) -> ExecutionSummary:
    """describe_execution carries error/cause that list_executions does not."""
    if summary.status not in _TERMINAL_ERROR_STATUSES:
        return summary
    try:
        detail = client.describe_execution(executionArn=summary.execution_arn)
    except (ClientError, BotoCoreError) as exc:
        logger.warning(
            "describe_execution failed for %s: %s", summary.execution_arn, exc
        )
        return summary
    return ExecutionSummary(
        execution_arn=summary.execution_arn,
        name=summary.name,
        status=summary.status,
        start_date=summary.start_date,
        stop_date=summary.stop_date,
        error=detail.get("error"),
        cause=detail.get("cause"),
    )


def collect_pipeline_state(pipeline: PipelineConfig) -> PipelineExecutionState:
    """Fetch and normalize the latest execution and latest success for one pipeline.

    Never raises: AWS/API failures are captured in the result's
    `collector_error` field instead, so a monitoring-side failure is always
    distinguishable from "the pipeline itself has no executions".
    """
    logger.info(
        "collecting pipeline=%s arn=%s region=%s",
        pipeline.name,
        pipeline.state_machine_arn,
        pipeline.region,
    )

    client = _get_client(pipeline.region)
    latest_execution: Optional[ExecutionSummary] = None
    latest_success: Optional[ExecutionSummary] = None
    recent_executions: List[ExecutionSummary] = []

    try:
        paginator = client.get_paginator("list_executions")
        scanned = 0
        for page in paginator.paginate(
            stateMachineArn=pipeline.state_machine_arn,
            PaginationConfig={"PageSize": 25},
        ):
            for execution in page.get("executions", []):
                summary = _to_execution_summary(execution)
                if latest_execution is None:
                    latest_execution = summary
                if len(recent_executions) < RECENT_EXECUTIONS_LIMIT:
                    recent_executions.append(summary)
                if latest_success is None and summary.status == ExecutionStatus.SUCCEEDED:
                    latest_success = summary

                scanned += 1
                have_enough = latest_success is not None and len(recent_executions) >= RECENT_EXECUTIONS_LIMIT
                if have_enough or scanned >= MAX_EXECUTIONS_TO_SCAN:
                    break
            have_enough = latest_success is not None and len(recent_executions) >= RECENT_EXECUTIONS_LIMIT
            if have_enough or scanned >= MAX_EXECUTIONS_TO_SCAN:
                break

        if latest_execution is not None:
            latest_execution = _enrich_with_error_detail(client, latest_execution)
            if recent_executions and recent_executions[0].execution_arn == latest_execution.execution_arn:
                recent_executions[0] = latest_execution

    except (ClientError, BotoCoreError) as exc:
        logger.error(
            "collector_error pipeline=%s arn=%s error=%s",
            pipeline.name,
            pipeline.state_machine_arn,
            exc,
        )
        return PipelineExecutionState(
            pipeline=pipeline,
            latest_execution=None,
            latest_successful_execution=None,
            collector_error=str(exc),
        )

    return PipelineExecutionState(
        pipeline=pipeline,
        latest_execution=latest_execution,
        latest_successful_execution=latest_success,
        collector_error=None,
        recent_executions=recent_executions,
    )
