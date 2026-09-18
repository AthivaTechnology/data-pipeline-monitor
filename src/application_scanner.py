"""Read-only, single-application discovery: given one CloudFormation stack
name (from config/applications.yaml - see application_registry.py), lists
every resource in it and the confirmed relationships between them.

Deliberately scoped to one named stack at a time, never account-wide - see
application_registry.py's docstring for why. Reuses lineage_scanner.py's
existing per-Step-Function walk unchanged for the Step Functions found in
the stack (their own Task->resource edges, already correctly handling this
pilot's Glue Job calls since those happen inside ASL Task definitions, not
as a separate relationship type). The only genuinely new detection here is
EventBridge Rule -> Lambda: a rule that targets a Lambda function directly,
never routing through a Step Function at all - lineage_scanner has no
reason to know about this, since it only ever starts from a state
machine's own definition.

Never raises: any AWS failure for one resource is logged and that resource
is skipped (inventoried, if already known, with no relationship data),
never taken as a reason to fail the whole application scan.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from .application_registry import ApplicationConfig
from .discovery import describe_state_machine_details
from .lineage_models import Confidence, LineageEdge, LineageNode, PipelineLineage
from .lineage_scanner import build_pipeline_graph
from .trigger_scanner import detect_trigger_detail

logger = logging.getLogger("pipeline_freshness_monitor.application_scanner")

_client_cache: Dict[str, Dict[str, "boto3.client"]] = {"cloudformation": {}, "events": {}}

# CloudFormation resource type -> our resource_type + how to build a stable,
# globally-meaningful id (an ARN) from CloudFormation's PhysicalResourceId,
# which is inconsistently either a full ARN or a bare name depending on the
# resource type. Only the types actually present in the pilot's stack are
# mapped; anything else is inventoried with its logical id as a fallback
# rather than guessed at.
def _resource_id_for(cfn_type: str, physical_id: str, region: str, account_id: str) -> Optional[tuple]:
    if cfn_type == "AWS::StepFunctions::StateMachine":
        return "step_function", physical_id  # already a full ARN
    if cfn_type == "AWS::Lambda::Function":
        return "lambda", f"arn:aws:lambda:{region}:{account_id}:function:{physical_id}"
    if cfn_type == "AWS::Glue::Job":
        return "glue", f"arn:aws:glue:{region}:{account_id}:job/{physical_id}"
    if cfn_type == "AWS::Events::Rule":
        return "eventbridge", f"arn:aws:events:{region}:{account_id}:rule/{physical_id}"
    if cfn_type == "AWS::IAM::Role":
        return "iam_role", f"arn:aws:iam::{account_id}:role/{physical_id}"
    if cfn_type == "AWS::CloudWatch::Alarm":
        return "cloudwatch_alarm", f"arn:aws:cloudwatch:{region}:{account_id}:alarm:{physical_id}"
    return None


def _cfn_client(region: str):
    if region not in _client_cache["cloudformation"]:
        _client_cache["cloudformation"][region] = boto3.client(
            "cloudformation", region_name=region, config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})
        )
    return _client_cache["cloudformation"][region]


def _events_client(region: str):
    if region not in _client_cache["events"]:
        _client_cache["events"][region] = boto3.client(
            "events", region_name=region, config=BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})
        )
    return _client_cache["events"][region]


def list_stack_resources(stack_name: str, region: str) -> List[dict]:
    """Returns [{logical_id, resource_type, physical_id}, ...] for every
    resource in the stack. Returns [] (never raises) on any AWS failure.
    """
    client = _cfn_client(region)
    resources: List[dict] = []
    try:
        paginator = client.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for entry in page.get("StackResourceSummaries", []):
                resources.append({
                    "logical_id": entry["LogicalResourceId"],
                    "resource_type": entry["ResourceType"],
                    "physical_id": entry.get("PhysicalResourceId"),
                })
    except (ClientError, BotoCoreError) as exc:
        logger.error("list_stack_resources failed for stack=%s region=%s error=%s", stack_name, region, exc)
        return []
    return resources


def _rule_targets(rule_name: str, region: str) -> List[dict]:
    """Every target ARN configured on one EventBridge rule. [] on failure."""
    client = _events_client(region)
    try:
        response = client.list_targets_by_rule(Rule=rule_name)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("list_targets_by_rule failed for rule=%s: %s", rule_name, exc)
        return []
    return response.get("Targets", [])


def _account_id_from_state_machine_arns(stack_resources: List[dict]) -> Optional[str]:
    """AWS account id isn't directly available to a Lambda's own permissions
    without an extra STS call - but a Step Function's PhysicalResourceId is
    already its full ARN (arn:aws:states:<region>:<account>:...), so it's
    free to read off the first one found in this stack instead. Returns
    None (not a guess) if the stack has no Step Function to read it from.
    """
    for r in stack_resources:
        if r["resource_type"] == "AWS::StepFunctions::StateMachine" and r["physical_id"]:
            parts = r["physical_id"].split(":")
            if len(parts) > 4:
                return parts[4]
    return None


def build_application_graph(app: ApplicationConfig, region: str) -> PipelineLineage:
    """Builds the full resource graph for one configured application. Never
    raises - degrades to whatever was successfully discovered, same
    conservative contract as lineage_scanner.build_pipeline_graph.
    """
    nodes: Dict[str, LineageNode] = {}
    edges: List[LineageEdge] = []

    stack_resources = list_stack_resources(app.stack_name, region)
    account_id = _account_id_from_state_machine_arns(stack_resources)

    if account_id is None:
        logger.warning(
            "application=%s: could not determine account id (no Step Function in stack) - "
            "only Step Function-derived nodes/edges will be produced, other resource types skipped",
            app.id,
        )

    # Pass 1: inventory every resource we can build a stable id for.
    resolved: Dict[str, tuple] = {}  # logical_id -> (resource_type, resource_id)
    for r in stack_resources:
        if not r["physical_id"]:
            continue
        classified = _resource_id_for(r["resource_type"], r["physical_id"], region, account_id) if account_id else None
        if classified is None:
            if r["resource_type"] == "AWS::StepFunctions::StateMachine":
                classified = ("step_function", r["physical_id"])  # ARN already, no account_id needed
            else:
                continue  # unmapped type or no account_id yet - inventoried nowhere, not guessed
        resource_type, resource_id = classified
        resolved[r["logical_id"]] = (resource_type, resource_id)
        nodes.setdefault(resource_id, LineageNode(
            resource_type=resource_type, resource_id=resource_id,
            display_name=r["physical_id"].rsplit("/", 1)[-1].rsplit(":", 1)[-1], region=region,
        ))

    # Pass 2: for every Step Function found, reuse the existing per-pipeline
    # walk unchanged - gives EventBridge->StepFunction and StepFunction->
    # Task edges for free, exactly matching what the per-pipeline Lineage
    # page already shows for these same pipelines.
    for logical_id, (resource_type, resource_id) in resolved.items():
        if resource_type != "step_function":
            continue
        name = nodes[resource_id].display_name
        details = describe_state_machine_details(resource_id, region)
        definition = details.get("definition") if details else None
        trigger_detail = detect_trigger_detail(resource_id, region)
        sub_graph = build_pipeline_graph(name, resource_id, region, definition, trigger_detail)
        for n in sub_graph.nodes:
            nodes.setdefault(n.resource_id, n)
        edges.extend(sub_graph.edges)

    # Pass 3: EventBridge Rule -> Lambda, direct (never discoverable by
    # walking a Step Function's own definition - the whole reason this
    # module exists). Every rule found in the stack is checked, not just
    # ones already known as a trigger, since one rule can have multiple
    # targets (e.g. both a Step Function and a Lambda).
    known_lambda_arns = {rid for (rtype, rid) in resolved.values() if rtype == "lambda"}
    for logical_id, (resource_type, resource_id) in resolved.items():
        if resource_type != "eventbridge":
            continue
        rule_name = resource_id.rsplit("/", 1)[-1]
        for target in _rule_targets(rule_name, region):
            target_arn = target.get("Arn", "")
            if target_arn in known_lambda_arns:
                edges.append(LineageEdge(
                    source_id=resource_id, target_id=target_arn,
                    relationship_source=f"EventBridge rule '{rule_name}' targets this Lambda directly",
                    confidence=Confidence.DIRECT.value, relationship_type="invokes",
                ))

    return PipelineLineage(
        pipeline_name=app.id, state_machine_arn="", region=region,
        nodes=list(nodes.values()), edges=edges,
    )
