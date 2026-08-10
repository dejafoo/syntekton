"""Review default policy: optional except high-risk fixed plans."""

from __future__ import annotations

from product_factory.domain.runs import RunRequest
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.plan_transforms import apply_plan_transforms
from product_factory.workflows.registry import resolve_workflow_pack


def _plan(request: RunRequest):
    pack = resolve_workflow_pack(request.workflow_type)
    proposal = handler_for(pack.id).plan_template(request.request_text)
    return apply_plan_transforms(proposal, request=request, pack=pack)


def test_low_risk_default_plan_omits_review() -> None:
    proposal = _plan(
        RunRequest(
            request_id="low-risk",
            workflow_type="code_change",
            request_text="Add a cache helper",
            approval_policy="none",
            metadata={"planner_mode": "fixed"},
        ),
    )
    caps = {task.capability for task in proposal.tasks}
    assert "independent_review" not in caps
    assert "implementation" in caps


def test_high_risk_fixed_plan_keeps_review() -> None:
    proposal = _plan(
        RunRequest(
            request_id="high-risk",
            workflow_type="code_change",
            request_text="Change authentication and database permissions",
            approval_policy="none",
            metadata={"planner_mode": "fixed"},
        ),
    )
    caps = {task.capability for task in proposal.tasks}
    assert "independent_review" in caps
