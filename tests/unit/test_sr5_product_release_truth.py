"""SR5 — product/release truth markers and evidence presence."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
EVIDENCE = ROOT / "docs" / "evidence" / "sustainable-remediation" / "sr5"
DOCS = ROOT / "docs"


def test_sr5_evidence_readme_lists_implemented_and_deferred() -> None:
    readme = (EVIDENCE / "README.md").read_text(encoding="utf-8")
    assert "SR5" in readme
    assert "## Implemented" in readme
    assert "## Deferred" in readme
    assert "Playwright" in readme
    assert "SBOM" in readme
    assert "Evidence level" in readme or "Evidence level:" in readme
    assert (EVIDENCE / "base_commit.txt").is_file()


def test_current_state_docs_carry_evidence_or_historical_markers() -> None:
    architecture = (DOCS / "architecture.md").read_text(encoding="utf-8")
    assert "Evidence level (SR5)" in architecture
    assert "Not verified" in architecture

    handover = (DOCS / "handover_sustainable_development.md").read_text(encoding="utf-8")
    assert "Historical document (SR5)" in handover

    sd_master = (DOCS / "next-work-packages-sustainable-development.md").read_text(encoding="utf-8")
    assert "Browser (Playwright) suites are not part of" in sd_master
    assert "Playwright browser coverage" in sd_master

    sd4 = (DOCS / "next-work-packages-sd4-protocol-clients.md").read_text(encoding="utf-8")
    assert "Playwright integration not verified" in sd4

    sd5 = (DOCS / "next-work-packages-sd5-release-engineering.md").read_text(encoding="utf-8")
    assert "Required?" in sd5
    assert "optional live" in sd5.lower()


def test_verify_script_does_not_soft_invoke_optional_live_integrations() -> None:
    verify = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")
    # Optional live modules must be behind explicit INTEGRATION gates, not
    # always invoked (which historically soft-skipped as success).
    assert 'if [[ "${DOCKER_INTEGRATION:-}" == "1" ]]; then' in verify
    assert 'if [[ "${BACKUP_INTEGRATION:-}" == "1" ]]; then' in verify
    assert 'if [[ "${DEPLOY_INTEGRATION:-}" == "1" ]]; then' in verify
    for module in (
        "tests/integration/test_remote_docker.py",
        "tests/integration/test_deploy_staging_live.py",
        "tests/integration/test_backup_restore.py",
    ):
        assert module in verify
        # The only invocation must sit inside the gated block (indented).
        matches = [line for line in verify.splitlines() if module in line]
        assert matches and all(line.startswith("  ") for line in matches)


def test_scheduled_recovery_labels_required_versus_optional() -> None:
    workflow = (ROOT / ".github" / "workflows" / "scheduled-recovery.yml").read_text(
        encoding="utf-8"
    )
    assert "hermetic-backup (required)" in workflow
    assert "docker-remote-restart (optional live)" in workflow
    assert "backup-restore-integration (optional live)" in workflow
    assert "FORCE_SCHEDULED" in workflow
