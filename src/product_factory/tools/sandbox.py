"""Process sandboxes for registered validation commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
    "PYTHONPATH",
}

# Bare interpreter names are not portable across macOS / CI images that only
# ship ``python3`` or a venv entrypoint. Resolve them to the active runtime.
_PYTHON_ALIASES = frozenset({"python", "python3"})


def resolve_executable(executable: str) -> str:
    """Resolve a registered command executable for the current process.

    ``python`` / ``python3`` always map to ``sys.executable`` so hermetic tests
    and workers use the same interpreter that loaded Product Factory, instead of
    assuming a bare ``python`` exists on ``PATH``.
    """
    name = Path(executable).name
    if name in _PYTHON_ALIASES:
        return sys.executable
    return executable


@dataclass
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    sandbox: str


CommandExecutionState = Literal["passed", "domain_failure", "timeout", "unavailable"]


def classify_command_result(result: SandboxResult, spec: dict[str, Any]) -> CommandExecutionState:
    """Classify a receipt without pretending unavailable execution is a test failure."""
    if result.returncode == 124:
        return "timeout"
    success_codes = {int(code) for code in spec.get("success_exit_codes", [0])}
    domain_failure_codes = {int(code) for code in spec.get("domain_failure_exit_codes", [1])}
    if result.returncode in success_codes:
        return "passed"
    if result.returncode in domain_failure_codes:
        return "domain_failure"
    return "unavailable"


@dataclass(frozen=True, slots=True)
class ManagedSandboxPaths:
    """Validated Product Factory-owned writable paths for a command sandbox."""

    cache_root: Path

    @classmethod
    def under(cls, data_root: Path) -> ManagedSandboxPaths:
        root = data_root.resolve()
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError("managed sandbox root must not be filesystem or home root")
        cache_root = (root / "cache" / "tooling" / "uv").resolve()
        try:
            cache_root.relative_to(root)
        except ValueError as exc:
            raise ValueError("managed sandbox cache escapes data root") from exc
        if cache_root.is_symlink() or any(parent.is_symlink() for parent in cache_root.parents):
            raise ValueError("managed sandbox cache must not traverse symlinks")
        return cls(cache_root=cache_root)

    def ensure(self) -> Path:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        resolved = self.cache_root.resolve()
        if resolved != self.cache_root:
            raise ValueError("managed sandbox cache changed while being prepared")
        return resolved


def _scrubbed_env(
    *, pythonpath: str | None = None, managed_paths: ManagedSandboxPaths
) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            env[key] = value
    if pythonpath:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = pythonpath if not existing else f"{pythonpath}:{existing}"
    # Never inherit cache or active-venv authority from the operator process.
    # A managed path is explicitly constructed by Product Factory instead.
    env["UV_CACHE_DIR"] = str(managed_paths.ensure())
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
    managed_paths: ManagedSandboxPaths | None = None,
    prefer_bwrap: bool = True,
) -> SandboxResult:
    """Run a registered command under restricted env; use bwrap when available."""
    cmd = [resolve_executable(executable), *args]
    # Direct test callers may use an explicitly isolated temporary root. All
    # production callers pass a Product Factory data-root derived value.
    paths = managed_paths or ManagedSandboxPaths.under(cwd / ".product-factory-sandbox")
    env = _scrubbed_env(pythonpath=pythonpath, managed_paths=paths)
    sandbox_name = "restricted"
    if prefer_bwrap and _has_bwrap():
        sandbox_name = "bwrap"
        # Minimal bubblewrap: private /tmp, no network, bind worktree RW, keep /usr read-only.
        work = str(cwd.resolve())
        bwrap_cmd = [
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
        ]
        uv_cache = env.get("UV_CACHE_DIR")
        if uv_cache:
            cache_resolved = str(paths.ensure())
            # The only writable mount outside the worktree is the validated
            # Product Factory cache. Ambient UV_CACHE_DIR can never reach here.
            if cache_resolved != work and not cache_resolved.startswith(work + os.sep):
                bwrap_cmd.extend(["--bind", cache_resolved, cache_resolved])
        bwrap_cmd.extend(
            [
                "--chdir",
                work,
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                *cmd,
            ]
        )
        cmd = bwrap_cmd
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
    except OSError as exc:
        duration = time.monotonic() - started
        return SandboxResult(
            returncode=127,
            stdout="",
            stderr=f"Command unavailable: {exc}",
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
