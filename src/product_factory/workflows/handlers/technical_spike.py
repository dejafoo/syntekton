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
        typed_refs: list[dict[str, str]] = []
        measurements: dict[str, object] = {}
        for dependency in ctx.dependency_outputs:
            for ref in dependency.get("artifact_refs") or []:
                schema_id = str(ref.get("schema_id") or "")
                if schema_id not in {
                    "contract_inventory.v1",
                    "contract_compatibility.v1",
                    "contract_simulation.v1",
                }:
                    continue
                typed_refs.append(
                    {
                        "role": str(ref.get("role") or ref.get("logical_name") or ""),
                        "sha256": str(ref.get("sha256") or ""),
                        "schema_id": schema_id,
                    }
                )
            for excerpt in dependency.get("artifact_excerpts") or []:
                schema_id = str(excerpt.get("schema_id") or "")
                if schema_id not in {
                    "contract_inventory.v1",
                    "contract_compatibility.v1",
                    "contract_simulation.v1",
                }:
                    continue
                try:
                    body = json.loads(str(excerpt.get("content") or "{}"))
                except json.JSONDecodeError:
                    continue
                result = body.get("result") or {}
                if schema_id == "contract_inventory.v1":
                    measurements["address_count"] = result.get("address_count", 0)
                    measurements["schema_count"] = result.get("schema_count", 0)
                elif schema_id == "contract_compatibility.v1":
                    measurements["compatibility"] = result.get("classification", "unknown")
                    measurements["change_count"] = len(result.get("changes") or [])
                elif schema_id == "contract_simulation.v1":
                    measurements["simulation_status"] = result.get("status", "unknown")
                    measurements.update(result.get("measurements") or {})
        roles = {ref["schema_id"] for ref in typed_refs}
        if "contract_inventory.v1" not in roles:
            raise RuntimeError("technical_spike requires a typed contract inventory")
        if "contract_compatibility.v1" not in roles:
            raise RuntimeError("technical_spike requires compatibility evidence")
        if "contract_simulation.v1" not in roles:
            raise RuntimeError("technical_spike requires simulation evidence")
        payload = {
            "schema_id": "spike_result.v1",
            "hypothesis": str(ctx.pack_input.get("hypothesis") or ctx.request.request_text),
            "method": {
                "contract_paths": list(ctx.pack_input.get("contract_paths") or []),
                "mode": "local_synthetic",
            },
            "measurements": measurements,
            "limits": [
                "Synthetic fixtures only",
                "No live authenticated partner endpoint was contacted",
            ],
            "findings": [str(getattr(finding, "message", finding)) for finding in ctx.findings],
            "artifact_refs": typed_refs,
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
