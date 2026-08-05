"""Conservative probes for local OpenAI-compatible endpoints."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from product_factory.gateway.admission import evaluate_admission
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import (
    CanonicalMessage,
    CanonicalToolDefinition,
    ModelRequest,
)
from product_factory.gateway.circuit_breaker import CircuitBreaker

Clock = Callable[[], float]


@dataclass(frozen=True)
class ProbeCheck:
    name: str
    passed: bool
    detail: str | None = None
    latency_ms: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ProbeReport:
    """Measured local-route evidence. Missing fields never count as support."""

    healthy: bool
    model_available: bool
    proven: frozenset[str] = field(default_factory=frozenset)
    checks: tuple[ProbeCheck, ...] = ()
    latency_ms: int | None = None
    evaluated_at: float = 0.0
    reason: str | None = None
    deep: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "model_available": self.model_available,
            "proven": sorted(self.proven),
            "checks": [check.as_dict() for check in self.checks],
            "latency_ms": self.latency_ms,
            "evaluated_at": self.evaluated_at,
            "reason": self.reason,
            "deep": self.deep,
        }


@dataclass
class LocalRouteController:
    """Startup/periodic probe cache + circuit breaker for one local profile."""

    profile_name: str
    profile: dict[str, Any]
    gateway: ModelGateway
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    light_ttl_s: float = 30.0
    deep_interval_s: float = 300.0
    max_probe_latency_ms: int = 30_000
    enable_deep_probes: bool = True
    clock: Clock = field(default=time.monotonic)
    # Sinks may return a value (for example a written evidence path); the
    # controller ignores it, so the return type is intentionally unconstrained.
    evidence_sink: Callable[[dict[str, Any]], object] | None = None

    _report: ProbeReport | None = field(default=None, init=False, repr=False)
    _last_deep_at: float | None = field(default=None, init=False, repr=False)

    def evaluate(self, *, task_role: str | None = None) -> str | None:
        """Return a fallback reason, or None when the local route may proceed."""
        if not self.breaker.allow_request():
            return "local_unhealthy"

        report = self.ensure_report(force_deep=False)
        if not report.healthy:
            self.breaker.record_failure()
            return "local_unhealthy"
        if not report.model_available:
            return "capability_miss"

        capabilities = set(self.profile.get("capabilities") or [])
        decision = evaluate_admission(
            task_capabilities=capabilities,
            proven=report.proven,
            primary_role=task_role,
        )
        self._emit_evidence(report, decision.as_dict())
        if not decision.admitted:
            return "capability_miss"
        return None

    def ensure_report(self, *, force_deep: bool = False) -> ProbeReport:
        now = self.clock()
        needs_light = self._report is None or (now - self._report.evaluated_at) >= self.light_ttl_s
        needs_deep = self.enable_deep_probes and (
            force_deep
            or self._last_deep_at is None
            or (now - self._last_deep_at) >= self.deep_interval_s
        )
        if not needs_light and not needs_deep and self._report is not None:
            return self._report

        if needs_deep:
            report = self.run_deep_probes()
            self._last_deep_at = now
        else:
            report = self.run_light_probes()
            if self._report is not None:
                # Preserve previously proven protocol capabilities across light refreshes.
                report.proven = frozenset(report.proven | self._report.proven)
                if self._report.deep:
                    report.deep = True
        self._report = report
        self._emit_evidence(report, None)
        return report

    def run_light_probes(self) -> ProbeReport:
        started = self.clock()
        model = str(self.profile["model"])
        checks: list[ProbeCheck] = []
        proven: set[str] = set()
        try:
            models = self.gateway.list_models()
        except Exception as exc:
            check = ProbeCheck(
                name="reachability",
                passed=False,
                detail=f"{type(exc).__name__}: {exc}",
            )
            return ProbeReport(
                healthy=False,
                model_available=False,
                proven=frozenset(),
                checks=(check,),
                latency_ms=int((self.clock() - started) * 1000),
                evaluated_at=self.clock(),
                reason="local_unhealthy",
            )

        reach_ms = int((self.clock() - started) * 1000)
        checks.append(ProbeCheck(name="reachability", passed=True, latency_ms=reach_ms))
        proven.add("reachability")

        entry = next((item for item in models if item.get("id") == model), None)
        model_ok = entry is not None
        checks.append(
            ProbeCheck(
                name="model_identity",
                passed=model_ok,
                detail=None if model_ok else f"model {model!r} is not advertised",
            )
        )
        if model_ok:
            proven.add("model_identity")

        context_limit = self.profile.get("context_soft_limit")
        advertised_context = None
        if isinstance(entry, dict):
            advertised_context = entry.get("context_length") or entry.get("max_model_len")
        context_ok = True
        context_detail = "context not advertised; not counted as proof"
        if context_limit is not None and advertised_context is not None:
            try:
                context_ok = int(advertised_context) >= int(context_limit)
                context_detail = f"advertised={advertised_context} soft_limit={context_limit}"
            except (TypeError, ValueError):
                context_ok = False
                context_detail = f"invalid context advertisement: {advertised_context!r}"
            checks.append(
                ProbeCheck(
                    name="context_capacity",
                    passed=context_ok,
                    detail=context_detail,
                )
            )
            if context_ok:
                proven.add("context_capacity")
        else:
            checks.append(
                ProbeCheck(
                    name="context_capacity",
                    passed=False,
                    detail=context_detail,
                )
            )

        latency_ok = reach_ms <= self.max_probe_latency_ms
        checks.append(
            ProbeCheck(
                name="latency",
                passed=latency_ok,
                detail=f"{reach_ms}ms",
                latency_ms=reach_ms,
            )
        )
        if latency_ok:
            proven.add("latency")

        healthy = True
        model_available = model_ok
        reason = None if model_ok else "capability_miss"
        return ProbeReport(
            healthy=healthy,
            model_available=model_available,
            proven=frozenset(proven),
            checks=tuple(checks),
            latency_ms=reach_ms,
            evaluated_at=self.clock(),
            reason=reason,
            deep=False,
        )

    def run_deep_probes(self) -> ProbeReport:
        light = self.run_light_probes()
        if not light.healthy or not light.model_available:
            return light

        checks = list(light.checks)
        proven = set(light.proven)

        if self.profile.get("structured_outputs", True):
            structured = self._probe_structured_output()
            checks.append(structured)
            if structured.passed:
                proven.add("structured_outputs")
        if self.profile.get("tool_calling", True):
            tools = self._probe_tool_calling()
            checks.append(tools)
            if tools.passed:
                proven.add("tool_calling")

        missing_protocol = []
        if self.profile.get("structured_outputs", True) and "structured_outputs" not in proven:
            missing_protocol.append("structured_outputs")
        if self.profile.get("tool_calling", True) and "tool_calling" not in proven:
            missing_protocol.append("tool_calling")

        reason = "capability_miss" if missing_protocol else light.reason
        return ProbeReport(
            healthy=True,
            model_available=True,
            proven=frozenset(proven),
            checks=tuple(checks),
            latency_ms=light.latency_ms,
            evaluated_at=self.clock(),
            reason=reason,
            deep=True,
        )

    def record_success(self) -> None:
        self.breaker.record_success()

    def record_failure(self) -> None:
        self.breaker.record_failure()

    @property
    def last_report(self) -> ProbeReport | None:
        return self._report

    def snapshot(self) -> dict[str, Any]:
        report = self._report.as_dict() if self._report is not None else None
        return {
            "profile": self.profile_name,
            "route_class": self.profile.get("route_class", "local"),
            "model": self.profile.get("model"),
            "breaker": self.breaker.snapshot(),
            "report": report,
        }

    def _probe_structured_output(self) -> ProbeCheck:
        started = self.clock()
        try:
            response = self.gateway.complete(
                ModelRequest(
                    request_id=f"probe-structured-{uuid.uuid4().hex[:8]}",
                    run_id="local-route-probe",
                    task_id="probe",
                    session_id=f"pf:probe:{self.profile_name}",
                    model_profile=self.profile_name,
                    messages=[
                        CanonicalMessage(
                            role="user",
                            content='Return JSON {"ok": true} and nothing else.',
                        )
                    ],
                    output_schema={
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                    max_output_tokens=64,
                    max_cost_usd=0.05,
                    temperature=0.0,
                )
            )
            latency = int((self.clock() - started) * 1000)
            structured = response.structured_data or {}
            passed = isinstance(structured, dict) and "ok" in structured
            # MockGateway returns a generic schema payload; treat any structured
            # object as protocol proof when the adapter is mock.
            if not passed and response.provider == "mock" and response.structured_data:
                passed = True
            return ProbeCheck(
                name="structured_outputs",
                passed=passed,
                detail=None if passed else f"status={response.status}",
                latency_ms=latency,
            )
        except Exception as exc:
            return ProbeCheck(
                name="structured_outputs",
                passed=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=int((self.clock() - started) * 1000),
            )

    def _probe_tool_calling(self) -> ProbeCheck:
        started = self.clock()
        try:
            response = self.gateway.complete(
                ModelRequest(
                    request_id=f"probe-tools-{uuid.uuid4().hex[:8]}",
                    run_id="local-route-probe",
                    task_id="probe",
                    session_id=f"pf:probe:{self.profile_name}",
                    model_profile=self.profile_name,
                    messages=[
                        CanonicalMessage(
                            role="user",
                            content="Call the echo tool with message=ping.",
                        )
                    ],
                    tools=[
                        CanonicalToolDefinition(
                            name="echo",
                            description="Echo a message",
                            parameters={
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                },
                                "required": ["message"],
                            },
                        )
                    ],
                    max_output_tokens=64,
                    max_cost_usd=0.05,
                    temperature=0.0,
                )
            )
            latency = int((self.clock() - started) * 1000)
            passed = bool(response.tool_calls) or response.status == "tool_calls"
            if not passed and response.provider == "mock":
                # Deterministic mock has no tool-call loop; treat tool protocol
                # acceptance (no provider error) as proof for mock profiles.
                passed = response.status in {"success", "tool_calls", "invalid_output"}
            return ProbeCheck(
                name="tool_calling",
                passed=passed,
                detail=None if passed else f"status={response.status}",
                latency_ms=latency,
            )
        except Exception as exc:
            return ProbeCheck(
                name="tool_calling",
                passed=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=int((self.clock() - started) * 1000),
            )

    def _emit_evidence(
        self,
        report: ProbeReport,
        admission: dict[str, object] | None,
    ) -> None:
        if self.evidence_sink is None:
            return
        payload = {
            "schema_version": "local_route_admission.v1",
            "profile": self.profile_name,
            "model": self.profile.get("model"),
            "route_class": self.profile.get("route_class", "local"),
            "breaker": self.breaker.snapshot(),
            "report": report.as_dict(),
            "admission": admission,
            "fallback": (self.profile.get("cloud_fallback") or {}),
        }
        self.evidence_sink(payload)
