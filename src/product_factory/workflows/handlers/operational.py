"""Shared composition for read-only operational workflow packs (PM5.D)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import ROLE_OPERATIONAL_RECORD
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)

OperationalRecordType = Literal["incident_triage", "service_health_review"]
FollowUpType = Literal[
    "change_intake",
    "repository_investigation",
    "rollback_decision",
    "human_escalation",
    "none",
]
FOLLOW_UP_TYPES = frozenset(
    {
        "change_intake",
        "repository_investigation",
        "rollback_decision",
        "human_escalation",
        "none",
    }
)


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    if not isinstance(value, (list, tuple, set)):
        text = str(value).strip()
        return [text] if text else []
    return [str(item).strip() for item in value if str(item).strip()]


def _labeled_objects(value: object, *, label: str, text_key: str) -> list[dict[str, object]]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    output: list[dict[str, object]] = []
    for item in values:
        row: dict[str, object] = (
            {str(key): value for key, value in item.items()}
            if isinstance(item, dict)
            else {text_key: str(item)}
        )
        row["label"] = label
        output.append(row)
    return output


class OperationalHandler:
    """Base handler whose subclasses select a fixed operational record type."""

    pack_id: str
    record_type: OperationalRecordType
    plan_factory: Callable[[str], PlannerOutput]

    def plan_template(self, request_text: str) -> PlannerOutput:
        return self.plan_factory(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_OPERATIONAL_RECORD:
            raise RuntimeError(f"{self.pack_id} does not compose role {role!r}")
        data = ctx.pack_input
        evidence = _labeled_objects(
            data.get("evidence") or data.get("observations") or data.get("signals"),
            label="observation",
            text_key="observation",
        )
        hypotheses = _labeled_objects(
            data.get("hypotheses") or data.get("inferences"),
            label="inference",
            text_key="hypothesis",
        )
        timeline = _labeled_objects(
            data.get("timeline"),
            label="observation",
            text_key="event",
        )
        if not evidence:
            evidence = [
                {
                    "label": "observation",
                    "observation": "No bounded operational evidence was supplied",
                    "status": "unknown",
                }
            ]

        requested = str(data.get("follow_up") or "").strip()
        if requested in FOLLOW_UP_TYPES:
            follow_up: FollowUpType = requested  # type: ignore[assignment]
        elif bool(data.get("rollback_candidate")):
            follow_up = "rollback_decision"
        elif bool(data.get("slo_breach")):
            follow_up = "change_intake"
        elif not hypotheses or bool(data.get("unknown")):
            follow_up = "human_escalation"
        else:
            follow_up = "repository_investigation"

        start = str(data.get("start") or data.get("window_start") or "")
        end = str(data.get("end") or data.get("window_end") or "")
        reason = (
            str(data.get("follow_up_reason") or "").strip()
            or {
                "change_intake": "Observed service-health gap requires a bounded change proposal",
                "repository_investigation": "Operational hypothesis requires read-only code investigation",
                "rollback_decision": "Rollback remains a human decision; this workflow cannot execute it",
                "human_escalation": "Evidence is insufficient or impact requires an operator decision",
                "none": "No follow-up is supported by the supplied evidence",
            }[follow_up]
        )
        payload = {
            "schema_id": "operational_record.v1",
            "record_type": self.record_type,
            "service_id": str(data.get("service_id") or "unknown"),
            "environment": str(data.get("environment") or "unknown"),
            "time_window": {"start": start, "end": end},
            "incident_id": str(data.get("incident_id") or "") or None,
            "query_hashes": _strings(data.get("query_hashes") or data.get("query_hash")),
            "impact": {
                "label": "observation",
                "summary": str(data.get("impact") or data.get("impact_summary") or "unknown"),
            },
            "timeline": timeline,
            "evidence": evidence,
            "hypotheses": hypotheses,
            "recommendations": _strings(data.get("recommendations")),
            "follow_up": follow_up,
            "follow_up_action": {
                "type": follow_up,
                "reason": reason,
                "requires_human": follow_up in {"rollback_decision", "human_escalation"},
            },
            "authority": {
                "class": "external_read",
                "deploy": False,
                "restart": False,
                "traffic_mutation": False,
            },
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def required_sections(self, role: str) -> tuple[str, ...]:
        return ()

    def validator_id(self, role: str) -> str:
        return "operational_record_contract"

    def authority_class(self) -> AuthorityClass:
        return "external_read"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="change_intake",
                reason="Observed operational gaps can be framed as a bounded change",
            ),
            EligibleNextAction(
                pack_id="repository_investigation",
                reason="Operational hypotheses can be investigated read-only",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return True


__all__ = [
    "FOLLOW_UP_TYPES",
    "FollowUpType",
    "OperationalHandler",
    "OperationalRecordType",
]
