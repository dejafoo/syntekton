"""Pack handler protocol (PM0.A / WF0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.plans import PlannerOutput
from product_factory.domain.runs import RunRequest

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


@dataclass
class ComposeContext:
    """Inputs for role-keyed document composition."""

    request: RunRequest
    role: str
    document_name: str
    findings: list[Any] = field(default_factory=list)
    dependency_outputs: list[dict[str, Any]] = field(default_factory=list)
    use_mock: bool = True
    # Coordinator-supplied live generation hooks (optional).
    generate_architecture: Any | None = None
    compose_architecture: Any | None = None
    compose_evidence_report: Any | None = None
    compose_feasibility_dossier: Any | None = None
    compose_change_intake: Any | None = None
    compose_quality_document: Any | None = None
    compose_patch: Any | None = None
    task: Any | None = None
    ctx_messages: list[dict[str, str]] | None = None
    run_id: str = ""
    profile: str = ""
    base_revision: str = ""
    validation_evidence_refs: list[str] = field(default_factory=list)
    validator_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pack_input(self) -> dict[str, Any]:
        """Typed pack payload validated at submit, for prompts and composition."""
        return getattr(self.request, "pack_input", None) or {}


class PackHandler(Protocol):
    pack_id: str

    def plan_template(self, request_text: str) -> PlannerOutput: ...

    def compose(self, role: str, ctx: ComposeContext) -> str: ...

    def required_sections(self, role: str) -> tuple[str, ...]: ...

    def validator_id(self, role: str) -> str: ...

    def authority_class(self) -> AuthorityClass: ...

    def eligible_next_actions(self) -> list[EligibleNextAction]: ...

    def findings_are_deliverable(self) -> bool: ...
