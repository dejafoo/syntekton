"""Handler for the confined `technical_spike` pack."""

from __future__ import annotations

import json

from product_factory.domain.plans import PlannerOutput
from product_factory.schemas import validate_write_payload
from product_factory.workflows.artifacts import ROLE_SPIKE_RESULT
from product_factory.workflows.default_plans import default_technical_spike_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)

SPIKE_REQUIRED_SECTIONS = ("hypothesis", "method", "measurements", "limits")


class TechnicalSpikeHandler:
    pack_id = "technical_spike"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_technical_spike_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_SPIKE_RESULT:
            raise RuntimeError(f"technical_spike does not compose role {role!r}")
        payload = {
            "schema_id": "spike_result.v1",
            "hypothesis": str(ctx.pack_input.get("hypothesis") or ctx.request.request_text),
            "method": {
                "contract_paths": list(ctx.pack_input.get("contract_paths") or []),
                "mode": "local_synthetic",
            },
            "measurements": {
                "dependency_output_count": len(ctx.dependency_outputs),
            },
            "limits": [
                "Synthetic fixtures only",
                "No live authenticated partner endpoint was contacted",
            ],
            "findings": [str(getattr(finding, "message", finding)) for finding in ctx.findings],
        }
        validate_write_payload("spike_result.v1", payload)
        return json.dumps(payload, indent=2, sort_keys=True)

    def required_sections(self, role: str) -> tuple[str, ...]:
        return SPIKE_REQUIRED_SECTIONS

    def validator_id(self, role: str) -> str:
        return "spike_result_schema"

    def authority_class(self) -> AuthorityClass:
        return "isolated_write"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="technical_plan",
                reason="Measured interface constraints can inform an implementation plan",
            ),
            EligibleNextAction(
                pack_id="feasibility_discovery",
                reason="Material unknowns may require additional bounded discovery",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return False
