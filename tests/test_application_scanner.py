import boto3
from botocore.stub import Stubber

from src import application_scanner, discovery, trigger_scanner
from src.application_registry import ApplicationConfig

REGION = "us-east-1"
SM_ARN = "arn:aws:states:us-east-1:111122223333:stateMachine:audit_load_batch"


def _summary(logical_id, resource_type, physical_id):
    return {
        "LogicalResourceId": logical_id,
        "ResourceType": resource_type,
        "PhysicalResourceId": physical_id,
        "LastUpdatedTimestamp": "2026-01-01T00:00:00Z",
        "ResourceStatus": "CREATE_COMPLETE",
    }


def _stub(client_key, service_name):
    client = boto3.client(service_name, region_name=REGION)
    stubber = Stubber(client)
    client_key[REGION] = client
    return stubber


def _stub_cfn():
    return _stub(application_scanner._client_cache["cloudformation"], "cloudformation")


def _stub_events():
    return _stub(application_scanner._client_cache["events"], "events")


def _stub_stepfunctions():
    return _stub(discovery._client_cache, "stepfunctions")


def _stub_trigger_events():
    return _stub(trigger_scanner._events_client_cache, "events")


def _stub_no_trigger_found():
    """Every build_application_graph test walks a Step Function through
    lineage_scanner, which always calls trigger_scanner.detect_trigger_detail
    - stub both of its checks (EventBridge Rules, then Scheduler) to "found
    nothing", the common case for these tests.
    """
    eb_stubber = _stub_trigger_events()
    eb_stubber.add_response("list_rule_names_by_target", {"RuleNames": []}, {"TargetArn": SM_ARN})
    eb_stubber.activate()

    sched_client = boto3.client("scheduler", region_name=REGION)
    sched_stubber = Stubber(sched_client)
    sched_stubber.add_response("list_schedules", {"Schedules": []})
    sched_stubber.activate()
    trigger_scanner._scheduler_client_cache[REGION] = sched_client


def _stub_state_machine_not_found():
    sf_stubber = _stub_stepfunctions()
    sf_stubber.add_client_error("describe_state_machine", service_error_code="StateMachineDoesNotExist")
    sf_stubber.activate()


def teardown_function(_):
    application_scanner._client_cache["cloudformation"].clear()
    application_scanner._client_cache["events"].clear()
    discovery._client_cache.clear()
    trigger_scanner._events_client_cache.clear()
    trigger_scanner._scheduler_client_cache.clear()


def _app():
    return ApplicationConfig(id="data-exporter", stack_name="data-exporter", display_name="Data Exporter")


def test_list_stack_resources_walks_every_page():
    stubber = _stub_cfn()
    stubber.add_response(
        "list_stack_resources",
        {"StackResourceSummaries": [_summary("A", "AWS::Lambda::Function", "fn-a")], "NextToken": "page2"},
        {"StackName": "data-exporter"},
    )
    stubber.add_response(
        "list_stack_resources",
        {"StackResourceSummaries": [_summary("B", "AWS::Lambda::Function", "fn-b")]},
        {"StackName": "data-exporter", "NextToken": "page2"},
    )
    stubber.activate()

    resources = application_scanner.list_stack_resources("data-exporter", REGION)

    assert [r["logical_id"] for r in resources] == ["A", "B"]


def test_list_stack_resources_returns_empty_on_aws_failure():
    stubber = _stub_cfn()
    stubber.add_client_error("list_stack_resources", service_error_code="AccessDeniedException")
    stubber.activate()

    assert application_scanner.list_stack_resources("data-exporter", REGION) == []


def test_no_step_function_in_stack_skips_non_state_machine_resources():
    # account_id can only be derived from a Step Function's own ARN - with
    # none present, other resource types must be skipped, never guessed.
    stubber = _stub_cfn()
    stubber.add_response(
        "list_stack_resources",
        {"StackResourceSummaries": [_summary("Fn", "AWS::Lambda::Function", "orphan-fn")]},
        {"StackName": "data-exporter"},
    )
    stubber.activate()

    graph = application_scanner.build_application_graph(_app(), REGION)

    assert graph.nodes == []
    assert graph.edges == []


def test_unmapped_resource_type_is_skipped_not_guessed():
    stubber = _stub_cfn()
    stubber.add_response(
        "list_stack_resources",
        {"StackResourceSummaries": [
            _summary("SM", "AWS::StepFunctions::StateMachine", SM_ARN),
            _summary("Bucket", "AWS::S3::Bucket", "some-bucket"),
        ]},
        {"StackName": "data-exporter"},
    )
    stubber.activate()
    _stub_state_machine_not_found()
    _stub_no_trigger_found()

    graph = application_scanner.build_application_graph(_app(), REGION)

    assert {n.resource_type for n in graph.nodes} == {"step_function"}


def test_cloudwatch_alarm_and_iam_role_inventoried_unconnected():
    stubber = _stub_cfn()
    stubber.add_response(
        "list_stack_resources",
        {"StackResourceSummaries": [
            _summary("SM", "AWS::StepFunctions::StateMachine", SM_ARN),
            _summary("Alarm", "AWS::CloudWatch::Alarm", "my-alarm"),
            _summary("Role", "AWS::IAM::Role", "my-role"),
        ]},
        {"StackName": "data-exporter"},
    )
    stubber.activate()
    _stub_state_machine_not_found()
    _stub_no_trigger_found()

    graph = application_scanner.build_application_graph(_app(), REGION)

    node_ids = {n.resource_id for n in graph.nodes}
    assert "arn:aws:cloudwatch:us-east-1:111122223333:alarm:my-alarm" in node_ids
    assert "arn:aws:iam::111122223333:role/my-role" in node_ids
    # Neither participates in any edge - no relationship can be confirmed.
    connected = {e.source_id for e in graph.edges} | {e.target_id for e in graph.edges}
    assert "arn:aws:cloudwatch:us-east-1:111122223333:alarm:my-alarm" not in connected
    assert "arn:aws:iam::111122223333:role/my-role" not in connected


def test_eventbridge_rule_targeting_lambda_directly_produces_invokes_edge():
    lambda_arn = "arn:aws:lambda:us-east-1:111122223333:function:standalone-fn"
    stubber = _stub_cfn()
    stubber.add_response(
        "list_stack_resources",
        {"StackResourceSummaries": [
            _summary("SM", "AWS::StepFunctions::StateMachine", SM_ARN),
            _summary("Fn", "AWS::Lambda::Function", "standalone-fn"),
            _summary("Rule", "AWS::Events::Rule", "direct-rule"),
        ]},
        {"StackName": "data-exporter"},
    )
    stubber.activate()
    _stub_state_machine_not_found()
    _stub_no_trigger_found()

    targets_stubber = _stub_events()
    targets_stubber.add_response(
        "list_targets_by_rule",
        {"Targets": [{"Id": "1", "Arn": lambda_arn}]},
        {"Rule": "direct-rule"},
    )
    targets_stubber.activate()

    graph = application_scanner.build_application_graph(_app(), REGION)

    edge_pairs = {(e.source_id, e.target_id, e.relationship_type) for e in graph.edges}
    rule_arn = "arn:aws:events:us-east-1:111122223333:rule/direct-rule"
    assert (rule_arn, lambda_arn, "invokes") in edge_pairs


def test_rule_targets_lookup_failure_produces_no_edge_not_a_crash():
    stubber = _stub_cfn()
    stubber.add_response(
        "list_stack_resources",
        {"StackResourceSummaries": [
            _summary("SM", "AWS::StepFunctions::StateMachine", SM_ARN),
            _summary("Rule", "AWS::Events::Rule", "broken-rule"),
        ]},
        {"StackName": "data-exporter"},
    )
    stubber.activate()
    _stub_state_machine_not_found()
    _stub_no_trigger_found()

    targets_stubber = _stub_events()
    targets_stubber.add_client_error("list_targets_by_rule", service_error_code="AccessDeniedException")
    targets_stubber.activate()

    graph = application_scanner.build_application_graph(_app(), REGION)

    assert graph.edges == []
