"""`technical_spike` pack for local, synthetic interface experiments."""

from __future__ import annotations

from product_factory.workflows.artifacts import ROLE_SPIKE_RESULT, ArtifactLandSpec
from product_factory.workflows.base import WorkflowPack, execution_policy

TECHNICAL_SPIKE_PACK = WorkflowPack(
    id="technical_spike",
    version="1.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "hypothesis": {"type": "string"},
            "contract_paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "schema_name": {"type": ["string", "null"]},
        },
        "required": ["hypothesis", "contract_paths"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "spike_result": {"type": "object"},
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset({"interface_analysis"}),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": ["spike_result_schema", "secret_scan"],
        "review": "optional",
        "behavioral_commands": "none",
        "workspace": "disposable_confined",
        "network": "denied",
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": [
            "integration.contract-analysis",
            "integration.technical-spike",
        ],
    },
    routing_defaults={"coding_worker_tier": "mid"},
    execution_policy=execution_policy(
        capabilities=frozenset({"interface_analysis"}),
        validators=["spike_result_schema", "secret_scan"],
        output_roles=(ROLE_SPIKE_RESULT,),
        denied_tool_names=frozenset(
            {
                "create_file",
                "apply_patch",
                "run_validation_command",
                "web_search",
                "fetch_source",
            }
        ),
        required_output_roles=frozenset({ROLE_SPIKE_RESULT}),
        fallback_composition_roles=frozenset({ROLE_SPIKE_RESULT}),
        evaluation_fixture_id="technical_spike.v1",
    ),
    artifacts=(
        ArtifactLandSpec(
            role=ROLE_SPIKE_RESULT,
            default_logical_name="SPIKE_RESULT.json",
            default_dest_path="docs/SPIKE_RESULT.json",
            media_type="application/json",
            landable=False,
            description="Disposable technical-spike measurements and explicit limits.",
        ),
    ),
    description=(
        "Local-only OpenAPI/JSON Schema analysis with synthetic fixtures and "
        "worktree-confined simulation."
    ),
)
