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
from typing import List

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
