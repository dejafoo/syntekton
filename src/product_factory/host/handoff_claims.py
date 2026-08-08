"""Convert host/v2 handoff claims into durable-backed HandoffRef values."""

from __future__ import annotations

from product_factory.domain.artifacts import HandoffRef
from product_factory.host.protocol_v2 import HandoffClaim
from product_factory.persistence.database import Database
from product_factory.trust.handoffs import HandoffRefusal


def claims_to_handoff_refs(db: Database, claims: list[HandoffClaim]) -> list[HandoffRef]:
    """Resolve `{handoff_id, expected_digest}` against durable authority.

    The client may only assert identity + digest. Producer fields are filled
    from the durable handoff record so forged fat claims cannot influence
    resolution.
    """
    refs: list[HandoffRef] = []
    for claim in claims:
        raw = db.get_handoff_record(claim.handoff_id)
        if raw is None:
            raise HandoffRefusal(f"Unknown handoff: {claim.handoff_id}")
        digest = str(raw["sha256"]).lower()
        if digest != claim.expected_digest.lower():
            raise HandoffRefusal(
                f"Handoff {claim.handoff_id} digest assertion does not match durable record"
            )
        refs.append(
            HandoffRef(
                schema_id=str(raw["schema_id"]),
                digest=digest,
                producer_run_id=str(raw["producer_run_id"]),
                producer_task_id=str(raw["producer_task_id"]),
                role=str(raw["role"]),
                state=raw["state"],  # type: ignore[arg-type]
                metadata={"handoff_id": claim.handoff_id},
            )
        )
    return refs
