"""Operator next-action hints derived from durable projections (RF6)."""

from __future__ import annotations

from typing import Any


def operator_next_action(
    *,
    run_status: str,
    task_status: str | None = None,
    task_capability: str | None = None,
    approval_required: bool | None = None,
    has_validation_failures: bool = False,
) -> str | None:
    """Return a concise CLI/host action when the operator must intervene."""
    status = (run_status or "").lower()
    task = (task_status or "").lower()

    if status in {"awaiting_approval", "awaiting-approval"} or task in {
        "awaiting_approval",
        "awaiting-approval",
        "approval_required",
    }:
        return (
            "Approve or reject via host CLI: "
            "`product-factory approve <run_id> [--apply]` or "
            "`product-factory reject <run_id>`."
        )
    if status in {"cancelled", "canceled", "cancel_requested"}:
        return "Inspect cancellation with the host CLI/MCP; submit a new run if needed."
    if task in {"blocked", "failed"} or status in {"blocked", "failed"}:
        if has_validation_failures:
            return (
                "Inspect validation evidence on the Evidence tab, then revise the "
                "request or re-run via the host CLI/MCP after addressing failures."
            )
        if task_capability == "repair":
            return (
                "Inspect repair lineage and tool receipts; continue or revise via the host CLI/MCP."
            )
        return (
            "Inspect task grants, model route, and evidence on the run detail view; "
            "continue via host CLI/MCP."
        )
    if approval_required and task in {"success", "succeeded", "completed"}:
        return (
            "Task completed under an approval-required policy; apply via "
            "`product-factory approve <run_id> --apply` when ready."
        )
    return None


def policy_projection(policy: dict[str, Any] | None) -> dict[str, Any] | None:
    """Public subset of effective task policy for API/dashboard consumers."""
    if not isinstance(policy, dict) or not policy:
        return None
    keys = (
        "schema_version",
        "task_id",
        "run_id",
        "pack_id",
        "pack_version",
        "capability",
        "executor_mode",
        "allowed_tool_names",
        "allowed_tool_classes",
        "connector_decisions",
        "prompt_tool_names",
        "prompt_reduction_reason",
        "skill_ids",
        "profile_ids",
        "stack_profile_artifact_sha256",
        "stack_profile_digest",
        "stack_profile_schema_version",
        "route_class",
        "primary_model_profile",
        "fallback_model_profile",
        "fallback_eligible",
        "budget_ceiling",
        "validator_ids",
        "repair_eligible",
        "approval_required",
    )
    return {key: policy.get(key) for key in keys if key in policy}
