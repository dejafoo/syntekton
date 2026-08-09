"""Lifecycle command facade — public mutations only (SR2).

``LifecycleCommandService`` owns the host-facing mutation surface (submit,
resume, cancel, revise, approve/reject, apply). It delegates to
``RunLifecycleEngine`` without reimplementing trust, policy, or execution.
"""

from __future__ import annotations

from typing import Any

from product_factory.application.ports import RunLifecyclePort
from product_factory.domain.runs import RunManifest, RunRequest


class LifecycleCommandService:
    """Immutable command facade over the lifecycle port."""

    def __init__(self, *, lifecycle: RunLifecyclePort) -> None:
        self._lifecycle = lifecycle

    @property
    def lifecycle(self) -> RunLifecyclePort:
        return self._lifecycle

    def submit(self, request: RunRequest, *, run_id: str | None = None) -> RunManifest:
        return self.lifecycle.run(request, run_id=run_id)

    def resume(self, run_id: str) -> RunManifest:
        return self.lifecycle.resume(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self.lifecycle.cancel(run_id)

    def revise(self, run_id: str, *, note: str) -> RunManifest:
        return self.lifecycle.revise(run_id, note=note)

    def approve(self, run_id: str, *, apply: bool = False) -> dict[str, Any]:
        return self.lifecycle.approve(run_id, apply=apply)

    def reject(self, run_id: str) -> dict[str, Any]:
        return self.lifecycle.reject(run_id)

    def apply(self, run_id: str) -> dict[str, Any]:
        return self.lifecycle.apply_patch(run_id)
