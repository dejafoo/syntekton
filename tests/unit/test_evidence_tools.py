from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.connectors.receipts import persist_source_capture
from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tools import CapabilityGrant
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.policy.source_policy import SourcePolicyProfile
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry


def _broker(tmp_path: Path) -> tuple[ToolBroker, ArtifactStore]:
    store = ArtifactStore(tmp_path / "artifacts")
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=store,
        source_policy=SourcePolicyProfile(
            id="test",
            allowed_source_classes=["standard", "vendor_api"],
        ),
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g-1",
            run_id="run-1",
            task_id="T-001",
            agent_profile="researcher",
            tool_names={"extract_document", "normalize_citation", "compare_options"},
            max_calls=10,
        )
    )
    return broker, store


def _capture(
    store: ArtifactStore,
    body: bytes,
    *,
    media_type: str,
) -> str:
    source, _, _ = persist_source_capture(
        store,
        body,
        url="https://example.com/source",
        media_type=media_type,
        redirect_chain=[],
        created_by_task_id="T-fetch",
        created_by_tool_call_id="tc-fetch",
        retrieved_at="2026-01-02T03:04:05+00:00",
    )
    return source.sha256


def test_extract_document_bounds_html_and_supports_section(tmp_path: Path) -> None:
    broker, store = _broker(tmp_path)
    source_sha = _capture(
        store,
        b"<h1>Intro</h1><p>ignore</p><h2>Details</h2><p>useful evidence</p>"
        b"<h2>Other</h2><p>ignore too</p>",
        media_type="text/html",
    )
    result = broker.execute(
        task_id="T-001",
        tool_name="extract_document",
        arguments={"source_sha256": source_sha, "max_chars": 6, "section": "Details"},
    )
    assert result["text"] == "Detail"
    assert result["truncated"] is True
    assert result["location"] == {"start_char": 0, "end_char": 6}


def test_extract_pdf_without_optional_dependency_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store = _broker(tmp_path)
    source_sha = _capture(store, b"%PDF fixture", media_type="application/pdf")
    monkeypatch.setitem(__import__("sys").modules, "pypdf", None)
    result = broker.execute(
        task_id="T-001",
        tool_name="extract_document",
        arguments={"source_sha256": source_sha, "max_chars": 100},
    )
    assert result["status"] == "unsupported_media_type"


def test_normalize_citation_is_deterministic_and_policy_checked(tmp_path: Path) -> None:
    broker, store = _broker(tmp_path)
    source_sha = _capture(store, b"standard text", media_type="text/plain")
    arguments = {
        "source_sha256": source_sha,
        "source_class": "standard",
        "published_at": "2025-12-01T00:00:00Z",
    }
    first = broker.execute(
        task_id="T-001", tool_name="normalize_citation", arguments=arguments
    )
    second = broker.execute(
        task_id="T-001", tool_name="normalize_citation", arguments=arguments
    )
    assert first["record_sha256"] == second["record_sha256"]

    with pytest.raises(ToolAuthorizationError, match="not allowed"):
        broker.execute(
            task_id="T-001",
            tool_name="normalize_citation",
            arguments={"source_sha256": source_sha, "source_class": "regulator"},
        )


def test_compare_options_writes_explicit_unknown_matrix(tmp_path: Path) -> None:
    broker, store = _broker(tmp_path)
    result = broker.execute(
        task_id="T-001",
        tool_name="compare_options",
        arguments={
            "options": ["Build", "Buy"],
            "criteria": ["Cost", "Risk"],
            "evidence_refs": ["a" * 64],
        },
    )
    matrix = __import__("json").loads(store.get_text(result["artifact_sha256"]))
    assert matrix["schema_id"] == "option_matrix.v1"
    assert len(matrix["cells"]) == 4
    assert {cell["value"] for cell in matrix["cells"]} == {"unknown"}


def test_evidence_tools_use_new_tool_class() -> None:
    registry = default_tool_registry()
    for name in ("extract_document", "normalize_citation", "compare_options"):
        assert registry.get(name).tool_class == "evidence_build"
