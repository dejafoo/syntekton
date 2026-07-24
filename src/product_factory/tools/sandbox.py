"""Process sandboxes for registered validation commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Env keys allowed into sandboxed validation commands (plus PATH/HOME/LANG basics).
_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
    "LOGNAME",
    "UV_PROJECT_ENVIRONMENT",
    "VIRTUAL_ENV",
    "PYTHONPATH",
}


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    sandbox: str


def _scrubbed_env(*, pythonpath: str | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            env[key] = value
    if pythonpath:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = pythonpath if not existing else f"{pythonpath}:{existing}"
    # Explicitly drop common secret-bearing vars even if somehow allowlisted later.
    for banned in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "PRODUCT_FACTORY_TEST_SECRET",
    ):
        env.pop(banned, None)
    return env


def _has_bwrap() -> bool:
    return shutil.which("bwrap") is not None


def run_sandboxed_command(
    *,
    executable: str,
    args: list[str],
    cwd: Path,
    timeout_seconds: int,
    pythonpath: str | None = None,
    prefer_bwrap: bool = True,
) -> SandboxResult:
    """Run a registered command under restricted env; use bwrap when available."""
    cmd = [executable, *args]
    env = _scrubbed_env(pythonpath=pythonpath)
    sandbox_name = "restricted"
    if prefer_bwrap and _has_bwrap():
        sandbox_name = "bwrap"
        # Minimal bubblewrap: private /tmp, no network, bind worktree RW, keep /usr read-only.
        work = str(cwd.resolve())
        cmd = [
            "bwrap",
            "--die-with-parent",
            "--unshare-net",
            "--tmpfs",
            "/tmp",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind-try",
            "/lib64",
            "/lib64",
            "--ro-bind-try",
            "/opt",
            "/opt",
            "--ro-bind-try",
            str(Path.home() / ".local"),
            str(Path.home() / ".local"),
            "--bind",
            work,
            work,
            "--chdir",
            work,
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            *cmd,
        ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd if sandbox_name == "restricted" else None,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
        duration = time.monotonic() - started
        return SandboxResult(
            returncode=proc.returncode,
            stdout=(proc.stdout or "")[-8000:],
            stderr=(proc.stderr or "")[-8000:],
            duration_seconds=duration,
            sandbox=sandbox_name,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        return SandboxResult(
            returncode=124,
            stdout=((exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))[
                -8000:
            ]
            if not isinstance(exc.stdout, bytes)
            else exc.stdout.decode(errors="replace")[-8000:],
            stderr=f"Command timed out after {timeout_seconds}s",
            duration_seconds=duration,
            sandbox=sandbox_name,
        )


def sandbox_info() -> dict[str, Any]:
    return {
        "restricted": True,
        "bwrap_available": _has_bwrap(),
        "platform_note": (
            "Darwin uses restricted subprocess (env scrub + cwd). "
            "Linux prefers bubblewrap when installed."
        ),
    }
