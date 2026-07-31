"""Registered pack handlers — keyed by pack.id, never planner-supplied code."""

from __future__ import annotations

from typing import Any

from product_factory.domain.errors import ConfigurationError
from product_factory.workflows.handlers.base import (
    ComposeContext,
    EligibleNextAction,
    PackHandler,
)
from product_factory.workflows.handlers.quality_gate import QualityGateHandler
from product_factory.workflows.handlers.repository_change import RepositoryChangeHandler
from product_factory.workflows.handlers.repository_investigation import (
    RepositoryInvestigationHandler,
)
from product_factory.workflows.handlers.technical_plan import TechnicalPlanHandler
from product_factory.workflows.registry import canonical_workflow_id

_HANDLERS: dict[str, PackHandler] = {
    QualityGateHandler().pack_id: QualityGateHandler(),
    RepositoryChangeHandler().pack_id: RepositoryChangeHandler(),
    RepositoryInvestigationHandler().pack_id: RepositoryInvestigationHandler(),
    TechnicalPlanHandler().pack_id: TechnicalPlanHandler(),
}


def handler_for(pack_id: str) -> PackHandler:
    canonical = canonical_workflow_id(pack_id)
    handler = _HANDLERS.get(canonical)
    if handler is None:
        raise ConfigurationError(f"No pack handler registered for {pack_id!r}")
    return handler


def eligible_next_actions_for_workflow(workflow_type: str) -> list[dict[str, Any]]:
    try:
        handler = handler_for(workflow_type)
    except ConfigurationError:
        return []
    return [action.as_payload() for action in handler.eligible_next_actions()]


__all__ = [
    "ComposeContext",
    "EligibleNextAction",
    "PackHandler",
    "eligible_next_actions_for_workflow",
    "handler_for",
]
