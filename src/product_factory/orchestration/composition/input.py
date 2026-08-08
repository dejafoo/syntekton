"""Typed composition inputs — immutable boundary for SR2 migration.

``CompositionInput`` documents the future typed data passed into composition
owners. Workflow handlers may keep a thin ``ComposeContext`` adapter until
callback fields are retired.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CompositionInput:
    """Immutable snapshot for draft/deliverable assembly.

    Field set matches the SR2 cut plan; values may be ``None`` / empty while
    callers migrate off ``ComposeContext`` callbacks.
    """

    run_snapshot: Mapping[str, Any] | None = None
    compiled_plan: Any | None = None
    task_results: tuple[Any, ...] = ()
    dependency_artifacts: tuple[Any, ...] = ()
    validation_evidence: tuple[Any, ...] = ()
    findings: tuple[Any, ...] = ()
    lineage: Mapping[str, Any] | None = None
    effective_policy: Mapping[str, Any] | None = None


def composition_input_from_compose_context(ctx: Any) -> CompositionInput:
    """Optional adapter from legacy ``ComposeContext`` during migration."""
    dependency_outputs = tuple(getattr(ctx, "dependency_outputs", None) or ())
    findings = tuple(getattr(ctx, "findings", None) or ())
    validation_refs = tuple(getattr(ctx, "validation_evidence_refs", None) or ())
    validator_results = tuple(getattr(ctx, "validator_results", None) or ())
    run_snapshot = {
        "run_id": getattr(ctx, "run_id", "") or "",
        "role": getattr(ctx, "role", "") or "",
        "document_name": getattr(ctx, "document_name", "") or "",
        "profile": getattr(ctx, "profile", "") or "",
        "base_revision": getattr(ctx, "base_revision", "") or "",
        "use_mock": bool(getattr(ctx, "use_mock", True)),
    }
    return CompositionInput(
        run_snapshot=run_snapshot,
        compiled_plan=None,
        task_results=(),
        dependency_artifacts=dependency_outputs,
        validation_evidence=validation_refs + validator_results,
        findings=findings,
        lineage=None,
        effective_policy=None,
    )
