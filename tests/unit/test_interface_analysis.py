"""Local contract analysis and technical-spike confinement tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from product_factory.domain.capabilities import CAPABILITIES, CAPABILITY_TOOL_CLASSES
from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tools import CapabilityGrant
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.planning.compiler import compile_plan
from product_factory.schemas import default_schema_registry, validate_write_payload
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.registry import resolve_workflow_pack

FIXTURES = Path(__file__).parents[1] / "fixtures" / "contracts"


@pytest.fixture
def spike_broker(tmp_path: Path) -> tuple[ToolBroker, Path]:
    root = tmp_path / "data" / "runs" / "run-1" / "scratch" / "T-001"
    shutil.copytree(FIXTURES, root)
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        worktree_root=root,
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g-spike",
            run_id="run-1",
            task_id="T-001",
            agent_profile="interface_analyst",
            tool_names={
                "parse_contract",
                "contract_inventory",
                "diff_contracts",
                "map_capabilities",
                "generate_synthetic_fixture",
                "run_contract_simulation",
            },
            readable_path_patterns=["**/*"],
            writable_path_patterns=["synthetic/**"],
        )
    )
    return broker, root


def test_inventory_addresses_openapi_and_json_schema(spike_broker) -> None:
    broker, _ = spike_broker
    openapi = broker.execute(
        task_id="T-001",
        tool_name="contract_inventory",
        arguments={"path": "openapi_baseline.yaml"},
    )
    schema = broker.execute(
        task_id="T-001",
        tool_name="contract_inventory",
        arguments={"path": "json_schema_valid.json"},
    )
    assert openapi["addresses"][0]["address"] == "GET /pets"
    assert openapi["schemas"] == ["Pet"]
    assert {item["address"] for item in schema["addresses"]} == {
        "$.active",
        "$.customer_id",
    }
    assert schema["schemas"] == ["Address"]


def test_diff_classifies_breaking_and_non_breaking_changes(spike_broker) -> None:
    broker, _ = spike_broker
    result = broker.execute(
        task_id="T-001",
        tool_name="diff_contracts",
        arguments={
            "baseline_path": "openapi_baseline.yaml",
            "candidate_path": "openapi_breaking.yaml",
        },
    )
    assert result["classification"] == "breaking"
    assert {"GET /pets", "#/components/schemas/Pet.id"} <= {
        change["address"] for change in result["changes"]
    }
    assert any(
        change["address"] == "GET /animals" and change["classification"] == "non_breaking"
        for change in result["changes"]
    )


def test_synthetic_fixture_and_simulation_stay_local(spike_broker) -> None:
    broker, root = spike_broker
    generated = broker.execute(
        task_id="T-001",
        tool_name="generate_synthetic_fixture",
        arguments={
            "contract_path": "json_schema_valid.json",
            "output_path": "synthetic/customer.json",
        },
    )
    assert generated["fixture"] == {"active": True, "customer_id": "synthetic"}
    assert (root / "synthetic" / "customer.json").is_file()
    simulation = broker.execute(
        task_id="T-001",
        tool_name="run_contract_simulation",
        arguments={
            "contract_path": "json_schema_valid.json",
            "fixture_path": "synthetic/customer.json",
        },
    )
    assert simulation["status"] == "passed"
    assert simulation["measurements"]["validation_error_count"] == 0


def test_spike_rejects_path_and_symlink_escape(spike_broker, tmp_path: Path) -> None:
    broker, root = spike_broker
    with pytest.raises(ToolAuthorizationError):
        broker.execute(
            task_id="T-001",
            tool_name="generate_synthetic_fixture",
            arguments={
                "contract_path": "json_schema_valid.json",
                "output_path": "../escaped.json",
            },
        )
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "synthetic").symlink_to(outside)
    with pytest.raises(ToolAuthorizationError):
        broker.execute(
            task_id="T-001",
            tool_name="generate_synthetic_fixture",
            arguments={
                "contract_path": "json_schema_valid.json",
                "output_path": "synthetic/escaped.json",
            },
        )
    assert not (outside / "escaped.json").exists()


def test_invalid_contract_is_rejected(spike_broker) -> None:
    broker, _ = spike_broker
    with pytest.raises(ToolAuthorizationError):
        broker.execute(
            task_id="T-001",
            tool_name="parse_contract",
            arguments={"path": "invalid_contract.yaml"},
        )


def test_interface_capability_tools_and_skills_are_wired() -> None:
    assert "interface_analysis" in CAPABILITIES
    assert CAPABILITY_TOOL_CLASSES["interface_analysis"] == frozenset(
        {"repository_read", "artifact_write", "interface_analysis", "synthetic_write"}
    )
    registry = default_tool_registry()
    assert {
        "parse_contract",
        "contract_inventory",
        "diff_contracts",
        "map_capabilities",
        "generate_synthetic_fixture",
        "run_contract_simulation",
    } <= registry.names()
    skills = SkillRegistry.load(Path(__file__).parents[2] / "skills")
    assert skills.get("integration.contract-analysis") is not None
    assert skills.get("integration.technical-spike") is not None


def test_technical_spike_pack_compiles_and_schema_is_writable() -> None:
    pack = resolve_workflow_pack("technical_spike")
    proposal = handler_for("technical_spike").plan_template("Can this contract support pets?")
    result = compile_plan(
        proposal,
        workflow_pack=pack,
        skill_registry=SkillRegistry.load(Path(__file__).parents[2] / "skills"),
    )
    assert result.ok, result.errors
    schema = default_schema_registry().get("spike_result.v1")
    assert schema is not None
    assert not schema.reserved
    validate_write_payload(
        "spike_result.v1",
        {
            "schema_id": "spike_result.v1",
            "hypothesis": "The contract can represent a customer.",
            "method": {"mode": "local_synthetic"},
            "measurements": {"validation_error_count": 0},
            "limits": ["No live endpoint tested"],
            "artifact_refs": [
                {
                    "role": "contract_inventory",
                    "sha256": "a" * 64,
                    "schema_id": "contract_inventory.v1",
                },
                {
                    "role": "contract_simulation",
                    "sha256": "b" * 64,
                    "schema_id": "contract_simulation.v1",
                },
                {
                    "role": "contract_compatibility",
                    "sha256": "c" * 64,
                    "schema_id": "contract_compatibility.v1",
                },
            ],
        },
    )
