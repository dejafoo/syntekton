"""code_cache regression: orchestration must emit a non-empty cache patch."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.evaluation.runners import FullOrchestrationRunner
from product_factory.evaluation.subjects import SubjectConfig
from product_factory.gateway.mock import MockGateway


def test_orchestration_code_cache_produces_cache_patch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    # Ensure fixture clone works for runner's internal clone path
    work = tmp_path / "work"
    work.mkdir()
    # Point a fake case repository through a path relative to config.root
    # FullOrchestrationRunner clones case.repository under work_dir/repo.
    runner = FullOrchestrationRunner(config, use_deterministic_planner=True)
    from product_factory.evaluation.cases import EvalCase

    case = EvalCase(
        id="code_cache",
        workflow_type="code_change",
        request="Introduce a simple cache helper behind an interface.",
        repository="tests/fixtures/sample_api",
        budgets={"max_cost_usd": "1.00"},
    )
    artifact = runner.run(
        case,
        config=SubjectConfig(subject_id="full_orchestration", model_profile="local_target"),
        gateway=MockGateway(),
        work_dir=work,
    )
    assert not artifact.error
    assert artifact.artifact_kind == "patch"
    assert artifact.artifact_text.strip(), "expected non-empty patch"
    assert "cache.py" in artifact.artifact_text
    assert "Cache" in artifact.artifact_text or "InMemoryCache" in artifact.artifact_text
    assert artifact.run_id
    lineage_files = list(
        (work / ".product-factory" / "runs" / artifact.run_id / "output").glob(
            "*-lineage.json"
        )
    )
    assert lineage_files, "composition must persist predecessor lineage"
