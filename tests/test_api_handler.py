from unittest.mock import MagicMock, patch

from src import api_handler, status_store


def test_status_list_happy_path():
    items = [
        {"pipeline_name": "a", "execution_status": "fresh"},
        {"pipeline_name": "b", "execution_status": "never_run"},
    ]
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_all_statuses", return_value=items):
        response = api_handler.lambda_handler({"pathParameters": None}, None)

    assert response["statusCode"] == 200
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    import json
    body = json.loads(response["body"])
    assert body["total_pipelines"] == 2
    assert body["summary"]["fresh"] == 1
    assert body["summary"]["never_run"] == 1


def test_status_detail_found():
    item = {"pipeline_name": "a", "execution_status": "fresh"}
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_pipeline_status", return_value=item):
        response = api_handler.lambda_handler({"pathParameters": {"name": "a"}}, None)

    assert response["statusCode"] == 200


def test_status_detail_not_found_returns_404_with_cors():
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_pipeline_status", return_value=None):
        response = api_handler.lambda_handler({"pathParameters": {"name": "missing"}}, None)

    assert response["statusCode"] == 404
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"


def test_unexpected_dynamodb_failure_returns_500_with_cors():
    """This is the fix for gap C: an unhandled exception must not escape
    lambda_handler entirely, or the browser would see a bare CORS failure
    instead of a real, readable error.
    """
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_all_statuses", side_effect=RuntimeError("dynamodb exploded")):
        response = api_handler.lambda_handler({"pathParameters": None}, None)

    assert response["statusCode"] == 500
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    import json
    body = json.loads(response["body"])
    assert "dynamodb exploded" in body["message"]


def test_missing_table_name_returns_500_without_touching_dynamodb():
    with patch.object(api_handler, "TABLE_NAME", ""):
        response = api_handler.lambda_handler({"pathParameters": None}, None)

    assert response["statusCode"] == 500


def test_status_detail_route_rejects_lineage_prefixed_name_end_to_end():
    """Exercises the real status_store.get_pipeline_status (not mocked) to
    confirm the fix actually reaches the API boundary: requesting
    /status/{name} with a LINEAGE#-prefixed name must 404, never return a
    lineage item disguised as a pipeline status.
    """
    fake_table = MagicMock()
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(status_store, "_table", return_value=fake_table):
        response = api_handler.lambda_handler(
            {"routeKey": "GET /status/{name}", "pathParameters": {"name": "LINEAGE#SUMMARY"}}, None
        )

    assert response["statusCode"] == 404
    fake_table.get_item.assert_not_called()


def test_status_routes_dispatch_correctly_via_routeKey():
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_all_statuses", return_value=[]):
        response = api_handler.lambda_handler({"routeKey": "GET /status", "pathParameters": None}, None)
    assert response["statusCode"] == 200

    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_pipeline_status", return_value={"pipeline_name": "a"}):
        response = api_handler.lambda_handler({"routeKey": "GET /status/{name}", "pathParameters": {"name": "a"}}, None)
    assert response["statusCode"] == 200


# ---------------- Lineage summary ----------------


def test_lineage_summary_when_discovery_has_never_run():
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_lineage_summary", return_value=None):
        response = api_handler.lambda_handler({"routeKey": "GET /lineage", "pathParameters": None}, None)

    import json
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["discovery_has_run"] is False
    assert body["total_resources"] == 0


def test_lineage_summary_when_discovery_has_run():
    summary_item = {
        "pipeline_name": "LINEAGE#SUMMARY", "total_pipelines_scanned": 3,
        "total_resources": 7, "total_relationships": 5, "last_discovery_at": "2026-01-01T00:00:00+00:00",
    }
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_lineage_summary", return_value=summary_item):
        response = api_handler.lambda_handler({"routeKey": "GET /lineage", "pathParameters": None}, None)

    import json
    body = json.loads(response["body"])
    assert body["discovery_has_run"] is True
    assert body["total_resources"] == 7
    assert "pipeline_name" not in body  # internal DynamoDB key, not part of the API contract


# ---------------- Lineage detail ----------------


def test_lineage_detail_found():
    item = {
        "pipeline_name": "LINEAGE#PIPELINE#a", "source_pipeline_name": "a",
        "nodes": [], "edges": [], "discovered_at": "2026-01-01T00:00:00+00:00",
    }
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_pipeline_lineage", return_value=item):
        response = api_handler.lambda_handler({"routeKey": "GET /lineage/{name}", "pathParameters": {"name": "a"}}, None)

    import json
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["pipeline_name"] == "a"


def test_lineage_detail_not_found_returns_404():
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_pipeline_lineage", return_value=None):
        response = api_handler.lambda_handler({"routeKey": "GET /lineage/{name}", "pathParameters": {"name": "missing"}}, None)

    assert response["statusCode"] == 404


# ---------------- Resources catalog ----------------


def test_resources_list_dedupes_nodes_across_pipelines_and_tracks_pipelines():
    lineage_items = [
        {
            "source_pipeline_name": "pipeline_a",
            "nodes": [{"resource_type": "lambda", "resource_id": "arn:fn", "display_name": "fn", "region": "us-east-1"}],
            "edges": [],
        },
        {
            "source_pipeline_name": "pipeline_b",
            "nodes": [{"resource_type": "lambda", "resource_id": "arn:fn", "display_name": "fn", "region": "us-east-1"}],
            "edges": [],
        },
    ]
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_all_pipeline_lineage", return_value=lineage_items):
        response = api_handler.lambda_handler({"routeKey": "GET /resources", "pathParameters": None}, None)

    import json
    body = json.loads(response["body"])
    assert body["total"] == 1
    assert set(body["resources"][0]["pipelines"]) == {"pipeline_a", "pipeline_b"}


def test_resource_detail_returns_upstream_and_downstream():
    lineage_items = [{
        "source_pipeline_name": "pipeline_a",
        "nodes": [
            {"resource_type": "step_function", "resource_id": "arn:sm", "display_name": "pipeline_a", "region": "us-east-1"},
            {"resource_type": "lambda", "resource_id": "arn:fn", "display_name": "fn", "region": "us-east-1"},
            {"resource_type": "s3", "resource_id": "arn:bucket", "display_name": "bucket", "region": "us-east-1"},
        ],
        "edges": [
            {"source_id": "arn:sm", "target_id": "arn:fn", "relationship_source": "task", "confidence": "direct"},
            {"source_id": "arn:fn", "target_id": "arn:bucket", "relationship_source": "task", "confidence": "direct"},
        ],
    }]
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_all_pipeline_lineage", return_value=lineage_items):
        response = api_handler.lambda_handler({"routeKey": "GET /resources/{arn+}", "pathParameters": {"arn": "arn:fn"}}, None)

    import json
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["upstream"][0]["resource_id"] == "arn:sm"
    assert body["downstream"][0]["resource_id"] == "arn:bucket"
    assert body["pipelines"] == ["pipeline_a"]


def test_resource_detail_not_found_returns_404():
    with patch.object(api_handler, "TABLE_NAME", "test-table"), \
         patch.object(api_handler, "get_all_pipeline_lineage", return_value=[]):
        response = api_handler.lambda_handler({"routeKey": "GET /resources/{arn+}", "pathParameters": {"arn": "arn:missing"}}, None)

    assert response["statusCode"] == 404
