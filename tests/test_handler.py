from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src import handler
from src.models import (
    ExecutionStatus,
    ExecutionSummary,
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


def _machine(name, arn, creation_date=NOW):
    return {"name": name, "arn": arn, "creation_date": creation_date}


def _never_run_state(pipeline):
    return PipelineExecutionState(pipeline=pipeline, latest_execution=None, latest_successful_execution=None)


# ---------------- Registry-sourced pipelines (single unified loop) ----------------


def test_happy_path_writes_one_item_per_registry_pipeline():
    pipeline = _pipeline()
    success = ExecutionSummary(
        execution_arn=ARN + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
    )
    state = PipelineExecutionState(
        pipeline=pipeline, latest_execution=success, latest_successful_execution=success, recent_executions=[success]
    )

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[pipeline]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("test_pipeline", ARN)]), \
         patch.object(handler, "collect_pipeline_state", return_value=state), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["from_registry"] == 1
    assert result["from_discovery"] == 0
    assert len(written) == 1
    item = written[0]
    assert item["pipeline_name"] == "test_pipeline"
    assert item["source"] == "registry"
    assert item["execution_status"] == "fresh"
    assert item["alerting_enabled"] is True
    assert item["last_execution_duration_seconds"] == 0
    assert item["recent_executions"][0]["status"] == "SUCCEEDED"
    assert item["schedule_type"] == "hourly"
    assert item["grace_period_minutes"] == 15
    assert item["created_at"] == NOW.isoformat()


def test_unexpected_exception_still_writes_an_unknown_item():
    """A bug/unexpected failure while processing one pipeline must not leave
    that pipeline's DynamoDB item untouched - the dashboard would then
    silently show stale data forever with no sign anything went wrong.
    """
    pipeline = _pipeline()
    written = []

    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[pipeline]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("test_pipeline", ARN)]), \
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
    assert item["source"] == "registry"


def test_one_pipeline_failing_does_not_stop_the_others():
    good_arn = ARN + "-good"
    bad_arn = ARN + "-bad"
    good_pipeline = _pipeline(name="good_pipeline", state_machine_arn=good_arn)
    bad_pipeline = _pipeline(name="bad_pipeline", state_machine_arn=bad_arn)
    success = ExecutionSummary(
        execution_arn=good_arn + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
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
         patch.object(handler, "load_registry", return_value=[good_pipeline, bad_pipeline]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[_machine("good_pipeline", good_arn), _machine("bad_pipeline", bad_arn)],
         ), \
         patch.object(handler, "collect_pipeline_state", side_effect=fake_collect), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["succeeded"] == 1
    assert result["failed"] == 1
    names_written = {item["pipeline_name"] for item in written}
    assert names_written == {"good_pipeline", "bad_pipeline"}


def test_disabled_registry_entry_is_skipped_entirely_not_shown_as_discovered():
    """monitoring_enabled=False must behave exactly as it did before this
    module had a single loop: invisible, not "falls back to auto-discovered".
    """
    disabled = _pipeline(monitoring_enabled=False)
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[disabled]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("test_pipeline", ARN)]), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["total"] == 0
    assert written == []


def test_missing_table_name_raises_clearly():
    with patch.object(handler, "TABLE_NAME", ""):
        try:
            handler.lambda_handler({}, None)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "STATUS_TABLE_NAME" in str(exc)


# ---------------- Mode dispatch (one Lambda, two EventBridge schedules) ----------------


def test_lineage_mode_dispatches_to_run_lineage_discovery_and_skips_monitoring():
    """The rate(1 day) schedule's {"mode": "lineage"} Input must reach
    run_lineage_discovery() and return immediately - none of the
    freshness-monitoring code path (registry lookup, discovery, per-pipeline
    collection) may run for this event.
    """
    sentinel = {"total": 3, "succeeded": 3, "failed": 0}
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "run_lineage_discovery", return_value=sentinel) as mock_lineage, \
         patch.object(handler, "list_all_state_machines") as mock_discovery:
        result = handler.lambda_handler({"mode": "lineage"}, None)

    assert result is sentinel
    mock_lineage.assert_called_once_with()
    mock_discovery.assert_not_called()


def test_normal_mode_does_not_dispatch_to_lineage():
    """The existing rate(15 minutes) schedule passes no Input, so event is
    {} - `.get("mode")` is None and the freshness-monitoring flow below the
    dispatch branch must run exactly as before.
    """
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "run_lineage_discovery") as mock_lineage, \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[]):
        result = handler.lambda_handler({}, None)

    mock_lineage.assert_not_called()
    assert result["total"] == 0


def test_missing_table_name_raises_before_lineage_dispatch_too():
    with patch.object(handler, "TABLE_NAME", ""), \
         patch.object(handler, "run_lineage_discovery") as mock_lineage:
        try:
            handler.lambda_handler({"mode": "lineage"}, None)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "STATUS_TABLE_NAME" in str(exc)
    mock_lineage.assert_not_called()


# ---------------- Discovered pipelines (no registry.yaml entry) ----------------

DISCOVERED_ARN = "arn:aws:states:us-east-1:382625484581:stateMachine:mystery_pipeline"


def test_a_state_machine_matching_a_registry_arn_is_sourced_from_the_registry():
    registered = _pipeline(name="already_registered")
    success = ExecutionSummary(
        execution_arn=ARN + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
    )
    state = PipelineExecutionState(pipeline=registered, latest_execution=success, latest_successful_execution=success)

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[registered]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[_machine("already_registered", registered.state_machine_arn)],
         ), \
         patch.object(handler, "collect_pipeline_state", return_value=state), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["from_registry"] == 1
    assert result["from_discovery"] == 0
    assert len(written) == 1
    assert written[0]["source"] == "registry"
    assert written[0]["environment"] == "prod"


def test_discovery_skips_excluded_names():
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value={"ignore_me"}), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("ignore_me", DISCOVERED_ARN)]), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["total"] == 0
    assert written == []


def test_new_machine_with_no_registry_entry_writes_needs_review_item():
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("mystery_pipeline", DISCOVERED_ARN)]), \
         patch.object(handler, "collect_pipeline_state", side_effect=_never_run_state), \
         patch.object(handler, "describe_state_machine_details", return_value=None), \
         patch.object(handler, "detect_trigger", return_value="Trigger not identified"), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["from_discovery"] == 1
    assert result["from_registry"] == 0
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
    assert item["created_at"] == NOW.isoformat()
    assert item["state_machine_status"] is None


def test_discovered_pipeline_reports_detected_resources_when_output_is_unresolved():
    success = ExecutionSummary(
        execution_arn=DISCOVERED_ARN + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
    )

    def _state(pipeline):
        return PipelineExecutionState(pipeline=pipeline, latest_execution=success, latest_successful_execution=success)

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("mystery_pipeline", DISCOVERED_ARN)]), \
         patch.object(handler, "collect_pipeline_state", side_effect=_state), \
         patch.object(handler, "describe_state_machine_details", return_value={"definition": '{"States": {}}', "status": "ACTIVE"}), \
         patch.object(handler, "scan_definition_json", return_value=["Lambda: my_fn"]), \
         patch.object(handler, "detect_trigger", return_value="Schedule detected: rate(1 day) (EventBridge rule r1)"), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["from_discovery"] == 1
    item = written[0]
    assert item["execution_status"] == "unknown"
    assert "Schedule detected" in item["execution_reason"]
    assert item["data_status"] == "source_detected_unavailable"
    assert "Lambda: my_fn" not in item["data_reason"]
    assert item["state_machine_status"] == "ACTIVE"


def test_one_bad_discovered_machine_does_not_stop_the_others():
    bad_arn = DISCOVERED_ARN + "-bad"
    good_arn = DISCOVERED_ARN + "-good"

    def fake_collect(pipeline):
        if pipeline.name == "bad_discovered":
            raise RuntimeError("boom")
        return _never_run_state(pipeline)

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(
             handler, "list_all_state_machines",
             return_value=[_machine("bad_discovered", bad_arn), _machine("good_discovered", good_arn)],
         ), \
         patch.object(handler, "collect_pipeline_state", side_effect=fake_collect), \
         patch.object(handler, "describe_state_machine_details", return_value=None), \
         patch.object(handler, "detect_trigger", return_value="Trigger not identified"), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["from_discovery"] == 1
    assert result["failed"] == 1
    assert len(written) == 2
    names_written = {item["pipeline_name"] for item in written}
    assert names_written == {"bad_discovered", "good_discovered"}


def test_no_state_machine_is_ever_processed_twice():
    """Structural duplicate-prevention check: a single account-wide listing
    feeding a single loop means the same ARN cannot be matched by both the
    registry branch and the discovered branch in the same run.
    """
    pipeline = _pipeline()
    success = ExecutionSummary(
        execution_arn=ARN + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
    )
    state = PipelineExecutionState(pipeline=pipeline, latest_execution=success, latest_successful_execution=success)

    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[pipeline]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("test_pipeline", ARN)]), \
         patch.object(handler, "collect_pipeline_state", return_value=state), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        handler.lambda_handler({}, None)

    assert len(written) == 1
