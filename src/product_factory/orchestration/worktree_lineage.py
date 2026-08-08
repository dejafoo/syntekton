"""WorktreeLineageService — patch inheritance and worktree lineage (SD2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from product_factory.domain.errors import ValidationFailureError
from product_factory.orchestration.repair import patch_fingerprint
from product_factory.repositories.patches import (
    apply_patch,
    apply_patch_check,
    changed_paths_from_patch,
    create_patch,
    detect_writer_conflicts,
)
from product_factory.repositories.worktrees import WorktreeManager


class WorktreeLineageService:
    """Owns worktree create/get, inherited patches, conflicts, and lineage JSON."""

    def prepare_task_worktree(
        self,
        *,
        worktrees: WorktreeManager,
        artifacts: Any,
        run_dir: Path,
        task_id: str,
        capability: str,
        dependencies: list[str],
        dependency_outputs: list[dict[str, Any]],
        base_commit: str,
        writable: bool,
    ) -> tuple[Path, list[str], list[dict[str, str]], str | None]:
        """Create/get worktree, inherit predecessor patches, persist lineage.

        Returns (worktree_path, inherited_artifact_sha256, conflicts, pre_patch_fp).
        """
        inherited_artifacts: list[str] = []
        lineage_conflicts: list[dict[str, str]] = []
        pre_patch_fingerprint: str | None = None

        try:
            wt = worktrees.get(task_id)
        except KeyError:
            wt = worktrees.create(task_id, base_commit=base_commit, writable=writable)
        wt_path = wt.path

        if capability in {
            "implementation",
            "repair",
            "composition",
            "independent_review",
        }:
            superseded = {
                predecessor
                for dependency in dependency_outputs or []
                for predecessor in dependency.get("dependencies", [])
            }
            owned_paths: dict[str, str] = {}
            for dependency in dependency_outputs or []:
                if dependency.get("task_id") in superseded:
                    continue
                for ref in dependency.get("artifact_refs", []):
                    if ref.get("media_type") != "text/x-diff":
                        continue
                    sha256 = str(ref.get("sha256", ""))
                    if not sha256 or sha256 in inherited_artifacts:
                        continue
                    predecessor_patch = artifacts.get_text(sha256)
                    writer_id = str(dependency.get("task_id") or "unknown")
                    conflicts = detect_writer_conflicts(
                        owned_paths,
                        changed_paths_from_patch(predecessor_patch),
                        writer_id,
                    )
                    if conflicts:
                        lineage_conflicts.extend(conflicts)
                        continue
                    if not apply_patch_check(wt_path, predecessor_patch):
                        lineage_conflicts.append(
                            {
                                "path": "(apply)",
                                "owner_task_id": writer_id,
                                "conflicting_task_id": task_id,
                                "reason": "patch_apply_conflict",
                            }
                        )
                        continue
                    apply_patch(wt_path, predecessor_patch)
                    inherited_artifacts.append(sha256)
            if inherited_artifacts:
                try:
                    current = create_patch(wt_path, base_commit)
                    pre_patch_fingerprint = (
                        patch_fingerprint(current) if current.strip() else None
                    )
                except ValidationFailureError:
                    pre_patch_fingerprint = None
            (run_dir / "output" / f"{task_id}-lineage.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "base_commit": base_commit,
                        "dependencies": dependencies,
                        "inherited_artifact_sha256": inherited_artifacts,
                        "pre_patch_fingerprint": pre_patch_fingerprint,
                        "conflicts": lineage_conflicts,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        return wt_path, inherited_artifacts, lineage_conflicts, pre_patch_fingerprint

    def detect_conflicts(
        self,
        *,
        planned_writes: dict[str, set[str]],
    ) -> list[tuple[str, str, set[str]]]:
        return detect_writer_conflicts(planned_writes)
