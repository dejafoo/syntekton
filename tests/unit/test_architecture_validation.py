"""Request-specific architecture validation for Stage D / N6."""

from __future__ import annotations

from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.deterministic import run_deterministic_checks
from product_factory.evaluation.subjects import SubjectArtifact
from product_factory.validation.pipeline import (
    validate_architecture_document,
    validate_architecture_request_specificity,
)

TEMPLATE_DOC = """# ARCHITECTURE.md

## Objective
Design a small SaaS billing service.

## Scope
MVP scope as requested.

## Assumptions
- Standard web service deployment.

## Functional requirements
- Deliver the requested capabilities.

## Nonfunctional requirements
- Reliability, observability, and security baselines.

## System context
Users interact via API/CLI; system persists to a database.

## Components and responsibilities
- API layer, domain services, persistence.

## Data flows
```mermaid
flowchart LR
  User --> API --> Service --> DB
```

## Security boundaries
- Authn/authz at API edge; secrets outside repo.

## Testing strategy
- Unit, contract, and integration tests.

## Trade-offs
- Simplicity over premature distribution.

## Open questions
- Exact SLA targets.

## Acceptance criteria
- Document sections complete; open questions listed.
"""


def test_section_validator_still_accepts_template_headers() -> None:
    result = validate_architecture_document(TEMPLATE_DOC)
    assert result.status == "pass"


def test_specificity_rejects_boilerplate_template() -> None:
    results = validate_architecture_request_specificity(
        TEMPLATE_DOC,
        must_cover=["tenant isolation", "invoice lifecycle"],
        reject_boilerplate=True,
    )
    assert any(r.status == "fail" for r in results)
    ids = {r.validator_id for r in results}
    assert "architecture_boilerplate" in ids
    assert "architecture_must_cover" in ids


def test_specificity_accepts_request_specific_content() -> None:
    doc = """# ARCHITECTURE.md

## Objective
Multi-tenant SaaS billing with invoice lifecycle and dunning for B2B customers who
need subscription management, webhook-driven payment updates, and audited invoice
history across tenants.

## Scope
MVP covers subscriptions, invoice generation, Stripe webhook ingestion, and a
tenant-scoped billing admin API. Out of scope: tax engines and marketplace payouts.

## Assumptions
- One Postgres database with row-level tenant_id enforcement.
- Card data never touches our servers; Stripe Elements collects PANs.

## Functional requirements
- Create and cancel subscriptions.
- Generate invoices through draft → open → paid → void states.
- Verify Stripe webhook signatures before mutating invoice state.

## Nonfunctional requirements
- Strong tenant isolation; p99 API latency under 300ms for read paths.
- Audit log retention of 90 days for billing mutations.

## Components
- Billing API, Invoice worker, Webhook ingress, Tenant auth gateway.

## Data flows
```mermaid
flowchart LR
  Tenant --> API --> InvoiceWorker --> Stripe
  Stripe --> WebhookIngress --> InvoiceWorker
```

## Security
- Tenant isolation via RLS on tenant_id; webhook signature verification; no raw
  PANs stored; admin actions require scoped API keys.

## Testing
- Contract tests for invoice lifecycle transitions.
- Isolation tests that fail if tenant A can read tenant B invoices.
- Webhook replay and signature-failure cases.

## Trade-offs
- Stripe Billing over an in-house ledger for MVP speed and PCI scope reduction.

## Open questions
- EU VAT edge cases for marketplace sellers.

## Acceptance criteria
- Tenant cannot read another tenant's invoices.
- Invoice lifecycle covers draft→open→paid→void.
- Webhooks are authenticated and idempotent.
"""
    results = validate_architecture_request_specificity(
        doc,
        must_cover=["tenant isolation", "invoice lifecycle", "webhook"],
        reject_boilerplate=True,
    )
    assert all(r.status == "pass" for r in results)


def test_deterministic_checks_wire_must_cover() -> None:
    case = EvalCase(
        id="arch_saas",
        workflow_type="architecture",
        request="Design a small SaaS billing service.",
        must_cover=["tenant isolation", "invoice lifecycle"],
    )
    artifact = SubjectArtifact(
        subject_id="full_orchestration",
        case_id=case.id,
        status="completed",
        artifact_text=TEMPLATE_DOC,
        artifact_kind="architecture",
    )
    results = run_deterministic_checks(case, artifact)
    assert any(
        r.validator_id == "architecture_boilerplate" and r.status == "fail" for r in results
    )
    assert any(
        r.validator_id == "architecture_must_cover" and r.status == "fail" for r in results
    )
