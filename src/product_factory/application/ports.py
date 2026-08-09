"""Typed application ports shared by hosts and lifecycle services."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from product_factory.domain.runs import RunManifest, RunRequest


@runtime_checkable
class RunLifecyclePort(Protocol):
    """Authoritative lifecycle mutation boundary."""

    def run(self, request: RunRequest, *, run_id: str | None = None) -> RunManifest: ...

    def resume(self, run_id: str) -> RunManifest: ...

    def cancel(self, run_id: str) -> dict[str, Any]: ...

    def revise(self, run_id: str, *, note: str) -> RunManifest: ...

    def approve(self, run_id: str, *, apply: bool = False) -> dict[str, Any]: ...

    def reject(self, run_id: str) -> dict[str, Any]: ...

    def apply_patch(self, run_id: str) -> dict[str, Any]: ...
