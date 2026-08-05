"""Registered pack handlers — keyed by pack.id, never planner-supplied code."""

from __future__ import annotations

from typing import Any

from product_factory.domain.errors import ConfigurationError
from product_factory.workflows.handlers.base import (
    ComposeContext,
    EligibleNextAction,
    PackHandler,
    validate_handler_authority,
)
from product_factory.workflows.handlers.change_intake import ChangeIntakeHandler
from product_factory.workflows.handlers.deployment_execution import DeploymentExecutionHandler
from product_factory.workflows.handlers.feasibility_discovery import (
    FeasibilityDiscoveryHandler,
)
from product_factory.workflows.handlers.incident_triage import IncidentTriageHandler
from product_factory.workflows.handlers.quality_gate import QualityGateHandler
from product_factory.workflows.handlers.release_readiness import ReleaseReadinessHandler
from product_factory.workflows.handlers.repository_change import RepositoryChangeHandler
from product_factory.workflows.handlers.repository_investigation import (
    RepositoryInvestigationHandler,
)
from product_factory.workflows.handlers.service_health_review import ServiceHealthReviewHandler
from product_factory.workflows.handlers.technical_plan import TechnicalPlanHandler
from product_factory.workflows.handlers.technical_spike import TechnicalSpikeHandler
from product_factory.workflows.registry import (
    canonical_workflow_id,
    resolve_workflow_pack,
)

_HANDLERS: dict[str, PackHandler] = {
    ChangeIntakeHandler().pack_id: ChangeIntakeHandler(),
    DeploymentExecutionHandler().pack_id: DeploymentExecutionHandler(),
    FeasibilityDiscoveryHandler().pack_id: FeasibilityDiscoveryHandler(),
    IncidentTriageHandler().pack_id: IncidentTriageHandler(),
    QualityGateHandler().pack_id: QualityGateHandler(),
    ReleaseReadinessHandler().pack_id: ReleaseReadinessHandler(),
    RepositoryChangeHandler().pack_id: RepositoryChangeHandler(),
    RepositoryInvestigationHandler().pack_id: RepositoryInvestigationHandler(),
    ServiceHealthReviewHandler().pack_id: ServiceHealthReviewHandler(),
    TechnicalPlanHandler().pack_id: TechnicalPlanHandler(),
    TechnicalSpikeHandler().pack_id: TechnicalSpikeHandler(),
}


def _validate_handler(handler: PackHandler, canonical: str) -> None:
    pack = resolve_workflow_pack(canonical)
    validate_handler_authority(
        canonical,
        handler.authority_class(),
        approval_required=pack.execution_policy.approval_required,
    )


def register_pack_handler(handler: PackHandler) -> None:
    """Register trusted runtime behavior for a canonical pack id."""

    canonical = canonical_workflow_id(handler.pack_id)
    _validate_handler(handler, canonical)
    if canonical in _HANDLERS:
        raise ConfigurationError(f"Pack handler already registered for {handler.pack_id!r}")
    _HANDLERS[canonical] = handler


def handler_for(pack_id: str) -> PackHandler:
    canonical = canonical_workflow_id(pack_id)
    handler = _HANDLERS.get(canonical)
    if handler is None:
        raise ConfigurationError(f"No pack handler registered for {pack_id!r}")
    _validate_handler(handler, canonical)
    return handler


def eligible_next_actions_for_workflow(
    workflow_type: str,
    *,
    outcome: str | None = None,
) -> list[dict[str, Any]]:
    try:
        handler = handler_for(workflow_type)
    except ConfigurationError:
        return []
    actions = [action.as_payload() for action in handler.eligible_next_actions()]
    if workflow_type == "release_readiness" and outcome == "ready":
        actions.append(
            EligibleNextAction(
                pack_id="deployment_execution",
                reason="Ready release plan is eligible for an approval-gated staging deployment",
            ).as_payload()
        )
    return actions


__all__ = [
    "ComposeContext",
    "EligibleNextAction",
    "PackHandler",
    "eligible_next_actions_for_workflow",
    "handler_for",
    "register_pack_handler",
]
