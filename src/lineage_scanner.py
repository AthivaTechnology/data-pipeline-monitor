"""Structural scan of a state machine's ASL definition into an ordered
lineage graph - who calls what, in what order - unlike resource_scanner.py's
scan_definition(), which only flags resource mentions with no notion of
order or direction.

Walks the real ASL control-flow graph (Next / Choices+Default / Parallel
Branches / Map Iterator/ItemProcessor) starting at StartAt, connecting each
Task state's classified resource to the next one reachable after it. A
non-Task state (Wait, Pass, Choice, Succeed, Fail) never breaks the chain:
the most recent Task node "propagates through" it to whatever Task state
comes next, so unrelated flow-control states don't fragment the graph.

Deliberately conservative, matching every other discovery module in this
project: a Task whose Resource doesn't classify (see resource_scanner.
classify_resource) contributes no node and no edge - it is dropped, not
guessed at.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Set, Tuple

from .lineage_models import Confidence, LineageEdge, LineageNode, PipelineLineage
from .resource_scanner import classify_resource

logger = logging.getLogger("pipeline_freshness_monitor.lineage_scanner")

# Safety bound against a pathological/malformed definition (unexpected large
# fan-out, near-cyclic Choice graphs) - not a realistic ASL size, just a
# backstop so one bad definition can't make a discovery run hang.
_MAX_STATES_WALKED = 500


def _task_node_and_label(
    resource_type: str, name: Optional[str], is_unique_arn: bool, resource: str,
    pipeline_name: str, state_name: str,
) -> Tuple[str, str]:
    """Returns (node_id, display_name). See classify_resource's docstring
    for why a service-integration ARN can't be used as the id directly.

    - Literal ARN (is_unique_arn): the ARN itself is the id - genuinely
      unique, and the same real resource used by two pipelines should
      dedupe in the catalog.
    - Service integration with a name recovered from Parameters: id keyed
      by (type, name) - still a real, checkable AWS resource identity, so
      it's fine (correct, even) for it to dedupe across pipelines too.
    - Service integration with no recoverable name: honestly unresolvable -
      scoped to this exact pipeline+state so it never falsely merges with
      an unrelated unresolved call elsewhere, instead of guessing a shared
      identity two different Tasks happen not to have.
    """
    if is_unique_arn:
        return resource, name or resource
    if name:
        return f"{resource_type}:{name}", name
    return f"{resource_type}:unresolved:{pipeline_name}:{state_name}", f"{resource_type} (target not resolved, via '{state_name}')"


def build_pipeline_graph(
    pipeline_name: str,
    state_machine_arn: str,
    region: str,
    definition: Optional[str],
    trigger_detail: Optional[dict],
) -> PipelineLineage:
    """Builds the full lineage graph for one pipeline: its EventBridge
    trigger (if any), the state machine itself, and every Task resource
    reachable from it, connected in call order. Never raises - a malformed
    or missing definition just yields a graph with only the trigger/state
    machine nodes, same "detected nothing further" honesty as the rest of
    this project's discovery code.
    """
    nodes: Dict[str, LineageNode] = {}
    edges: List[LineageEdge] = []

    sm_node = LineageNode(
        resource_type="step_function", resource_id=state_machine_arn,
        display_name=pipeline_name, region=region,
    )
    nodes[sm_node.resource_id] = sm_node

    if trigger_detail:
        eb_node = LineageNode(
            resource_type=trigger_detail["kind"], resource_id=trigger_detail["resource_id"],
            display_name=trigger_detail["name"], region=region,
        )
        nodes[eb_node.resource_id] = eb_node
        edges.append(LineageEdge(
            source_id=eb_node.resource_id, target_id=sm_node.resource_id,
            relationship_source=trigger_detail["label"], confidence=Confidence.DIRECT.value,
        ))

    states = _parse_states(definition)
    if states is None:
        return PipelineLineage(pipeline_name, state_machine_arn, region, list(nodes.values()), edges)

    try:
        start_at, state_map = states
        visited: Set[str] = set()
        _walk(state_map, start_at, predecessor_id=sm_node.resource_id,
              predecessor_label=f"pipeline '{pipeline_name}'",
              nodes=nodes, edges=edges, visited=visited, region=region, pipeline_name=pipeline_name)
    except Exception:
        logger.exception("lineage walk failed for pipeline=%s - returning trigger/state-machine nodes only", pipeline_name)

    return PipelineLineage(pipeline_name, state_machine_arn, region, list(nodes.values()), edges)


def _parse_states(definition: Optional[str]) -> Optional[Tuple[str, dict]]:
    if not definition:
        return None
    try:
        doc = json.loads(definition)
    except (ValueError, TypeError):
        return None
    start_at = doc.get("StartAt")
    state_map = doc.get("States")
    if not start_at or not isinstance(state_map, dict):
        return None
    return start_at, state_map


def _walk(
    state_map: dict, state_name: Optional[str], predecessor_id: str, predecessor_label: str,
    nodes: Dict[str, LineageNode], edges: List[LineageEdge], visited: Set[str], region: str,
    pipeline_name: str,
) -> None:
    """Depth-first walk from `state_name`, threading `predecessor_id`
    (the most recent Task/trigger node) through non-Task states. `visited`
    prevents re-descending into a state already fully processed - the
    correct, conservative choice for the rare back-edge in a malformed
    definition: it stops infinite recursion at the cost of a possibly-missed
    reconvergence edge, never the other way around.
    """
    if state_name is None or state_name not in state_map or state_name in visited:
        return
    if len(visited) >= _MAX_STATES_WALKED:
        return
    visited.add(state_name)

    state = state_map[state_name]
    if not isinstance(state, dict):
        return

    current_predecessor_id = predecessor_id
    current_predecessor_label = predecessor_label

    if state.get("Type") == "Task":
        resource = state.get("Resource")
        classified = classify_resource(resource, state.get("Parameters")) if resource else None
        if classified:
            resource_type, name, is_unique_arn = classified
            node_id, display_name = _task_node_and_label(
                resource_type, name, is_unique_arn, resource, pipeline_name, state_name,
            )
            nodes.setdefault(node_id, LineageNode(
                resource_type=resource_type, resource_id=node_id, display_name=display_name, region=region,
            ))
            edges.append(LineageEdge(
                source_id=current_predecessor_id, target_id=node_id,
                relationship_source=f"ASL Task '{state_name}' calls this resource, reached from {current_predecessor_label}",
                confidence=Confidence.DIRECT.value,
            ))
            current_predecessor_id = node_id
            current_predecessor_label = f"Task '{state_name}'"

    branches: List[dict] = state.get("Branches") or []
    for branch in branches:
        branch_start = branch.get("StartAt")
        branch_states = branch.get("States")
        if branch_start and isinstance(branch_states, dict):
            _walk(branch_states, branch_start, current_predecessor_id, current_predecessor_label,
                  nodes, edges, visited, region, pipeline_name)

    iterator = state.get("ItemProcessor") or state.get("Iterator")
    if isinstance(iterator, dict):
        it_start = iterator.get("StartAt")
        it_states = iterator.get("States")
        if it_start and isinstance(it_states, dict):
            _walk(it_states, it_start, current_predecessor_id, current_predecessor_label,
                  nodes, edges, visited, region, pipeline_name)

    for choice in state.get("Choices") or []:
        _walk(state_map, choice.get("Next"), current_predecessor_id, current_predecessor_label,
              nodes, edges, visited, region, pipeline_name)
    if state.get("Default"):
        _walk(state_map, state.get("Default"), current_predecessor_id, current_predecessor_label,
              nodes, edges, visited, region, pipeline_name)

    if state.get("Next"):
        _walk(state_map, state.get("Next"), current_predecessor_id, current_predecessor_label,
              nodes, edges, visited, region, pipeline_name)
