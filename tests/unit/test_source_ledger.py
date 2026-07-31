"""Source ledger gating tests (PM1.B1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.errors import ConnectorPolicyDenied
from product_factory.connectors.manifest import (
    ConnectorManifest,
    ConnectorToolSpec,
    EgressPolicy,
)
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.connectors.registry import ConnectorInvocation, ConnectorRegistry
from product_factory.connectors.result import ConnectorResult, Provenance
from product_factory.connectors.source_ledger import (
    LEDGER_FILENAME,
    ORIGIN_SEARCH,
    ORIGIN_SEED,
    SourceLedger,
    SourceNotInLedger,
    canonical_url,
    urls_from_provenance,
)
from product_factory.domain.tools import CapabilityGrant
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.policy.source_policy import SourcePolicyProfile
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry


def ledger(tmp_path: Path) -> SourceLedger:
    return SourceLedger.for_run(tmp_path / "runs" / "run-1")


def test_ledger_lives_under_the_run_content_directory(tmp_path: Path) -> None:
    assert ledger(tmp_path).path == tmp_path / "runs" / "run-1" / "content" / LEDGER_FILENAME


def test_empty_ledger_denies_every_url(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    assert not gate.is_allowed("https://docs.example.com/guide")
    with pytest.raises(SourceNotInLedger) as excinfo:
        gate.assert_allowed("https://docs.example.com/guide")
    assert excinfo.value.denial_code == "source_not_in_ledger"
    assert isinstance(excinfo.value, ConnectorPolicyDenied)


def test_search_results_admit_their_urls(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    added = gate.record_search_results(
        ["https://docs.example.com/guide", "https://cdn.example.org/spec.pdf"],
        task_id="T-001",
        tool_call_id="tc-1",
    )
    assert len(added) == 2
    entry = gate.assert_allowed("https://docs.example.com/guide")
    assert (entry.origin, entry.task_id, entry.tool_call_id) == (ORIGIN_SEARCH, "T-001", "tc-1")
    assert entry.host == "docs.example.com"
    # A sibling URL on an admitted host is still not admitted.
    with pytest.raises(SourceNotInLedger):
        gate.assert_allowed("https://docs.example.com/other")


def test_admission_is_per_document_not_per_host(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    gate.record_search_results(["https://docs.example.com/guide"])
    assert not gate.is_allowed("https://docs.example.com/guide/../../admin")
    assert not gate.is_allowed("https://evil.test/guide")


def test_canonicalization_ignores_presentation_but_not_the_query() -> None:
    assert canonical_url("HTTPS://Docs.Example.COM:443/guide#section") == (
        "https://docs.example.com/guide"
    )
    assert canonical_url("https://docs.example.com") == "https://docs.example.com/"
    assert canonical_url("https://docs.example.com/g?v=2") == "https://docs.example.com/g?v=2"
    assert canonical_url("https://docs.example.com/g") != "https://docs.example.com/g?v=2"
    assert canonical_url("  ") == ""
    assert canonical_url("not a url") == ""


def test_matching_tolerates_presentation_differences(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    gate.record_search_results(["https://Docs.Example.com/guide"])
    assert gate.is_allowed("https://docs.example.com:443/guide#anchor")


def test_non_https_and_junk_urls_are_never_admitted(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    added = gate.record_search_results(
        ["http://docs.example.com/guide", "file:///etc/passwd", "", "not a url"]
    )
    assert added == ()
    assert len(gate) == 0
    assert not gate.is_allowed("http://docs.example.com/guide")


def test_re_recording_keeps_the_first_admission(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    gate.record_search_results(["https://docs.example.com/guide"], tool_call_id="tc-1")
    assert gate.record_search_results(["https://docs.example.com/guide"], tool_call_id="tc-2") == ()
    assert gate.assert_allowed("https://docs.example.com/guide").tool_call_id == "tc-1"


def test_seed_urls_need_a_policy_that_allows_the_domain(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    policy = SourcePolicyProfile(id="test", allowed_domains=["example.com"])
    added = gate.record_seed_urls(
        ["https://docs.example.com/seed", "https://elsewhere.test/seed"],
        policy=policy,
        task_id="T-001",
    )
    assert added == ("https://docs.example.com/seed",)
    assert gate.assert_allowed("https://docs.example.com/seed").origin == ORIGIN_SEED
    with pytest.raises(SourceNotInLedger):
        gate.assert_allowed("https://elsewhere.test/seed")


def test_seed_urls_are_refused_when_no_source_policy_is_resolved(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    assert gate.record_seed_urls(["https://docs.example.com/seed"], policy=None) == ()
    assert len(gate) == 0


def test_denied_domains_win_over_the_seed_list(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    policy = SourcePolicyProfile(id="test", denied_domains=["blocked.example.com"])
    added = gate.record_seed_urls(
        ["https://blocked.example.com/seed", "https://ok.example.com/seed"],
        policy=policy,
    )
    assert added == ("https://ok.example.com/seed",)


def test_ledger_survives_a_new_process(tmp_path: Path) -> None:
    ledger(tmp_path).record_search_results(["https://docs.example.com/guide"])
    reopened = ledger(tmp_path)
    assert reopened.urls() == ("https://docs.example.com/guide",)
    payload = json.loads(reopened.path.read_text(encoding="utf-8"))
    assert payload["schema_id"] == "source_ledger.v1"
    assert payload["entries"][0]["origin"] == ORIGIN_SEARCH


def test_a_second_ledger_handle_sees_concurrent_admissions(tmp_path: Path) -> None:
    first = ledger(tmp_path)
    second = ledger(tmp_path)
    first.record_search_results(["https://docs.example.com/guide"])
    second.record_search_results(["https://cdn.example.org/spec.pdf"])
    assert set(first.urls()) == {
        "https://docs.example.com/guide",
        "https://cdn.example.org/spec.pdf",
    }


def test_a_corrupt_ledger_denies_rather_than_crashes(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    gate.path.parent.mkdir(parents=True, exist_ok=True)
    gate.path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SourceNotInLedger):
        gate.assert_allowed("https://docs.example.com/guide")


def _search_broker(tmp_path: Path, *, gate: SourceLedger | None) -> ToolBroker:
    """A `ToolBroker` whose only connector tool is a fixture `web_search`."""
    manifest = ConnectorManifest(
        connector_id="fake_search",
        version="1.0.0",
        provider="fake",
        tool_class="web_read",
        tools=(ConnectorToolSpec(name="web_search", description="Search a fake index"),),
        egress=EgressPolicy(mode="domains", allowed_domains=("example.com", "example.org")),
    )

    def handler(invocation: ConnectorInvocation) -> ConnectorResult:
        urls = ["https://docs.example.com/guide", "https://cdn.example.org/spec.pdf"]
        return ConnectorResult(
            payload={"results": [{"url": url} for url in urls]},
            provenance=tuple(Provenance(source=url, kind="url") for url in urls),
        )

    connector_registry = ConnectorRegistry()
    connector_registry.register(manifest, handler)
    tools = default_tool_registry()
    for definition in connector_registry.tool_definitions():
        tools.register(definition)

    broker = ToolBroker(
        registry=tools,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        connectors=ConnectorBroker(
            connector_registry,
            config=ConnectorsConfig(connectors={"fake_search": ConnectorSettings(enabled=True)}),
            environ={},
        ),
        source_ledger=gate,
        run_id="run-1",
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g-1",
            run_id="run-1",
            task_id="T-001",
            agent_profile="researcher",
            tool_names={"web_search"},
            max_calls=5,
        )
    )
    return broker


def test_search_through_the_tool_broker_admits_its_results(tmp_path: Path) -> None:
    gate = ledger(tmp_path)
    broker = _search_broker(tmp_path, gate=gate)

    result = broker.execute(task_id="T-001", tool_name="web_search", arguments={"query": "x"})

    assert set(result["source_ledger_urls"]) == {
        "https://docs.example.com/guide",
        "https://cdn.example.org/spec.pdf",
    }
    admitted = gate.assert_allowed("https://docs.example.com/guide")
    assert admitted.origin == ORIGIN_SEARCH
    assert admitted.task_id == "T-001"
    assert admitted.tool_call_id == result["tool_call_id"]


def test_without_a_bound_ledger_nothing_is_admitted(tmp_path: Path) -> None:
    """A run with no ledger cannot fetch anything; searching does not change that."""
    broker = _search_broker(tmp_path, gate=None)

    result = broker.execute(task_id="T-001", tool_name="web_search", arguments={"query": "x"})

    assert "source_ledger_urls" not in result
    assert len(ledger(tmp_path)) == 0


def test_provenance_yields_only_url_sources() -> None:
    assert urls_from_provenance(
        [
            {"source": "https://docs.example.com/guide", "kind": "url"},
            {"source": "https://docs.example.com/guide", "kind": "url"},
            {"source": "/local/file.md", "kind": "file"},
            {"source": "", "kind": "url"},
            "not a mapping",
        ]
    ) == ("https://docs.example.com/guide",)
