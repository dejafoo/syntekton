"""Contracts for the shared hermetic PM5 connector fakes."""

from __future__ import annotations

from product_factory.connectors.broker import ConnectorBroker


def test_fake_git_ci_is_pinned_and_read_only(fake_git_ci: ConnectorBroker) -> None:
    assert fake_git_ci.grantable_tool_names({"ci_read"}) == {
        "get_commit_checks",
        "get_build_artifacts",
    }
    result = fake_git_ci.invoke(
        tool_name="get_commit_checks",
        arguments={
            "repository": "example/service",
            "commit_sha": "a" * 40,
        },
        task_id="T-CI",
        tool_call_id="call-ci",
    )
    assert result["result"]["commit_sha"] == "a" * 40
    assert result["result"]["checks"][0]["conclusion"] == "success"


def test_fake_ops_read_preserves_bounded_window(fake_ops_read: ConnectorBroker) -> None:
    result = fake_ops_read.invoke(
        tool_name="query_service_signals",
        arguments={
            "service_id": "checkout",
            "environment": "staging",
            "start": "2026-08-01T10:00:00Z",
            "end": "2026-08-01T10:15:00Z",
        },
        task_id="T-OPS",
        tool_call_id="call-ops",
    )
    assert result["result"]["time_window"] == {
        "start": "2026-08-01T10:00:00Z",
        "end": "2026-08-01T10:15:00Z",
    }


def test_fake_deploy_is_only_grantable_by_deployment_class(
    fake_deploy: ConnectorBroker,
) -> None:
    assert fake_deploy.grantable_tool_names({"ci_read", "ops_read"}) == frozenset()
    assert "start_deployment" in fake_deploy.grantable_tool_names({"deployment_write"})
    result = fake_deploy.invoke(
        tool_name="start_deployment",
        arguments={"target_id": "staging-us", "idempotency_key": "release-1"},
        task_id="T-DEPLOY",
        tool_call_id="call-deploy",
    )
    assert result["result"]["deployment_id"] == "dep-release-1"
    assert result["result"]["idempotency_key"] == "release-1"
