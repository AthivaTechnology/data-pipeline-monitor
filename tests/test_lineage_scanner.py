import json

from src.lineage_models import Confidence
from src.lineage_scanner import build_pipeline_graph

ARN = "arn:aws:states:us-east-1:1:stateMachine:my_pipeline"
REGION = "us-east-1"


def _ids(items):
    return {i.resource_id if hasattr(i, "resource_id") else i["resource_id"] for i in items}


def test_no_definition_yields_only_the_state_machine_node():
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition=None, trigger_detail=None)
    assert [n.resource_type for n in graph.nodes] == ["step_function"]
    assert graph.edges == []


def test_trigger_detail_adds_eventbridge_node_and_edge():
    trigger = {"kind": "eventbridge", "name": "my-rule", "resource_id": "arn:aws:events:us-east-1:1:rule/my-rule", "label": "Schedule detected: rate(1 day) (EventBridge rule my-rule)"}
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition=None, trigger_detail=trigger)

    assert {n.resource_type for n in graph.nodes} == {"step_function", "eventbridge"}
    assert len(graph.edges) == 1
    assert graph.edges[0].source_id == "arn:aws:events:us-east-1:1:rule/my-rule"
    assert graph.edges[0].target_id == ARN
    assert graph.edges[0].confidence == Confidence.DIRECT.value


def test_simple_task_chain_produces_ordered_edges():
    definition = json.dumps({
        "StartAt": "Extract",
        "States": {
            "Extract": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:extract_fn", "Next": "Load"},
            "Load": {"Type": "Task", "Resource": "arn:aws:s3:::my-bucket/out.csv", "End": True},
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    node_ids = _ids(graph.nodes)
    assert "arn:aws:lambda:us-east-1:1:function:extract_fn" in node_ids
    assert "arn:aws:s3:::my-bucket/out.csv" in node_ids

    edge_pairs = {(e.source_id, e.target_id) for e in graph.edges}
    assert (ARN, "arn:aws:lambda:us-east-1:1:function:extract_fn") in edge_pairs
    assert ("arn:aws:lambda:us-east-1:1:function:extract_fn", "arn:aws:s3:::my-bucket/out.csv") in edge_pairs


def test_non_task_states_do_not_break_the_chain():
    # Wait/Pass between two Tasks must not fragment the lineage - the most
    # recent Task node should carry through to the next real Task.
    definition = json.dumps({
        "StartAt": "Extract",
        "States": {
            "Extract": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:extract_fn", "Next": "Pause"},
            "Pause": {"Type": "Wait", "Seconds": 5, "Next": "Notify"},
            "Notify": {"Type": "Task", "Resource": "arn:aws:sns:us-east-1:1:my-topic", "End": True},
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    edge_pairs = {(e.source_id, e.target_id) for e in graph.edges}
    assert ("arn:aws:lambda:us-east-1:1:function:extract_fn", "arn:aws:sns:us-east-1:1:my-topic") in edge_pairs
    # No node/edge was fabricated for the Wait state itself.
    assert not any("Pause" in n.resource_id for n in graph.nodes)


def test_choice_branches_both_produce_edges_from_the_same_predecessor():
    definition = json.dumps({
        "StartAt": "Extract",
        "States": {
            "Extract": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:extract_fn", "Next": "Route"},
            "Route": {
                "Type": "Choice",
                "Choices": [{"Variable": "$.ok", "BooleanEquals": True, "Next": "Success"}],
                "Default": "Failure",
            },
            "Success": {"Type": "Task", "Resource": "arn:aws:s3:::bucket/success.csv", "End": True},
            "Failure": {"Type": "Task", "Resource": "arn:aws:sns:us-east-1:1:alert-topic", "End": True},
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    edge_pairs = {(e.source_id, e.target_id) for e in graph.edges}
    assert ("arn:aws:lambda:us-east-1:1:function:extract_fn", "arn:aws:s3:::bucket/success.csv") in edge_pairs
    assert ("arn:aws:lambda:us-east-1:1:function:extract_fn", "arn:aws:sns:us-east-1:1:alert-topic") in edge_pairs


def test_parallel_branches_are_walked():
    definition = json.dumps({
        "StartAt": "FanOut",
        "States": {
            "FanOut": {
                "Type": "Parallel",
                "Branches": [
                    {"StartAt": "A", "States": {"A": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:fn_a", "End": True}}},
                    {"StartAt": "B", "States": {"B": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:fn_b", "End": True}}},
                ],
                "End": True,
            },
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    node_ids = _ids(graph.nodes)
    assert "arn:aws:lambda:us-east-1:1:function:fn_a" in node_ids
    assert "arn:aws:lambda:us-east-1:1:function:fn_b" in node_ids


def test_map_iterator_is_walked():
    definition = json.dumps({
        "StartAt": "ProcessItems",
        "States": {
            "ProcessItems": {
                "Type": "Map",
                "ItemProcessor": {
                    "StartAt": "Handle",
                    "States": {"Handle": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:handle_item", "End": True}},
                },
                "End": True,
            },
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    assert "arn:aws:lambda:us-east-1:1:function:handle_item" in _ids(graph.nodes)


def test_unclassifiable_task_resource_is_dropped_not_guessed():
    definition = json.dumps({
        "StartAt": "RunActivity",
        "States": {"RunActivity": {"Type": "Task", "Resource": "arn:aws:states:us-east-1:1:activity:my-activity", "End": True}},
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    assert [n.resource_type for n in graph.nodes] == ["step_function"]
    assert graph.edges == []


def test_malformed_json_definition_degrades_gracefully():
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition="not valid json {", trigger_detail=None)
    assert [n.resource_type for n in graph.nodes] == ["step_function"]
    assert graph.edges == []


def test_missing_start_at_degrades_gracefully():
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition=json.dumps({"States": {}}), trigger_detail=None)
    assert [n.resource_type for n in graph.nodes] == ["step_function"]


def test_cyclic_definition_does_not_hang():
    # Malformed/unusual ASL where two states point back at each other -
    # must terminate via the visited-set guard, not recurse forever.
    definition = json.dumps({
        "StartAt": "A",
        "States": {
            "A": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:fn_a", "Next": "B"},
            "B": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:fn_b", "Next": "A"},
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)
    assert "arn:aws:lambda:us-east-1:1:function:fn_a" in _ids(graph.nodes)
    assert "arn:aws:lambda:us-east-1:1:function:fn_b" in _ids(graph.nodes)


def test_two_unresolved_service_integration_calls_do_not_collapse_into_one_node():
    # Regression test: arn:aws:states:::lambda:invoke is a constant string
    # AWS reuses for every optimized-integration Lambda call - two distinct
    # Tasks using it (with no FunctionName in Parameters to disambiguate)
    # must still produce two distinct nodes, not one fake shared node.
    definition = json.dumps({
        "StartAt": "First",
        "States": {
            "First": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke", "Next": "Second"},
            "Second": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke", "End": True},
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    lambda_nodes = [n for n in graph.nodes if n.resource_type == "lambda"]
    assert len(lambda_nodes) == 2
    assert len({n.resource_id for n in lambda_nodes}) == 2
    # No edge should point from a node back to itself.
    assert all(e.source_id != e.target_id for e in graph.edges)


def test_unresolved_service_integration_in_different_pipelines_does_not_falsely_merge():
    definition = json.dumps({
        "StartAt": "Notify",
        "States": {"Notify": {"Type": "Task", "Resource": "arn:aws:states:::sns:publish", "End": True}},
    })
    graph_a = build_pipeline_graph("pipeline_a", ARN, REGION, definition, trigger_detail=None)
    graph_b = build_pipeline_graph("pipeline_b", "arn:aws:states:us-east-1:1:stateMachine:pipeline_b", REGION, definition, trigger_detail=None)

    id_a = next(n.resource_id for n in graph_a.nodes if n.resource_type == "sns")
    id_b = next(n.resource_id for n in graph_b.nodes if n.resource_type == "sns")
    assert id_a != id_b


def test_pass_state_does_not_break_the_chain():
    definition = json.dumps({
        "StartAt": "Extract",
        "States": {
            "Extract": {"Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:extract_fn", "Next": "Reformat"},
            "Reformat": {"Type": "Pass", "Result": {"ok": True}, "Next": "Load"},
            "Load": {"Type": "Task", "Resource": "arn:aws:s3:::bucket/out.csv", "End": True},
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    edge_pairs = {(e.source_id, e.target_id) for e in graph.edges}
    assert ("arn:aws:lambda:us-east-1:1:function:extract_fn", "arn:aws:s3:::bucket/out.csv") in edge_pairs


def test_retry_configuration_does_not_affect_the_walk():
    # Retry has no `Next` of its own - it's retry policy on the same state,
    # not a control-flow edge. Must not error, must not add a phantom node.
    definition = json.dumps({
        "StartAt": "Extract",
        "States": {
            "Extract": {
                "Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:extract_fn",
                "Retry": [{"ErrorEquals": ["States.ALL"], "IntervalSeconds": 5, "MaxAttempts": 3, "BackoffRate": 2.0}],
                "End": True,
            },
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    assert len(graph.nodes) == 2  # step_function + the one Lambda
    assert len(graph.edges) == 1


def test_catch_targets_are_not_currently_walked_known_gap():
    # Documents a real, known limitation (see README "Lineage & Catalog"
    # Limitations): a Task's error-handling path (Catch -> Next) is not
    # walked, so a resource only reachable via a failure branch (e.g. an
    # SNS alert-on-error topic) is invisible to this scanner. This test
    # exists so that gap is tracked, not accidentally "fixed" into a
    # different silent behavior later without a conscious decision.
    definition = json.dumps({
        "StartAt": "Extract",
        "States": {
            "Extract": {
                "Type": "Task", "Resource": "arn:aws:lambda:us-east-1:1:function:extract_fn",
                "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "AlertOnFailure"}],
                "End": True,
            },
            "AlertOnFailure": {"Type": "Task", "Resource": "arn:aws:sns:us-east-1:1:alert-topic", "End": True},
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    node_ids = _ids(graph.nodes)
    assert "arn:aws:lambda:us-east-1:1:function:extract_fn" in node_ids
    # Not currently discovered - see docstring above. Update this assertion
    # deliberately (not silently) if Catch-walking is ever implemented.
    assert "arn:aws:sns:us-east-1:1:alert-topic" not in node_ids


def test_service_integration_with_parameters_recovers_name():
    definition = json.dumps({
        "StartAt": "Notify",
        "States": {
            "Notify": {
                "Type": "Task",
                "Resource": "arn:aws:states:::sns:publish",
                "Parameters": {"TopicArn": "arn:aws:sns:us-east-1:1:real-topic"},
                "End": True,
            },
        },
    })
    graph = build_pipeline_graph("my_pipeline", ARN, REGION, definition, trigger_detail=None)

    sns_nodes = [n for n in graph.nodes if n.resource_type == "sns"]
    assert len(sns_nodes) == 1
    assert sns_nodes[0].display_name == "arn:aws:sns:us-east-1:1:real-topic"
