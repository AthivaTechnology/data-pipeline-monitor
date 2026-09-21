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


def _machine(name, arn, creation_date=NOW):
    return {"name": name, "arn": arn, "creation_date": creation_date}


def _never_run_state(pipeline):
    return PipelineExecutionState(pipeline=pipeline, latest_execution=None, latest_successful_execution=None)


# ---------------- Environment detection (_detect_environment) ----------------


def test_detect_environment_finds_a_direct_environment_tag():
    env, reason = handler._detect_environment({"Environment": "staging"})
    assert env == "staging"
    assert reason is None


def test_detect_environment_checks_common_key_capitalizations():
    for key in ["Environment", "environment", "Env", "env", "Stage", "stage"]:
        env, reason = handler._detect_environment({key: "prod"})
        assert env == "prod"
        assert reason is None


def test_detect_environment_no_tags_at_all_gives_a_specific_reason():
    env, reason = handler._detect_environment({})
    assert env is None
    assert "no tags at all" in reason


def test_detect_environment_unrelated_tags_names_them_in_the_reason():
    tags = {"aws:cloudformation:stack-name": "data-exporter", "stateMachine:createdBy": "SAM"}
    env, reason = handler._detect_environment(tags)
    assert env is None
    assert "aws:cloudformation:stack-name" in reason
    assert "stateMachine:createdBy" in reason
    assert "no tags at all" not in reason


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


# ---------------- Data freshness is independent of execution health ----------------
# The dashboard no longer shows a Data Freshness column, but the backend
# capability (S3 output check -> data_status) is kept for pipelines that do
# get an output location. These pin that it still runs, and that its result
# can never change execution_status - which is what every summary card and
# Healthy/Failed/Delayed/Stale decision is based on.


def _fresh_run_with_s3_output():
    pipeline = _pipeline(output=OutputConfig(type="s3", bucket="exports", key="daily/latest.csv"))
    success = ExecutionSummary(
        execution_arn=ARN + ":e1", name="e1", status=ExecutionStatus.SUCCEEDED, start_date=NOW, stop_date=NOW
    )
    state = PipelineExecutionState(pipeline=pipeline, latest_execution=success, latest_successful_execution=success)
    return pipeline, state


def test_stale_data_output_does_not_change_execution_health():
    pipeline, state = _fresh_run_with_s3_output()
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[pipeline]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("test_pipeline", ARN)]), \
         patch.object(handler, "collect_pipeline_state", return_value=state), \
         patch.object(handler, "get_last_modified", return_value=(NOW - timedelta(days=3), None)) as mock_s3, \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        handler.lambda_handler({}, None)

    item = written[0]
    mock_s3.assert_called_once()  # the S3 checker still runs when an output exists
    assert item["data_status"] == "stale"
    assert item["data_checked_location"] == "s3://exports/daily/latest.csv"
    assert item["execution_status"] == "fresh"


def test_unreadable_data_output_does_not_change_execution_health():
    pipeline, state = _fresh_run_with_s3_output()
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[pipeline]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("test_pipeline", ARN)]), \
         patch.object(handler, "collect_pipeline_state", return_value=state), \
         patch.object(handler, "get_last_modified", return_value=(None, "AccessDenied")), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        handler.lambda_handler({}, None)

    item = written[0]
    assert item["data_status"] == "unknown"
    assert item["execution_status"] == "fresh"


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
         patch.object(handler, "detect_trigger_detail", return_value=None), \
         patch.object(handler, "get_state_machine_tags", return_value={}), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        result = handler.lambda_handler({}, None)

    assert result["from_discovery"] == 1
    assert result["from_registry"] == 0
    assert len(written) == 1
    item = written[0]
    assert item["pipeline_name"] == "mystery_pipeline"
    assert item["source"] == "discovered"
    assert item["review_status"] == "needs_review"
    assert item["environment"] is None
    assert "no tags at all" in item["environment_reason"]
    assert item["execution_status"] == "never_run"
    assert item["data_status"] == "source_not_detected"
    assert item["detected_trigger"] == "Trigger not identified"
    assert item["detected_resources"] == []
    assert item["created_at"] == NOW.isoformat()
    assert item["state_machine_status"] is None
    assert item["expected_next_run"] is None
    assert "No trigger was identified" in item["next_run_reason"]


def test_discovered_machine_uses_real_environment_tag_when_present():
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("mystery_pipeline", DISCOVERED_ARN)]), \
         patch.object(handler, "collect_pipeline_state", side_effect=_never_run_state), \
         patch.object(handler, "describe_state_machine_details", return_value=None), \
         patch.object(handler, "detect_trigger_detail", return_value=None), \
         patch.object(handler, "get_state_machine_tags", return_value={"Environment": "prod"}), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        handler.lambda_handler({}, None)

    item = written[0]
    assert item["environment"] == "prod"
    assert item["environment_reason"] is None


def test_discovered_machine_with_unrelated_tags_names_them_in_the_reason():
    written = []
    with patch.object(handler, "TABLE_NAME", "test-table"), \
         patch.object(handler, "load_registry", return_value=[]), \
         patch.object(handler, "load_excluded_names", return_value=set()), \
         patch.object(handler, "list_all_state_machines", return_value=[_machine("mystery_pipeline", DISCOVERED_ARN)]), \
         patch.object(handler, "collect_pipeline_state", side_effect=_never_run_state), \
         patch.object(handler, "describe_state_machine_details", return_value=None), \
         patch.object(handler, "detect_trigger_detail", return_value=None), \
         patch.object(handler, "get_state_machine_tags", return_value={"aws:cloudformation:stack-name": "data-exporter"}), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        handler.lambda_handler({}, None)

    item = written[0]
    assert item["environment"] is None
    assert "aws:cloudformation:stack-name" in item["environment_reason"]
    assert "no tags at all" not in item["environment_reason"]


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
         patch.object(handler, "detect_trigger_detail", return_value={
             "kind": "eventbridge", "name": "r1", "resource_id": "arn:aws:events:us-east-1:1:rule/r1",
             "label": "Schedule detected: rate(1 day) (EventBridge rule r1)", "schedule_expression": "rate(1 day)",
         }), \
         patch.object(handler, "get_state_machine_tags", return_value={}), \
         patch.object(handler, "put_pipeline_status", side_effect=lambda table, item: written.append(item)):
        before = datetime.now(timezone.utc)
        result = handler.lambda_handler({}, None)
        after = datetime.now(timezone.utc)

    assert result["from_discovery"] == 1
    item = written[0]
    assert item["execution_status"] == "unknown"
    assert "Schedule detected" in item["execution_reason"]
    assert item["data_status"] == "source_detected_unavailable"
    assert "Lambda: my_fn" not in item["data_reason"]
    assert item["state_machine_status"] == "ACTIVE"
    # The real point of this test: a detected rate() schedule now produces
    # a computed Next Run, even though execution_status stayed "unknown"
    # (freshness classification is untouched - see schedule_parser.py).
    # handler.py's own `now` isn't injectable, so bracket it between two
    # real clock reads taken immediately around the call, rather than
    # asserting an exact timestamp.
    next_run = datetime.fromisoformat(item["expected_next_run"])
    assert before + timedelta(days=1) <= next_run <= after + timedelta(days=1)
    assert item["next_run_reason"] is None


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
         patch.object(handler, "detect_trigger_detail", return_value=None), \
         patch.object(handler, "get_state_machine_tags", return_value={}), \
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
