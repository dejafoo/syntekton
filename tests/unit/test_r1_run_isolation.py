"""RF1 race-focused tests for run-scoped execution state."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.manifest import ConnectorManifest, ConnectorToolSpec
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.connectors.registry import ConnectorInvocation, ConnectorRegistry
from product_factory.connectors.result import ConnectorResult
from product_factory.domain.budgets import RunBudget
from product_factory.domain.errors import BudgetExhaustedError
from product_factory.domain.runs import RunRequest
from product_factory.domain.tools import CapabilityGrant
from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import (
    CanonicalMessage,
    ModelRequest,
    ModelResponse,
)
from product_factory.orchestration.coordinator import (
    RunCoordinator,
    default_code_change_plan,
)
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry


class _BarrierGateway(ModelGateway):
    def __init__(self, parties: int = 2) -> None:
        self.barrier = threading.Barrier(parties)
        self.seen: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def complete(self, request: ModelRequest) -> ModelResponse:
        with self._lock:
            self.seen.append((request.run_id, request.task_id))
        self.barrier.wait(timeout=5)
        return _response(
            request,
            structured_data=default_code_change_plan("isolation fixture").model_dump(mode="json"),
        )

    def refresh_catalog(self) -> dict[str, object]:
        return {"models": []}

    def list_models(self) -> list[dict[str, object]]:
        return []


class _SelectiveBlockingGateway(ModelGateway):
    def __init__(self, blocked_run_id: str) -> None:
        self.blocked_run_id = blocked_run_id
        self.blocked = threading.Event()
        self.release = threading.Event()

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.run_id == self.blocked_run_id:
            self.blocked.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("test did not release blocked run")
        return _response(request)

    def refresh_catalog(self) -> dict[str, object]:
        return {"models": []}

    def list_models(self) -> list[dict[str, object]]:
        return []


class _UsageGateway(ModelGateway):
    def complete(self, request: ModelRequest) -> ModelResponse:
        return _response(request, usage=UsageMetrics(input_tokens=1))

    def refresh_catalog(self) -> dict[str, object]:
        return {"models": []}

    def list_models(self) -> list[dict[str, object]]:
        return []


def _response(
    request: ModelRequest,
    *,
    structured_data: dict[str, object] | None = None,
    usage: UsageMetrics | None = None,
) -> ModelResponse:
    return ModelResponse(
        request_id=request.request_id,
        provider="fixture",
        provider_model_id="fixture-model",
        resolved_model_id="fixture-model",
        status="success",
        text="ok",
        structured_data=structured_data,
        usage=usage or UsageMetrics(input_tokens=1, output_tokens=1),
        response_hash=f"hash-{request.request_id}",
    )


def _coordinator(tmp_path: Path, gateway: ModelGateway) -> RunCoordinator:
    root = Path(__file__).resolve().parents[2]
    return RunCoordinator(
        config=load_config(root),
        gateway=gateway,
        data_dir=tmp_path / ".product-factory",
    )


def _request(run_id: str, *, max_input_tokens: int = 10) -> RunRequest:
    return RunRequest(
        request_id=f"request-{run_id}",
        workflow_type="code_change",
        request_text="Add an isolation fixture.",
        metadata={"planner_mode": "live"},
        budget=RunBudget(
            max_input_tokens=max_input_tokens,
            max_output_tokens=100,
            max_cost_usd=Decimal("1"),
        ),
    )


def _context(coord: RunCoordinator, tmp_path: Path, run_id: str, request: RunRequest):
    run_dir = tmp_path / ".product-factory" / "runs" / run_id
    for name in ("artifacts", "content"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    return coord._build_execution_context(
        run_id=run_id,
        request=request,
        run_dir=run_dir,
    )


def _model_request(run_id: str, suffix: str = "1") -> ModelRequest:
    return ModelRequest(
        request_id=f"model-{run_id}-{suffix}",
        run_id=run_id,
        task_id=f"task-{run_id}",
        session_id=f"session-{run_id}",
        model_profile="fixture",
        messages=[CanonicalMessage(role="user", content="go")],
    )


def test_concurrent_runs_isolate_model_invocation_run_ids(tmp_path: Path) -> None:
    gateway = _BarrierGateway()
    coord = _coordinator(tmp_path, gateway)
    requests = {run_id: _request(run_id) for run_id in ("run-a", "run-b")}
    contexts = {
        run_id: _context(coord, tmp_path, run_id, request) for run_id, request in requests.items()
    }

    def plan(run_id: str) -> None:
        coord._plan(
            run_id,
            requests[run_id],
            None,
            execution_context=contexts[run_id],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(plan, ("run-a", "run-b")))

    assert sorted(gateway.seen) == [("run-a", "plan"), ("run-b", "plan")]
    for run_id in ("run-a", "run-b"):
        rows = coord.db.list_invocations(run_id)
        assert len(rows) == 1
        assert rows[0]["run_id"] == run_id
        assert rows[0]["task_id"] == "plan"
        assert contexts[run_id].gateway.ledger is contexts[run_id].ledger
    assert contexts["run-a"].gateway is not contexts["run-b"].gateway
    assert contexts["run-a"].ledger is not contexts["run-b"].ledger


def test_resume_run_a_during_active_run_b_preserves_attribution(tmp_path: Path) -> None:
    gateway = _SelectiveBlockingGateway("run-b")
    coord = _coordinator(tmp_path, gateway)
    request_a = _request("run-a")
    request_b = _request("run-b")
    context_b = _context(coord, tmp_path, "run-b", request_b)

    with ThreadPoolExecutor(max_workers=2) as pool:
        active_b = pool.submit(context_b.gateway.complete, _model_request("run-b"))
        assert gateway.blocked.wait(timeout=5)

        initial_a = _context(coord, tmp_path, "run-a", request_a)
        initial_a.ledger.record_usage(UsageMetrics(input_tokens=2))
        resumed_a = coord._build_execution_context(
            run_id="run-a",
            request=request_a,
            run_dir=initial_a.run_dir,
            budget_snapshot=initial_a.ledger.snapshot(),
        )
        response_a = resumed_a.gateway.complete(_model_request("run-a", "resume"))
        gateway.release.set()
        response_b = active_b.result(timeout=5)

    assert response_a.status == response_b.status == "success"
    assert resumed_a.ledger.usage.input_tokens == 3
    assert context_b.ledger.usage.input_tokens == 1
    assert [row["run_id"] for row in coord.db.list_invocations("run-a")] == ["run-a"]
    assert [row["run_id"] for row in coord.db.list_invocations("run-b")] == ["run-b"]


def test_budget_exhaustion_does_not_spend_sibling_run(tmp_path: Path) -> None:
    coord = _coordinator(tmp_path, _UsageGateway())
    request_a = _request("run-a", max_input_tokens=1)
    request_b = _request("run-b", max_input_tokens=10)
    context_a = _context(coord, tmp_path, "run-a", request_a)
    context_b = _context(coord, tmp_path, "run-b", request_b)

    context_a.gateway.complete(_model_request("run-a"))
    with pytest.raises(BudgetExhaustedError, match="input token"):
        context_a.gateway.complete(_model_request("run-a", "2"))

    response_b = context_b.gateway.complete(_model_request("run-b"))
    assert response_b.status == "success"
    assert context_a.ledger.usage.input_tokens == 1
    assert context_b.ledger.usage.input_tokens == 1


def test_concurrent_connector_calls_isolate_audit_run_and_task_ids(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    registry = ConnectorRegistry()
    manifest = ConnectorManifest(
        connector_id="isolation",
        version="1",
        provider="fixture",
        tool_class="web_read",
        tools=(ConnectorToolSpec(name="isolation_read", description="read fixture"),),
    )

    def handler(invocation: ConnectorInvocation) -> ConnectorResult:
        barrier.wait(timeout=5)
        return ConnectorResult(payload={"task_id": invocation.task_id})

    registry.register(manifest, handler)
    broker = ConnectorBroker(
        registry,
        config=ConnectorsConfig(connectors={"isolation": ConnectorSettings(enabled=True)}),
    )
    audited: dict[str, list[dict[str, object]]] = {"run-a": [], "run-b": []}
    receipts: dict[str, str] = {}

    def invoke(run_id: str) -> None:
        registry_for_task = default_tool_registry()
        for definition in registry.tool_definitions():
            registry_for_task.register(definition)
        tool_broker = ToolBroker(
            registry=registry_for_task,
            artifact_store=ArtifactStore(tmp_path / run_id / "artifacts"),
            connectors=broker,
            run_id=run_id,
            connector_audit=lambda _event, payload: audited[run_id].append(payload),
        )
        tool_broker.set_grant(
            CapabilityGrant(
                grant_id=f"grant-{run_id}",
                run_id=run_id,
                task_id=f"task-{run_id}",
                agent_profile="fixture",
                tool_names={"isolation_read"},
                max_calls=1,
            )
        )
        result = tool_broker.execute(
            task_id=f"task-{run_id}",
            tool_name="isolation_read",
            arguments={},
        )
        receipts[run_id] = str(result["receipt_sha256"])
        assert tool_broker.history[0].task_id == f"task-{run_id}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(invoke, ("run-a", "run-b")))

    for run_id in ("run-a", "run-b"):
        assert len(audited[run_id]) == 1
        assert audited[run_id][0]["run_id"] == run_id
        assert audited[run_id][0]["task_id"] == f"task-{run_id}"
        assert receipts[run_id]
    assert receipts["run-a"] != receipts["run-b"]
