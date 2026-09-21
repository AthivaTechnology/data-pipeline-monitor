import boto3
from botocore.stub import Stubber

from src import discovery

REGION = "us-east-1"


def _stub_client():
    client = boto3.client("stepfunctions", region_name=REGION)
    stubber = Stubber(client)
    discovery._client_cache[REGION] = client
    return client, stubber


def teardown_function(_):
    discovery._client_cache.clear()


def test_list_all_state_machines_walks_every_page():
    client, stubber = _stub_client()
    stubber.add_response(
        "list_state_machines",
        {
            "stateMachines": [
                {"name": "a", "stateMachineArn": "arn:a", "creationDate": "2026-01-01T00:00:00Z", "type": "STANDARD"}
            ],
            "nextToken": "page2",
        },
    )
    stubber.add_response(
        "list_state_machines",
        {
            "stateMachines": [
                {"name": "b", "stateMachineArn": "arn:b", "creationDate": "2026-01-02T00:00:00Z", "type": "STANDARD"}
            ]
        },
    )
    stubber.activate()

    machines = discovery.list_all_state_machines(REGION)

    assert [m["name"] for m in machines] == ["a", "b"]
    assert machines[0]["arn"] == "arn:a"


def test_list_all_state_machines_returns_empty_list_on_aws_failure():
    client, stubber = _stub_client()
    stubber.add_client_error("list_state_machines", service_error_code="AccessDeniedException")
    stubber.activate()

    machines = discovery.list_all_state_machines(REGION)

    assert machines == []


def test_describe_state_machine_details_returns_none_on_failure():
    client, stubber = _stub_client()
    stubber.add_client_error("describe_state_machine", service_error_code="StateMachineDoesNotExist")
    stubber.activate()

    details = discovery.describe_state_machine_details("arn:missing", REGION)

    assert details is None


def test_describe_state_machine_details_returns_definition_and_status():
    client, stubber = _stub_client()
    stubber.add_response(
        "describe_state_machine",
        {
            "stateMachineArn": "arn:a",
            "name": "a",
            "definition": '{"States": {}}',
            "roleArn": "arn:aws:iam::123:role/x",
            "type": "STANDARD",
            "status": "ACTIVE",
            "creationDate": "2026-01-01T00:00:00Z",
        },
    )
    stubber.activate()

    details = discovery.describe_state_machine_details("arn:a", REGION)

    assert details == {"definition": '{"States": {}}', "status": "ACTIVE"}


def test_get_state_machine_tags_returns_key_value_dict():
    client, stubber = _stub_client()
    stubber.add_response(
        "list_tags_for_resource",
        {"tags": [{"key": "Environment", "value": "prod"}, {"key": "aws:cloudformation:stack-name", "value": "data-exporter"}]},
        {"resourceArn": "arn:a"},
    )
    stubber.activate()

    tags = discovery.get_state_machine_tags("arn:a", REGION)

    assert tags == {"Environment": "prod", "aws:cloudformation:stack-name": "data-exporter"}


def test_get_state_machine_tags_returns_empty_dict_when_untagged():
    client, stubber = _stub_client()
    stubber.add_response("list_tags_for_resource", {"tags": []}, {"resourceArn": "arn:a"})
    stubber.activate()

    tags = discovery.get_state_machine_tags("arn:a", REGION)

    assert tags == {}


def test_get_state_machine_tags_returns_empty_dict_on_aws_failure():
    client, stubber = _stub_client()
    stubber.add_client_error("list_tags_for_resource", service_error_code="StateMachineDoesNotExist")
    stubber.activate()

    tags = discovery.get_state_machine_tags("arn:missing", REGION)

    assert tags == {}
