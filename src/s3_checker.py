"""Read-only S3 output freshness lookup.

Mirrors collector.py's shape: a single HeadObject call, never raises, and
distinguishes "object not found / access denied" from "object is old" so the
caller never mistakes a broken check for a genuinely stale pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from .models import OutputConfig

logger = logging.getLogger("pipeline_freshness_monitor.s3_checker")

_client_cache: Dict[str, "boto3.client"] = {}


def _get_client(region: str):
    if region not in _client_cache:
        _client_cache[region] = boto3.client(
            "s3",
            region_name=region,
            config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"}),
        )
    return _client_cache[region]


def get_last_modified(output: OutputConfig, region: str) -> Tuple[Optional[datetime], Optional[str]]:
    """Returns (last_modified, error). Exactly one of the two is set."""
    if output.type != "s3":
        return None, f"unsupported output type {output.type!r}"

    client = _get_client(region)
    try:
        response = client.head_object(Bucket=output.bucket, Key=output.key)
    except (ClientError, BotoCoreError) as exc:
        logger.warning(
            "head_object failed for s3://%s/%s: %s", output.bucket, output.key, exc
        )
        return None, str(exc)

    return response["LastModified"], None
