"""SD7 simplification/governance guardrails."""

from __future__ import annotations

from pathlib import Path

from product_factory.catalogs import full_catalog
from product_factory.connectors import deploy
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.domain.runs import RunRequest
from product_factory.host.service import HostService
from product_factory.workflows.registry import resolve_workflow_pack

ROOT = Path(__file__).resolve().parents[2]


def test_langgraph_demo_and_deps_removed() -> None:
    assert not (ROOT / "src/product_factory/orchestration/graph.py").exists()
    assert not (ROOT / "src/product_factory/orchestration/state.py").exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "langgraph" not in pyproject
    assert "aiosqlite" not in pyproject
    assert "build_graph" not in (ROOT / "src/product_factory/cli/app.py").read_text(
        encoding="utf-8"
    )
    adr = (ROOT / "docs/architecture/ADR-001-langgraph.md").read_text(encoding="utf-8")
    assert "Superseded" in adr


def test_websocket_and_stub_remain_absent() -> None:
    src_blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src").rglob("*.py")
    )
    assert "/api/v1/events/ws" not in src_blob
    assert "completed (stub)" not in src_blob


def test_project_profile_removed_from_run_request() -> None:
    assert "project_profile" not in RunRequest.model_fields
    assert "model_profile_set" in RunRequest.model_fields
    assert "requested_artifacts" in RunRequest.model_fields


def test_workflows_yaml_is_not_pack_authority() -> None:
    # Mutating AppConfig.workflows must not change registry resolution.
    pack = resolve_workflow_pack("code_change")
    assert pack.id == "repository_change"
    assert pack.execution_policy is not None


def test_jsonl_is_not_host_event_authority(tmp_path: Path) -> None:
    # HostService must not expose a protocol path that synthesizes authority
    # from events.jsonl (method removed in SD7).
    assert not hasattr(HostService, "_events_from_jsonl")


def test_simulated_staging_connector_id_and_legacy_config_alias() -> None:
    assert deploy.CONNECTOR_ID == "simulated_staging"
    assert deploy.LEGACY_CONNECTOR_ID == "staging_deploy"
    legacy = ConnectorsConfig(
        connectors={"staging_deploy": ConnectorSettings(enabled=True)}
    )
    assert legacy.settings_for("simulated_staging").enabled is True
    modern = ConnectorsConfig(
        connectors={"simulated_staging": ConnectorSettings(enabled=True)}
    )
    assert modern.settings_for("staging_deploy").enabled is True


def test_registry_catalogs_include_simulated_staging() -> None:
    catalog = full_catalog()
    workflow_ids = {row["workflow_type"] for row in catalog["workflows"]}
    assert "repository_change" in workflow_ids
    assert resolve_workflow_pack("code_change").id == "repository_change"
    connector_ids = {row["connector_id"] for row in catalog["connectors"]}
    assert "simulated_staging" in connector_ids
    assert catalog["capabilities"]
