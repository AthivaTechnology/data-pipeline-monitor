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
