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
         patch.object(handler, "list_all_state_machines", return_value=[]), \
         patch.object(handler, "collect_pipeline_state", return_value=state), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["discovery"] == {"discovered": 0, "failed": 0}
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
         patch.object(handler, "list_all_state_machines", return_value=[]), \
         patch.object(handler, "collect_pipeline_state", side_effect=RuntimeError("boom")), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["succeeded"] == 0
    assert result["failed"] == 1
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
         patch.object(handler, "list_all_state_machines", return_value=[]), \
         patch.object(handler, "collect_pipeline_state", side_effect=fake_collect), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["succeeded"] == 1
    assert result["failed"] == 1
    names_written = {item["pipeline_name"] for item in written}
    assert names_written == {"good_pipeline", "bad_pipeline"}


def test_missing_table_name_raises_clearly():
    with patch.object(handler, "TABLE_NAME", ""):
        try:
            handler.lambda_handler({}, None)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "STATUS_TABLE_NAME" in str(exc)


# ---------------- Discovery phase ----------------

DISCOVERED_ARN = "arn:aws:states:us-east-1:382625484581:stateMachine:mystery_pipeline"


def _never_run_state(pipeline):
    return PipelineExecutionState(pipeline=pipeline, latest_execution=None, latest_successful_execution=None)


def test_discovery_phase_skips_arns_already_in_the_registry():
    registered = _pipeline(name="already_registered")
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[]), \
         patch.object(handler, "load_registry", return_value=[registered]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[{"name": "already_registered", "arn": registered.state_machine_arn, "creation_date": NOW}],
         ), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["discovery"] == {"discovered": 0, "failed": 0}
    assert written == []


def test_discovery_phase_skips_excluded_names():
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[]), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value={"ignore_me"}), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[{"name": "ignore_me", "arn": DISCOVERED_ARN, "creation_date": NOW}],
         ), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["discovery"] == {"discovered": 0, "failed": 0}
    assert written == []


def test_discovery_phase_writes_needs_review_item_for_new_machine():
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[]), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[{"name": "mystery_pipeline", "arn": DISCOVERED_ARN, "creation_date": NOW}],
         ), \
         patch.object(handler, "collect_pipeline_state", side_effect=_never_run_state), \
         patch.object(handler, "get_definition", return_value=None), \
         patch.object(handler, "detect_trigger", return_value="Trigger not identified"), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["discovery"] == {"discovered": 1, "failed": 0}
    assert len(written) == 1
    item = written[0]
    assert item["pipeline_name"] == "mystery_pipeline"
    assert item["source"] == "discovered"
    assert item["review_status"] == "needs_review"
    assert item["environment"] == "unregistered"
    assert item["execution_status"] == "never_run"
    assert item["data_status"] == "source_not_detected"
    assert item["detected_trigger"] == "Trigger not identified"
    assert item["detected_resources"] == []


def test_discovery_phase_reports_detected_resources_when_output_is_unresolved():
    success = ExecutionSummary(
        execution_arn=DISCOVERED_ARN + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
    )

    def _state(pipeline):
        return PipelineExecutionState(
            pipeline=pipeline, latest_execution=success, latest_successful_execution=success
        )

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[]), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[{"name": "mystery_pipeline", "arn": DISCOVERED_ARN, "creation_date": NOW}],
         ), \
         patch.object(handler, "collect_pipeline_state", side_effect=_state), \
         patch.object(handler, "get_definition", return_value='{"States": {}}'), \
         patch.object(handler, "scan_definition_json", return_value=["Lambda: my_fn"]), \
         patch.object(handler, "detect_trigger", return_value="Schedule detected: rate(1 day) (EventBridge rule r1)"), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["discovery"] == {"discovered": 1, "failed": 0}
    item = written[0]
    assert item["execution_status"] == "unknown"
    assert "Schedule detected" in item["execution_reason"]
    assert item["data_status"] == "source_detected_unavailable"
    assert "Lambda: my_fn" in item["data_reason"]


def test_discovery_phase_one_bad_machine_does_not_stop_the_others():
    written = []

    def fake_collect(pipeline):
        if pipeline.name == "bad_discovered":
            raise RuntimeError("boom")
        return _never_run_state(pipeline)

    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_monitored_registry", return_value=[]), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[
                 {"name": "bad_discovered", "arn": DISCOVERED_ARN + "-bad", "creation_date": NOW},
                 {"name": "good_discovered", "arn": DISCOVERED_ARN + "-good", "creation_date": NOW},
             ],
         ), \
         patch.object(handler, "collect_pipeline_state", side_effect=fake_collect), \
         patch.object(handler, "get_definition", return_value=None), \
         patch.object(handler, "detect_trigger", return_value="Trigger not identified"), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["discovery"] == {"discovered": 1, "failed": 1}
    assert len(written) == 1
    assert written[0]["pipeline_name"] == "good_discovered"
