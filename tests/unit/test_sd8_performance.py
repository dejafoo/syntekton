"""SD8 performance scaffolding and inventory-cache safety."""

from __future__ import annotations

from pathlib import Path

from product_factory.context.safe_inventory import (
    InventoryPolicy,
    SafeInventoryCache,
    build_safe_repository_inventory,
)
from product_factory.observability.performance import (
    KNOWN_STAGES,
    MEASUREMENT_GLOSSARY,
    MeasurementSession,
    synthesize_baseline_samples,
)


def test_measurement_session_records_correlation_and_percentiles() -> None:
    session = MeasurementSession(fixture_id="synthetic-small")
    with session.measure("plan"):
        pass
    session.record("inventory", 12.0)
    session.record("inventory", 20.0)
    session.record("inventory", 40.0)
    payload = session.as_payload()
    assert payload["correlation_id"]
    assert payload["fixture_id"] == "synthetic-small"
    assert "inventory" in payload["stats"]
    assert payload["stats"]["inventory"]["count"] == 3
    assert payload["stats"]["inventory"]["p50_ms"] == 20.0
    assert payload["honesty"].startswith("Baselines recorded")
    assert set(KNOWN_STAGES).issubset(MEASUREMENT_GLOSSARY)


def test_synthetic_baselines_for_small_and_medium_fixtures() -> None:
    small = synthesize_baseline_samples(
        fixture_id="synthetic-small",
        stage_durations_ms={
            "plan": [5.0, 6.0, 7.0, 8.0, 9.0],
            "inventory": [10.0, 11.0, 12.0, 13.0, 14.0],
            "sqlite": [1.0, 1.2, 1.1, 1.3, 1.4],
        },
    )
    medium = synthesize_baseline_samples(
        fixture_id="synthetic-medium",
        stage_durations_ms={
            "plan": [20.0, 22.0, 24.0, 26.0, 28.0],
            "inventory": [40.0, 45.0, 50.0, 55.0, 60.0],
            "prompt": [15.0, 16.0, 17.0, 18.0, 19.0],
            "validation": [8.0, 9.0, 10.0, 11.0, 12.0],
        },
    )
    assert small.stats_by_stage()["plan"].p50_ms == 7.0
    assert medium.stats_by_stage()["inventory"].p95_ms >= 55.0


def test_inventory_cache_keyed_by_snapshot_and_policy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ok.txt").write_text("hello\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

    cache = SafeInventoryCache()
    first = cache.get_or_build(root=repo, snapshot_revision="rev-a")
    second = cache.get_or_build(root=repo, snapshot_revision="rev-a")
    assert first is second
    assert cache.hits == 1
    assert cache.misses == 1
    assert first.contains("ok.txt")
    assert not first.contains(".env")

    # Different snapshot must not reuse the previous inventory object.
    other_rev = cache.get_or_build(root=repo, snapshot_revision="rev-b")
    assert other_rev is not first
    assert cache.misses == 2

    # Different policy digest must rebuild (never serve prohibited under old policy).
    tight = InventoryPolicy(max_files=1)
    tight_inventory = cache.get_or_build(
        root=repo, snapshot_revision="rev-a", policy=tight
    )
    assert tight_inventory is not first
    assert tight_inventory.policy_digest != first.policy_digest


def test_inventory_cache_invalidation_drops_stale_revision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    cache = SafeInventoryCache()
    cached = cache.get_or_build(root=repo, snapshot_revision="rev-a")
    assert cached.contains("a.txt")
    cache.invalidate(snapshot_revision="rev-a")
    rebuilt = cache.get_or_build(root=repo, snapshot_revision="rev-a")
    assert rebuilt is not cached
    # Fresh build still excludes secrets after invalidation.
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    fresh = build_safe_repository_inventory(repo)
    assert not fresh.contains(".env")
