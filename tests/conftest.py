"""Shared test helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def hermetic_validation_config(root: Path):
    """Return test-only command policy for graph fixtures.

    Production commands remain the operator-configured commands.  These two
    standard-library scripts deliberately run through the same ToolBroker and
    sandbox as production validation, while needing neither network nor the
    Product Factory virtual environment.
    """
    from product_factory.config.loader import load_config

    config = load_config(root)
    commands = dict(config.policies.registered_commands)
    commands.update(
        {
            "python_tests": {
                "executable": "python",
                "args": ["tools/run_tests.py"],
                "timeout_seconds": 30,
                "success_exit_codes": [0],
                "domain_failure_exit_codes": [1],
            },
            "python_typecheck": {
                "executable": "python",
                "args": ["tools/typecheck.py"],
                "timeout_seconds": 30,
                "success_exit_codes": [0],
                "domain_failure_exit_codes": [1],
            },
        }
    )
    return config.model_copy(
        update={"policies": config.policies.model_copy(update={"registered_commands": commands})}
    )


@pytest.fixture(autouse=True)
def _reset_host_registry() -> None:
    """SD4.A: each test starts without a cached HostService supervisor."""
    from product_factory.host.registry import reset_host_registry

    reset_host_registry()
    yield
    reset_host_registry()


def materialize_git_fixture(fixture: Path, dest: Path) -> Path:
    """Copy a fixture directory into dest as a fresh git repository."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        fixture,
        dest,
        ignore=shutil.ignore_patterns(".git"),
    )
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=fixture@example.com",
            "-c",
            "user.name=Fixture",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    return dest


def clone_fixture(fixture: Path, dest: Path) -> Path:
    """Clone a clean git fixture into dest for isolated runs."""
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=True)
    if (fixture / ".git").exists():
        result = subprocess.run(
            ["git", "clone", "--local", str(fixture), str(dest)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=dest,
                capture_output=True,
                text=True,
                check=True,
            )
            if status.stdout.strip():
                subprocess.run(["git", "clean", "-fdx"], cwd=dest, check=True, capture_output=True)
                subprocess.run(
                    ["git", "checkout", "--", "."],
                    cwd=dest,
                    check=True,
                    capture_output=True,
                )
            return dest
    return materialize_git_fixture(fixture, dest)
