"""Connector disable must preserve durable evidence (PMX / S6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.connectors.broker import (
    EVENT_DENIED,
    EVENT_INVOKED,
    ConnectorBroker,
)
from product_factory.connectors.errors import ConnectorPolicyDenied
from product_factory.connectors.policy import ConnectorsConfig
from product_factory.persistence.database import Database

from .conftest import (
    GIT_CI_ID,
    AuditSink,
    enabled_config,
    git_ci_handler,
    git_ci_manifest,
    registry_with,
)


def test_disabling_connector_preserves_prior_receipts_and_run_rows(
    tmp_path: Path, audit: AuditSink
) -> None:
    registry = registry_with((git_ci_manifest(), git_ci_handler))
    enabled = ConnectorBroker(
        registry,
        config=enabled_config(GIT_CI_ID),
        audit=audit,
        environ={},
        mock=True,
    )
    result = enabled.invoke(
        tool_name="get_commit_checks",
        arguments={"repository": "example/service", "commit_sha": "a" * 40},
        task_id="task-1",
        tool_call_id="call-1",
        run_id="run-preserve",
    )
    assert result["result_sha256"]
    assert audit.types() == [EVENT_INVOKED]
    invoked = audit.of_type(EVENT_INVOKED)[0]
    assert invoked["arguments_hash"]
    assert invoked["result_sha256"] == result["result_sha256"]

    db = Database(tmp_path / "data" / "product_factory.sqlite")
    db.upsert_run(
        run_id="run-preserve",
        workflow_type="release_readiness",
        status="completed",
        request={
            "request_id": "req-preserve",
            "workflow_type": "release_readiness",
            "request_text": "preserve evidence",
        },
    )
    evidence = tmp_path / "evidence" / f"{result['result_sha256']}.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        json.dumps(
            {
                "result_sha256": result["result_sha256"],
                "arguments_hash": invoked["arguments_hash"],
                "connector_id": GIT_CI_ID,
            }
        ),
        encoding="utf-8",
    )

    disabled = ConnectorBroker(
        registry,
        config=ConnectorsConfig(),  # default: nothing enabled
        audit=audit,
        environ={},
        mock=True,
    )
    with pytest.raises(ConnectorPolicyDenied, match="not enabled"):
        disabled.invoke(
            tool_name="get_commit_checks",
            arguments={"repository": "example/service", "commit_sha": "a" * 40},
            task_id="task-2",
            tool_call_id="call-2",
            run_id="run-preserve",
        )
    assert EVENT_DENIED in audit.types()

    row = db.get_run("run-preserve")
    assert row is not None
    assert row["status"] == "completed"
    assert evidence.is_file()
    # Prior success audit remains durable alongside the later denial.
    assert len(audit.of_type(EVENT_INVOKED)) == 1
    db.close()
