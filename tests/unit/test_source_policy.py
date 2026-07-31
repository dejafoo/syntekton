"""Unit tests for source policy profiles (PM1.0)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.runs import RunRequest
from product_factory.policy.source_policy import (
    SOURCE_CLASSES,
    SourcePolicyProfile,
    SourcePolicyRegistry,
    resolve_request_source_policy,
    resolve_source_policy,
)

REPO_PROFILES = Path(__file__).resolve().parents[2] / "profiles"


def _registry() -> SourcePolicyRegistry:
    return SourcePolicyRegistry.load(REPO_PROFILES)


def test_shipped_profiles_load() -> None:
    assert _registry().ids() == ["public-technical", "regulated-domain"]


def test_public_technical_admits_commentary_but_never_unknown() -> None:
    profile = _registry().require("public-technical")
    assert profile.allows_source_class("secondary_commentary")
    assert profile.allows_source_class("standard")
    assert not profile.allows_source_class("unknown")
    assert profile.require_expert_review_for == []


def test_regulated_profile_escalates_regulated_topics() -> None:
    profile = _registry().require("regulated-domain")
    for topic in ("compliance", "clinical", "legal", "privacy"):
        assert profile.requires_expert_review(topic)
    assert profile.requires_expert_review("COMPLIANCE")
    assert not profile.requires_expert_review("performance")
    # Commentary is not an admissible basis for a regulated claim.
    assert not profile.allows_source_class("secondary_commentary")
    assert profile.allows_source_class("regulator")


def test_every_declared_source_class_is_a_known_class() -> None:
    for profile in (
        _registry().require("public-technical"),
        _registry().require("regulated-domain"),
    ):
        assert set(profile.allowed_source_classes) <= set(SOURCE_CLASSES)
        assert set(profile.preferred_source_classes) <= set(profile.allowed_source_classes)


def test_domain_rules_deny_before_allow() -> None:
    profile = SourcePolicyProfile(
        id="test",
        allowed_domains=["example.test"],
        denied_domains=["blocked.example.test"],
    )
    assert profile.allows_domain("example.test")
    assert profile.allows_domain("docs.example.test")
    assert not profile.allows_domain("blocked.example.test")
    assert not profile.allows_domain("elsewhere.test")
    assert not profile.allows_domain("")
    # A host that merely ends with the same characters is not a subdomain.
    assert not profile.allows_domain("notexample.test")


def test_empty_allow_list_admits_any_non_denied_host() -> None:
    profile = SourcePolicyProfile(id="test", denied_domains=["blocked.test"])
    assert profile.allows_domain("anything.test")
    assert not profile.allows_domain("sub.blocked.test")


def test_staleness_uses_max_source_age_days() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    profile = SourcePolicyProfile(id="test", max_source_age_days=365)
    assert not profile.is_stale(now - timedelta(days=30), now=now)
    assert profile.is_stale(now - timedelta(days=400), now=now)
    # Freshness that cannot be shown has not been shown.
    assert profile.is_stale(None, now=now)
    assert not SourcePolicyProfile(id="test").is_stale(None, now=now)


def test_digest_is_stable_and_content_addressed() -> None:
    registry = _registry()
    public = registry.require("public-technical")
    regulated = registry.require("regulated-domain")
    assert public.digest == registry.require("public-technical").digest
    assert public.digest != regulated.digest
    assert public.as_manifest_entry() == {"source_policy:public-technical": public.digest}

    widened = public.model_copy(update={"max_source_age_days": 30})
    assert widened.digest != public.digest


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match="Unknown source policy profile"):
        resolve_source_policy("no-such-profile", profiles_root=REPO_PROFILES)


def test_missing_profiles_root_yields_empty_registry(tmp_path: Path) -> None:
    assert SourcePolicyRegistry.load(tmp_path).ids() == []


def test_profile_id_defaults_to_filename(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "custom.yaml").write_text(
        yaml.safe_dump({"allowed_source_classes": ["standard"]}),
        encoding="utf-8",
    )
    assert SourcePolicyRegistry.load(tmp_path).require("custom").id == "custom"


def test_profile_rejects_undeclared_fields(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "bad.yaml").write_text(
        yaml.safe_dump({"allowed_source_classes": ["standard"], "grants": ["apply_patch"]}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        SourcePolicyRegistry.load(tmp_path)


def test_request_resolution_is_opt_in_via_pack_input() -> None:
    plain = RunRequest(
        request_id="req-1",
        workflow_type="technical_plan",
        request_text="Design the retry policy.",
    )
    assert resolve_request_source_policy(plain, profiles_root=REPO_PROFILES) is None

    selected = RunRequest(
        request_id="req-2",
        workflow_type="technical_plan",
        request_text="Assess feasibility.",
        pack_input={"source_policy_profile": "regulated-domain"},
    )
    resolved = resolve_request_source_policy(selected, profiles_root=REPO_PROFILES)
    assert resolved is not None
    assert resolved.id == "regulated-domain"


def test_request_resolution_defaults_discovery_to_public_technical() -> None:
    request = RunRequest(
        request_id="req-discovery",
        workflow_type="feasibility_discovery",
        request_text="Assess feasibility.",
        pack_input={"decision_statement": "X?", "domain": "payments"},
    )
    resolved = resolve_request_source_policy(request, profiles_root=REPO_PROFILES)
    assert resolved is not None
    assert resolved.id == "public-technical"


def test_request_resolution_fails_closed_on_unknown_profile() -> None:
    request = RunRequest(
        request_id="req-3",
        workflow_type="technical_plan",
        request_text="Assess feasibility.",
        pack_input={"source_policy_profile": "ghost"},
    )
    with pytest.raises(ConfigurationError):
        resolve_request_source_policy(request, profiles_root=REPO_PROFILES)
