"""SD4 contract tests: host/v2 decoding, bounds, ingress parity, registry."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from product_factory.api.app import create_app
from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.host.bounds import BoundViolation, enforce_pack_input, enforce_request_text
from product_factory.host.protocol import HOST_PROTOCOL
from product_factory.host.protocol_v2 import (
    HOST_PROTOCOL_V2,
    HandoffClaim,
    SubmitRunV2Body,
    protocol_metadata,
)
from product_factory.host.registry import get_host_service, reset_host_registry
from product_factory.host.service import HostService
from tests.conftest import clone_fixture


def _project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    real_config = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(real_config, root / "config")
    return root


def _fixture_repo(tmp_path: Path) -> Path:
    real_root = Path(__file__).resolve().parents[2]
    return clone_fixture(real_root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


def test_handoff_claim_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        HandoffClaim.model_validate(
            {
                "handoff_id": "handoff-1",
                "expected_digest": "a" * 64,
                "producer_run_id": "run-forged",
            }
        )


def test_submit_v2_body_rejects_dead_fields() -> None:
    for dead in (
        "mock",
        "inline",
        "sync",
        "model_profile_set",
        "project_profile",
        "requested_artifacts",
    ):
        with pytest.raises(ValidationError):
            SubmitRunV2Body.model_validate(
                {
                    "request_text": "x",
                    dead: True if dead in {"mock", "inline", "sync"} else "local-target",
                }
            )


def test_submit_v2_body_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SubmitRunV2Body.model_validate({"request_text": "x", "secret_debug": True})


def test_bounds_reject_oversized_request_text() -> None:
    with pytest.raises(BoundViolation):
        enforce_request_text("x" * 200_001)


def test_bounds_reject_deep_pack_input() -> None:
    node: dict = {}
    cur = node
    for _ in range(12):
        cur["child"] = {}
        cur = cur["child"]
    with pytest.raises(BoundViolation):
        enforce_pack_input(node)


def test_registry_reuses_sole_supervisor(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    config = load_config(project)
    data_dir = tmp_path / "pf-data"
    first = get_host_service(config=config, data_dir=data_dir, force_mock=True)
    # Later callers (status/approve) often omit mock; still reuse the same instance.
    second = get_host_service(config=config, data_dir=data_dir, force_mock=False)
    assert first is second
    assert len({id(get_host_service(config=config, data_dir=data_dir, force_mock=True))}) == 1
    reset_host_registry()
    # A fresh construction after reset may choose a different mode.
    live = get_host_service(config=config, data_dir=data_dir, force_mock=False)
    assert live is not first
    reset_host_registry()


def test_api_v2_meta_advertises_protocols_and_dashboard(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "pf"))
    meta = client.get("/api/v2/meta").json()
    assert meta["protocol"] == HOST_PROTOCOL_V2
    assert HOST_PROTOCOL_V2 in meta["supported_protocols"]
    assert HOST_PROTOCOL in meta["supported_protocols"]
    assert meta["default_protocol"] == HOST_PROTOCOL_V2
    assert meta["dashboard"]["deployment_support"] == "loopback_monitor_only"
    assert meta["dashboard"]["mutations"] is False
    assert meta["dashboard"]["bearer_token_storage"] is False
    assert meta["dashboard"]["remote_browser"] == "unsupported"


def test_api_v1_meta_includes_protocol_ads(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "pf"))
    meta = client.get("/api/v1/meta").json()
    assert meta["protocol"] == HOST_PROTOCOL
    assert meta["supported_protocols"] == protocol_metadata()["supported_protocols"]
    assert meta["dashboard"]["deployment_support"] == "loopback_monitor_only"


def test_api_v2_rejects_dead_submit_fields(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "pf"))
    response = client.post(
        "/api/v2/runs",
        json={"request_text": "hello", "mock": True},
    )
    assert response.status_code == 422


def test_api_v1_still_accepts_mock_compatibility(tmp_path: Path) -> None:
    """v1 bodies still allow mock/sync fields (compatibility); v2 forbids them."""
    # Schema-level acceptance only — do not require a successful execution.
    from product_factory.api.control import SubmitRunBody

    body = SubmitRunBody.model_validate(
        {
            "request_text": "Add a health check.",
            "workflow_type": "code_change",
            "mock": True,
            "sync": True,
            "inline": False,
        }
    )
    assert body.mock is True
    assert body.sync is True
    client = TestClient(create_app(tmp_path / "pf"))
    # Extra dead fields on v2 still 422.
    assert client.post("/api/v2/runs", json={"request_text": "x", "mock": True}).status_code == 422


def test_ingress_parity_approve_handoff_host_vs_http(tmp_path: Path) -> None:
    """Local HostService and HTTP /api/v2 share handoff approval authority."""
    project = _project_root(tmp_path)
    data_dir = tmp_path / "pf"
    config = load_config(project)
    service = get_host_service(config=config, data_dir=data_dir, force_mock=True)

    from product_factory.trust.handoffs import HandoffService

    hs = HandoffService(service.coord.db, data_dir)
    service.coord.db.upsert_run(
        run_id="run-producer", workflow_type="code_change", status="completed", request={}
    )
    service.coord.db.upsert_run(
        run_id="run-producer-2", workflow_type="code_change", status="completed", request={}
    )

    instance_id = "artifact-instance-parity"
    service.coord.db.record_artifact_instance(
        {
            "instance_id": instance_id,
            "run_id": "run-producer",
            "sha256": "b" * 64,
            "role": "architecture_document",
            "producer_task_id": "task-1",
            "media_type": "text/markdown",
            "schema_id": "architecture_document",
            "size_bytes": 12,
        }
    )
    record = hs.create_from_artifact_instance(instance_id, role="architecture_document")
    hs.promote_evidence_complete(record.handoff_id)

    via_service = service.approve_handoff(record.handoff_id, actor="parity_cli")
    assert via_service.ok
    assert via_service.data is not None
    assert via_service.data["handoff"]["state"] == "approved"

    instance_id_2 = "artifact-instance-parity-2"
    service.coord.db.record_artifact_instance(
        {
            "instance_id": instance_id_2,
            "run_id": "run-producer-2",
            "sha256": "c" * 64,
            "role": "architecture_document",
            "producer_task_id": "task-2",
            "media_type": "text/markdown",
            "schema_id": "architecture_document",
            "size_bytes": 12,
        }
    )
    record2 = hs.create_from_artifact_instance(instance_id_2, role="architecture_document")
    hs.promote_evidence_complete(record2.handoff_id)

    client = TestClient(create_app(data_dir, project_root=project))
    via_http = client.post(f"/api/v2/handoffs/{record2.handoff_id}/approve")
    assert via_http.status_code == 200
    body = via_http.json()
    assert body["protocol"] == HOST_PROTOCOL_V2
    assert body["ok"] is True
    assert body["result"]["handoff"]["state"] == "approved"


def test_host_service_submit_status_parity_envelope(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    data_dir = tmp_path / "pf"
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=data_dir,
        use_deterministic_planner=True,
    )
    request = RunRequest(
        request_id="req-parity",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("3.00")),
    )
    submitted = service.submit(request, mock=True, detach=False, inline_thread=False)
    assert submitted.ok
    assert submitted.protocol == HOST_PROTOCOL
    assert submitted.run_id
    status = service.status(submitted.run_id)
    assert status.ok
    assert status.status in {
        "awaiting_approval",
        "completed",
        "failed",
        "blocked",
        "budget_exhausted",
        "queued",
        "executing",
        "validating",
        "planning",
        "initializing",
    }


def test_golden_v2_submit_fixture_roundtrip() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "host_protocol" / "v2_submit.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    body = SubmitRunV2Body.model_validate(payload["request"])
    assert body.workflow_type == "code_change"
    assert body.handoffs == []
    assert "mock" not in payload["request"]
