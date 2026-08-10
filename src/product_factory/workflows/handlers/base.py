"""Pack handler protocol (PM0.A / WF0)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.plans import PlannerOutput

if TYPE_CHECKING:
    from product_factory.orchestration.composition.input import CompositionInput
    from product_factory.orchestration.composition.service import CompositionService

AuthorityClass = Literal[
    "read_only",
    "isolated_write",
    "approval_gated_write",
    "external_read",
    "external_write",
]

# External mutation is intentionally a single-pack authority. Keeping this
# allowlist beside the authority type prevents future read-oriented handlers
# from acquiring it by convention or through skill composition.
EXTERNAL_WRITE_PACK_IDS: frozenset[str] = frozenset({"deployment_execution"})


def validate_handler_authority(
    pack_id: str,
    authority: AuthorityClass,
    *,
    approval_required: bool = False,
) -> None:
    """Fail closed when external-write authority appears outside deployment."""

    if authority == "external_write":
        if pack_id not in EXTERNAL_WRITE_PACK_IDS:
            raise ConfigurationError(
                f"external_write authority is reserved for {sorted(EXTERNAL_WRITE_PACK_IDS)}, "
                f"not {pack_id!r}"
            )
        if not approval_required:
            raise ConfigurationError(
                f"external_write authority for {pack_id!r} requires an approval-gated pack"
            )


@dataclass(frozen=True)
class EligibleNextAction:
    pack_id: str
    reason: str

    def as_payload(self) -> dict[str, str]:
        return {"pack_id": self.pack_id, "reason": self.reason}


class PackHandler(Protocol):
    pack_id: str

    def plan_template(self, request_text: str) -> PlannerOutput: ...

    def compose(
        self,
        role: str,
        ctx: CompositionInput,
        drafts: CompositionService | None = None,
    ) -> str: ...

    def required_sections(self, role: str) -> tuple[str, ...]: ...

    def validator_id(self, role: str) -> str: ...

    def authority_class(self) -> AuthorityClass: ...

    def eligible_next_actions(self) -> list[EligibleNextAction]: ...

    def findings_are_deliverable(self) -> bool: ...
