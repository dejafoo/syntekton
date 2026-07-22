"""Bounded implementation agent-loop tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.domain.tools import CapabilityGrant
from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import (
    CanonicalMessage,
    CanonicalToolCall,
    CanonicalToolDefinition,
    ModelRequest,
    ModelResponse,
)
from product_factory.orchestration.agent_loop import run_tool_agent
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry


class ScriptedGateway(ModelGateway):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)

    def refresh_catalog(self) -> dict:
        return {"models": []}

    def list_models(self) -> list[dict]:
        return []


def _response(
    status: str, *, calls: list[CanonicalToolCall] | None = None, text: str = ""
) -> ModelResponse:
    return ModelResponse(
        request_id="r",
        provider="test",
        provider_model_id="test",
        resolved_model_id="test",
        status=status,  # type: ignore[arg-type]
        text=text,
        tool_calls=calls or [],
    )


def _broker(tmp_path: Path) -> ToolBroker:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        worktree_root=repo,
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g",
            run_id="run",
            task_id="task",
            agent_profile="implementation_worker",
            tool_names={"list_files", "create_file"},
            allowed_path_patterns=["**/*"],
            max_calls=10,
        )
    )
    return broker


def test_tool_loop_inspects_then_writes(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                "tool_calls",
                calls=[
                    CanonicalToolCall(id="c1", name="list_files", arguments={"directory": "."})
                ],
            ),
            _response(
                "tool_calls",
                calls=[
                    CanonicalToolCall(
                        id="c2",
                        name="create_file",
                        arguments={"path": "x.py", "content": "x = 1\n"},
                    )
                ],
            ),
            _response("success", text="done"),
        ]
    )
    result = run_tool_agent(
        gateway=gateway,
        broker=_broker(tmp_path),
        run_id="run",
        task_id="task",
        session_id="session",
        model_profile="worker",
        messages=[CanonicalMessage(role="user", content="implement")],
        tools=[
            CanonicalToolDefinition(name="list_files", description="list", parameters={}),
            CanonicalToolDefinition(name="create_file", description="write", parameters={}),
        ],
        max_rounds=5,
        max_tool_calls=5,
        max_cost_usd=Decimal("1"),
        max_input_tokens=10_000,
        max_output_tokens=10_000,
        timeout_seconds=30,
    )
    assert result.status == "success"
    assert (tmp_path / "repo" / "x.py").exists()
    assert len(gateway.requests) == 3
    assert any(m.role == "tool" for m in gateway.requests[-1].messages)


def test_tool_loop_stops_repeated_calls(tmp_path: Path) -> None:
    repeated = _response(
        "tool_calls",
        calls=[CanonicalToolCall(id="c", name="list_files", arguments={"directory": "."})],
    )
    gateway = ScriptedGateway([repeated, repeated, repeated])
    result = run_tool_agent(
        gateway=gateway,
        broker=_broker(tmp_path),
        run_id="run",
        task_id="task",
        session_id="session",
        model_profile="worker",
        messages=[CanonicalMessage(role="user", content="implement")],
        tools=[CanonicalToolDefinition(name="list_files", description="list", parameters={})],
        max_rounds=5,
        max_tool_calls=5,
        max_cost_usd=Decimal("1"),
        max_input_tokens=10_000,
        max_output_tokens=10_000,
        timeout_seconds=30,
    )
    assert result.status == "failed"
    assert result.termination_reason == "no_progress"


def test_write_before_inspection_is_not_executed(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                "tool_calls",
                calls=[
                    CanonicalToolCall(
                        id="write",
                        name="create_file",
                        arguments={"path": "forbidden.py", "content": "x = 1\n"},
                    )
                ],
            ),
            _response("success", text="stopped"),
        ]
    )
    broker = _broker(tmp_path)
    result = run_tool_agent(
        gateway=gateway,
        broker=broker,
        run_id="run",
        task_id="task",
        session_id="session",
        model_profile="worker",
        messages=[CanonicalMessage(role="user", content="implement")],
        tools=[CanonicalToolDefinition(name="create_file", description="write", parameters={})],
        max_rounds=3,
        max_tool_calls=3,
        max_cost_usd=Decimal("1"),
        max_input_tokens=10_000,
        max_output_tokens=10_000,
        timeout_seconds=30,
    )
    assert result.status == "success"
    assert not (tmp_path / "repo" / "forbidden.py").exists()


def test_token_budget_stops_loop(tmp_path: Path) -> None:
    response = _response("success", text="done").model_copy(
        update={"usage": UsageMetrics(input_tokens=11)}
    )
    result = run_tool_agent(
        gateway=ScriptedGateway([response]),
        broker=_broker(tmp_path),
        run_id="run",
        task_id="task",
        session_id="session",
        model_profile="worker",
        messages=[CanonicalMessage(role="user", content="implement")],
        tools=[],
        max_rounds=2,
        max_tool_calls=2,
        max_cost_usd=Decimal("1"),
        max_input_tokens=10,
        max_output_tokens=10,
        timeout_seconds=30,
    )
    assert result.termination_reason == "token_budget_exhausted"


def test_repeated_patch_fingerprint_stops_loop(tmp_path: Path) -> None:
    writes = [
        _response(
            "tool_calls",
            calls=[
                CanonicalToolCall(
                    id=f"w{i}",
                    name="create_file",
                    arguments={
                        "path": "x.py",
                        "content": content,
                        "overwrite": True,
                    },
                )
            ],
        )
        for i, content in enumerate(["x = 1\n", "x = 1 \n", "x = 1  \n"])
    ]
    gateway = ScriptedGateway(
        [
            _response(
                "tool_calls",
                calls=[
                    CanonicalToolCall(id="l", name="list_files", arguments={"directory": "."})
                ],
            ),
            *writes,
        ]
    )
    broker = _broker(tmp_path)
    broker.set_grant(
        CapabilityGrant(
            grant_id="g",
            run_id="run",
            task_id="task",
            agent_profile="implementation_worker",
            tool_names={"list_files", "create_file"},
            allowed_path_patterns=["**/*"],
            max_calls=20,
        )
    )
    result = run_tool_agent(
        gateway=gateway,
        broker=broker,
        run_id="run",
        task_id="task",
        session_id="session",
        model_profile="worker",
        messages=[CanonicalMessage(role="user", content="implement")],
        tools=[
            CanonicalToolDefinition(name="list_files", description="list", parameters={}),
            CanonicalToolDefinition(name="create_file", description="write", parameters={}),
        ],
        max_rounds=6,
        max_tool_calls=10,
        max_cost_usd=Decimal("1"),
        max_input_tokens=10_000,
        max_output_tokens=10_000,
        timeout_seconds=30,
    )
    assert result.status == "failed"
    assert result.termination_reason == "no_progress"
    assert "patch fingerprint" in (result.error or "")

