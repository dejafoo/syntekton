from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from product_factory.delivery import DeliveryEntry, DeliveryManifest, LandingAdapter, LandingError
from product_factory.delivery.store import _canonical_manifest_bytes


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    return root, head


def _manifest(head: str, content: bytes, *, destination: str = "docs/PLAN.md") -> DeliveryManifest:
    digest = hashlib.sha256(content).hexdigest()
    manifest = DeliveryManifest(
        delivery_id="delivery-1",
        run_id="run-1",
        base_revision=head,
        entries=[
            DeliveryEntry(
                role="architecture_document",
                logical_name="PLAN.md",
                blob_sha256=digest,
                size_bytes=len(content),
                media_type="text/markdown",
                suggested_dest_path=destination,
            )
        ],
    )
    manifest.manifest_sha256 = hashlib.sha256(_canonical_manifest_bytes(manifest)).hexdigest()
    return manifest


def test_landing_verifies_then_writes_under_workspace(tmp_path: Path) -> None:
    root, head = _repo(tmp_path)
    content = b"# Plan\n"
    manifest = _manifest(head, content)

    result = LandingAdapter().land(
        manifest,
        workspace_root=root,
        blob_loader=lambda _: content,
    )

    assert (root / "docs" / "PLAN.md").read_bytes() == content
    assert result.landed_paths == ("docs/PLAN.md",)


@pytest.mark.parametrize("failure", ["missing", "digest", "base", "escape"])
def test_landing_failures_write_nothing(tmp_path: Path, failure: str) -> None:
    root, head = _repo(tmp_path)
    content = b"# Plan\n"
    manifest = _manifest(
        "0" * 40 if failure == "base" else head,
        content,
        destination="../escaped.md" if failure == "escape" else "docs/PLAN.md",
    )

    def load(_: str) -> bytes:
        if failure == "missing":
            raise FileNotFoundError
        return b"tampered" if failure == "digest" else content

    with pytest.raises(LandingError):
        LandingAdapter().land(manifest, workspace_root=root, blob_loader=load)

    assert not (root / "docs" / "PLAN.md").exists()
    assert not (tmp_path / "escaped.md").exists()


def test_landing_rejects_changed_local_head(tmp_path: Path) -> None:
    root, head = _repo(tmp_path)
    manifest = _manifest(head, b"# Plan\n")
    (root / "local.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "local"], cwd=root, check=True)

    with pytest.raises(LandingError, match="Workspace base changed"):
        LandingAdapter().land(
            manifest,
            workspace_root=root,
            blob_loader=lambda _: b"# Plan\n",
        )

    assert not (root / "docs" / "PLAN.md").exists()
