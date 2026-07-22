"""Tool broker security tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tools import CapabilityGrant
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.tools.broker import ToolBroker
from product_factory.tools.policies import resolve_under_root
from product_factory.tools.registry import default_tool_registry


@pytest.fixture
def broker(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "ok.txt").write_text("hello", encoding="utf-8")
    store = ArtifactStore(tmp_path / "artifacts")
    b = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=store,
        worktree_root=wt,
        registered_commands={
            "echo_ok": {"executable": "echo", "args": ["ok"], "timeout_seconds": 5}
        },
    )
    b.set_grant(
        CapabilityGrant(
            grant_id="g1",
            run_id="r1",
            task_id="t1",
            agent_profile="implementation_worker",
            tool_names={
                "list_files",
                "read_file",
                "create_file",
                "run_validation_command",
                "write_artifact",
            },
            allowed_path_patterns=["**/*"],
            max_calls=50,
        )
    )
    return b, wt


def test_path_traversal_rejected(broker) -> None:
    b, wt = broker
    with pytest.raises(ToolAuthorizationError):
        resolve_under_root(wt, "../secret.txt")


def test_unregistered_command_rejected(broker) -> None:
    b, _ = broker
    with pytest.raises(ToolAuthorizationError):
        b.execute(
            task_id="t1",
            tool_name="run_validation_command",
            arguments={"command_id": "rm_rf"},
        )


def test_unregistered_tool_rejected(broker) -> None:
    b, _ = broker
    with pytest.raises(ToolAuthorizationError):
        b.execute(task_id="t1", tool_name="shell", arguments={})


def test_create_and_read_file(broker) -> None:
    b, wt = broker
    b.execute(
        task_id="t1",
        tool_name="create_file",
        arguments={"path": "src/x.py", "content": "x=1\n"},
    )
    assert (wt / "src" / "x.py").exists()
    out = b.execute(task_id="t1", tool_name="read_file", arguments={"path": "src/x.py"})
    assert "x=1" in out["content"]


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = wt / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(ToolAuthorizationError):
        resolve_under_root(wt, "link.txt")
