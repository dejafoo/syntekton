from product_factory.domain.capabilities import CAPABILITY_TOOL_CLASSES
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.registry import resolve_workflow_pack


def test_operations_analysis_has_only_ops_read_and_artifact_write() -> None:
    classes = CAPABILITY_TOOL_CLASSES["operations_analysis"]
    assert classes == {"ops_read", "artifact_write"}
    assert not classes & {
        "repository_write",
        "git_write",
        "deployment_read",
        "deployment_write",
    }


def test_operational_skills_and_next_actions_cannot_add_mutation_authority() -> None:
    for pack_id in ("incident_triage", "service_health_review"):
        pack = resolve_workflow_pack(pack_id)
        assert pack.skill_policy["allow"] == ["operations.incident-synthesis"]
        assert handler_for(pack_id).authority_class() == "external_read"
        next_ids = {action.pack_id for action in handler_for(pack_id).eligible_next_actions()}
        assert next_ids == {"change_intake", "repository_investigation"}
        assert "deployment_execution" not in next_ids
