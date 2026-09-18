from unittest.mock import patch

from src import application_handler
from src.application_registry import ApplicationConfig
from src.lineage_models import LineageEdge, LineageNode, PipelineLineage

SM_ARN = "arn:aws:states:us-east-1:1:stateMachine:data_exporter_pipeline"
LAMBDA_ARN = "arn:aws:lambda:us-east-1:1:function:data_exporter_fn"


def _app(app_id="data-exporter"):
    return ApplicationConfig(id=app_id, stack_name=app_id, display_name="Data Exporter")


def _graph_with_one_edge():
    sm = LineageNode(resource_type="step_function", resource_id=SM_ARN, display_name="data_exporter_pipeline")
    lam = LineageNode(resource_type="lambda", resource_id=LAMBDA_ARN, display_name="data_exporter_fn")
    edge = LineageEdge(
        source_id=SM_ARN, target_id=LAMBDA_ARN, relationship_source="test", confidence="direct",
        relationship_type="invokes",
    )
    return PipelineLineage(pipeline_name="data-exporter", state_machine_arn=SM_ARN, region="us-east-1", nodes=[sm, lam], edges=[edge])


def test_happy_path_writes_one_resource_item_per_node_and_a_summary():
    written_resources = []
    written_summaries = []

    with patch.object(application_handler, "TABLE_NAME", "test-table"), \
         patch.object(application_handler, "load_applications", return_value=[_app()]), \
         patch.object(application_handler, "build_application_graph", return_value=_graph_with_one_edge()), \
         patch.object(application_handler, "put_application_resource", side_effect=lambda table, item: written_resources.append(item)), \
         patch.object(application_handler, "put_application_summary", side_effect=lambda table, item: written_summaries.append(item)):
        result = application_handler.run_application_discovery()

    assert result == {"total": 1, "succeeded": 1, "failed": 0}
    assert len(written_resources) == 2

    sm_item = next(r for r in written_resources if r["resource_id"] == SM_ARN)
    assert sm_item["pipeline_name"] == "APPRESOURCE#data-exporter#" + SM_ARN
    assert sm_item["application_id"] == "data-exporter"
    assert sm_item["downstream"] == [{
        "resource_id": LAMBDA_ARN, "relationship_type": "invokes", "evidence_type": "direct",
        "relationship_source": "test", "discovered_at": sm_item["discovered_at"],
    }]
    assert sm_item["upstream"] == []

    lam_item = next(r for r in written_resources if r["resource_id"] == LAMBDA_ARN)
    assert lam_item["upstream"][0]["resource_id"] == SM_ARN
    assert lam_item["downstream"] == []

    assert len(written_summaries) == 1
    summary = written_summaries[0]
    assert summary["pipeline_name"] == "APP#data-exporter"
    assert summary["application_id"] == "data-exporter"
    assert summary["total_resources"] == 2
    assert summary["total_relationships"] == 1
    assert summary["resource_type_counts"] == {"step_function": 1, "lambda": 1}


def test_one_bad_application_does_not_stop_the_others():
    def fake_build_graph(app, region):
        if app.id == "bad-app":
            raise RuntimeError("boom")
        return _graph_with_one_edge()

    written = []
    with patch.object(application_handler, "TABLE_NAME", "test-table"), \
         patch.object(application_handler, "load_applications", return_value=[_app("bad-app"), _app("good-app")]), \
         patch.object(application_handler, "build_application_graph", side_effect=fake_build_graph), \
         patch.object(application_handler, "put_application_resource", side_effect=lambda table, item: written.append(item)), \
         patch.object(application_handler, "put_application_summary"):
        result = application_handler.run_application_discovery()

    assert result == {"total": 2, "succeeded": 1, "failed": 1}
    assert all(item["application_id"] == "good-app" for item in written)


def test_no_applications_configured_yields_a_clean_empty_result():
    with patch.object(application_handler, "TABLE_NAME", "test-table"), \
         patch.object(application_handler, "load_applications", return_value=[]):
        result = application_handler.run_application_discovery()

    assert result == {"total": 0, "succeeded": 0, "failed": 0}


def test_missing_table_name_raises_clearly():
    with patch.object(application_handler, "TABLE_NAME", ""):
        try:
            application_handler.run_application_discovery()
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "STATUS_TABLE_NAME" in str(exc)
