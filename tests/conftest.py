"""Shared test helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


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
