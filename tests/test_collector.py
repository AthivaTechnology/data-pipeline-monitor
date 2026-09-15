from datetime import datetime, timezone

import boto3
from botocore.stub import Stubber

from src import collector
from src.models import ExecutionStatus, OutputConfig, PipelineConfig, ScheduleConfig

REGION = "us-east-1"
ARN = "arn:aws:states:us-east-1:382625484581:stateMachine:test_pipeline"


def _pipeline(**overrides) -> PipelineConfig:
    defaults = dict(
        name="test_pipeline",
        state_machine_arn=ARN,
        region=REGION,
        environment="prod",
        monitoring_enabled=True,
        alerting_enabled=False,
        owner=None,
        schedule=ScheduleConfig(type="daily", cron_utc="0 0 * * ? *"),
        grace_period_minutes=60,
        output=None,
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def _stub_client():
    client = boto3.client("stepfunctions", region_name=REGION)
    stubber = Stubber(client)
    collector._client_cache[REGION] = client
    return client, stubber


def _execution(name, status, start, stop=None):
    return {
        "executionArn": f"{ARN}:{name}",
        "stateMachineArn": ARN,
        "name": name,
        "status": status,
        "startDate": start,
        "stopDate": stop or start,
    }


def teardown_function(_):
    collector._client_cache.clear()


def test_never_run_pipeline():
    client, stubber = _stub_client()
    stubber.add_response("list_executions", {"executions": []})
    stubber.activate()

    state = collector.collect_pipeline_state(_pipeline())

    assert state.collector_error is None
    assert state.latest_execution is None
    assert state.latest_successful_execution is None
    assert state.recent_executions == []


def test_latest_execution_is_the_success_itself():
    client, stubber = _stub_client()
    start = datetime(2026, 9, 14, 16, 46, tzinfo=timezone.utc)
    execution = _execution("exec-1", "SUCCEEDED", start)
    stubber.add_response("list_executions", {"executions": [execution]})
    stubber.activate()

    state = collector.collect_pipeline_state(_pipeline())

    assert state.collector_error is None
    assert state.latest_execution.status == ExecutionStatus.SUCCEEDED
    assert state.latest_successful_execution.execution_arn == execution["executionArn"]
    assert [e.execution_arn for e in state.recent_executions] == [execution["executionArn"]]


def test_latest_execution_failed_but_prior_success_found():
    client, stubber = _stub_client()
    fail_start = datetime(2026, 9, 14, 16, 46, tzinfo=timezone.utc)
    success_start = datetime(2026, 9, 13, 16, 46, tzinfo=timezone.utc)

    failed_execution = _execution("exec-2", "FAILED", fail_start)
    success_execution = _execution("exec-1", "SUCCEEDED", success_start)

    stubber.add_response(
        "list_executions", {"executions": [failed_execution, success_execution]}
    )
    stubber.add_response(
        "describe_execution",
        {
            "executionArn": failed_execution["executionArn"],
            "stateMachineArn": ARN,
            "name": "exec-2",
            "status": "FAILED",
            "startDate": fail_start,
            "stopDate": fail_start,
            "input": "{}",
            "error": "States.TaskFailed",
            "cause": "Lambda threw an exception",
        },
        {"executionArn": failed_execution["executionArn"]},
    )
    stubber.activate()

    state = collector.collect_pipeline_state(_pipeline())

    assert state.latest_execution.status == ExecutionStatus.FAILED
    assert state.latest_execution.error == "States.TaskFailed"
    assert state.latest_successful_execution.execution_arn == success_execution["executionArn"]
    # recent_executions[0] must carry the same enriched error/cause as
    # latest_execution, not the bare pre-describe_execution summary.
    assert state.recent_executions[0].error == "States.TaskFailed"
    assert len(state.recent_executions) == 2


def test_collector_error_is_captured_not_raised():
    # Non-retryable error code so the stub only needs one queued response -
    # retry/backoff behavior itself is exercised separately below.
    client, stubber = _stub_client()
    stubber.add_client_error("list_executions", service_error_code="ValidationException")
    stubber.activate()

    state = collector.collect_pipeline_state(_pipeline())

    assert state.collector_error is not None
    assert state.latest_execution is None
    assert state.latest_successful_execution is None


def test_pagination_walks_multiple_pages_looking_for_success():
    """First page has no SUCCEEDED execution; the collector must fetch the
    next page rather than giving up after page 1."""
    client, stubber = _stub_client()
    old = datetime(2026, 9, 1, tzinfo=timezone.utc)
    older = datetime(2026, 8, 1, tzinfo=timezone.utc)

    page1 = [_execution(f"fail-{i}", "FAILED", old) for i in range(3)]
    page2_success = _execution("exec-success", "SUCCEEDED", older)

    # Stubber consumes responses strictly in call order. The collector's loop
    # finishes ALL pagination (list_executions x2) before it ever enriches
    # the latest execution with describe_execution, so that must be queued
    # last, not interleaved with the list_executions calls.
    stubber.add_response(
        "list_executions", {"executions": page1, "nextToken": "page2"}
    )
    stubber.add_response("list_executions", {"executions": [page2_success]})
    stubber.add_response(
        "describe_execution",
        {
            "executionArn": page1[0]["executionArn"],
            "stateMachineArn": ARN,
            "name": page1[0]["name"],
            "status": "FAILED",
            "startDate": old,
            "stopDate": old,
            "input": "{}",
        },
        {"executionArn": page1[0]["executionArn"]},
    )
    stubber.activate()

    state = collector.collect_pipeline_state(_pipeline())

    assert state.collector_error is None
    assert state.latest_execution.status == ExecutionStatus.FAILED
    assert state.latest_successful_execution.execution_arn == page2_success["executionArn"]
    # Pulled from both pages: 3 from page1 + 1 from page2 = 4 (RECENT_EXECUTIONS_LIMIT is 5)
    assert len(state.recent_executions) == 4


def test_retry_config_is_applied_to_the_client():
    """Confirms the retry policy handler.py relies on is actually wired onto
    the client _get_client() builds, not just declared and forgotten.

    Note: botocore.stub.Stubber intercepts before its own retry loop runs, so
    it can't be used to prove retries physically happen (a queued error is
    surfaced immediately, not retried) - that would need a real/mocked HTTP
    layer, which is out of scope here. This test instead verifies the one
    thing that actually matters for our own code: the Config we pass into
    boto3.client(...) is the Config the client ends up using.
    """
    collector._client_cache.clear()
    client = collector._get_client(REGION)

    retries_config = client.meta.config.retries
    # botocore normalizes our {"max_attempts": 5} into total_max_attempts=6
    # (1 initial call + 5 retries) - this asserts on its actual normalized
    # form rather than our input, so it fails loudly if that mapping ever changes.
    assert retries_config["total_max_attempts"] == 6
    assert retries_config["mode"] == "adaptive"
