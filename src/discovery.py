"""Read-only Step Functions account-wide discovery.

Lists every state machine that exists in the account/region, regardless of
whether it is in config/registry.yaml. Used by handler.py to find pipelines
that were never manually registered, so they can still be surfaced on the
dashboard (as "needs_review") instead of staying invisible. Makes no
mutating AWS calls, never raises - a failure here is caught by the caller
and simply means the discovery phase is skipped for that run, never that
the whole monitor run fails.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("pipeline_freshness_monitor.discovery")

_client_cache: Dict[str, "boto3.client"] = {}


def _get_client(region: str):
    if region not in _client_cache:
        _client_cache[region] = boto3.client(
            "stepfunctions",
            region_name=region,
            config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"}),
        )
    return _client_cache[region]


def list_all_state_machines(region: str) -> List[dict]:
    """Returns [{name, arn, creation_date}, ...] for every state machine in the
    account/region, walking every page. Returns [] (not a raise) on any AWS
    failure - the caller treats an empty discovery result as "nothing new to
    report this run", not as an error that should take down the monitor.
    """
    client = _get_client(region)
    machines: List[dict] = []
    try:
        paginator = client.get_paginator("list_state_machines")
        for page in paginator.paginate(PaginationConfig={"PageSize": 100}):
            for entry in page.get("stateMachines", []):
                machines.append(
                    {
                        "name": entry["name"],
                        "arn": entry["stateMachineArn"],
                        "creation_date": entry.get("creationDate"),
                    }
                )
    except (ClientError, BotoCoreError) as exc:
        logger.error("discovery failed to list state machines region=%s error=%s", region, exc)
        return []

    return machines


def get_definition(arn: str, region: str) -> Optional[str]:
    """Best-effort fetch of a state machine's ASL definition (raw JSON string)
    for resource_scanner.py to inspect. Returns None (never raises) on any
    failure - a missing definition just means resource detection is skipped
    for that pipeline, not that discovery fails.
    """
    client = _get_client(region)
    try:
        response = client.describe_state_machine(stateMachineArn=arn)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("describe_state_machine failed for %s: %s", arn, exc)
        return None
    return response.get("definition")
