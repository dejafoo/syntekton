"""Portable interpreter resolution for registered validation commands."""

from __future__ import annotations

import sys
from pathlib import Path

from product_factory.tools.sandbox import resolve_executable, run_sandboxed_command


def test_python_aliases_resolve_to_active_interpreter() -> None:
    assert resolve_executable("python") == sys.executable
    assert resolve_executable("python3") == sys.executable
    assert resolve_executable("/usr/bin/echo") == "/usr/bin/echo"


def test_sandboxed_python_alias_runs_under_active_interpreter(tmp_path: Path) -> None:
    result = run_sandboxed_command(
        executable="python",
        args=["-c", "print('portable-ok')"],
        cwd=tmp_path,
        timeout_seconds=10,
        prefer_bwrap=False,
    )
    assert result.returncode == 0
    assert "portable-ok" in result.stdout
