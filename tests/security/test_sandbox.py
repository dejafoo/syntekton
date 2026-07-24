"""Sandbox security tests (P1.D): secret scrubbing, timeout kill, bwrap network deny."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from product_factory.tools.sandbox import run_sandboxed_command, sandbox_info


def test_secret_env_var_not_visible_to_sandboxed_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRODUCT_FACTORY_TEST_SECRET", "super-secret-value")
    result = run_sandboxed_command(
        executable=sys.executable,
        args=["-c", "import os; print(os.environ.get('PRODUCT_FACTORY_TEST_SECRET', 'MISSING'))"],
        cwd=tmp_path,
        timeout_seconds=10,
        prefer_bwrap=False,
    )
    assert result.returncode == 0
    assert "super-secret-value" not in result.stdout
    assert "MISSING" in result.stdout


def test_only_allowlisted_env_vars_pass_through(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SOME_RANDOM_SECRET", "leak-me-not")
    result = run_sandboxed_command(
        executable=sys.executable,
        args=["-c", "import os; print(len(os.environ))"],
        cwd=tmp_path,
        timeout_seconds=10,
        prefer_bwrap=False,
    )
    assert result.returncode == 0
    # A handful of allowlisted keys at most (PATH/HOME/etc.), never the ambient
    # environment the test runner itself has (which is much larger).
    assert int(result.stdout.strip()) < 15


def test_timeout_kills_hung_command(tmp_path: Path) -> None:
    result = run_sandboxed_command(
        executable=sys.executable,
        args=["-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=1,
        prefer_bwrap=False,
    )
    assert result.returncode == 124
    assert result.duration_seconds < 10


def test_sandbox_reports_restricted_when_bwrap_disabled(tmp_path: Path) -> None:
    result = run_sandboxed_command(
        executable=sys.executable,
        args=["-c", "print('ok')"],
        cwd=tmp_path,
        timeout_seconds=5,
        prefer_bwrap=False,
    )
    assert result.sandbox == "restricted"
    assert result.stdout.strip() == "ok"


@pytest.mark.skipif(not shutil.which("bwrap"), reason="bwrap not installed")
def test_bwrap_denies_network_access(tmp_path: Path) -> None:
    result = run_sandboxed_command(
        executable=sys.executable,
        args=[
            "-c",
            (
                "import socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                "s.settimeout(2)\n"
                "try:\n"
                "    s.connect(('1.1.1.1', 80))\n"
                "    print('connected')\n"
                "except OSError as exc:\n"
                "    print(f'blocked: {exc}')\n"
            ),
        ],
        cwd=tmp_path,
        timeout_seconds=10,
        prefer_bwrap=True,
    )
    assert result.sandbox == "bwrap"
    assert "connected" not in result.stdout


def test_sandbox_info_reports_platform_note() -> None:
    info = sandbox_info()
    assert info["restricted"] is True
    assert "platform_note" in info
    assert isinstance(info["bwrap_available"], bool)
