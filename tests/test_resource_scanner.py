import json

from src.resource_scanner import scan_definition, scan_definition_json


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
