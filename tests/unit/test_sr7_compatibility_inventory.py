"""SR7 — compatibility inventory guardrails (inventory slice only)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs" / "architecture" / "sr7-compatibility-inventory.md"
EVIDENCE = ROOT / "docs" / "evidence" / "sustainable-remediation" / "sr7" / "README.md"
SD8_BASELINES = (
    ROOT
    / "docs"
    / "evidence"
    / "sustainable-development"
    / "sd8"
    / "baselines"
    / "synthetic-small-medium.json"
)


def test_sr7_compatibility_inventory_exists_and_lists_required_surfaces() -> None:
    assert INVENTORY.is_file()
    text = INVENTORY.read_text(encoding="utf-8")
    assert "host/v1" in text
    assert "workflow aliases" in text.lower() or "Workflow aliases" in text
    assert "RunCoordinator" in text
    assert "Do not remove" in text or "do not remove" in text.lower()
    assert "baseline-only" in text.lower() or "baselines only" in text.lower()
    assert "SD8" in text


def test_sr7_evidence_readme_and_sd8_baselines_are_referenced() -> None:
    assert EVIDENCE.is_file()
    evidence = EVIDENCE.read_text(encoding="utf-8").lower()
    assert "baseline-only" in evidence or "baselines only" in evidence
    assert "optimization win" in evidence
    assert SD8_BASELINES.is_file()
