"""Policy helpers (classification, ingress, source/domain/composition policy)."""

from product_factory.policy.classification import (
    RULE_VERSION,
    ClassificationDecision,
    assert_ingress_allowed,
    classify_payload,
    classify_text,
)
from product_factory.policy.composition_gates import (
    CompositionConflictError,
    CompositionGateResult,
    assert_no_authority_widening,
    evaluate_composition_gates,
)
from product_factory.policy.domain_packs import (
    DomainPackRegistry,
    DomainReferencePack,
    resolve_domain_reference_pack,
    resolve_request_domain_packs,
)
from product_factory.policy.policy_profiles import (
    CompositionPolicyProfile,
    PolicyProfileRegistry,
    resolve_policy_profile,
    resolve_request_policy_profiles,
)
from product_factory.policy.source_policy import (
    DEFAULT_SOURCE_POLICY_PROFILE_ID,
    SOURCE_CLASSES,
    SourceClass,
    SourcePolicyProfile,
    SourcePolicyRegistry,
    resolve_request_source_policy,
    resolve_source_policy,
)

__all__ = [
    "DEFAULT_SOURCE_POLICY_PROFILE_ID",
    "RULE_VERSION",
    "SOURCE_CLASSES",
    "ClassificationDecision",
    "CompositionConflictError",
    "CompositionGateResult",
    "CompositionPolicyProfile",
    "DomainPackRegistry",
    "DomainReferencePack",
    "PolicyProfileRegistry",
    "SourceClass",
    "SourcePolicyProfile",
    "SourcePolicyRegistry",
    "assert_ingress_allowed",
    "assert_no_authority_widening",
    "classify_payload",
    "classify_text",
    "evaluate_composition_gates",
    "resolve_domain_reference_pack",
    "resolve_policy_profile",
    "resolve_request_domain_packs",
    "resolve_request_policy_profiles",
    "resolve_request_source_policy",
    "resolve_source_policy",
]
