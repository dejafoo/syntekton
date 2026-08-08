"""Tool broker — sole execution path for tools."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from product_factory.connectors.broker import ConnectorAudit, ConnectorBroker
from product_factory.connectors.source_ledger import (
    SEARCH_TOOL_NAMES,
    SourceLedger,
    urls_from_provenance,
)
from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tools import CapabilityGrant, ToolCallRecord
from product_factory.observability.contracts import CaptureLevel
from product_factory.observability.recorder import capture_level_from_env
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.policy.source_policy import SourcePolicyProfile
from product_factory.schemas import validate_write_payload
from product_factory.tools import interface_analysis
from product_factory.tools.policies import assert_path_allowed, resolve_under_root
from product_factory.tools.registry import ToolRegistry
from product_factory.tools.sandbox import run_sandboxed_command
from product_factory.validation.evidence import write_validation_evidence

ToolObserver = Callable[[str, dict[str, Any]], None]
ArtifactInstanceRecorder = Callable[[ArtifactInstance], None]


class _DocumentTextParser(HTMLParser):
    def __init__(self, *, section: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.section = section.casefold()
        self.parts: list[str] = []
        self._heading: list[str] | None = None
        self._include = not self.section
        self._suppressed_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._suppressed_depth += 1
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = []
        elif self._include and tag in {"p", "div", "li", "br", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._suppressed_depth = max(0, self._suppressed_depth - 1)
        if self._heading is not None and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading = " ".join(self._heading).strip()
            if self.section:
                if heading.casefold() == self.section:
                    self._include = True
                elif self._include:
                    self._include = False
            if self._include:
                self.parts.extend(["\n", heading, "\n"])
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)
        elif self._include and not self._suppressed_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def _text_section(text: str, section: str) -> str:
    wanted = section.casefold()
    selected: list[str] = []
    active = False
    for line in text.splitlines():
        heading = line.lstrip("#").strip()
        is_heading = line.startswith("#")
        if is_heading and heading.casefold() == wanted:
            active = True
            selected.append(line)
            continue
        if active and is_heading:
            break
        if active:
            selected.append(line)
    return "\n".join(selected)


def _named_item(item: Any, kind: str) -> str:
    if isinstance(item, str):
        value = item.strip()
    elif isinstance(item, dict):
        value = str(item.get("name") or item.get("id") or "").strip()
    else:
        value = ""
    if not value:
        raise ToolAuthorizationError(f"Each {kind} must have a non-empty name")
    return value


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
        connectors: ConnectorBroker | None = None,
        connector_audit: ConnectorAudit | None = None,
        source_ledger: SourceLedger | None = None,
        source_policy: SourcePolicyProfile | None = None,
        connector_approval_verified: bool = False,
        run_id: str = "",
        validation_baselines: dict[str, str] | None = None,
        capture_level: CaptureLevel | str | None = None,
        on_artifact_instance: ArtifactInstanceRecorder | None = None,
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
        self.connectors = connectors
        self.connector_audit = connector_audit
        # Search results are what make a URL retrievable later (PM1.B1); with no
        # ledger bound, nothing is recorded and every gated fetch stays denied.
        self.source_ledger = source_ledger
        self.source_policy = source_policy
        self.connector_approval_verified = connector_approval_verified
        self.run_id = run_id
        self.validation_baselines = validation_baselines or {}
        if isinstance(capture_level, CaptureLevel):
            self.capture_level = capture_level
        elif capture_level is not None:
            try:
                self.capture_level = CaptureLevel(str(capture_level))
            except ValueError:
                self.capture_level = capture_level_from_env()
        else:
            self.capture_level = capture_level_from_env()
        self.on_artifact_instance = on_artifact_instance

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
        if tool_name == "extract_document":
            return self._extract_document(arguments)
        if tool_name == "normalize_citation":
            return self._normalize_citation(arguments, grant.task_id, tool_call_id)
        if tool_name == "compare_options":
            return self._compare_options(arguments, grant.task_id, tool_call_id)
        if tool_name == "parse_contract":
            assert_path_allowed(str(arguments["path"]), grant.read_patterns())
            return interface_analysis.parse_contract(
                self._require_worktree(), str(arguments["path"])
            )
        if tool_name == "contract_inventory":
            assert_path_allowed(str(arguments["path"]), grant.read_patterns())
            return interface_analysis.contract_inventory(
                self._require_worktree(), str(arguments["path"])
            )
        if tool_name == "diff_contracts":
            assert_path_allowed(str(arguments["baseline_path"]), grant.read_patterns())
            assert_path_allowed(str(arguments["candidate_path"]), grant.read_patterns())
            return interface_analysis.diff_contracts(
                self._require_worktree(),
                str(arguments["baseline_path"]),
                str(arguments["candidate_path"]),
            )
        if tool_name == "map_capabilities":
            assert_path_allowed(str(arguments["path"]), grant.read_patterns())
            return interface_analysis.map_capabilities(
                self._require_worktree(), str(arguments["path"])
            )
        if tool_name == "generate_synthetic_fixture":
            assert_path_allowed(str(arguments["contract_path"]), grant.read_patterns())
            assert_path_allowed(str(arguments["output_path"]), grant.write_patterns())
            return interface_analysis.generate_synthetic_fixture(
                self._require_worktree(),
                str(arguments["contract_path"]),
                str(arguments["output_path"]),
                arguments.get("schema_name"),
            )
        if tool_name == "run_contract_simulation":
            assert_path_allowed(str(arguments["contract_path"]), grant.read_patterns())
            assert_path_allowed(str(arguments["fixture_path"]), grant.read_patterns())
            return interface_analysis.run_contract_simulation(
                self._require_worktree(),
                str(arguments["contract_path"]),
                str(arguments["fixture_path"]),
                arguments.get("schema_name"),
            )
        if tool_name == "run_validation_command":
            return self._run_command(
                arguments,
                task_id=grant.task_id,
                tool_call_id=tool_call_id,
            )
        if self.connectors is not None and self.connectors.handles(tool_name):
            # The grant, max_calls, and budget checks in `execute` have already
            # passed; connector policy is the additional gate for third parties.
            return self._invoke_connector(
                tool_name=tool_name,
                arguments=arguments,
                task_id=grant.task_id,
                tool_call_id=tool_call_id,
                run_id=self.run_id or grant.run_id,
            )
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

    def _capture_metadata(self, source_sha256: str) -> dict[str, Any]:
        path = self.artifact_store.root / "source-captures" / f"{source_sha256}.json"
        if not path.is_file() or not self.artifact_store.exists(source_sha256):
            raise ToolAuthorizationError(f"Unknown source capture: {source_sha256}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("sha256") != source_sha256:
            raise ToolAuthorizationError("Source capture index does not match requested digest")
        return payload

    def _extract_document(self, arguments: dict[str, Any]) -> dict[str, Any]:
        source_sha256 = str(arguments["source_sha256"])
        capture = self._capture_metadata(source_sha256)
        media_type = str(capture.get("media_type") or "")
        body = self.artifact_store.get_bytes(source_sha256)
        max_chars = max(1, min(int(arguments.get("max_chars", 20_000)), 200_000))
        section = str(arguments.get("section") or "").strip()

        if media_type == "application/pdf":
            try:
                from pypdf import PdfReader  # type: ignore[import-not-found]
            except ImportError:
                return {
                    "status": "unsupported_media_type",
                    "media_type": media_type,
                    "source_sha256": source_sha256,
                }
            text = "\n\n".join(
                page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages
            )
        elif media_type == "text/html":
            parser = _DocumentTextParser(section=section)
            parser.feed(body.decode("utf-8", errors="replace"))
            text = parser.text()
        elif media_type in {
            "text/plain",
            "text/markdown",
            "application/json",
            "application/yaml",
        }:
            text = body.decode("utf-8", errors="replace")
            if section:
                text = _text_section(text, section)
        else:
            return {
                "status": "unsupported_media_type",
                "media_type": media_type,
                "source_sha256": source_sha256,
            }

        bounded = text[:max_chars]
        return {
            "status": "ok",
            "source_sha256": source_sha256,
            "media_type": media_type,
            "section": section or None,
            "text": bounded,
            "location": {"start_char": 0, "end_char": len(bounded)},
            "truncated": len(text) > len(bounded),
        }

    def _normalize_citation(
        self,
        arguments: dict[str, Any],
        task_id: str,
        tool_call_id: str,
    ) -> dict[str, Any]:
        from product_factory.connectors.receipts import build_source_record, persist_source_records

        source_sha256 = str(arguments["source_sha256"])
        source_class = str(arguments["source_class"])
        if self.source_policy is None:
            raise ToolAuthorizationError("No active source policy for normalize_citation")
        if not self.source_policy.allows_source_class(source_class):
            raise ToolAuthorizationError(
                f"Source class {source_class!r} is not allowed by policy {self.source_policy.id!r}"
            )
        capture = self._capture_metadata(source_sha256)
        published_at = str(arguments.get("published_at") or "").strip() or None
        if published_at is not None:
            try:
                datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ToolAuthorizationError("published_at must be an ISO-8601 timestamp") from exc
        record = build_source_record(
            source=str(capture["url"]),
            source_type=source_class,
            source_class=source_class,
            sha256=source_sha256,
            connector_id="source_fetch",
            tool_call_id=str(capture.get("tool_call_id") or ""),
            retrieved_at=str(capture["retrieved_at"]),
            published_at=published_at,
            freshness="published" if published_at else "retrieved",
        )
        ref = persist_source_records(
            self.artifact_store,
            [record],
            created_by_task_id=task_id,
            created_by_tool_call_id=tool_call_id,
        )[0]
        return {"source_sha256": source_sha256, "record_sha256": ref.sha256}

    def _compare_options(
        self,
        arguments: dict[str, Any],
        task_id: str,
        tool_call_id: str,
    ) -> dict[str, Any]:
        options = [_named_item(item, "option") for item in arguments.get("options") or []]
        criteria = [_named_item(item, "criterion") for item in arguments.get("criteria") or []]
        if not options or not criteria:
            raise ToolAuthorizationError("compare_options requires non-empty options and criteria")
        evidence_refs = [str(ref) for ref in arguments.get("evidence_refs") or []]
        cells = [
            {
                "option": option,
                "criterion": criterion,
                "value": "unknown",
                "evidence_refs": [],
            }
            for option in options
            for criterion in criteria
        ]
        matrix = {
            "schema_id": "option_matrix.v1",
            "options": options,
            "criteria": criteria,
            "cells": cells,
            "evidence_refs": evidence_refs,
        }
        validate_write_payload("option_matrix.v1", matrix)
        ref = self.artifact_store.put_json(
            matrix,
            logical_name="option-matrix.json",
            created_by_task_id=task_id,
            created_by_tool_call_id=tool_call_id,
            schema_id="option_matrix.v1",
            schema_version="1",
            trust_level="generated",
        )
        return {"artifact_sha256": ref.sha256, "schema_id": "option_matrix.v1"}

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

    def _invoke_connector(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        task_id: str,
        tool_call_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        assert self.connectors is not None
        from product_factory.connectors.receipts import (
            build_connector_receipt,
            build_source_record,
            persist_connector_receipt,
            persist_source_capture,
            persist_source_records,
        )
        from product_factory.policy.classification import (
            assert_ingress_allowed,
            classify_payload,
        )

        result = self.connectors.invoke(
            tool_name=tool_name,
            arguments=arguments,
            task_id=task_id,
            tool_call_id=tool_call_id,
            run_id=run_id,
            invocation_options={
                "source_ledger": self.source_ledger,
            },
            audit=self.connector_audit,
            approved=self.connector_approval_verified,
        )
        handler_metadata = result.pop("_handler_metadata", {})
        if tool_name == "fetch_source":
            capture_data = handler_metadata.get("source_capture")
            if not isinstance(capture_data, dict) or not isinstance(
                capture_data.get("body"), bytes
            ):
                raise ToolAuthorizationError("fetch_source returned no persistable source capture")
            body = capture_data["body"]
            # URL-policy media/size checks happened inside the connector. The
            # PM0 ingress guard is the final gate before these bytes are stored.
            assert_ingress_allowed(
                body.decode("utf-8", errors="replace"),
                source="connector:source_fetch",
            )
            decision = classify_payload(body.decode("utf-8", errors="replace"), fail_closed=True)
            source_ref, _capture_ref, capture = persist_source_capture(
                self.artifact_store,
                body,
                url=str(capture_data.get("url") or ""),
                media_type=str(capture_data.get("media_type") or ""),
                redirect_chain=list(capture_data.get("redirect_chain") or []),
                created_by_task_id=task_id,
                created_by_tool_call_id=tool_call_id,
            )
            record = build_source_record(
                source=str(capture["url"]),
                source_type="url",
                sha256=source_ref.sha256,
                trust_label="untrusted",
                connector_id="source_fetch",
                tool_call_id=tool_call_id,
                retrieved_at=str(capture["retrieved_at"]),
            )
            record_ref = persist_source_records(
                self.artifact_store,
                [record],
                created_by_task_id=task_id,
                created_by_tool_call_id=tool_call_id,
            )[0]
            result["result"] = {
                "source_sha256": source_ref.sha256,
                "record_sha256": record_ref.sha256,
                "media_type": capture["media_type"],
                "bytes": capture["bytes"],
                "redirect_chain": capture["redirect_chain"],
            }
            result["result_sha256"] = hashlib.sha256(
                json.dumps(result["result"], sort_keys=True).encode()
            ).hexdigest()
        else:
            # Fail closed on known secret material before the model sees / stores it.
            assert_ingress_allowed(
                json.dumps(result.get("result"), default=str),
                source=f"connector:{result.get('connector_id', tool_name)}",
            )
            decision = classify_payload(result.get("result"), fail_closed=True)
        receipt = build_connector_receipt(
            connector_id=str(result.get("connector_id") or ""),
            tool_name=tool_name,
            result_sha256=str(result.get("result_sha256") or ""),
            tool_call_id=tool_call_id,
            task_id=task_id,
            run_id=run_id,
            provenance=list(result.get("provenance") or []),
            trust_label=str(result.get("trust_label") or "untrusted"),
            truncated=bool(result.get("truncated")),
        )
        receipt_ref = persist_connector_receipt(
            self.artifact_store,
            receipt,
            created_by_task_id=task_id,
            created_by_tool_call_id=tool_call_id,
        )
        source_refs = []
        source_records = []
        for item in () if tool_name == "fetch_source" else result.get("provenance") or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "")
            if not source:
                continue
            record = build_source_record(
                source=source,
                source_type=str(item.get("kind") or "url"),
                sha256=str(item.get("sha256") or result.get("result_sha256") or ""),
                trust_label=str(result.get("trust_label") or "untrusted"),
                connector_id=str(result.get("connector_id") or ""),
                tool_call_id=tool_call_id,
            )
            source_records.append(record)
        if source_records:
            source_refs = persist_source_records(
                self.artifact_store,
                source_records,
                created_by_task_id=task_id,
                created_by_tool_call_id=tool_call_id,
            )
        result = {
            **result,
            "receipt_sha256": receipt_ref.sha256,
            "source_record_sha256s": [ref.sha256 for ref in source_refs],
            "classification": decision.as_payload(),
        }
        if self.source_ledger is not None and tool_name in SEARCH_TOOL_NAMES:
            admitted = self.source_ledger.record_search_results(
                urls_from_provenance(result.get("provenance") or []),
                task_id=task_id,
                tool_call_id=tool_call_id,
            )
            if admitted:
                result["source_ledger_urls"] = list(admitted)
        if self.observer is not None:
            self.observer(
                "connector_receipt",
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "task_id": task_id,
                    "receipt_sha256": receipt_ref.sha256,
                    "classification": decision.as_payload(),
                },
            )
        return result

    def _run_command(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str,
        tool_call_id: str,
    ) -> dict[str, Any]:
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
        evidence = write_validation_evidence(
            artifact_store=self.artifact_store,
            command_id=command_id,
            registered_command_ids=set(self.registered_commands),
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            input_revision=self.base_commit or "worktree",
            created_by_task_id=task_id,
            created_by_tool_call_id=tool_call_id,
            sandbox=result.sandbox,
            duration_seconds=result.duration_seconds,
            previous_evidence_ref=self.validation_baselines.get(command_id),
            run_id=self.run_id,
            capture_level=self.capture_level,
            on_instance=self.on_artifact_instance,
        )
        return {
            "command_id": command_id,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "sandbox": result.sandbox,
            "validation_evidence_ref": evidence.artifact_ref.sha256,
            "validation_raw_ref": evidence.raw_ref.sha256,
            "normalized_outcomes": evidence.payload["normalized_outcomes"],
            "baseline_comparison": evidence.payload["baseline_comparison"],
        }
