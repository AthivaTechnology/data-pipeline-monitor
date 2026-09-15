from unittest.mock import patch

from src import api_handler


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
