"""Bounded Git/CI read connector keyed only by immutable commit digests."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from product_factory.connectors.errors import ConnectorPolicyDenied, ConnectorUnavailable
from product_factory.connectors.manifest import ConnectorManifest, ConnectorToolSpec, EgressPolicy
from product_factory.connectors.registry import ConnectorInvocation
from product_factory.connectors.result import ConnectorResult, Provenance, sha256_of

CONNECTOR_ID = "git_ci_read"
TOOL_CLASS = "ci_read"
TOOL_GET_CHECKS = "get_commit_checks"
TOOL_GET_ARTIFACTS = "get_build_artifacts"
API_HOST = "api.github.com"

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


def _input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["repository", "commit_sha"],
        "properties": {
            "repository": {"type": "string", "pattern": r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"},
            "commit_sha": {"type": "string", "pattern": r"^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$"},
        },
        "additionalProperties": False,
    }


def git_ci_manifest() -> ConnectorManifest:
    return ConnectorManifest(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        provider="github-actions",
        tool_class=TOOL_CLASS,
        permissions=frozenset({"read"}),
        tools=(
            ConnectorToolSpec(
                name=TOOL_GET_CHECKS,
                description="Read check runs for an immutable repository commit SHA",
                input_schema=_input_schema(),
                risk_class="R2",
            ),
            ConnectorToolSpec(
                name=TOOL_GET_ARTIFACTS,
                description="Read build artifact metadata for an immutable repository commit SHA",
                input_schema=_input_schema(),
                risk_class="R2",
            ),
        ),
        egress=EgressPolicy(mode="domains", allowed_domains=(API_HOST,)),
        auth_env_var="GITHUB_TOKEN",
        timeout_seconds=20,
        max_concurrency=2,
        result_retention="full",
        max_result_bytes=64_000,
        description="Read-only GitHub-Actions-shaped checks and artifacts",
    )


def _scope(invocation: ConnectorInvocation) -> tuple[str, str]:
    repository = str(invocation.arguments.get("repository") or "").strip()
    commit_sha = str(invocation.arguments.get("commit_sha") or "").strip().lower()
    if not _SHA_RE.fullmatch(commit_sha):
        raise ConnectorPolicyDenied(
            "Git/CI reads require a full immutable commit SHA",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
            details={"commit_sha": commit_sha},
        )
    allowed = {
        str(item).strip().lower()
        for item in invocation.options.get("allowed_repositories", ())
        if str(item).strip()
    }
    if allowed and repository.lower() not in allowed:
        raise ConnectorPolicyDenied(
            f"Repository {repository!r} is outside the Git/CI read scope",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
            details={"allowed_repositories": sorted(allowed)},
        )
    return repository, commit_sha


def _mock_result(invocation: ConnectorInvocation, repository: str, sha: str) -> ConnectorResult:
    observed_at = "2026-01-01T00:00:00+00:00"
    if invocation.tool_name == TOOL_GET_CHECKS:
        payload: dict[str, Any] = {
            "repository": repository,
            "commit_sha": sha,
            "observed_at": observed_at,
            "checks": [
                {
                    "name": "unit-and-contract",
                    "status": "completed",
                    "conclusion": "success",
                    "details_url": f"https://github.com/{repository}/actions",
                }
            ],
        }
    else:
        payload = {
            "repository": repository,
            "commit_sha": sha,
            "observed_at": observed_at,
            "artifacts": [
                {
                    "name": "service-image",
                    "sha256": sha256_of({"repository": repository, "commit_sha": sha}),
                    "expired": False,
                }
            ],
        }
    return ConnectorResult(
        payload=payload,
        provenance=(Provenance(source=f"fixture://git-ci/{repository}/{sha}", kind="fixture"),),
        metadata={"mock": True, "immutable_revision": sha},
    )


def git_ci_read(invocation: ConnectorInvocation) -> ConnectorResult:
    repository, sha = _scope(invocation)
    if invocation.mock:
        return _mock_result(invocation, repository, sha)

    invocation.assert_egress_allowed(f"https://{API_HOST}")
    backend = invocation.options.get("backend")
    if backend is None:
        raise ConnectorUnavailable(
            "git_ci_read has no configured backend",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
        )
    try:
        raw = backend(
            tool_name=invocation.tool_name,
            repository=repository,
            commit_sha=sha,
            token=invocation.secret,
            timeout_seconds=invocation.timeout_seconds,
        )
    except ConnectorUnavailable:
        raise
    except Exception as exc:
        raise ConnectorUnavailable(
            f"Git/CI backend failed: {type(exc).__name__}",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
            details={"error_type": type(exc).__name__},
        ) from exc
    if not isinstance(raw, dict):
        raise ConnectorUnavailable(
            "Git/CI backend returned a non-object",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
        )
    payload = dict(raw)
    # Provider data cannot change the pinned scope.
    payload["repository"] = repository
    payload["commit_sha"] = sha
    payload.setdefault("observed_at", datetime.now(UTC).isoformat())
    return ConnectorResult(
        payload=payload,
        provenance=(Provenance(source=f"github://{repository}/{sha}", kind="ci"),),
        metadata={"immutable_revision": sha},
    )


__all__ = [
    "API_HOST",
    "CONNECTOR_ID",
    "TOOL_CLASS",
    "TOOL_GET_ARTIFACTS",
    "TOOL_GET_CHECKS",
    "git_ci_manifest",
    "git_ci_read",
]
