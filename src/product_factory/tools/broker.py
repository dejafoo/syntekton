"""Tool broker — sole execution path for tools."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tools import CapabilityGrant, ToolCallRecord
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.tools.policies import assert_path_allowed, resolve_under_root
from product_factory.tools.registry import ToolRegistry
from product_factory.tools.sandbox import run_sandboxed_command

ToolObserver = Callable[[str, dict[str, Any]], None]


class ToolBroker:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        artifact_store: ArtifactStore,
        worktree_root: Path | None = None,
        original_repo: Path | None = None,
        registered_commands: dict[str, dict[str, Any]] | None = None,
        base_commit: str | None = None,
        observer: ToolObserver | None = None,
        ledger: BudgetLedger | None = None,
    ) -> None:
        self.registry = registry
        self.artifact_store = artifact_store
        # Resolve so macOS /var → /private/var (and similar) matches glob results.
        self.worktree_root = worktree_root.resolve() if worktree_root else None
        self.original_repo = original_repo.resolve() if original_repo else None
        self.registered_commands = registered_commands or {}
        self.base_commit = base_commit
        self.grants: dict[str, CapabilityGrant] = {}
        self.history: list[ToolCallRecord] = []
        self.observer = observer
        self.ledger = ledger

    def set_grant(self, grant: CapabilityGrant) -> None:
        self.grants[grant.task_id] = grant

    def set_observer(self, observer: ToolObserver | None) -> None:
        self.observer = observer

    def execute(self, *, task_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.registry.get(tool_name)
        grant = self.grants.get(task_id)
        if grant is None:
            raise ToolAuthorizationError(f"No capability grant for task {task_id}")
        if tool_name not in grant.tool_names:
            raise ToolAuthorizationError(
                f"Tool {tool_name} not granted to task {task_id}",
            )
        if grant.calls_made >= grant.max_calls:
            raise ToolAuthorizationError("Grant max_calls exceeded")
        if self.ledger is not None:
            # ToolBroker is the sole execution path for tools — enforce the
            # run-level tool-call budget here (P1.A) before every dispatch.
            self.ledger.check_before_tool()

        started = time.perf_counter()
        tool_call_id = f"tc-{uuid.uuid4().hex[:12]}"
        args_hash = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode()
        ).hexdigest()
        error: str | None = None
        exit_status = 0
        result: dict[str, Any]
        output_ref: str | None = None

        if self.observer is not None:
            self.observer(
                "started",
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "task_id": task_id,
                    "arguments_hash": args_hash,
                },
            )

        try:
            result = self._dispatch(tool_name, arguments, grant, tool_call_id)
            if "artifact_sha256" in result:
                output_ref = result["artifact_sha256"]
        except Exception as exc:
            exit_status = 1
            error = str(exc)
            result = {"error": error}
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            grant.calls_made += 1
            if self.ledger is not None:
                self.ledger.record_tool_call()
            record = ToolCallRecord(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                task_id=task_id,
                arguments_hash=args_hash,
                resource_scope=str(self.worktree_root or ""),
                duration_ms=duration_ms,
                exit_status=exit_status,
                output_artifact_ref=output_ref,
                trust_label="untrusted" if tool.result_may_be_untrusted else "trusted",
                error=error,
            )
            self.history.append(record)
            if self.observer is not None:
                self.observer(
                    "failed" if error else "completed",
                    record.model_dump(mode="json"),
                )

        result["tool_call_id"] = tool_call_id
        return result

    def _require_worktree(self) -> Path:
        if self.worktree_root is None:
            raise ToolAuthorizationError("No worktree bound for tool execution")
        return self.worktree_root

    def _dispatch(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        grant: CapabilityGrant,
        tool_call_id: str,
    ) -> dict[str, Any]:
        if tool_name == "list_files":
            return self._list_files(arguments, grant)
        if tool_name == "read_file":
            return self._read_file(arguments, grant)
        if tool_name == "search_text":
            return self._search_text(arguments, grant)
        if tool_name == "git_diff":
            return self._git_diff(arguments, tool_call_id)
        if tool_name == "git_status":
            return self._git_status()
        if tool_name == "apply_patch":
            return self._apply_patch(arguments, grant)
        if tool_name == "create_file":
            return self._create_file(arguments, grant)
        if tool_name == "write_artifact":
            return self._write_artifact(arguments, grant.task_id, tool_call_id)
        if tool_name == "run_validation_command":
            return self._run_command(arguments)
        raise ToolAuthorizationError(f"No implementation for tool {tool_name}")

    def _list_files(self, arguments: dict[str, Any], grant: CapabilityGrant) -> dict[str, Any]:
        root = self._require_worktree()
        directory = arguments.get("directory", ".")
        assert_path_allowed(directory, grant.read_patterns())
        path = resolve_under_root(root, directory)
        if self.original_repo and path == self.original_repo:
            raise ToolAuthorizationError("Cannot operate on original repository path")
        glob = arguments.get("glob", "**/*")
        max_results = int(arguments.get("max_results", 200))
        matches = []
        root_resolved = root.resolve()
        for p in sorted(path.glob(glob)):
            if p.is_file():
                rel = str(p.resolve().relative_to(root_resolved))
                matches.append({"path": rel, "size": p.stat().st_size})
                if len(matches) >= max_results:
                    break
        return {"files": matches}

    def _read_file(self, arguments: dict[str, Any], grant: CapabilityGrant) -> dict[str, Any]:
        root = self._require_worktree()
        rel = arguments["path"]
        assert_path_allowed(rel, grant.read_patterns())
        path = resolve_under_root(root, rel)
        max_bytes = int(arguments.get("max_bytes", 200_000))
        data = path.read_bytes()[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = int(arguments.get("start_line", 1))
        end = int(arguments.get("end_line", len(lines)))
        sliced = lines[max(0, start - 1) : end]
        content = "\n".join(sliced)
        return {
            "path": rel,
            "content": content,
            "start_line": start,
            "end_line": end,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }

    def _search_text(self, arguments: dict[str, Any], grant: CapabilityGrant) -> dict[str, Any]:
        root = self._require_worktree()
        query = arguments["query"]
        path_filter = arguments.get("path_filter", "**/*")
        max_results = int(arguments.get("max_results", 50))
        hits = []
        root_resolved = root.resolve()
        for path in root.glob(path_filter):
            if not path.is_file():
                continue
            rel = str(path.resolve().relative_to(root_resolved))
            from product_factory.tools.policies import path_allowed

            read_patterns = grant.read_patterns()
            if read_patterns and not path_allowed(rel, read_patterns):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    hits.append({"file": rel, "line": i, "text": line[:300]})
                    if len(hits) >= max_results:
                        return {"matches": hits}
        return {"matches": hits}

    def _git_diff(self, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
        root = self._require_worktree()
        base = arguments.get("base_ref") or self.base_commit or "HEAD"
        # Include untracked files in the diff (create_file leaves them untracked).
        subprocess.run(
            ["git", "add", "-N", "--", "."],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        proc = subprocess.run(
            ["git", "diff", base],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        patch = proc.stdout
        art = self.artifact_store.put_text(
            patch,
            media_type="text/x-diff",
            logical_name="worktree.patch",
            created_by_task_id="system",
        )
        return {
            "patch": patch,
            "artifact_sha256": art.sha256,
            "changed_files": [line[6:] for line in patch.splitlines() if line.startswith("+++ b/")],
            "tool_call_id": tool_call_id,
        }

    def _git_status(self) -> dict[str, Any]:
        root = self._require_worktree()
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        entries = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            entries.append({"status": line[:2].strip(), "path": line[3:]})
        return {"entries": entries}

    def _apply_patch(self, arguments: dict[str, Any], grant: CapabilityGrant) -> dict[str, Any]:
        root = self._require_worktree()
        if (root / ".product-factory-readonly").exists():
            raise ToolAuthorizationError("Worktree is read-only")
        patch = arguments["patch"]
        # Basic path extraction for allowlist
        for line in patch.splitlines():
            if line.startswith("+++ b/"):
                rel = line[6:]
                assert_path_allowed(rel, grant.write_patterns())
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=root,
            input=patch,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ToolAuthorizationError(f"apply_patch failed: {proc.stderr.strip()}")
        return {"applied": True}

    def _create_file(self, arguments: dict[str, Any], grant: CapabilityGrant) -> dict[str, Any]:
        root = self._require_worktree()
        if (root / ".product-factory-readonly").exists():
            raise ToolAuthorizationError("Worktree is read-only")
        rel = arguments["path"]
        assert_path_allowed(rel, grant.write_patterns())
        path = resolve_under_root(root, rel)
        if self.original_repo and path.resolve().is_relative_to(self.original_repo):
            # Ensure we are under worktree, not original — resolve_under_root already scopes.
            pass
        overwrite = bool(arguments.get("overwrite", False))
        if path.exists() and not overwrite:
            raise ToolAuthorizationError(f"File already exists: {rel}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return {"path": rel, "bytes": len(arguments["content"].encode())}

    def _write_artifact(
        self, arguments: dict[str, Any], task_id: str, tool_call_id: str
    ) -> dict[str, Any]:
        art = self.artifact_store.put_text(
            arguments["content"],
            media_type=arguments.get("media_type", "text/plain"),
            logical_name=arguments["logical_name"],
            created_by_task_id=task_id,
            trust_level="generated",
        )
        return {"artifact_sha256": art.sha256, "logical_name": art.logical_name}

    @staticmethod
    def normalize_validation_command_id(command_id: str) -> str:
        """Map common model mistakes onto registered policy ids.

        Validator results are labeled ``behavioral:<id>``; models often pass that
        label (or raw ``pytest``) as ``command_id``. Strip the prefix and accept
        a small alias table so repair does not thrash on ToolAuthorizationError.
        """
        raw = (command_id or "").strip()
        if raw.startswith("behavioral:"):
            raw = raw[len("behavioral:") :].strip()
        aliases = {
            "pytest": "python_tests",
            "py.test": "python_tests",
            "tests": "python_tests",
            "typecheck": "python_typecheck",
            "basedpyright": "python_typecheck",
            "pyright": "python_typecheck",
        }
        return aliases.get(raw, raw)

    def _run_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root = self._require_worktree()
        requested = str(arguments.get("command_id") or "")
        command_id = self.normalize_validation_command_id(requested)
        if command_id not in self.registered_commands:
            allowed = ", ".join(sorted(self.registered_commands)) or "(none)"
            raise ToolAuthorizationError(
                f"Unregistered command: {requested!r} (normalized={command_id!r}). "
                f"Use one of the registered ids: [{allowed}]"
            )
        spec = self.registered_commands[command_id]
        executable = spec["executable"]
        args = list(spec.get("args", []))
        timeout = int(spec.get("timeout_seconds", 300))
        if self.ledger is not None:
            # Registered commands run under the run-level command-seconds
            # budget (P1.A), not just their own configured timeout.
            self.ledger.check_before_command(timeout_seconds=timeout)
        # Restricted subprocess sandbox (+ optional bwrap): scrub inherited env,
        # confine cwd to the worktree (P1.D) — never raw subprocess with ambient env.
        result = run_sandboxed_command(
            executable=str(executable),
            args=[str(a) for a in args],
            cwd=root,
            timeout_seconds=timeout,
            pythonpath=str(root / "src"),
        )
        if self.ledger is not None:
            self.ledger.record_command(duration_seconds=result.duration_seconds)
        return {
            "command_id": command_id,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "sandbox": result.sandbox,
        }
