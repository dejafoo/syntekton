"""Mock graph tests for seeded review detection harness."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.runners import SeededReviewRunner, default_subject_configs
from product_factory.gateway.mock import MockGateway


def test_seeded_review_detects_correctness_defect(tmp_path: Path) -> None:
    config = load_config()
    runner = SeededReviewRunner(config, use_deterministic_planner=True)
    case = EvalCase(
        id="code_cache",
        title="cache",
        request="Implement an in-memory cache helper.",
        workflow_type="code_change",
        repository=str(Path("tests/fixtures/sample_api")),
        acceptance_criteria=["cache roundtrip works"],
        smoke_commands=["python_tests"],
        metadata={"seed_review_defect": "failing_test"},
    )
    subject = default_subject_configs()["seeded_review"]
    artifact = runner.run(case, config=subject, gateway=MockGateway(), work_dir=tmp_path)
    detection = artifact.metadata.get("seed_review_detection") or {}
    assert detection.get("detected") is True
    assert detection.get("false_block") is False
    assert detection.get("cited_seed_path") is True


def test_seeded_review_style_only_not_blocking(tmp_path: Path) -> None:
    config = load_config()
    runner = SeededReviewRunner(config, use_deterministic_planner=True)
    case = EvalCase(
        id="code_cache",
        title="cache",
        request="Implement an in-memory cache helper.",
        workflow_type="code_change",
        repository=str(Path("tests/fixtures/sample_api")),
        acceptance_criteria=["cache roundtrip works"],
        smoke_commands=["python_tests"],
        metadata={"seed_review_defect": "style_only"},
    )
    subject = default_subject_configs()["seeded_review"]
    artifact = runner.run(case, config=subject, gateway=MockGateway(), work_dir=tmp_path)
    detection = artifact.metadata.get("seed_review_detection") or {}
    assert detection.get("false_block") is False
    assert detection.get("blocking_count", 0) == 0
