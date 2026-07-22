"""LangGraph wiring for checkpointed runs."""

from __future__ import annotations

from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from product_factory.orchestration.state import RunState


def _initialize(state: RunState) -> dict[str, Any]:
    return {"final_status": "initializing", "events": [{"type": "initialize"}]}


def _snapshot(state: RunState) -> dict[str, Any]:
    return {"final_status": "planning", "events": [{"type": "snapshot"}]}


def _plan(state: RunState) -> dict[str, Any]:
    return {"final_status": "planning", "plan_attempt": int(state.get("plan_attempt") or 0) + 1}


def _compile_plan(state: RunState) -> dict[str, Any]:
    errors = state.get("compiler_errors") or []
    if errors and int(state.get("plan_attempt") or 0) > 1:
        return {"final_status": "plan_rejected"}
    if errors:
        return {"final_status": "planning"}
    return {"final_status": "executing"}


def _schedule(state: RunState) -> dict[str, Any]:
    return {"final_status": "executing", "events": [{"type": "schedule"}]}


def _execute(state: RunState) -> dict[str, Any]:
    return {"final_status": "validating", "events": [{"type": "execute_wave"}]}


def _validate(state: RunState) -> dict[str, Any]:
    return {"final_status": "validating", "events": [{"type": "validate"}]}


def _review(state: RunState) -> dict[str, Any]:
    return {"final_status": "validating", "events": [{"type": "review"}]}


def _repair(state: RunState) -> dict[str, Any]:
    return {
        "final_status": "repairing",
        "repair_count": int(state.get("repair_count") or 0) + 1,
        "events": [{"type": "repair"}],
    }


def _compose(state: RunState) -> dict[str, Any]:
    return {"final_status": "validating", "events": [{"type": "compose"}]}


def _final_validate(state: RunState) -> dict[str, Any]:
    if state.get("workflow_type") == "code_change":
        return {"final_status": "awaiting_approval"}
    return {"final_status": "completed"}


def _approval(state: RunState) -> dict[str, Any]:
    return {"final_status": "awaiting_approval", "pending_approvals": [{"action": "approve"}]}


def _finalize(state: RunState) -> dict[str, Any]:
    status = state.get("final_status") or "completed"
    if status == "awaiting_approval":
        return {"final_status": "awaiting_approval"}
    return {"final_status": "completed", "events": [{"type": "finalize"}]}


def _route_after_compile(state: RunState) -> Literal["repair_plan", "schedule", "end_rejected"]:
    status = state.get("final_status")
    if status == "plan_rejected":
        return "end_rejected"
    if state.get("compiler_errors"):
        return "repair_plan"
    return "schedule"


def _route_after_validate(state: RunState) -> Literal["repair", "review", "compose"]:
    results = state.get("validation_results") or []
    if any(r.get("status") == "fail" for r in results):
        return "repair"
    return "review"


def build_graph(checkpointer: Any | None = None):
    graph = StateGraph(RunState)
    graph.add_node("initialize", _initialize)
    graph.add_node("snapshot_repository", _snapshot)
    graph.add_node("plan", _plan)
    graph.add_node("compile_plan", _compile_plan)
    graph.add_node("repair_plan", _plan)
    graph.add_node("schedule_wave", _schedule)
    graph.add_node("execute_wave", _execute)
    graph.add_node("validate_wave", _validate)
    graph.add_node("review_wave", _review)
    graph.add_node("create_repairs", _repair)
    graph.add_node("compose", _compose)
    graph.add_node("final_validate", _final_validate)
    graph.add_node("approval_interrupt", _approval)
    graph.add_node("finalize", _finalize)

    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "snapshot_repository")
    graph.add_edge("snapshot_repository", "plan")
    graph.add_edge("plan", "compile_plan")
    graph.add_conditional_edges(
        "compile_plan",
        _route_after_compile,
        {
            "repair_plan": "repair_plan",
            "schedule": "schedule_wave",
            "end_rejected": "finalize",
        },
    )
    graph.add_edge("repair_plan", "compile_plan")
    graph.add_edge("schedule_wave", "execute_wave")
    graph.add_edge("execute_wave", "validate_wave")
    graph.add_conditional_edges(
        "validate_wave",
        _route_after_validate,
        {"repair": "create_repairs", "review": "review_wave", "compose": "compose"},
    )
    graph.add_edge("create_repairs", "schedule_wave")
    graph.add_edge("review_wave", "compose")
    graph.add_edge("compose", "final_validate")
    graph.add_edge("final_validate", "approval_interrupt")
    graph.add_edge("approval_interrupt", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
