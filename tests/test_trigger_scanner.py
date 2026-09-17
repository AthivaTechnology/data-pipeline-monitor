import boto3
from botocore.stub import Stubber

from src import trigger_scanner

REGION = "us-east-1"
ARN = "arn:aws:states:us-east-1:382625484581:stateMachine:mystery_pipeline"


def _stub_events():
    client = boto3.client("events", region_name=REGION)
    stubber = Stubber(client)
    trigger_scanner._events_client_cache[REGION] = client
    return stubber


def _stub_scheduler():
    client = boto3.client("scheduler", region_name=REGION)
    stubber = Stubber(client)
    trigger_scanner._scheduler_client_cache[REGION] = client
    return stubber


def teardown_function(_):
    trigger_scanner._events_client_cache.clear()
    trigger_scanner._scheduler_client_cache.clear()


def test_detects_schedule_from_enabled_eventbridge_rule():
    events_stubber = _stub_events()
    events_stubber.add_response("list_rule_names_by_target", {"RuleNames": ["my-rule"]})
    events_stubber.add_response(
        "describe_rule",
        {"Name": "my-rule", "Arn": "arn:rule", "State": "ENABLED", "ScheduleExpression": "rate(1 day)"},
    )
    events_stubber.activate()

    label = trigger_scanner.detect_trigger(ARN, REGION)

    assert "Schedule detected: rate(1 day)" in label
    assert "my-rule" in label


def test_detects_event_pattern_rule_as_event_triggered():
    events_stubber = _stub_events()
    events_stubber.add_response("list_rule_names_by_target", {"RuleNames": ["my-rule"]})
    events_stubber.add_response(
        "describe_rule",
        {"Name": "my-rule", "Arn": "arn:rule", "State": "ENABLED", "EventPattern": '{"source": ["aws.s3"]}'},
    )
    events_stubber.activate()

    label = trigger_scanner.detect_trigger(ARN, REGION)

    assert label == "Event triggered (EventBridge rule my-rule)"


def test_ignores_disabled_rules():
    events_stubber = _stub_events()
    events_stubber.add_response("list_rule_names_by_target", {"RuleNames": ["my-rule"]})
    events_stubber.add_response(
        "describe_rule",
        {"Name": "my-rule", "Arn": "arn:rule", "State": "DISABLED", "ScheduleExpression": "rate(1 day)"},
    )
    events_stubber.activate()
    scheduler_stubber = _stub_scheduler()
    scheduler_stubber.add_response("list_schedules", {"Schedules": []})
    scheduler_stubber.activate()

    label = trigger_scanner.detect_trigger(ARN, REGION)

    assert label == "Trigger not identified"


def test_falls_back_to_eventbridge_scheduler():
    events_stubber = _stub_events()
    events_stubber.add_response("list_rule_names_by_target", {"RuleNames": []})
    events_stubber.activate()

    scheduler_stubber = _stub_scheduler()
    scheduler_stubber.add_response("list_schedules", {"Schedules": [{"Name": "my-schedule", "Arn": "arn:sched"}]})
    scheduler_stubber.add_response(
        "get_schedule",
        {
            "Name": "my-schedule",
            "ScheduleExpression": "cron(0 6 * * ? *)",
            "State": "ENABLED",
            "Target": {"Arn": ARN, "RoleArn": "arn:aws:iam::382625484581:role/scheduler-role"},
        },
    )
    scheduler_stubber.activate()

    label = trigger_scanner.detect_trigger(ARN, REGION)

    assert "Schedule detected: cron(0 6 * * ? *)" in label
    assert "my-schedule" in label


def test_returns_not_identified_when_nothing_found_anywhere():
    events_stubber = _stub_events()
    events_stubber.add_response("list_rule_names_by_target", {"RuleNames": []})
    events_stubber.activate()

    scheduler_stubber = _stub_scheduler()
    scheduler_stubber.add_response("list_schedules", {"Schedules": []})
    scheduler_stubber.activate()

    label = trigger_scanner.detect_trigger(ARN, REGION)

    assert label == "Trigger not identified"


def test_detect_trigger_detail_returns_structured_payload_for_lineage():
    events_stubber = _stub_events()
    events_stubber.add_response("list_rule_names_by_target", {"RuleNames": ["my-rule"]})
    events_stubber.add_response(
        "describe_rule",
        {"Name": "my-rule", "Arn": "arn:aws:events:us-east-1:1:rule/my-rule", "State": "ENABLED", "ScheduleExpression": "rate(1 day)"},
    )
    events_stubber.activate()

    detail = trigger_scanner.detect_trigger_detail(ARN, REGION)

    assert detail["kind"] == "eventbridge"
    assert detail["name"] == "my-rule"
    assert detail["resource_id"] == "arn:aws:events:us-east-1:1:rule/my-rule"
    assert "Schedule detected: rate(1 day)" in detail["label"]


def test_detect_trigger_detail_returns_none_when_nothing_found():
    events_stubber = _stub_events()
    events_stubber.add_response("list_rule_names_by_target", {"RuleNames": []})
    events_stubber.activate()
    scheduler_stubber = _stub_scheduler()
    scheduler_stubber.add_response("list_schedules", {"Schedules": []})
    scheduler_stubber.activate()

    assert trigger_scanner.detect_trigger_detail(ARN, REGION) is None


def test_aws_failure_is_never_raised():
    events_stubber = _stub_events()
    events_stubber.add_client_error("list_rule_names_by_target", service_error_code="AccessDeniedException")
    events_stubber.activate()

    scheduler_stubber = _stub_scheduler()
    scheduler_stubber.add_client_error("list_schedules", service_error_code="AccessDeniedException")
    scheduler_stubber.activate()

    label = trigger_scanner.detect_trigger(ARN, REGION)

    assert label == "Trigger not identified"
