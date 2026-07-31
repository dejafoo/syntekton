"""Contract tests: typed `pack_input` across host, MCP, and HTTP surfaces (PM1.0)."""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from product_factory.api.control import SubmitRunBody, _run_request
from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.host.service import HostService
from product_factory.host_mcp import tools as mcp_tools
from product_factory.workflows import persist_pack_input, resolve_workflow_pack
from product_factory.workflows import registry as pack_registry
from product_factory.workflows.handlers.base import ComposeContext

TYPED_SCHEMA = {
    "type": "object",
    "properties": {
        "request_text": {"type": "string"},
        "decision_statement": {"type": "string"},
        "domain": {"type": "string"},
        "source_policy_profile": {"type": "string"},
    },
    "required": ["request_text", "decision_statement", "domain"],
    "additionalProperties": False,
}

VALID_INPUT = {"decision_statement": "Adopt protocol X?", "domain": "payments"}


@pytest.fixture
def typed_technical_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the `technical_plan` pack a typed contract for the duration of a test."""
    typed = dataclasses.replace(resolve_workflow_pack("technical_plan"), input_schema=TYPED_SCHEMA)
    monkeypatch.setitem(pack_registry._PACKS, "technical_plan", typed)


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HostService:
    project = tmp_path / "project"
    project.mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repo_root / "config", project / "config")
    shutil.copytree(repo_root / "profiles", project / "profiles")
    host = HostService(
        config=load_config(project),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    # Validation must pass or fail before any worker is started.
    monkeypatch.setattr(HostService, "_spawn_worker", lambda *a, **k: None)
    return host


def _request(**pack_input: object) -> RunRequest:
    return RunRequest(
        request_id="req-pack-input",
        workflow_type="technical_plan",
        request_text="Assess whether protocol X is feasible.",
        pack_input=dict(pack_input),
    )


def test_submit_accepts_valid_pack_input(service: HostService, typed_technical_plan: None) -> None:
    response = service.submit(_request(**VALID_INPUT), mock=True)
    assert response.ok, response.model_dump()
    assert response.status == "queued"

    run_dir = service.pf_root / "runs" / str(response.run_id)
    stored = json.loads((run_dir / "input" / "request.json").read_text(encoding="utf-8"))
    assert stored["pack_input"] == VALID_INPUT


def test_submit_rejects_missing_required_pack_input(
    service: HostService, typed_technical_plan: None
) -> None:
    response = service.submit(_request(domain="payments"), mock=True)
    assert not response.ok
    assert response.error is not None
    assert response.error.code == "invalid_pack_input"
    assert response.error.details["missing"] == ["decision_statement"]
    # Nothing was queued.
    assert not (service.pf_root / "runs").exists() or not list((service.pf_root / "runs").iterdir())


def test_submit_rejects_unknown_pack_input_key(
    service: HostService, typed_technical_plan: None
) -> None:
    response = service.submit(_request(**VALID_INPUT, escalate_to="apply_patch"), mock=True)
    assert not response.ok
    assert response.error is not None
    assert response.error.code == "invalid_pack_input"
    assert response.error.details["unknown"] == ["escalate_to"]


def test_submit_rejects_unknown_source_policy_profile(
    service: HostService, typed_technical_plan: None
) -> None:
    response = service.submit(
        _request(**VALID_INPUT, source_policy_profile="ghost-profile"), mock=True
    )
    assert not response.ok
    assert response.error is not None
    assert response.error.code == "invalid_pack_input"
    assert response.error.details["profile_id"] == "ghost-profile"


def test_submit_accepts_shipped_source_policy_profile(
    service: HostService, typed_technical_plan: None
) -> None:
    response = service.submit(
        _request(**VALID_INPUT, source_policy_profile="regulated-domain"), mock=True
    )
    assert response.ok, response.model_dump()


def test_existing_packs_submit_without_pack_input(service: HostService) -> None:
    response = service.submit(
        RunRequest(
            request_id="req-legacy",
            workflow_type="technical_plan",
            request_text="Design the retry policy.",
        ),
        mock=True,
    )
    assert response.ok, response.model_dump()


def test_mcp_pf_submit_declares_and_forwards_pack_input(
    service: HostService, typed_technical_plan: None
) -> None:
    schema = next(s for s in mcp_tools.tool_schemas() if s["name"] == "pf_submit")
    assert schema["inputSchema"]["properties"]["pack_input"]["type"] == "object"

    accepted = mcp_tools.pf_submit(
        service,
        {
            "request_text": "Assess protocol X.",
            "workflow": "technical_plan",
            "pack_input": VALID_INPUT,
        },
    )
    assert accepted["ok"], accepted

    rejected = mcp_tools.pf_submit(
        service,
        {
            "request_text": "Assess protocol X.",
            "workflow": "technical_plan",
            "pack_input": {"domain": "payments"},
        },
    )
    assert rejected["error"]["code"] == "invalid_pack_input"

    malformed = mcp_tools.pf_submit(
        service,
        {"request_text": "Assess protocol X.", "pack_input": "decision_statement"},
    )
    assert malformed["error"]["code"] == "invalid_pack_input"


def test_http_submit_body_carries_pack_input() -> None:
    body = SubmitRunBody(
        request_text="Assess protocol X.",
        workflow_type="technical_plan",
        pack_input=VALID_INPUT,
    )
    assert _run_request(body).pack_input == VALID_INPUT
    assert SubmitRunBody(request_text="x").pack_input == {}


def test_compose_context_exposes_pack_input() -> None:
    ctx = ComposeContext(
        request=_request(**VALID_INPUT),
        role="architecture_document",
        document_name="ARCHITECTURE.md",
    )
    assert ctx.pack_input == VALID_INPUT
    plain = ComposeContext(
        request=RunRequest(
            request_id="req-plain",
            workflow_type="technical_plan",
            request_text="Design the retry policy.",
        ),
        role="architecture_document",
        document_name="ARCHITECTURE.md",
    )
    assert plain.pack_input == {}


def test_persisted_pack_input_is_deterministic(tmp_path: Path) -> None:
    path = persist_pack_input({"domain": "payments", "decision_statement": "X?"}, tmp_path)
    assert path.name == "pack-input.json"
    assert path.read_text(encoding="utf-8") == (
        '{\n  "decision_statement": "X?",\n  "domain": "payments"\n}\n'
    )
    assert json.loads(persist_pack_input(None, tmp_path).read_text(encoding="utf-8")) == {}
