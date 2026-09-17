import json

from src.resource_scanner import classify_resource, scan_definition, scan_definition_json


def test_no_references_found():
    definition = json.dumps({"States": {"Wait": {"Type": "Wait", "Seconds": 5}}})
    assert scan_definition(definition) == []


def test_detects_s3_reference():
    definition = '{"Resource": "arn:aws:s3:::my-bucket/key.csv"}'
    result = scan_definition(definition)
    assert result == ["S3: my-bucket/key.csv"]


def test_detects_lambda_reference():
    definition = '{"Resource": "arn:aws:lambda:us-east-1:382625484581:function:my_fn"}'
    assert scan_definition(definition) == ["Lambda: my_fn"]


def test_detects_dynamodb_reference():
    definition = '{"Resource": "arn:aws:dynamodb:us-east-1:382625484581:table/my_table"}'
    assert scan_definition(definition) == ["DynamoDB: my_table"]


def test_detects_athena_sdk_integration_without_literal_arn():
    definition = '{"Resource": "arn:aws:states:::aws-sdk:athena:startQueryExecution"}'
    assert scan_definition(definition) == ["Athena/Glue reference"]


def test_detects_multiple_distinct_references_deduped_and_sorted():
    definition = json.dumps(
        {
            "a": "arn:aws:lambda:us-east-1:1:function:fn1",
            "b": "arn:aws:lambda:us-east-1:1:function:fn1",
            "c": "arn:aws:s3:::bucket/key",
        }
    )
    assert scan_definition(definition) == ["Lambda: fn1", "S3: bucket/key"]


def test_scan_definition_json_rejects_invalid_json():
    assert scan_definition_json("not json at all { arn:aws:s3:::bucket") == []


def test_scan_definition_json_accepts_valid_json():
    definition = json.dumps({"Resource": "arn:aws:s3:::bucket/key"})
    assert scan_definition_json(definition) == ["S3: bucket/key"]


def test_empty_or_none_definition_yields_no_detections():
    assert scan_definition("") == []
    assert scan_definition(None) == []


# ---------------- classify_resource (used by lineage_scanner.py) ----------------


def test_classify_direct_lambda_arn():
    # Third element: a literal per-resource ARN is a globally unique id.
    assert classify_resource("arn:aws:lambda:us-east-1:1:function:my_fn") == ("lambda", "my_fn", True)


def test_classify_direct_s3_arn():
    assert classify_resource("arn:aws:s3:::my-bucket/key.csv") == ("s3", "my-bucket", True)


def test_classify_direct_firehose_arn():
    assert classify_resource("arn:aws:firehose:us-east-1:1:deliverystream/my-stream") == ("firehose", "my-stream", True)


def test_classify_direct_kinesis_arn():
    assert classify_resource("arn:aws:kinesis:us-east-1:1:stream/my-stream") == ("kinesis", "my-stream", True)


def test_classify_service_integration_without_parameters_returns_no_name():
    # arn:aws:states:::service:action is a constant, generic string AWS
    # reuses for every Task using that integration - never globally unique,
    # and never a name unless Parameters actually reveals one.
    resource_type, name, is_unique_arn = classify_resource("arn:aws:states:::aws-sdk:firehose:putRecord")
    assert resource_type == "firehose"
    assert name is None
    assert is_unique_arn is False


def test_classify_service_integration_recovers_name_from_parameters():
    result = classify_resource(
        "arn:aws:states:::lambda:invoke", parameters={"FunctionName": "my_real_function"}
    )
    assert result == ("lambda", "my_real_function", False)


def test_classify_service_integration_ignores_parameter_referencing_execution_input():
    # "$.foo" is a JSONPath reference to runtime input, not a real, checkable
    # name - must not be treated as evidence.
    resource_type, name, is_unique_arn = classify_resource(
        "arn:aws:states:::dynamodb:putItem", parameters={"TableName": "$.tableName"}
    )
    assert resource_type == "dynamodb"
    assert name is None


def test_classify_unrecognized_resource_returns_none():
    assert classify_resource("arn:aws:states:::activity:my-activity") is None
    assert classify_resource("") is None
    assert classify_resource(None) is None
