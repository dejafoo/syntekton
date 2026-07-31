"""P4.A unit tests — deliverable land map resolution and fail-closed overrides."""

from __future__ import annotations

import pytest

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.plans import FinalArtifactSpec
from product_factory.domain.runs import ArtifactOverride, RunRequest
from product_factory.workflows.artifacts import (
    ROLE_ARCHITECTURE_DOCUMENT,
    ArtifactLandSpec,
    ArtifactOverrideError,
    normalize_overrides,
    resolve_artifact_land_map,
)
from product_factory.workflows.registry import land_map_for_request, resolve_workflow_pack

SPECS = (
    ArtifactLandSpec(
        role="architecture_document",
        default_logical_name="ARCHITECTURE.md",
        default_dest_path="docs/ARCHITECTURE.md",
    ),
    ArtifactLandSpec(
        role="proposed_patch",
        default_logical_name="proposed.patch",
        default_dest_path="proposed.patch",
        media_type="text/x-diff",
        landable=False,
        renamable=False,
    ),
)


def _request(**updates) -> RunRequest:
    base = RunRequest(
        request_id="req-1",
        workflow_type="technical_plan",
        request_text="Design integration testing.",
    )
    return base.model_copy(update=updates)


def test_defaults_apply_when_no_overrides() -> None:
    land_map = resolve_artifact_land_map(SPECS)
    entry = land_map.by_role("architecture_document")
    assert entry is not None
    assert entry.logical_name == "ARCHITECTURE.md"
    assert entry.dest_path == "docs/ARCHITECTURE.md"


def test_dest_override_also_renames_produced_artifact() -> None:
    land_map = resolve_artifact_land_map(
        SPECS,
        overrides={"architecture_document": "docs/integration_testing_architecture.md"},
    )
    entry = land_map.by_role("architecture_document")
    assert entry is not None
    assert entry.logical_name == "integration_testing_architecture.md"
    assert entry.dest_path == "docs/integration_testing_architecture.md"


def test_logical_override_keeps_default_directory() -> None:
    land_map = resolve_artifact_land_map(
        SPECS,
        overrides={"architecture_document": {"logical_name": "TEST_ARCH.md"}},
    )
    entry = land_map.by_role("architecture_document")
    assert entry is not None
    assert entry.logical_name == "TEST_ARCH.md"
    assert entry.dest_path == "docs/TEST_ARCH.md"


def test_request_override_beats_planner_proposal() -> None:
    planner = [
        FinalArtifactSpec(
            logical_name="planner_choice.md",
            composer_task_id="T-003",
            role="architecture_document",
            dest_path="docs/planner_choice.md",
        )
    ]
    land_map = resolve_artifact_land_map(
        SPECS,
        overrides={"architecture_document": "docs/host_choice.md"},
        planner_artifacts=planner,
    )
    entry = land_map.by_role("architecture_document")
    assert entry is not None
    assert entry.dest_path == "docs/host_choice.md"


def test_planner_proposal_beats_pack_default() -> None:
    planner = [
        FinalArtifactSpec(
            logical_name="planner_choice.md",
            composer_task_id="T-003",
            role="architecture_document",
        )
    ]
    land_map = resolve_artifact_land_map(SPECS, planner_artifacts=planner)
    entry = land_map.by_role("architecture_document")
    assert entry is not None
    assert entry.logical_name == "planner_choice.md"


@pytest.mark.parametrize(
    "dest",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "~/notes.md",
        "docs/../../escape.md",
        "docs/",
    ],
)
def test_unsafe_destinations_are_rejected(dest: str) -> None:
    with pytest.raises(ArtifactOverrideError):
        resolve_artifact_land_map(SPECS, overrides={"architecture_document": dest})


def test_unknown_role_fails_closed() -> None:
    with pytest.raises(ArtifactOverrideError) as exc:
        resolve_artifact_land_map(SPECS, overrides={"architcture_doc": "docs/x.md"})
    assert "Unknown artifact role" in str(exc.value)


def test_fixed_name_role_cannot_be_renamed() -> None:
    with pytest.raises(ArtifactOverrideError):
        resolve_artifact_land_map(SPECS, overrides={"proposed_patch": "docs/x.patch"})


def test_logical_name_must_be_a_bare_filename() -> None:
    with pytest.raises(ArtifactOverrideError):
        resolve_artifact_land_map(
            SPECS,
            overrides={"architecture_document": {"logical_name": "docs/x.md"}},
        )


def test_role_lookup_accepts_resolved_and_default_names() -> None:
    land_map = resolve_artifact_land_map(
        SPECS, overrides={"architecture_document": "docs/renamed.md"}
    )
    assert land_map.role_for_logical_name("renamed.md") == "architecture_document"
    # Runs recorded before the override still classify by the pack default.
    assert land_map.role_for_logical_name("ARCHITECTURE.md") == "architecture_document"
    assert land_map.role_for_logical_name("unrelated.md") is None


def test_landable_excludes_patch_role() -> None:
    land_map = resolve_artifact_land_map(SPECS)
    assert [entry.role for entry in land_map.landable()] == ["architecture_document"]


@pytest.mark.parametrize(
    "raw",
    [
        {"architecture_document": {"dest_path": "docs/x.md"}},
        {"architecture_document": "docs/x.md"},
        ["architecture_document=docs/x.md"],
        '{"architecture_document": "docs/x.md"}',
    ],
)
def test_normalize_overrides_accepts_every_host_shape(raw: object) -> None:
    assert normalize_overrides(raw) == {"architecture_document": {"dest_path": "docs/x.md"}}


def test_normalize_overrides_rejects_malformed_json() -> None:
    with pytest.raises(ArtifactOverrideError):
        normalize_overrides("{not json")


def test_land_map_for_request_reads_typed_field() -> None:
    request = _request(
        artifact_overrides={
            ROLE_ARCHITECTURE_DOCUMENT: ArtifactOverride(
                dest_path="docs/integration_testing_architecture.md"
            )
        }
    )
    entry = land_map_for_request(request).by_role(ROLE_ARCHITECTURE_DOCUMENT)
    assert entry is not None
    assert entry.logical_name == "integration_testing_architecture.md"


def test_land_map_for_request_reads_metadata_passthrough() -> None:
    request = _request(
        metadata={"artifact_overrides": '{"architecture_document": "docs/scoped.md"}'}
    )
    entry = land_map_for_request(request).by_role(ROLE_ARCHITECTURE_DOCUMENT)
    assert entry is not None
    assert entry.dest_path == "docs/scoped.md"


def test_land_map_for_request_reads_deprecated_requested_artifacts() -> None:
    request = _request(requested_artifacts=["architecture_document=docs/legacy.md"])
    entry = land_map_for_request(request).by_role(ROLE_ARCHITECTURE_DOCUMENT)
    assert entry is not None
    assert entry.dest_path == "docs/legacy.md"


def test_typed_override_wins_over_deprecated_alias() -> None:
    request = _request(
        requested_artifacts=["architecture_document=docs/legacy.md"],
        artifact_overrides={
            ROLE_ARCHITECTURE_DOCUMENT: ArtifactOverride(dest_path="docs/typed.md")
        },
    )
    entry = land_map_for_request(request).by_role(ROLE_ARCHITECTURE_DOCUMENT)
    assert entry is not None
    assert entry.dest_path == "docs/typed.md"


def test_land_map_for_request_raises_configuration_error() -> None:
    request = _request(artifact_overrides={"unknown_role": ArtifactOverride(dest_path="docs/x.md")})
    with pytest.raises(ConfigurationError):
        land_map_for_request(request)


@pytest.mark.parametrize(
    ("workflow_type", "role", "logical_name", "dest_path"),
    [
        ("technical_plan", "architecture_document", "ARCHITECTURE.md", "docs/ARCHITECTURE.md"),
        (
            "repository_investigation",
            "evidence_report",
            "EVIDENCE_REPORT.md",
            "docs/EVIDENCE_REPORT.md",
        ),
        ("repository_change", "proposed_patch", "proposed.patch", "proposed.patch"),
    ],
)
def test_shipped_packs_declare_their_deliverables(
    workflow_type: str, role: str, logical_name: str, dest_path: str
) -> None:
    pack = resolve_workflow_pack(workflow_type)
    specs = {spec.role: spec for spec in pack.artifacts}
    assert role in specs
    assert specs[role].default_logical_name == logical_name
    assert specs[role].default_dest_path == dest_path


def test_pack_hash_covers_land_map() -> None:
    pack = resolve_workflow_pack("technical_plan")
    renamed = pack.artifacts[0]
    mutated = pack.__class__(
        **{
            **pack.__dict__,
            "artifacts": (
                renamed.__class__(**{**renamed.__dict__, "default_dest_path": "docs/OTHER.md"}),
            ),
        }
    )
    assert mutated.content_hash() != pack.content_hash()
