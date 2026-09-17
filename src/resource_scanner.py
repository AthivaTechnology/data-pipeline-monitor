"""Best-effort, read-only scan of a state machine's ASL definition for hints
about what AWS resources it touches (S3, Lambda, DynamoDB, Athena/Glue,
Redshift, RDS).

Deliberately conservative: this only ever *detects references*, never
resolves them to a concrete, checkable output location (bucket/key). Real
pipelines seen during the account-wide discovery pass build their S3 keys
from execution input at runtime, so guessing a literal path from the static
definition would be exactly the kind of invented data this project's rules
forbid. A caller wanting real freshness tracking for a detected resource
still has to register an explicit `output:` in config/registry.yaml.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

_PATTERNS = [
    ("S3", re.compile(r'arn:aws:s3:::([^"\s]+)')),
    ("Lambda", re.compile(r'arn:aws:lambda:[^:]*:[^:]*:function:([^"\s]+)')),
    ("DynamoDB", re.compile(r'arn:aws:dynamodb:[^:]*:[^:]*:table/([^"\s]+)')),
    ("Athena/Glue", re.compile(r"\barn:aws:(athena|glue):", re.IGNORECASE)),
    ("Redshift", re.compile(r"\barn:aws:redshift", re.IGNORECASE)),
    ("RDS", re.compile(r"\barn:aws:rds", re.IGNORECASE)),
]

# Some pipelines invoke these services via an SDK integration
# ("arn:aws:states:::aws-sdk:athena:...") rather than a literal resource ARN -
# the service name still appears in the action string, so it's worth its own
# lighter-weight check.
_SDK_INTEGRATION_PATTERNS = [
    ("Athena/Glue", re.compile(r"aws-sdk:(athena|glue)", re.IGNORECASE)),
    ("Redshift", re.compile(r"aws-sdk:redshift", re.IGNORECASE)),
    ("RDS", re.compile(r"aws-sdk:rds", re.IGNORECASE)),
    ("DynamoDB", re.compile(r"aws-sdk:dynamodb", re.IGNORECASE)),
    ("S3", re.compile(r"aws-sdk:s3", re.IGNORECASE)),
]

# ---------------------------------------------------------------------------
# Per-resource classification, used by lineage_scanner.py to type a single
# ASL Task state's `Resource` value (as opposed to scan_definition() above,
# which flags every mention anywhere in the raw JSON with no notion of
# order). Broader service coverage than _PATTERNS: lineage explicitly needs
# Kinesis/Firehose/SNS/SQS, which the freshness-monitor scan never did.
# ---------------------------------------------------------------------------

# (resource_type, regex capturing the resource's short name from a literal ARN)
_DIRECT_ARN_PATTERNS: List[Tuple[str, "re.Pattern"]] = [
    ("s3", re.compile(r"^arn:aws:s3:::([^/\s]+)")),
    ("lambda", re.compile(r"^arn:aws:lambda:[^:]*:[^:]*:function:([^:\s]+)")),
    ("dynamodb", re.compile(r"^arn:aws:dynamodb:[^:]*:[^:]*:table/([^\s]+)")),
    ("sns", re.compile(r"^arn:aws:sns:[^:]*:[^:]*:([^\s]+)")),
    ("sqs", re.compile(r"^arn:aws:sqs:[^:]*:[^:]*:([^\s]+)")),
    ("kinesis", re.compile(r"^arn:aws:kinesis:[^:]*:[^:]*:stream/([^\s]+)")),
    ("firehose", re.compile(r"^arn:aws:firehose:[^:]*:[^:]*:deliverystream/([^\s]+)")),
    ("glue", re.compile(r"^arn:aws:glue:[^:]*:[^:]*:(?:table|database|job)/([^\s]+)")),
    ("redshift", re.compile(r"^arn:aws:redshift:", re.IGNORECASE)),
    ("rds", re.compile(r"^arn:aws:rds:", re.IGNORECASE)),
    ("athena", re.compile(r"^arn:aws:athena:", re.IGNORECASE)),
]

# A Task using a Step Functions service integration
# ("arn:aws:states:::[aws-sdk:]service:action[.sync]") names the service in
# the ARN itself, but not the specific resource - the concrete target (a
# function name, a table name, ...) lives in the state's `Parameters`
# instead. See _PARAMETER_NAME_KEYS below for how that's recovered.
_SERVICE_INTEGRATION_PATTERN = re.compile(
    r"^arn:aws:states:::(?:aws-sdk:)?([a-z0-9-]+):[A-Za-z.]+$"
)
_SERVICE_INTEGRATION_TYPE_MAP = {
    "lambda": "lambda", "sns": "sns", "sqs": "sqs", "dynamodb": "dynamodb",
    "s3": "s3", "athena": "athena", "glue": "glue", "firehose": "firehose",
    "kinesis": "kinesis", "redshift-data": "redshift", "rds-data": "rds",
}

# Well-known Task Parameters keys that name the actual target of a service
# integration - reading these is not a guess, it's the literal configured
# destination for that Task, so a name recovered this way still counts as
# DIRECT evidence (see lineage_models.Confidence).
_PARAMETER_NAME_KEYS = [
    "FunctionName", "TableName", "TopicArn", "QueueUrl", "QueueName",
    "DeliveryStreamName", "StreamName", "DatabaseName", "JobName", "Bucket",
]


def classify_resource(resource: str, parameters: Optional[dict] = None) -> Optional[Tuple[str, Optional[str], bool]]:
    """Classifies a single ASL Task state's `Resource` value.

    Returns (resource_type, display_name_or_None, is_globally_unique_arn),
    or None if `resource` isn't a recognizable AWS service call (e.g. an
    activity ARN) - callers must treat None as "not enough evidence to
    include", never guess a type.

    The third element matters for building a stable node id: a literal
    resource ARN (`arn:aws:lambda:...:function:x`) is a real, globally
    unique identifier. A Step Functions *service-integration* ARN like
    `arn:aws:states:::lambda:invoke` looks like an ARN but is a constant,
    generic string AWS reuses for every Task using that integration style,
    in every state machine in the account - two unrelated Lambda calls (even
    in different pipelines) share that exact string. Treating it as a
    unique id would silently merge unrelated resources into one fake node,
    so callers must build the id from (resource_type, display_name) instead
    whenever this is False, never from `resource` itself.

    display_name is None when no concrete target could be recovered from
    `parameters` (see _name_from_parameters) - an honest "type is known,
    specific identity is not", never a fabricated placeholder name.
    """
    if not resource:
        return None

    for resource_type, pattern in _DIRECT_ARN_PATTERNS:
        m = pattern.match(resource)
        if m:
            name = m.group(1) if m.groups() else resource.rsplit(":", 1)[-1]
            return resource_type, name, True

    m = _SERVICE_INTEGRATION_PATTERN.match(resource)
    if m:
        service = m.group(1).lower()
        resource_type = _SERVICE_INTEGRATION_TYPE_MAP.get(service)
        if resource_type is None:
            return None
        return resource_type, _name_from_parameters(parameters), False

    return None


def _name_from_parameters(parameters: Optional[dict]) -> Optional[str]:
    if not isinstance(parameters, dict):
        return None
    for key in _PARAMETER_NAME_KEYS:
        value = parameters.get(key)
        if isinstance(value, str) and value:
            # Parameters can reference execution input ("$.foo") rather than
            # a literal value - that's not a real, checkable name.
            if value.startswith("$"):
                return None
            return value
    return None


def scan_definition(definition: str) -> List[str]:
    """Returns a sorted, deduped list like ["Athena/Glue reference", "Lambda: my_fn"].

    `definition` is the raw ASL JSON string from describe_state_machine. Never
    raises - malformed/unparseable input just yields no detections rather
    than failing the discovery run for that pipeline.
    """
    if not definition:
        return []

    found = set()

    for label, pattern in _PATTERNS:
        for match in pattern.finditer(definition):
            if match.groups():
                found.add(f"{label}: {match.group(1)}")
            else:
                found.add(f"{label} reference")

    for label, pattern in _SDK_INTEGRATION_PATTERNS:
        if pattern.search(definition):
            found.add(f"{label} reference")

    return sorted(found)


def scan_definition_json(definition: str) -> List[str]:
    """Same as scan_definition, but validates the input is real JSON first so
    a truncated/corrupt definition never produces misleading partial matches.
    Falls back to [] (not a raise) on invalid JSON.
    """
    try:
        json.loads(definition)
    except (ValueError, TypeError):
        return []
    return scan_definition(definition)
