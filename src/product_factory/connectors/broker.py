"""Connector broker — the policy gate between `ToolBroker` and any provider.

`ToolBroker` stays the sole execution path (ADR-004). It performs the grant,
`max_calls`, and budget checks it always has, then hands connector-backed tool
names here. This layer adds the checks that only make sense for third-party
providers: is the connector enabled by the operator, is it read-only, is its
credential present, is the response bounded and labelled untrusted.

Every outcome is audited, including denials — a blocked egress attempt is the
event worth keeping.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from product_factory.connectors.errors import (
    ConnectorError,
    ConnectorPolicyDenied,
    ConnectorTimeout,
    ConnectorUnavailable,
)
from product_factory.connectors.manifest import ConnectorManifest, ConnectorToolSpec
from product_factory.connectors.policy import ConnectorsConfig
from product_factory.connectors.registry import ConnectorInvocation, ConnectorRegistry
from product_factory.connectors.result import (
    ConnectorResult,
    Payload,
    bound_payload,
    sha256_of,
)

EVENT_INVOKED = "connector.invoked"
EVENT_DENIED = "connector.denied"
EVENT_FAILED = "connector.failed"

ConnectorAudit = Callable[[str, dict[str, Any]], None]
ApprovalCheck = Callable[[ConnectorManifest, ConnectorToolSpec], bool]


class ConnectorBroker:
    """Policy, audit, and containment for connector-backed tools."""

    def __init__(
        self,
        registry: ConnectorRegistry,
        *,
        config: ConnectorsConfig | None = None,
        audit: ConnectorAudit | None = None,
        environ: Mapping[str, str] | None = None,
        mock: bool = False,
        approvals: ApprovalCheck | None = None,
    ) -> None:
        self.registry = registry
        self.config = config or ConnectorsConfig()
        self.audit = audit
        self.environ = environ if environ is not None else os.environ
        self.mock = mock
        self.approvals = approvals
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()

    def handles(self, tool_name: str) -> bool:
        return self.registry.handles(tool_name)

    def enabled_tool_names(self) -> frozenset[str]:
        """Connector tools an operator has actually switched on.

        Grant construction uses this so a task is never handed a tool name that
        policy would reject at call time.
        """
        names: set[str] = set()
        for entry in self.registry:
            manifest = entry.manifest
            if not self.config.is_enabled(manifest.connector_id):
                continue
            if not manifest.read_only and not self.config.allow_write_connectors:
                continue
            names.update(manifest.tool_names)
        return frozenset(names)

    def grantable_tool_names(self, required_tool_classes: Iterable[str]) -> frozenset[str]:
        """Connector tools a task may be granted.

        Both conditions must hold: the task's pack asked for the tool class, and
        an operator enabled the connector. A task never receives an external
        provider by falling through a default, which is why the caller subtracts
        all connector tools before adding this back.
        """
        classes = {name for name in required_tool_classes if name}
        if not classes:
            return frozenset()
        return self.registry.tool_names_for_classes(classes) & self.enabled_tool_names()

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        task_id: str,
        tool_call_id: str,
        run_id: str = "",
        invocation_options: Mapping[str, Any] | None = None,
        audit: ConnectorAudit | None = None,
    ) -> dict[str, Any]:
        decision_id = f"cd-{uuid.uuid4().hex[:12]}"
        args_hash = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode("utf-8", "replace")
        ).hexdigest()
        base: dict[str, Any] = {
            "policy_decision_id": decision_id,
            "tool_name": tool_name,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "run_id": run_id,
            "arguments_hash": args_hash,
        }

        try:
            entry = self.registry.for_tool(tool_name)
        except ConnectorError as exc:
            self._emit(EVENT_DENIED, {**base, **_error_payload(exc)}, audit=audit)
            raise

        manifest = entry.manifest
        base["connector_id"] = manifest.connector_id
        base["provider"] = manifest.provider
        base["connector_version"] = manifest.version

        started = time.perf_counter()
        try:
            invocation = self._authorize(
                manifest=manifest,
                tool_name=tool_name,
                arguments=arguments,
                task_id=task_id,
                tool_call_id=tool_call_id,
                invocation_options=invocation_options,
            )
        except ConnectorError as exc:
            self._emit(EVENT_DENIED, {**base, **_error_payload(exc)}, audit=audit)
            raise

        try:
            raw = self._call_handler(entry.handler, invocation, manifest)
        except ConnectorError as exc:
            payload = {
                **base,
                **_error_payload(exc),
                "duration_ms": _elapsed_ms(started),
            }
            self._emit(
                EVENT_DENIED if exc.exit_code == 8 else EVENT_FAILED,
                payload,
                audit=audit,
            )
            raise

        result = self._envelope(raw, manifest=manifest, tool_name=tool_name, invocation=invocation)
        self._emit(
            EVENT_INVOKED,
            {
                **base,
                "duration_ms": _elapsed_ms(started),
                "result_sha256": result["result_sha256"],
                "truncated": result["truncated"],
                "provenance": result["provenance"],
                "mock": invocation.mock,
                "result_excerpt": self._retained_excerpt(manifest, result["result"]),
            },
            audit=audit,
        )
        return result

    def _authorize(
        self,
        *,
        manifest: ConnectorManifest,
        tool_name: str,
        arguments: dict[str, Any],
        task_id: str,
        tool_call_id: str,
        invocation_options: Mapping[str, Any] | None = None,
    ) -> ConnectorInvocation:
        connector_id = manifest.connector_id
        if not self.config.is_enabled(connector_id):
            raise ConnectorPolicyDenied(
                f"Connector {connector_id!r} is not enabled in connectors.yaml",
                connector_id=connector_id,
                tool_name=tool_name,
                details={"enabled_connectors": list(self.config.enabled_ids())},
            )

        tool = manifest.tool(tool_name)
        if tool is None:
            raise ConnectorPolicyDenied(
                f"Connector {connector_id!r} does not declare tool {tool_name!r}",
                connector_id=connector_id,
                tool_name=tool_name,
                details={"declared_tools": sorted(manifest.tool_names)},
            )

        if not manifest.read_only and not self.config.allow_write_connectors:
            raise ConnectorPolicyDenied(
                f"Connector {connector_id!r} requests "
                f"{sorted(manifest.permissions - {'read'})} but write-capable "
                "connectors are disabled",
                connector_id=connector_id,
                tool_name=tool_name,
                details={"permissions": sorted(manifest.permissions)},
            )

        requires_approval = self.config.requires_approval(manifest) or tool.requires_approval
        approved = False
        if requires_approval:
            approved = bool(
                (invocation_options or {}).get("_approval_binding_verified")
                or (self.approvals is not None and self.approvals(manifest, tool))
            )
            if not approved:
                raise ConnectorPolicyDenied(
                    f"Connector {connector_id!r} requires operator approval",
                    connector_id=connector_id,
                    tool_name=tool_name,
                    details={
                        "requires_approval": True,
                        "tool_requires_approval": tool.requires_approval,
                    },
                )

        settings = self.config.settings_for(connector_id)
        egress = self.config.effective_egress(manifest)
        secret = self._resolve_secret(manifest, tool_name)
        declared_timeout = manifest.timeout_for(tool)

        options = dict(settings.options)
        # Run-scoped objects (for example SourceLedger) are supplied only by the
        # trusted ToolBroker and override static YAML options.
        options.update(invocation_options or {})
        # This marker is derived exclusively from the trusted approval callback.
        # Connector arguments cannot forge it.
        options["_connector_approved"] = approved
        return ConnectorInvocation(
            connector_id=connector_id,
            tool_name=tool_name,
            upstream_name=tool.upstream_name,
            arguments=dict(arguments),
            task_id=task_id,
            tool_call_id=tool_call_id,
            timeout_seconds=self.config.effective_timeout(manifest, declared_timeout),
            manifest=manifest,
            tool=tool,
            egress=egress,
            mock=self.mock,
            secret=secret,
            options=options,
            max_result_bytes=self.config.effective_max_result_bytes(manifest),
        )

    def _resolve_secret(self, manifest: ConnectorManifest, tool_name: str) -> str | None:
        """Read the credential by variable name.

        Mock mode skips this so CI never needs provider keys.
        """
        if manifest.auth_env_var is None or self.mock:
            return None
        secret = (self.environ.get(manifest.auth_env_var) or "").strip()
        if not secret:
            raise ConnectorUnavailable(
                f"Connector {manifest.connector_id!r} needs {manifest.auth_env_var} to be set",
                connector_id=manifest.connector_id,
                tool_name=tool_name,
                details={"auth_env_var": manifest.auth_env_var},
            )
        return secret

    def _semaphore(self, manifest: ConnectorManifest) -> threading.BoundedSemaphore:
        with self._lock:
            existing = self._semaphores.get(manifest.connector_id)
            if existing is None:
                existing = threading.BoundedSemaphore(max(1, manifest.max_concurrency))
                self._semaphores[manifest.connector_id] = existing
            return existing

    def _call_handler(
        self,
        handler: Callable[[ConnectorInvocation], ConnectorResult | dict[str, Any]],
        invocation: ConnectorInvocation,
        manifest: ConnectorManifest,
    ) -> ConnectorResult:
        semaphore = self._semaphore(manifest)
        if not semaphore.acquire(timeout=invocation.timeout_seconds):
            raise ConnectorTimeout(
                f"Connector {manifest.connector_id!r} is at its concurrency limit "
                f"({manifest.max_concurrency})",
                connector_id=manifest.connector_id,
                tool_name=invocation.tool_name,
                details={"max_concurrency": manifest.max_concurrency},
            )
        try:
            raw = handler(invocation)
        except ConnectorError:
            raise
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise ConnectorTimeout(
                f"Connector {manifest.connector_id!r} timed out after "
                f"{invocation.timeout_seconds}s",
                connector_id=manifest.connector_id,
                tool_name=invocation.tool_name,
                details={"timeout_seconds": invocation.timeout_seconds},
            ) from exc
        except Exception as exc:
            # A provider library raising something exotic is still an outage, not
            # a model problem: keep it typed so no retry path treats it as one.
            raise ConnectorUnavailable(
                f"Connector {manifest.connector_id!r} failed: {type(exc).__name__}",
                connector_id=manifest.connector_id,
                tool_name=invocation.tool_name,
                details={"error_type": type(exc).__name__},
            ) from exc
        finally:
            semaphore.release()

        if isinstance(raw, ConnectorResult):
            return raw
        if isinstance(raw, dict):
            return ConnectorResult(payload=raw)
        raise ConnectorUnavailable(
            f"Connector {manifest.connector_id!r} returned "
            f"{type(raw).__name__}, expected ConnectorResult or dict",
            connector_id=manifest.connector_id,
            tool_name=invocation.tool_name,
        )

    def _envelope(
        self,
        result: ConnectorResult,
        *,
        manifest: ConnectorManifest,
        tool_name: str,
        invocation: ConnectorInvocation,
    ) -> dict[str, Any]:
        payload, truncated = bound_payload(result.payload, invocation.max_result_bytes)
        envelope = {
            "connector_id": manifest.connector_id,
            "connector_version": manifest.version,
            "tool": tool_name,
            # Third-party content, always. Prompt builders rely on this label to
            # frame the payload as data rather than instructions.
            "trust_label": "untrusted",
            "result": payload,
            "result_sha256": sha256_of(payload),
            "truncated": truncated,
            "provenance": [item.as_payload() for item in result.provenance],
        }
        if result.metadata:
            # Handler-controlled data for ToolBroker post-processing. It is not
            # retained in connector audit events and must be removed before a
            # result is exposed to the caller.
            envelope["_handler_metadata"] = result.metadata
        return envelope

    def _retained_excerpt(self, manifest: ConnectorManifest, payload: Payload) -> Any:
        """Apply the manifest's retention policy to what the audit trail keeps."""
        retention = manifest.result_retention
        if retention == "none":
            return None
        if retention == "hash_only":
            return None
        if retention == "full":
            return payload
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        return text[:2_000]

    def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        audit: ConnectorAudit | None = None,
    ) -> None:
        sink = audit if audit is not None else self.audit
        if sink is None:
            return
        sink(event_type, payload)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _error_payload(exc: ConnectorError) -> dict[str, Any]:
    return {
        "denial_code": exc.denial_code,
        "error": exc.message,
        "details": exc.details,
    }


__all__ = [
    "EVENT_DENIED",
    "EVENT_FAILED",
    "EVENT_INVOKED",
    "ApprovalCheck",
    "ConnectorAudit",
    "ConnectorBroker",
]
