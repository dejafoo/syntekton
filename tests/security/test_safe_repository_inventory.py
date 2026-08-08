"""SD0.E SafeRepositoryInventory security tests."""

from __future__ import annotations

import os
from pathlib import Path

from product_factory.context.assembler import list_repository_paths, select_repository_excerpts
from product_factory.context.safe_inventory import (
    InventoryPolicy,
    build_safe_repository_inventory,
)
from product_factory.repository.stack_profile import discover_stack_profile


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    return root


def test_symlink_file_is_excluded(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("SECRET", encoding="utf-8")
    (root / "link.txt").symlink_to(secret)
    inventory = build_safe_repository_inventory(root, policy=InventoryPolicy(admit_untracked=True))
    assert not inventory.contains("link.txt")
    assert any(item.reason == "symlink" for item in inventory.exclusions)
    paths, omitted = list_repository_paths(root)
    assert all(item["path"] != "link.txt" for item in paths)
    assert any("SafeRepositoryInventory" in item for item in omitted)


def test_symlink_escape_cannot_enter_excerpts(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "leak.py"
    outside.write_text("LEAKED = 1\n", encoding="utf-8")
    (root / "src" / "leak.py").symlink_to(outside)
    excerpts, _ = select_repository_excerpts(root, objective="leak main")
    assert all("LEAKED" not in item["content"] for item in excerpts)


def test_prohibited_and_binary_excluded(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\0\1\2\3" + os.urandom(32))
    inventory = build_safe_repository_inventory(root)
    assert not inventory.contains(".env")
    assert not any(e.relative_path.startswith("node_modules/") for e in inventory.entries)
    assert not inventory.contains("blob.bin")


def test_file_ceiling_truncates(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for i in range(10):
        (root / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
    inventory = build_safe_repository_inventory(
        root,
        policy=InventoryPolicy(max_files=3, admit_untracked=True),
    )
    assert inventory.truncated
    assert len(inventory.entries) == 3
    evidence = inventory.manifest_evidence()
    assert evidence["truncated"] is True


def test_stack_profile_ignores_symlinked_manifest(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "evil.toml"
    outside.write_text(
        '[project]\nname = "evil"\nrequires-python = ">=3.13"\ndependencies = ["django"]\n',
        encoding="utf-8",
    )
    (root / "pyproject.toml").symlink_to(outside)
    profile = discover_stack_profile(root)
    assert "pyproject.toml" not in profile.source_files
    assert profile.status in {"unknown", "limited"}
