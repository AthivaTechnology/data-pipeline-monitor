"""Normalized data models for pipeline lineage - kept free of boto3/AWS
types, same separation as models.py. A node is one AWS resource; an edge is
one directed, evidenced relationship between two nodes. Nothing here is
ever invented: every edge traces back to a concrete piece of evidence
(an ASL Task's Resource field, a Firehose destination config, etc.) named
in `relationship_source`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class Confidence(str, Enum):
    # A fact read directly from an AWS API/definition - e.g. an ASL Task's
    # literal Resource ARN, or a Firehose delivery stream's configured
    # destination. Not a guess.
    DIRECT = "direct"
    # A plausible but unverified association - e.g. a resource name found
    # inside a Lambda environment variable's value. Never presented the
    # same way as a DIRECT edge.
    INFERRED = "inferred"


@dataclass(frozen=True)
class LineageNode:
    resource_type: str  # "step_function" | "lambda" | "s3" | "firehose" | "glue_table" | "athena" | "eventbridge" | ...
    resource_id: str  # ARN when known, otherwise a stable synthetic id (see lineage_scanner)
    display_name: str
    region: Optional[str] = None


@dataclass(frozen=True)
class LineageEdge:
    source_id: str  # LineageNode.resource_id
    target_id: str
    relationship_source: str  # human-readable evidence, e.g. "ASL Task resource in state 'ExportToS3'"
    confidence: str = Confidence.DIRECT.value


@dataclass(frozen=True)
class PipelineLineage:
    pipeline_name: str
    state_machine_arn: str
    region: str
    nodes: List[LineageNode] = field(default_factory=list)
    edges: List[LineageEdge] = field(default_factory=list)
    discovered_at: Optional[datetime] = None
