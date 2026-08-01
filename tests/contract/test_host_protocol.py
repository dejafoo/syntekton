"""Contract tests for host submit → status → approve/reject with MockGateway."""

from __future__ import annotations

import json
import shutil
import time
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from product_factory.cli.app import app
from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import ArtifactOverride, RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.host.protocol import HOST_PROTOCOL, HostResponse
from product_factory.host.service import HostService
from tests.conftest import clone_fixture

runner = CliRunner()


def _clear_pf_env(monkeypatch) -> None:
    """CLI host now honors PRODUCT_FACTORY_ROOT / DATA_DIR (same as MCP).

    Contract tests that chdir into a temp project must clear these so an
    ambient developer/CI env cannot point the CLI at another checkout.
    """
    monkeypatch.delenv("PRODUCT_FACTORY_ROOT", raising=False)
    monkeypatch.delenv("PRODUCT_FACTORY_DATA_DIR", raising=False)
    monkeypatch.delenv("PRODUCT_FACTORY_FORCE_MOCK", raising=False)


def _project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    real_config = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(real_config, root / "config")
    return root


def _fixture_repo(tmp_path: Path) -> Path:
    real_root = Path(__file__).resolve().parents[2]
    return clone_fixture(real_root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


def _request_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "request.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _wait_for_status(
    service: HostService,
    run_id: str,
    *,
    wanted: set[str],
    timeout: float = 60.0,
) -> HostResponse:
    deadline = time.time() + timeout
    last = service.status(run_id)
    while time.time() < deadline:
        last = service.status(run_id)
        if last.status in wanted:
            return last
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {wanted}; last={last.model_dump()}")


def test_host_submit_status_loop_with_mock(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    data_dir = tmp_path / ".product-factory"
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=data_dir,
        use_deterministic_planner=True,
    )
    request = RunRequest(
        request_id="req-host-1",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("3.00")),
    )
    submitted = service.submit(request, mock=True, detach=False, inline_thread=True)
    assert submitted.ok
    assert submitted.protocol == HOST_PROTOCOL
    assert submitted.run_id
    assert submitted.status == "queued"
    assert submitted.subscription is not None
    assert submitted.subscription.cli_tail.endswith(submitted.run_id)

    terminal = _wait_for_status(
        service,
        submitted.run_id,
        wanted={"awaiting_approval", "completed", "failed", "blocked", "budget_exhausted"},
    )
    assert terminal.ok
    assert terminal.status in {"awaiting_approval", "completed"}

    # Tail resumes with after_seq and reads local store (observe is down).
    first = next(
        service.tail(
            submitted.run_id,
            after_seq=0,
            follow=False,
            max_idle_polls=1,
            stop_when_terminal=False,
        )
    )
    assert first.ok
    assert first.data is not None
    assert first.data["source"] in {"sqlite", "jsonl"}
    cursor = int(first.data.get("after_seq") or 0)
    if first.events:
        cursor = int(first.events[-1]["seq"])
    resumed = next(
        service.tail(
            submitted.run_id,
            after_seq=cursor,
            follow=False,
            max_idle_polls=1,
            stop_when_terminal=False,
        )
    )
    assert resumed.ok
    if resumed.events:
        assert all(int(e["seq"]) > cursor for e in resumed.events)

    inspected = service.inspect(submitted.run_id)
    assert inspected.ok
    assert inspected.plan_summary is not None or inspected.data is not None

    arts = service.artifacts(submitted.run_id)
    assert arts.ok
    assert isinstance(arts.artifacts, list)

    if terminal.status == "awaiting_approval":
        approved = service.approve(submitted.run_id)
        assert approved.ok
        assert approved.status == "completed"
        assert approved.data is not None
        assert approved.data["approval"]["status"] == "approved"


def test_host_reject_round_trip(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    submitted = service.submit(
        RunRequest(
            request_id="req-host-2",
            workflow_type="code_change",
            request_text="Add a validated health-check endpoint with tests.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        ),
        mock=True,
        detach=False,
        inline_thread=False,  # sync for deterministic approval state
    )
    assert submitted.ok and submitted.run_id
    status = service.status(submitted.run_id)
    assert status.status == "awaiting_approval"
    rejected = service.reject(submitted.run_id)
    assert rejected.ok
    assert rejected.status == "blocked"
    assert rejected.data is not None
    assert rejected.data["approval"]["status"] == "rejected"


def test_host_cli_submit_status_approve(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    request = _request_file(tmp_path, "Add a validated health-check endpoint with tests.")
    _clear_pf_env(monkeypatch)
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "host",
            "submit",
            "--request",
            str(request),
            "--repo",
            str(fixture),
            "--mock",
            "--sync",
        ],
    )
    assert result.exit_code == 0, result.output
    submitted = HostResponse.model_validate(json.loads(result.output))
    assert submitted.ok
    assert submitted.protocol == HOST_PROTOCOL
    assert submitted.run_id

    status_result = runner.invoke(app, ["host", "status", submitted.run_id])
    assert status_result.exit_code == 0, status_result.output
    status = HostResponse.model_validate(json.loads(status_result.output))
    assert status.ok
    assert status.status in {"awaiting_approval", "completed"}

    if status.status == "awaiting_approval":
        approve_result = runner.invoke(app, ["host", "approve", submitted.run_id])
        assert approve_result.exit_code == 0, approve_result.output
        approved = HostResponse.model_validate(json.loads(approve_result.output))
        assert approved.ok
        assert approved.status == "completed"

    export_result = runner.invoke(app, ["host", "export-bundle", submitted.run_id])
    assert export_result.exit_code == 0, export_result.output
    exported = HostResponse.model_validate(json.loads(export_result.output))
    assert exported.ok
    assert exported.data is not None
    assert exported.data["format"] == "zip"
    assert Path(exported.data["path"]).is_file()


def test_host_cancel_mid_mock_run(tmp_path: Path) -> None:
    import threading

    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    gate = threading.Event()
    released = threading.Event()
    original = service.coord._raise_if_cancelled

    def gated_raise(run_id: str) -> None:
        row = service.coord.db.get_run(run_id)
        if row and row["status"] == "executing" and not gate.is_set():
            gate.set()
            assert released.wait(timeout=10), "cancel was not signalled in time"
        original(run_id)

    service.coord._raise_if_cancelled = gated_raise  # type: ignore[method-assign]

    submitted = service.submit(
        RunRequest(
            request_id="req-cancel-1",
            workflow_type="code_change",
            request_text="Add a validated health-check endpoint with tests.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        ),
        mock=True,
        detach=False,
        inline_thread=True,
    )
    assert submitted.ok and submitted.run_id
    assert gate.wait(timeout=30), "run never reached executing cancel gate"
    cancelled = service.cancel(submitted.run_id)
    assert cancelled.ok
    assert cancelled.data is not None
    assert cancelled.data["cancel_requested"] is True
    released.set()

    terminal = _wait_for_status(
        service,
        submitted.run_id,
        wanted={"cancelled"},
        timeout=30.0,
    )
    assert terminal.status == "cancelled"
    row = service.coord.db.get_run(submitted.run_id)
    assert row is not None
    assert int(row.get("cancel_requested") or 0) == 1


def test_host_revise_after_awaiting_approval(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    submitted = service.submit(
        RunRequest(
            request_id="req-revise-1",
            workflow_type="code_change",
            request_text="Add a validated health-check endpoint with tests.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        ),
        mock=True,
        detach=False,
        inline_thread=False,
    )
    assert submitted.ok and submitted.run_id
    assert service.status(submitted.run_id).status == "awaiting_approval"

    note = "Tighten path scope; do not add new tool grants."
    revised = service.revise(submitted.run_id, note=note)
    assert revised.ok, revised.model_dump()
    assert revised.data is not None
    assert revised.data["grants_unchanged"] is True
    assert revised.status in {"awaiting_approval", "completed"}

    approval = json.loads(
        (service.pf_root / "runs" / submitted.run_id / "output" / "approval.json").read_text(
            encoding="utf-8"
        )
    )
    # After a successful follow-up re-run, approval is rewritten; revision audit
    # is retained in revisions.jsonl and events.
    revisions = (
        service.pf_root / "runs" / submitted.run_id / "output" / "revisions.jsonl"
    ).read_text(encoding="utf-8")
    assert note in revisions
    events = service.list_events(submitted.run_id, after_seq=0, limit=500)
    assert any(e.get("type") == "run.revision_requested" for e in events)
    request = json.loads(
        (service.pf_root / "runs" / submitted.run_id / "input" / "request.json").read_text(
            encoding="utf-8"
        )
    )
    assert request["metadata"]["revision_note"] == note
    assert request["metadata"]["revision_count"] == "1"
    assert "## Operator revision" in request["request_text"]
    # No silent grant widening: budget / workflow / validation commands unchanged.
    assert request["workflow_type"] == "code_change"
    assert float(request["budget"]["max_cost_usd"]) == 3.0
    # Approval actions remain the bounded operator set (no extra grant knobs).
    assert set(approval.get("actions") or []) <= {"approve", "reject", "request_revision"}


def test_host_export_bundle_contents_redacted(tmp_path: Path) -> None:
    import zipfile

    from product_factory.observability.contracts import EventSeverity
    from product_factory.observability.recorder import TelemetryRecorder

    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    submitted = service.submit(
        RunRequest(
            request_id="req-export-1",
            workflow_type="code_change",
            request_text="Add a validated health-check endpoint with tests.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        ),
        mock=True,
        detach=False,
        inline_thread=False,
    )
    assert submitted.ok and submitted.run_id
    secret = "sk-secret-export-fixture-should-not-leak"
    TelemetryRecorder(service.coord.db).emit(
        run_id=submitted.run_id,
        event_type="test.secret_fixture",
        summary="fixture",
        severity=EventSeverity.INFO,
        payload={"api_key": secret, "ok": True},
    )

    exported = service.export_bundle(submitted.run_id)
    assert exported.ok
    assert exported.data is not None
    zip_path = Path(exported.data["path"])
    assert zip_path.is_file()
    names = set(exported.data["files"])
    for required in (
        "bundle-manifest.json",
        "plan.json",
        "validations.json",
        "cost-summary.json",
        "events.json",
        "proposed.patch",
    ):
        assert required in names

    with zipfile.ZipFile(zip_path) as zf:
        events_raw = zf.read("events.json").decode("utf-8")
        assert secret not in events_raw
        assert "***" in events_raw
        manifest = json.loads(zf.read("bundle-manifest.json"))
        assert manifest["run_id"] == submitted.run_id
        assert "proposed.patch" in zf.namelist()
        assert "cost-summary.json" in zf.namelist()
        cost = json.loads(zf.read("cost-summary.json"))
        assert cost["run_id"] == submitted.run_id
        plan = json.loads(zf.read("plan.json"))
        assert "objective" in plan or "tasks" in plan


def _seed_materialize_run(
    service: HostService,
    fixture: Path,
    *,
    run_id: str = "run-mat-1",
    status: str = "awaiting_approval",
    artifact_name: str = "ARCHITECTURE.md",
    artifact_body: str = "# ARCHITECTURE.md\n\n## Objective\nLand me.\n",
    artifact_overrides: dict[str, dict[str, str]] | None = None,
) -> str:
    run_dir = service.pf_root / "runs" / run_id
    for sub in ("input", "output", "artifacts/blobs", "content"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    (run_dir / "output" / artifact_name).write_text(artifact_body, encoding="utf-8")
    request = {
        "request_id": "req-mat",
        "workflow_type": "technical_plan",
        "request_text": "Design a health-check architecture.",
        "repository_path": str(fixture.resolve()),
        "model_profile_set": "local-target",
        "validation_commands": [],
        "artifact_overrides": artifact_overrides or {},
        "budget": {"max_cost_usd": "3.00"},
        "metadata": {},
    }
    (run_dir / "input" / "request.json").write_text(
        json.dumps(request, indent=2) + "\n", encoding="utf-8"
    )
    service.coord.db.upsert_run(
        run_id=run_id,
        workflow_type="technical_plan",
        status=status,
        request=request,
        active_operation=status,
    )
    return run_id


def test_host_materialize_happy_path(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    run_id = _seed_materialize_run(service, fixture)
    dest = fixture / "docs" / "ARCHITECTURE.md"
    assert not dest.exists()

    result = service.materialize(
        run_id,
        artifact="ARCHITECTURE.md",
        dest_path="docs/ARCHITECTURE.md",
    )
    assert result.ok, result.model_dump()
    assert result.protocol == HOST_PROTOCOL
    assert result.data is not None
    assert Path(result.data["written_path"]) == dest.resolve()
    assert dest.is_file()
    assert "Land me" in dest.read_text(encoding="utf-8")
    assert result.data["artifact"]["logical_name"] == "ARCHITECTURE.md"
    assert len(result.data["artifact"]["sha256"]) == 64

    events = service.list_events(run_id, after_seq=0, limit=50)
    assert any(e.get("type") == "artifact.materialized" for e in events)


def test_host_materialize_rejects_path_escape(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    run_id = _seed_materialize_run(service, fixture)

    result = service.materialize(
        run_id,
        artifact="ARCHITECTURE.md",
        dest_path="../escape.md",
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "path_escape"
    assert not (tmp_path / "escape.md").exists()
    assert not (fixture.parent / "escape.md").exists()


def test_host_materialize_rejects_pre_approval_status(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    run_id = _seed_materialize_run(service, fixture, status="planning")
    result = service.materialize(
        run_id,
        artifact="ARCHITECTURE.md",
        dest_path="docs/ARCHITECTURE.md",
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "invalid_state"
    assert result.status == "planning"
    assert not (fixture / "docs" / "ARCHITECTURE.md").exists()


def test_host_cli_materialize(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    _clear_pf_env(monkeypatch)
    monkeypatch.chdir(project)
    config = load_config(project)
    service = HostService(
        config=config,
        gateway=MockGateway(),
        use_deterministic_planner=True,
    )
    run_id = _seed_materialize_run(service, fixture, run_id="run-cli-mat")

    result = runner.invoke(
        app,
        [
            "host",
            "materialize",
            run_id,
            "--artifact",
            "ARCHITECTURE.md",
            "--to",
            "docs/ARCHITECTURE.md",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = HostResponse.model_validate(json.loads(result.output))
    assert payload.ok
    assert (fixture / "docs" / "ARCHITECTURE.md").is_file()


def _named_service(tmp_path: Path) -> HostService:
    return HostService(
        config=load_config(_project_root(tmp_path)),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def test_host_inspect_exposes_land_map_and_suggested_dest(tmp_path: Path) -> None:
    fixture = _fixture_repo(tmp_path)
    service = _named_service(tmp_path)
    run_id = _seed_materialize_run(
        service,
        fixture,
        run_id="run-land-map",
        artifact_name="integration_testing_architecture.md",
        artifact_body="# integration_testing_architecture.md\n\n## Objective\nScoped.\n",
        artifact_overrides={
            "architecture_document": {"dest_path": "docs/integration_testing_architecture.md"}
        },
    )

    inspected = service.inspect(run_id)
    assert inspected.ok, inspected.model_dump()
    assert inspected.data is not None
    land_map = inspected.data["artifact_land_map"]
    assert land_map == [
        {
            "role": "architecture_document",
            "logical_name": "integration_testing_architecture.md",
            "suggested_dest_path": "docs/integration_testing_architecture.md",
            "media_type": "text/markdown",
            "landable": True,
            "renamable": True,
            "required": True,
        }
    ]

    named = [
        a for a in inspected.artifacts if a["logical_name"] == "integration_testing_architecture.md"
    ]
    assert named, inspected.artifacts
    assert named[0]["role"] == "architecture_document"
    assert named[0]["suggested_dest_path"] == "docs/integration_testing_architecture.md"


def test_host_materialize_all_lands_named_deliverable(tmp_path: Path) -> None:
    fixture = _fixture_repo(tmp_path)
    service = _named_service(tmp_path)
    run_id = _seed_materialize_run(
        service,
        fixture,
        run_id="run-mat-all",
        artifact_name="integration_testing_architecture.md",
        artifact_body="# integration_testing_architecture.md\n\n## Objective\nScoped.\n",
        artifact_overrides={
            "architecture_document": {"dest_path": "docs/integration_testing_architecture.md"}
        },
    )

    result = service.materialize_all(run_id)
    assert result.ok, result.model_dump()
    assert result.data is not None
    assert [entry["role"] for entry in result.data["landed"]] == ["architecture_document"]
    landed = fixture / "docs" / "integration_testing_architecture.md"
    assert landed.is_file()
    assert "Scoped." in landed.read_text(encoding="utf-8")

    events = service.list_events(run_id, after_seq=0, limit=50)
    materialized = [e for e in events if e.get("type") == "artifact.materialized"]
    assert len(materialized) == 1


def test_host_materialize_all_skips_deliverables_not_produced(tmp_path: Path) -> None:
    fixture = _fixture_repo(tmp_path)
    service = _named_service(tmp_path)
    run_id = _seed_materialize_run(
        service,
        fixture,
        run_id="run-mat-all-missing",
        artifact_name="unrelated.txt",
        artifact_body="nothing to land\n",
    )

    result = service.materialize_all(run_id)
    assert result.ok, result.model_dump()
    assert result.data is not None
    assert result.data["landed"] == []
    assert result.data["skipped"] == [
        {
            "role": "architecture_document",
            "artifact": "ARCHITECTURE.md",
            "reason": "not_produced",
        }
    ]
    assert not (fixture / "docs").exists()


def test_host_materialize_all_rejects_unknown_role(tmp_path: Path) -> None:
    fixture = _fixture_repo(tmp_path)
    service = _named_service(tmp_path)
    run_id = _seed_materialize_run(service, fixture, run_id="run-mat-all-role")

    result = service.materialize_all(run_id, roles=["not_a_role"])
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "invalid_artifact_override"


def test_host_submit_rejects_unsafe_artifact_override(tmp_path: Path) -> None:
    service = _named_service(tmp_path)
    response = service.submit(
        RunRequest(
            request_id="req-bad-override",
            workflow_type="technical_plan",
            request_text="Design something.",
            artifact_overrides={
                "architecture_document": ArtifactOverride(dest_path="../../escape.md")
            },
            budget=RunBudget(max_cost_usd=Decimal("1.00")),
        ),
        mock=True,
        detach=False,
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "invalid_artifact_override"
    assert response.run_id is None


def test_host_cli_submit_accepts_artifact_override(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    _clear_pf_env(monkeypatch)
    monkeypatch.chdir(project)
    request = _request_file(tmp_path, "Design an integration testing architecture.")

    result = runner.invoke(
        app,
        [
            "host",
            "submit",
            "--request",
            str(request),
            "--workflow",
            "technical_plan",
            "--artifact-override",
            "architecture_document=docs/integration_testing_architecture.md",
            "--mock",
            "--sync",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = HostResponse.model_validate(json.loads(result.output.strip().splitlines()[-1]))
    assert payload.ok, payload.model_dump()
    assert payload.run_id is not None

    service = HostService(
        config=load_config(project),
        gateway=MockGateway(),
        use_deterministic_planner=True,
    )
    inspected = service.inspect(payload.run_id)
    assert inspected.data is not None
    assert inspected.data["artifact_land_map"][0]["suggested_dest_path"] == (
        "docs/integration_testing_architecture.md"
    )
    output = service.pf_root / "runs" / payload.run_id / "output"
    assert (output / "integration_testing_architecture.md").is_file()


def test_host_cli_submit_rejects_malformed_artifact_override(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    _clear_pf_env(monkeypatch)
    monkeypatch.chdir(project)
    request = _request_file(tmp_path, "Design something.")

    result = runner.invoke(
        app,
        [
            "host",
            "submit",
            "--request",
            str(request),
            "--artifact-override",
            "no-equals-sign",
            "--mock",
        ],
    )
    assert result.exit_code != 0


def test_host_cli_materialize_all(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    _clear_pf_env(monkeypatch)
    monkeypatch.chdir(project)
    service = HostService(
        config=load_config(project),
        gateway=MockGateway(),
        use_deterministic_planner=True,
    )
    run_id = _seed_materialize_run(
        service,
        fixture,
        run_id="run-cli-mat-all",
        artifact_name="scoped_architecture.md",
        artifact_body="# scoped_architecture.md\n\n## Objective\nLand all.\n",
        artifact_overrides={"architecture_document": {"dest_path": "docs/scoped_architecture.md"}},
    )

    result = runner.invoke(app, ["host", "materialize-all", run_id])
    assert result.exit_code == 0, result.output
    payload = HostResponse.model_validate(json.loads(result.output))
    assert payload.ok, payload.model_dump()
    assert (fixture / "docs" / "scoped_architecture.md").is_file()


def test_host_quality_gate_submit_inspect_and_materialize_all(tmp_path: Path) -> None:
    """One quality_gate run lands three deliverables through one host call (P4.E)."""
    fixture = _fixture_repo(tmp_path)
    service = _named_service(tmp_path)

    submitted = service.submit(
        RunRequest(
            request_id="req-quality-contract",
            workflow_type="quality_gate",
            request_text="Assess release readiness for the sample API.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
            approval_policy="none",
            artifact_overrides={
                "quality_findings": ArtifactOverride(
                    dest_path="docs/qa/release_readiness.md"
                )
            },
        ),
        mock=True,
        detach=False,
    )
    assert submitted.ok, submitted.model_dump()
    run_id = submitted.run_id
    assert run_id is not None

    inspected = service.inspect(run_id)
    assert inspected.ok, inspected.model_dump()
    assert inspected.data is not None
    land_map = {entry["role"]: entry for entry in inspected.data["artifact_land_map"]}
    assert set(land_map) == {
        "test_plan",
        "quality_findings",
        "security_evidence",
        "verification_report",
    }
    assert land_map["quality_findings"]["logical_name"] == "release_readiness.md"
    assert land_map["quality_findings"]["suggested_dest_path"] == "docs/qa/release_readiness.md"
    assert land_map["test_plan"]["suggested_dest_path"] == "docs/TEST_PLAN.md"
    assert land_map["verification_report"]["landable"] is False

    result = service.materialize_all(run_id)
    assert result.ok, result.model_dump()
    assert result.data is not None
    assert {entry["role"] for entry in result.data["landed"]} == {
        "test_plan",
        "quality_findings",
        "security_evidence",
    }
    assert (fixture / "docs" / "qa" / "release_readiness.md").is_file()
    assert (fixture / "docs" / "TEST_PLAN.md").is_file()
    assert (fixture / "docs" / "SECURITY_EVIDENCE.md").is_file()


def test_host_quality_gate_materialize_all_can_land_one_role(tmp_path: Path) -> None:
    fixture = _fixture_repo(tmp_path)
    service = _named_service(tmp_path)
    submitted = service.submit(
        RunRequest(
            request_id="req-quality-one-role",
            workflow_type="quality_gate",
            request_text="Assess release readiness for the sample API.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
            approval_policy="none",
        ),
        mock=True,
        detach=False,
    )
    assert submitted.ok, submitted.model_dump()
    assert submitted.run_id is not None

    result = service.materialize_all(submitted.run_id, roles=["test_plan"])
    assert result.ok, result.model_dump()
    assert result.data is not None
    assert [entry["role"] for entry in result.data["landed"]] == ["test_plan"]
    assert (fixture / "docs" / "TEST_PLAN.md").is_file()
    assert not (fixture / "docs" / "QUALITY_FINDINGS.md").exists()
