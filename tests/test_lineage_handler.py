from unittest.mock import patch

from src import lineage_handler
from src.lineage_models import LineageEdge, LineageNode, PipelineLineage

ARN_A = "arn:aws:states:us-east-1:1:stateMachine:pipeline_a"
ARN_B = "arn:aws:states:us-east-1:1:stateMachine:pipeline_b"


def _machine(name, arn):
    return {"name": name, "arn": arn, "creation_date": None}


def _graph_with_one_edge(name, arn):
    sm = LineageNode(resource_type="step_function", resource_id=arn, display_name=name)
    lam = LineageNode(resource_type="lambda", resource_id=f"arn:aws:lambda:us-east-1:1:function:{name}_fn", display_name=f"{name}_fn")
    edge = LineageEdge(source_id=arn, target_id=lam.resource_id, relationship_source="test", confidence="direct")
    return PipelineLineage(pipeline_name=name, state_machine_arn=arn, region="us-east-1", nodes=[sm, lam], edges=[edge])


def test_happy_path_writes_one_lineage_item_per_pipeline_and_a_summary():
    written_items = []
    written_summaries = []

    with patch.object(lineage_handler, "TABLE_NAME", "test-table"), \
         patch.object(lineage_handler, "load_excluded_names", return_value=set()), \
         patch.object(lineage_handler, "list_all_state_machines", return_value=[_machine("pipeline_a", ARN_A)]), \
         patch.object(lineage_handler, "describe_state_machine_details", return_value={"definition": "{}", "status": "ACTIVE"}), \
         patch.object(lineage_handler, "detect_trigger_detail", return_value=None), \
         patch.object(lineage_handler, "build_pipeline_graph", return_value=_graph_with_one_edge("pipeline_a", ARN_A)), \
         patch.object(lineage_handler, "put_pipeline_lineage", side_effect=lambda table, item: written_items.append(item)), \
         patch.object(lineage_handler, "put_lineage_summary", side_effect=lambda table, item: written_summaries.append(item)):
        result = lineage_handler.lambda_handler({}, None)

    assert result == {"total": 1, "succeeded": 1, "failed": 0}
    assert len(written_items) == 1
    item = written_items[0]
    assert item["pipeline_name"] == "LINEAGE#PIPELINE#pipeline_a"
    assert item["source_pipeline_name"] == "pipeline_a"
    assert len(item["nodes"]) == 2
    assert len(item["edges"]) == 1

    assert len(written_summaries) == 1
    summary = written_summaries[0]
    assert summary["total_pipelines_scanned"] == 1
    assert summary["total_resources"] == 2
    assert summary["total_relationships"] == 1
    assert summary["last_discovery_at"] is not None


def test_excluded_names_are_skipped_entirely():
    written = []
    with patch.object(lineage_handler, "TABLE_NAME", "test-table"), \
         patch.object(lineage_handler, "load_excluded_names", return_value={"ignore_me"}), \
         patch.object(lineage_handler, "list_all_state_machines", return_value=[_machine("ignore_me", ARN_A)]), \
         patch.object(lineage_handler, "put_pipeline_lineage", side_effect=lambda table, item: written.append(item)), \
         patch.object(lineage_handler, "put_lineage_summary"):
        result = lineage_handler.lambda_handler({}, None)

    assert result["total"] == 0
    assert written == []


def test_one_bad_pipeline_does_not_stop_the_others():
    def fake_build_graph(name, arn, region, definition, trigger_detail):
        if name == "bad_pipeline":
            raise RuntimeError("boom")
        return _graph_with_one_edge(name, arn)

    written = []
    with patch.object(lineage_handler, "TABLE_NAME", "test-table"), \
         patch.object(lineage_handler, "load_excluded_names", return_value=set()), \
         patch.object(
             lineage_handler, "list_all_state_machines",
             return_value=[_machine("bad_pipeline", ARN_A), _machine("good_pipeline", ARN_B)],
         ), \
         patch.object(lineage_handler, "describe_state_machine_details", return_value=None), \
         patch.object(lineage_handler, "detect_trigger_detail", return_value=None), \
         patch.object(lineage_handler, "build_pipeline_graph", side_effect=fake_build_graph), \
         patch.object(lineage_handler, "put_pipeline_lineage", side_effect=lambda table, item: written.append(item)), \
         patch.object(lineage_handler, "put_lineage_summary"):
        result = lineage_handler.lambda_handler({}, None)

    assert result == {"total": 2, "succeeded": 1, "failed": 1}
    assert len(written) == 1
    assert written[0]["source_pipeline_name"] == "good_pipeline"


def test_missing_table_name_raises_clearly():
    with patch.object(lineage_handler, "TABLE_NAME", ""):
        try:
            lineage_handler.lambda_handler({}, None)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "STATUS_TABLE_NAME" in str(exc)
