"""Seeded-repair harness: plant broken candidates and recover via repair."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.defects import defect_files, resolve_defect_kind
from product_factory.evaluation.runners import SeededRepairRunner
from product_factory.evaluation.subjects import SubjectConfig
from product_factory.gateway.mock import MockGateway


def test_resolve_defect_kind_defaults() -> None:
    assert resolve_defect_kind("code_health") == "failing_test"
    assert resolve_defect_kind("code_cache", explicit="incomplete_impl") == "incomplete_impl"


def test_defect_files_are_non_empty() -> None:
    files = defect_files("code_cache", "broken_syntax")
    assert any(path.endswith("cache.py") for path, _ in files)
    assert any("test" in path for path, _ in files)


def test_seeded_repair_mock_recovers_and_triggers_repair(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runner = SeededRepairRunner(load_config(root), use_deterministic_planner=True)
    case = EvalCase(
        id="code_health",
        workflow_type="code_change",
        request=(
            "Add a validated health-check function in a dedicated module, with unit tests."
        ),
        repository="tests/fixtures/sample_api",
        expected_files=["src/app/health.py", "tests/test_health.py"],
        smoke_commands=["python_tests"],
        budgets={"max_cost_usd": "1.00"},
    )
    artifact = runner.run(
        case,
        config=SubjectConfig(subject_id="seeded_repair", model_profile="supervisor"),
        gateway=MockGateway(),
        work_dir=tmp_path / "work",
    )
    assert artifact.subject_id == "seeded_repair"
    assert not artifact.error, artifact.error
    assert artifact.artifact_text.strip()
    assert artifact.metadata.get("repair_triggered") is True
    assert artifact.metadata.get("seed_repair_defect") == "failing_test"
    assert "health" in artifact.artifact_text.lower()
    # Final candidate should be the repaired good health helper, not status=bad.
    assert '"status": "bad"' not in artifact.artifact_text or "ok" in artifact.artifact_text
    assert "ok" in artifact.artifact_text


def test_seeded_repair_cache_failing_test_mock(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runner = SeededRepairRunner(load_config(root), use_deterministic_planner=True)
    case = EvalCase(
        id="code_cache",
        workflow_type="code_change",
        request="Introduce a simple cache helper behind an interface.",
        repository="tests/fixtures/sample_api",
        expected_files=["src/app/cache.py", "tests/test_cache.py"],
        smoke_commands=["python_tests"],
        budgets={"max_cost_usd": "1.00"},
    )
    artifact = runner.run(
        case,
        config=SubjectConfig(subject_id="seeded_repair", model_profile="supervisor"),
        gateway=MockGateway(),
        work_dir=tmp_path / "cache-work",
    )
    assert artifact.metadata.get("repair_triggered") is True
    assert artifact.metadata.get("seed_repair_defect") == "failing_test"
    assert "InMemoryCache" in artifact.artifact_text or "Cache" in artifact.artifact_text
