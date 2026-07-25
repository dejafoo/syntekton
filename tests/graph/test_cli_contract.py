"""CLI contract tests (P1.A / P1.B / P1.C): mock end-to-end via `typer.testing.CliRunner`.

Each test runs against an isolated project root (`tmp_path`) with its own
`.product-factory` data dir, so nothing touches the real repository's state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from product_factory.cli.app import app
from tests.conftest import clone_fixture

runner = CliRunner()


def _project_root(tmp_path: Path) -> Path:
    """An isolated project root with its own copy of `config/`."""
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


def test_run_cli_mock_completes_and_writes_validation_commands(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    request = _request_file(tmp_path, "Add a validated health-check endpoint with tests.")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "run",
            "--request",
            str(request),
            "--repo",
            str(fixture),
            "--mock",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads(result.output)
    assert manifest["final_status"] in {"completed", "awaiting_approval"}


def test_run_cli_unknown_validation_command_fails_closed(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    request = _request_file(tmp_path, "Add a validated health-check endpoint with tests.")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "run",
            "--request",
            str(request),
            "--repo",
            str(fixture),
            "--mock",
            "--json",
            "--validation-command",
            "definitely-not-registered",
        ],
    )
    # No host-shell fallback: an unregistered command id is a typed, blocking
    # validation failure rather than a silently skipped or invented check.
    assert result.exit_code == 4, result.output
    manifest = json.loads(result.output)
    assert manifest["final_status"] == "failed"


def test_run_cli_validation_commands_comma_separated_and_repeatable(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    request = _request_file(tmp_path, "Add a validated health-check endpoint with tests.")
    monkeypatch.chdir(project)

    # `--validation-commands a,b` and repeatable `--validation-command c` both
    # select unknown ids here on purpose — cheap way to assert both CLI entry
    # points reach the same fail-closed validation path without needing to
    # actually execute `uv run pytest` inside the fixture repo.
    result = runner.invoke(
        app,
        [
            "run",
            "--request",
            str(request),
            "--repo",
            str(fixture),
            "--mock",
            "--json",
            "--validation-commands",
            "missing_one,missing_two",
            "--validation-command",
            "missing_three",
        ],
    )
    assert result.exit_code == 4, result.output
    manifest = json.loads(result.output)
    assert manifest["final_status"] == "failed"


def test_run_cli_max_wall_clock_seconds_zero_exhausts_budget(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    request = _request_file(tmp_path, "Add a validated health-check endpoint with tests.")
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "run",
            "--request",
            str(request),
            "--repo",
            str(fixture),
            "--mock",
            "--max-wall-clock-seconds",
            "0",
        ],
    )
    assert result.exit_code == 6, result.output
    assert "BudgetExhaustedError" in result.output


def test_run_cli_policy_override_registers_custom_validation_command(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    request = _request_file(tmp_path, "Add a validated health-check endpoint with tests.")
    monkeypatch.chdir(project)

    policy_path = tmp_path / "custom-policy.yaml"
    policy_path.write_text(
        "registered_commands:\n"
        "  say_ok:\n"
        "    executable: python3\n"
        '    args: ["-c", "print(\'ok\')"]\n'
        "    timeout_seconds: 30\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--request",
            str(request),
            "--repo",
            str(fixture),
            "--mock",
            "--json",
            "--policy",
            str(policy_path),
            "--validation-command",
            "say_ok",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads(result.output)
    assert manifest["final_status"] in {"completed", "awaiting_approval"}


def test_resume_cli_unknown_run_id_fails_closed(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["resume", "run-does-not-exist", "--mock"])
    assert result.exit_code == 2, result.output
    assert "ConfigurationError" in result.output


def test_run_then_resume_cli_round_trip(tmp_path: Path, monkeypatch) -> None:
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    request = _request_file(tmp_path, "Add a validated health-check endpoint with tests.")
    monkeypatch.chdir(project)

    run_result = runner.invoke(
        app, ["run", "--request", str(request), "--repo", str(fixture), "--mock", "--json"]
    )
    assert run_result.exit_code == 0, run_result.output
    run_id = json.loads(run_result.output)["run_id"]

    # Already-terminal run: resume must fail closed rather than silently no-op.
    resume_result = runner.invoke(app, ["resume", run_id, "--mock"])
    assert resume_result.exit_code == 2, resume_result.output
    assert "ConfigurationError" in resume_result.output
