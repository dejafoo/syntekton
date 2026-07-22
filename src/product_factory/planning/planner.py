"""Planner invocation helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any

from product_factory.domain.plans import PlannerOutput
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest

PLANNER_SYSTEM = """You are the Product Factory planner.
Return ONLY structured JSON matching the provided schema.
Create a typed task DAG using registered capabilities only.
Do not invent tools or capabilities.
Every task needs acceptance criteria with verification methods.
Include a composition or implementation task that produces the final artifact.
"""


def build_planner_messages(
    *,
    request_text: str,
    workflow_type: str,
    repository_summary: dict[str, Any] | None,
    budget: dict[str, Any],
) -> list[CanonicalMessage]:
    payload = {
        "workflow_type": workflow_type,
        "request": request_text,
        "repository_summary": repository_summary or {},
        "budget": budget,
        "capabilities": [
            "requirements",
            "architecture",
            "repository_analysis",
            "implementation",
            "security_review",
            "test_design",
            "test_execution",
            "documentation",
            "composition",
            "independent_review",
            "repair",
        ],
    }
    return [
        CanonicalMessage(role="system", content=PLANNER_SYSTEM),
        CanonicalMessage(role="user", content=json.dumps(payload, indent=2, default=str)),
    ]


def plan_with_gateway(
    gateway: ModelGateway,
    *,
    run_id: str,
    request_text: str,
    workflow_type: str,
    repository_summary: dict[str, Any] | None,
    budget: dict[str, Any],
    model_profile: str = "supervisor",
    repair_errors: list[dict[str, Any]] | None = None,
    seed: int | None = None,
) -> PlannerOutput:
    messages = build_planner_messages(
        request_text=request_text,
        workflow_type=workflow_type,
        repository_summary=repository_summary,
        budget=budget,
    )
    if repair_errors:
        messages.append(
            CanonicalMessage(
                role="user",
                content="Previous plan failed compilation. Fix these errors:\n"
                + json.dumps(repair_errors, indent=2),
            )
        )
    schema = PlannerOutput.model_json_schema()
    # OpenRouter strict schemas often need additionalProperties false at root;
    # Pydantic already sets this for model_config extra=forbid on PlannerOutput.
    req = ModelRequest(
        request_id=f"req-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        task_id="plan",
        session_id=f"pf:{run_id}:supervisor:plan",
        model_profile=model_profile,
        messages=messages,
        output_schema=schema,
        max_output_tokens=8000,
        temperature=0.2,
        seed=seed,
    )
    resp = gateway.complete(req)
    raw: dict[str, Any] | None = None
    if resp.structured_data:
        raw = resp.structured_data
    elif resp.text:
        raw = json.loads(resp.text)
    if raw is None:
        raise ValueError(f"Planner failed with status {resp.status}")
    return PlannerOutput.model_validate(_normalize_planner_payload(raw))


def _normalize_planner_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept common provider wrappers around PlannerOutput."""
    data = dict(raw)
    if "tasks" not in data and isinstance(data.get("dag"), dict):
        nested = dict(data["dag"])
        for key, value in data.items():
            if key != "dag" and key not in nested:
                nested[key] = value
        data = nested
    if "tasks" not in data and isinstance(data.get("plan"), dict):
        data = dict(data["plan"])
    if "objective" not in data:
        data["objective"] = str(data.get("goal") or data.get("summary") or "planned change")
    return data
