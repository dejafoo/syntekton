"""Shared test helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path


def clone_fixture(fixture: Path, dest: Path) -> Path:
    """Clone a clean git fixture into dest for isolated runs."""
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=True)
    subprocess.run(
        ["git", "clone", "--local", str(fixture), str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=dest,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        subprocess.run(["git", "clean", "-fdx"], cwd=dest, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "--", "."], cwd=dest, check=True, capture_output=True)
    return dest
