from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src import handler
from src.models import (
    ExecutionStatus,
    ExecutionSummary,
    OutputConfig,
    PipelineConfig,
    PipelineExecutionState,
    ScheduleConfig,
)

ARN = "arn:aws:states:us-east-1:382625484581:stateMachine:test_pipeline"
# handler.py reads the real clock internally (not injectable, unlike the
# pure freshness engine), so fixtures must be relative to "now", not a fixed
# historical timestamp, or they'd drift into "stale" as real time passes.
NOW = datetime.now(timezone.utc) - timedelta(minutes=5)


def _pipeline(**overrides) -> PipelineConfig:
    defaults = dict(
        name="test_pipeline",
        state_machine_arn=ARN,
        region="us-east-1",
        environment="prod",
        monitoring_enabled=True,
        alerting_enabled=True,
        owner="team-data",
        schedule=ScheduleConfig(type="hourly", cron_utc=None),
        grace_period_minutes=15,
        output=None,
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def test_happy_path_writes_one_item_per_pipeline():
    pipeline = _pipeline()
    success = ExecutionSummary(
        execution_arn=ARN + ":e1",
        name="e1",
        status=ExecutionStatus.SUCCEEDED,
        start_date=NOW,
        stop_date=NOW,
    )
    state = PipelineExecutionState(
        pipeline=pipeline,
        latest_execution=success,
        latest_successful_execution=success,
        recent_executions=[success],
    )

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[pipeline]), \
         patch.object(handler, "collect_pipeline_state", return_value=state), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result == {"succeeded": 1, "failed": 0}
    assert len(written) == 1
    item = written[0]
    assert item["pipeline_name"] == "test_pipeline"
    assert item["execution_status"] == "fresh"
    assert item["alerting_enabled"] is True
    assert item["last_execution_duration_seconds"] == 0
    assert item["recent_executions"][0]["status"] == "SUCCEEDED"
    assert item["schedule_type"] == "hourly"
    assert item["grace_period_minutes"] == 15


def test_unexpected_exception_still_writes_an_unknown_item():
    """This is the core fix for gap B: a bug/unexpected failure while
    processing one pipeline must not leave that pipeline's DynamoDB item
    untouched - the dashboard would then silently show stale data forever
    with no sign anything went wrong.
    """
    pipeline = _pipeline()
    written = []

    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[pipeline]), \
         patch.object(handler, "collect_pipeline_state", side_effect=RuntimeError("boom")), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result == {"succeeded": 0, "failed": 1}
    assert len(written) == 1
    item = written[0]
    assert item["execution_status"] == "unknown"
    assert "boom" in item["execution_reason"]
    assert item["last_checked_at"] is not None


def test_one_pipeline_failing_does_not_stop_the_others():
    good_pipeline = _pipeline(name="good_pipeline")
    bad_pipeline = _pipeline(name="bad_pipeline")
    success = ExecutionSummary(
        execution_arn=ARN + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
    )
    good_state = PipelineExecutionState(
        pipeline=good_pipeline, latest_execution=success, latest_successful_execution=success
    )

    def fake_collect(pipeline):
        if pipeline.name == "bad_pipeline":
            raise RuntimeError("collector exploded")
        return good_state

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[good_pipeline, bad_pipeline]), \
         patch.object(handler, "collect_pipeline_state", side_effect=fake_collect), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result == {"succeeded": 1, "failed": 1}
    names_written = {item["pipeline_name"] for item in written}
    assert names_written == {"good_pipeline", "bad_pipeline"}


def test_missing_table_name_raises_clearly():
    with patch.object(handler, "TABLE_NAME", ""):
        try:
            handler.lambda_handler({}, None)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "STATUS_TABLE_NAME" in str(exc)
