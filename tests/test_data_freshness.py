from datetime import datetime, timedelta, timezone

import boto3
from botocore.stub import Stubber

from src import s3_checker
from src.data_freshness import DataFreshnessStatus, evaluate_data_freshness
from src.models import OutputConfig, PipelineConfig, ScheduleConfig

REGION = "us-east-1"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _pipeline(output=None, schedule_type="daily", grace_period_minutes=60, interval_minutes=None) -> PipelineConfig:
    return PipelineConfig(
        name="test_pipeline",
        state_machine_arn="arn:aws:states:us-east-1:382625484581:stateMachine:test_pipeline",
        region=REGION,
        environment="prod",
        monitoring_enabled=True,
        alerting_enabled=False,
        owner=None,
        schedule=ScheduleConfig(type=schedule_type, cron_utc=None, interval_minutes=interval_minutes),
        grace_period_minutes=grace_period_minutes,
        output=output,
    )


def teardown_function(_):
    s3_checker._client_cache.clear()


def test_not_configured_when_no_output():
    result = evaluate_data_freshness(_pipeline(output=None), last_modified=None, fetch_error=None, now=NOW)
    assert result.status == DataFreshnessStatus.NOT_CONFIGURED


def test_fetch_error_is_unknown_not_stale():
    output = OutputConfig(type="s3", bucket="b", key="k")
    result = evaluate_data_freshness(
        _pipeline(output=output), last_modified=None, fetch_error="404 Not Found", now=NOW
    )
    assert result.status == DataFreshnessStatus.UNKNOWN
    assert "404 Not Found" in result.reason


def test_fresh_object_within_interval():
    output = OutputConfig(type="s3", bucket="b", key="k")
    last_modified = NOW - timedelta(hours=10)  # daily interval = 24h
    result = evaluate_data_freshness(
        _pipeline(output=output, schedule_type="daily", grace_period_minutes=60),
        last_modified=last_modified,
        fetch_error=None,
        now=NOW,
    )
    assert result.status == DataFreshnessStatus.FRESH


def test_stale_object_past_grace():
    output = OutputConfig(type="s3", bucket="b", key="k")
    last_modified = NOW - timedelta(hours=26)  # past 24h + 60min grace
    result = evaluate_data_freshness(
        _pipeline(output=output, schedule_type="daily", grace_period_minutes=60),
        last_modified=last_modified,
        fetch_error=None,
        now=NOW,
    )
    assert result.status == DataFreshnessStatus.STALE


def test_recovery_from_stale_to_fresh_when_object_updates():
    """A stale output that gets refreshed must report FRESH again on the
    next check - same idempotent/stateless guarantee as execution freshness."""
    output = OutputConfig(type="s3", bucket="b", key="k")
    pipeline = _pipeline(output=output, schedule_type="daily", grace_period_minutes=60)

    stale_check = evaluate_data_freshness(
        pipeline, last_modified=NOW - timedelta(hours=26), fetch_error=None, now=NOW
    )
    assert stale_check.status == DataFreshnessStatus.STALE

    fresh_check = evaluate_data_freshness(
        pipeline, last_modified=NOW - timedelta(hours=1), fetch_error=None, now=NOW
    )
    assert fresh_check.status == DataFreshnessStatus.FRESH


def test_s3_checker_returns_last_modified_on_success():
    client = boto3.client("s3", region_name=REGION)
    stubber = Stubber(client)
    s3_checker._client_cache[REGION] = client

    lm = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
    stubber.add_response(
        "head_object",
        {"LastModified": lm, "ContentLength": 123, "ETag": '"abc"'},
        {"Bucket": "b", "Key": "k"},
    )
    stubber.activate()

    output = OutputConfig(type="s3", bucket="b", key="k")
    last_modified, error = s3_checker.get_last_modified(output, REGION)

    assert error is None
    assert last_modified == lm


def test_s3_checker_captures_error_without_raising():
    client = boto3.client("s3", region_name=REGION)
    stubber = Stubber(client)
    s3_checker._client_cache[REGION] = client
    stubber.add_client_error("head_object", service_error_code="404")
    stubber.activate()

    output = OutputConfig(type="s3", bucket="b", key="missing")
    last_modified, error = s3_checker.get_last_modified(output, REGION)

    assert last_modified is None
    assert error is not None
