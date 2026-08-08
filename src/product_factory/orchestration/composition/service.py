"""CompositionService — draft and deliverable assembly (SD2).

Owns architecture/evidence/intake/quality composition previously on
RunCoordinator. Pack handlers and CompositionExecutor call this service
through typed methods; no coordinator compose callbacks.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.domain.errors import BudgetExhaustedError, RuntimeFailureError
from product_factory.domain.findings import Finding
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskSpec
from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.workflows.artifacts import (
    ROLE_CHANGE_BRIEF,
    ROLE_CLARIFICATION_REQUEST,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
)

logger = logging.getLogger("product_factory.orchestration.composition")

# Live architecture compose used to hardcode 8k output tokens, which truncated
# long research docs mid-Citations even when the model profile allowed more.
# Continuations recover when finish_reason/token cap still clips the draft.
_ARCHITECTURE_COMPOSE_MAX_CONTINUATIONS = 2
_LENGTH_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


def output_was_truncated(
    *,
    finish_reason: str | None,
    output_tokens: int,
    max_output_tokens: int,
) -> bool:
    """True when the provider stopped because the output token limit was hit."""
    reason = (finish_reason or "").strip().lower()
    if reason in _LENGTH_FINISH_REASONS:
        return True
    return max_output_tokens > 0 and output_tokens >= max_output_tokens


def append_markdown_continuation(base: str, continuation: str) -> str:
    """Append a continuation fragment to a truncated markdown draft."""
    cont = (continuation or "").strip()
    if not cont:
        return base
    # Mid-token / mid-link cuts (e.g. ending in '(') should glue without a newline.
    if base and base[-1] not in "\n.!?`\"')" and not cont.startswith(("#", "-", "*", "|", ">")):
        return f"{base}{cont}"
    if not base.endswith("\n"):
        base = f"{base}\n"
    return f"{base}{cont}"




class CompositionService:
    """Typed composition boundary for pack deliverables."""

    def __init__(
        self,
        *,
        config: AppConfig,
        gateway: ModelGateway,
        resolve_validation_command_ids: Any | None = None,
    ) -> None:
        self.config = config
        self._raw_gateway = gateway
        self._resolve_validation_command_ids_fn = resolve_validation_command_ids

    def resolve_validation_command_ids(self, request: RunRequest) -> list[str]:
        if self._resolve_validation_command_ids_fn is not None:
            return list(self._resolve_validation_command_ids_fn(request))
        if request.validation_commands:
            return list(request.validation_commands)
        return [
            value.strip()
            for value in str(request.metadata.get("smoke_commands", "")).split(",")
            if value.strip()
        ]

    def profile_max_output_tokens(self, profile: str, *, default: int = 8_000) -> int:
        """Resolve the model-profile output ceiling used for one-shot compose calls."""
        profile_cfg = self.config.models.profiles.get(profile)
        if profile_cfg is None:
            return max(1, default)
        return max(1, int(profile_cfg.max_output_tokens))

    def generate_architecture_document(
        self,
        *,
        request: RunRequest,
        task: TaskSpec,
        ctx_messages: list[dict[str, Any]],
        run_id: str,
        profile: str,
        dependency_outputs: list[dict[str, Any]],
        document_name: str = "ARCHITECTURE.md",
        gateway: ModelGateway | None = None,
    ) -> tuple[str, UsageMetrics]:
        """Ask the live model for a request-specific architecture document.

        `document_name` is the resolved deliverable name, so a run that asked for
        `integration_testing_architecture.md` gets a document scoped to that
        subject rather than a whole-system template.

        Uses the assigned model profile's `max_output_tokens` (not a hardcoded
        8k). If the provider still truncates (`finish_reason=length` or
        `output_tokens` hitting the request cap), continues up to
        `_ARCHITECTURE_COMPOSE_MAX_CONTINUATIONS` times, then fails closed.
        """
        from product_factory.validation.pipeline import ARCHITECTURE_REQUIRED_SECTIONS

        must_cover = [
            item.strip()
            for item in str(request.metadata.get("must_cover") or "").split("|")
            if item.strip()
        ]
        section_list = ", ".join(ARCHITECTURE_REQUIRED_SECTIONS)
        system = (
            f"You are the architecture composer. Write a complete {document_name} "
            "markdown document for the user request. Use these section headings "
            f"(as markdown ## headings): {section_list}. "
            "Every section must contain request-specific detail — never generic "
            "boilerplate such as 'MVP scope as requested' or 'Deliver the requested "
            "capabilities'. Include at least one mermaid data-flow diagram when useful. "
            "Return markdown only."
        )
        payload = {
            "request": request.request_text,
            "deliverable_name": document_name,
            "task_objective": task.objective,
            "must_cover_topics": must_cover,
            "reference_hints": request.metadata.get("reference_hints") or "",
            "dependency_drafts": dependency_outputs[:4],
            "prior_context_messages": ctx_messages[-4:],
        }
        max_output_tokens = self.profile_max_output_tokens(profile)
        seed = (
            int(request.metadata["benchmark_seed"])
            if request.metadata.get("benchmark_seed") is not None
            else None
        )
        usage = UsageMetrics()
        model_gateway = gateway or self._raw_gateway
        try:
            messages = [
                CanonicalMessage(role="system", content=system),
                CanonicalMessage(
                    role="user",
                    content=json.dumps(payload, indent=2, default=str),
                ),
            ]
            resp = model_gateway.complete(
                ModelRequest(
                    request_id=f"arch-{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    task_id=task.id,
                    session_id=f"pf:{run_id}:{profile}:{task.id}",
                    model_profile=profile,
                    messages=messages,
                    max_output_tokens=max_output_tokens,
                    temperature=0.2,
                    seed=seed,
                    max_cost_usd=float(request.budget.max_cost_usd),
                )
            )
            usage = usage.merge(resp.usage)
            text = (resp.text or "").strip()
            if not text:
                raise RuntimeFailureError("Architecture composition returned empty text")

            continuations = 0
            while output_was_truncated(
                finish_reason=resp.finish_reason,
                output_tokens=resp.usage.output_tokens,
                max_output_tokens=max_output_tokens,
            ):
                if continuations >= _ARCHITECTURE_COMPOSE_MAX_CONTINUATIONS:
                    raise RuntimeFailureError(
                        "Architecture composition truncated after "
                        f"{_ARCHITECTURE_COMPOSE_MAX_CONTINUATIONS} continuation "
                        f"attempt(s) (finish_reason={resp.finish_reason!r}, "
                        f"output_tokens={resp.usage.output_tokens}, "
                        f"max_output_tokens={max_output_tokens})"
                    )
                continuations += 1
                logger.warning(
                    "Architecture compose truncated for %s/%s "
                    "(finish_reason=%s output_tokens=%s max=%s); continuing (%s/%s)",
                    run_id,
                    task.id,
                    resp.finish_reason,
                    resp.usage.output_tokens,
                    max_output_tokens,
                    continuations,
                    _ARCHITECTURE_COMPOSE_MAX_CONTINUATIONS,
                )
                messages = [
                    *messages,
                    CanonicalMessage(role="assistant", content=text),
                    CanonicalMessage(
                        role="user",
                        content=(
                            "The previous draft was cut off because the output token "
                            "limit was reached. Continue the markdown document exactly "
                            "where it left off. Do not restart or rewrite completed "
                            "sections. Output only the continuation text."
                        ),
                    ),
                ]
                resp = model_gateway.complete(
                    ModelRequest(
                        request_id=f"arch-cont-{uuid.uuid4().hex[:8]}",
                        run_id=run_id,
                        task_id=task.id,
                        session_id=f"pf:{run_id}:{profile}:{task.id}",
                        model_profile=profile,
                        messages=messages,
                        max_output_tokens=max_output_tokens,
                        temperature=0.2,
                        seed=seed,
                        max_cost_usd=float(request.budget.max_cost_usd),
                    )
                )
                usage = usage.merge(resp.usage)
                fragment = (resp.text or "").strip()
                if not fragment:
                    raise RuntimeFailureError(
                        "Architecture composition continuation returned empty text"
                    )
                text = append_markdown_continuation(text, fragment)

            if not text.lstrip().startswith("#"):
                text = f"# {document_name}\n\n{text}"
            return text, usage
        except BudgetExhaustedError:
            raise
        except RuntimeFailureError:
            raise
        except Exception:
            pass
        # Fail closed toward an explicit thin draft rather than silent template success.
        fallback = (
            f"# {document_name}\n\n## Objective\n{request.request_text.strip()}\n\n"
            "## Scope\nGeneration failed; document incomplete.\n\n"
            "## Assumptions\n- None captured.\n\n"
            "## Functional requirements\n- None captured.\n\n"
            "## Nonfunctional requirements\n- None captured.\n\n"
            "## Components\n- None captured.\n\n"
            "## Data flows\nNone captured.\n\n"
            "## Security\nNone captured.\n\n"
            "## Testing\nNone captured.\n\n"
            "## Trade-offs\nNone captured.\n\n"
            "## Open questions\n- Architecture generation failed.\n\n"
            "## Acceptance criteria\n- Regenerate architecture document.\n"
        )
        return fallback, usage

    def compose_architecture(
        self,
        request_text: str,
        findings: list[Finding],
        *,
        document_name: str = "ARCHITECTURE.md",
    ) -> str:
        sections = [
            f"# {document_name}",
            "",
            "## Objective",
            request_text.strip() or "TBD",
            "",
            "## Scope",
            "MVP scope as requested.",
            "",
            "## Assumptions",
            "- Standard web service deployment.",
            "",
            "## Functional requirements",
            "- Deliver the requested capabilities.",
            "",
            "## Nonfunctional requirements",
            "- Reliability, observability, and security baselines.",
            "",
            "## System context",
            "Users interact via API/CLI; system persists to a database.",
            "",
            "## Components and responsibilities",
            "- API layer, domain services, persistence.",
            "",
            "## Data flows",
            "```mermaid",
            "flowchart LR",
            "  User --> API --> Service --> DB",
            "```",
            "",
            "## External dependencies",
            "- Managed database; optional object storage.",
            "",
            "## Security boundaries",
            "- Authn/authz at API edge; secrets outside repo.",
            "",
            "## Failure handling",
            "- Timeouts, retries with backoff, graceful degradation.",
            "",
            "## Observability",
            "- Structured logs, metrics, traces.",
            "",
            "## Testing strategy",
            "- Unit, contract, and integration tests.",
            "",
            "## Deployment assumptions",
            "- Single-region container deploy for MVP.",
            "",
            "## Trade-offs",
            "- Simplicity over premature distribution.",
            "",
            "## Rejected alternatives",
            "- Multi-region active-active for MVP.",
            "",
            "## Open questions",
            "- Exact SLA targets.",
            "",
            "## Implementation stages",
            "1. Scaffold 2. Core API 3. Hardening",
            "",
            "## Acceptance criteria",
            "- Document sections complete; open questions listed.",
            "",
        ]
        if findings:
            sections.append("## Review findings")
            for f in findings:
                sections.append(f"- {f.summary}")
        return "\n".join(sections) + "\n"

    def compose_evidence_report(
        self,
        request_text: str,
        *,
        findings: list[Finding],
        dependency_outputs: list[dict[str, Any]],
        document_name: str = "EVIDENCE_REPORT.md",
    ) -> str:
        """Deterministic evidence report with cited paths and assumptions (P3.D)."""
        cited_paths: list[str] = []
        for prior in dependency_outputs:
            for excerpt in prior.get("artifact_excerpts") or []:
                if excerpt.get("logical_name") != "repository-analysis.json":
                    continue
                try:
                    payload = json.loads(excerpt.get("content") or "{}")
                except json.JSONDecodeError:
                    continue
                for key in ("files", "entry_points", "tests", "configuration"):
                    for path in payload.get(key) or []:
                        path_s = str(path).strip()
                        if path_s and path_s not in cited_paths:
                            cited_paths.append(path_s)
                for item in payload.get("relevant_excerpts") or []:
                    if isinstance(item, dict) and item.get("path"):
                        path_s = str(item["path"]).strip()
                        if path_s and path_s not in cited_paths:
                            cited_paths.append(path_s)
        if not cited_paths:
            cited_paths = ["README.md"]
        cited_paths = cited_paths[:20]
        finding_lines = [f"- {f.summary} (see `{cited_paths[0]}`)" for f in findings] or [
            f"- Request focuses on: {request_text.strip()[:240] or 'repository structure'}",
            f"- Observed entry points and modules under `{cited_paths[0]}`",
        ]
        assumption_lines = [
            "- Analysis is read-only; no repository mutations were performed.",
            "- Path citations come from repository listing and targeted excerpts.",
            "- Scope is limited to files visible in the snapshotted worktree.",
        ]
        sections = [
            f"# {document_name}",
            "",
            "## Summary",
            request_text.strip() or "Repository investigation",
            "",
            "## Findings",
            *finding_lines,
            "",
            "## Cited paths",
            *[f"- `{path}`" for path in cited_paths],
            "",
            "## Assumptions",
            *assumption_lines,
            "",
        ]
        return "\n".join(sections) + "\n"

    def compose_feasibility_dossier(
        self,
        request: RunRequest,
        *,
        findings: list[Finding],
        dependency_outputs: list[dict[str, Any]],
        document_name: str = "FEASIBILITY_DISCOVERY.md",
    ) -> str:
        """Deterministic feasibility dossier for mock / fallback compose (PM1.D)."""
        from product_factory.policy.composition_gates import evaluate_composition_gates
        from product_factory.policy.domain_packs import resolve_request_domain_packs
        from product_factory.policy.policy_profiles import resolve_request_policy_profiles
        from product_factory.policy.source_policy import resolve_request_source_policy

        pack_input = getattr(request, "pack_input", None) or {}
        decision = str(
            pack_input.get("decision_statement") or request.request_text or "Decision pending"
        ).strip()
        domain = str(pack_input.get("domain") or "unspecified").strip()
        jurisdiction = str(pack_input.get("jurisdiction") or "").strip()
        policy = resolve_request_source_policy(request, profiles_root=self.config.root / "profiles")
        regulated_topics = list(getattr(policy, "require_expert_review_for", None) or [])
        domain_lower = domain.lower()
        text_lower = f"{decision}\n{domain}".lower()
        hits_regulated = any(
            topic.lower() in text_lower or topic.lower() in domain_lower
            for topic in ("compliance", "clinical", "legal", "privacy", *regulated_topics)
        ) or bool(regulated_topics and (policy and policy.id == "regulated-domain"))
        composition_gate = evaluate_composition_gates(
            request=request,
            domain_packs=resolve_request_domain_packs(
                request, packs_root=self.config.root / "packs"
            ),
            policy_profiles=resolve_request_policy_profiles(
                request, profiles_root=self.config.root / "profiles"
            ),
        )
        if composition_gate.requires_human_review:
            hits_regulated = True

        if hits_regulated:
            recommendation = "needs_expert_review"
            expert_line = "Expert review: required — named human specialist must confirm"
            next_step = "Route the dossier to a named expert before technical planning."
        else:
            recommendation = "insufficient_evidence"
            expert_line = ""
            next_step = "Obtain a current primary source, then continue with technical_plan."

        jurisdiction_lines = []
        if jurisdiction:
            jurisdiction_lines = [
                f"- Jurisdiction: {jurisdiction}",
                "- Source date: 2024-01-01",
            ]
        elif hits_regulated:
            jurisdiction_lines = [
                "- Jurisdiction: unknown",
                "- Source date: unknown",
            ]

        finding_lines = [f"- inference: {f.summary}" for f in findings[:5]]
        evidence_lines = [
            "- fact: Vendor documentation describes a public integration surface "
            "(source_id: src-mock-1, https://example.com/docs).",
            "- inference: Operational burden depends on operator-run adapters.",
            "- unknown: Contractual SLA and liability terms.",
            *finding_lines,
        ]
        if hits_regulated:
            evidence_lines.insert(
                0,
                "- assumption: Compliance/clinical/legal/privacy conclusions are not "
                "authoritative without expert review.",
            )

        sections = [
            f"# {document_name}",
            "",
            "## Decision",
            decision,
            "",
            "## Scope",
            "Bounded public-evidence discovery only; no live system access.",
            f"- Domain: {domain}",
            *jurisdiction_lines,
            "",
            "## Domain model",
            f"Actors and integration boundaries for {domain}.",
            "",
            "## Options",
            "- Option A: reuse an existing certified or documented pathway.",
            "- Option B: build a custom adapter behind a policy gate.",
            "",
            "## Comparison rubric",
            "- Capability, interoperability, security/privacy, operational burden, reversibility.",
            "- Option A / Capability: unknown",
            "- Option A / Interoperability: unknown",
            "- Option A / Security/privacy: unknown",
            "- Option A / Operational burden: unknown",
            "- Option A / Reversibility: scored as high",
            "- Option B / Capability: unknown",
            "- Option B / Interoperability: unknown",
            "- Option B / Security/privacy: unknown",
            "- Option B / Operational burden: unknown",
            "- Option B / Reversibility: scored as medium",
            "",
            "## Evidence",
            *evidence_lines,
            "",
            "## Assumptions",
            "- Operators supply jurisdiction and deployment context when required.",
            "- Discovery uses public or operator-approved sources only.",
            "",
            "## Unknowns",
            "- Missing primary-source confirmation for contested claims.",
            "",
            "## Risks",
            "- Treating secondary commentary as authoritative policy.",
            "",
            "## Constraints",
            "- Read-only; no repository write or technical spike in PM1.",
            "",
            "## Recommendation",
            recommendation,
            *([expert_line] if expert_line else []),
            "",
            "## Next step",
            next_step,
            "",
        ]
        return "\n".join(sections) + "\n"

    def compose_change_intake(
        self,
        request: RunRequest,
        *,
        role: str,
        findings: list[Finding],
        dependency_outputs: list[dict[str, Any]],
        document_name: str = "CHANGE_BRIEF.md",
    ) -> str:
        """Deterministic change brief / clarification for mock compose (PM2.A)."""
        from product_factory.validation.pipeline import request_looks_underspecified

        pack_input = getattr(request, "pack_input", None) or {}
        request_text = (request.request_text or "").strip()
        desired = str(pack_input.get("desired_outcome") or "").strip()
        decision = str(pack_input.get("decision_statement") or "").strip()
        constraints = [
            str(item).strip()
            for item in (pack_input.get("known_constraints") or [])
            if str(item).strip()
        ]
        underspecified = request_looks_underspecified(request_text, pack_input=pack_input)
        wants_clarification = role == ROLE_CLARIFICATION_REQUEST or (
            role != ROLE_CHANGE_BRIEF and underspecified
        )

        outcome = desired or decision or request_text or "Change outcome pending"
        if wants_clarification or role == ROLE_CLARIFICATION_REQUEST:
            name = document_name or "CLARIFICATION_REQUEST.md"
            questions = [
                "- What concrete outcome should this change produce?",
                "- What is explicitly out of scope?",
                "- Which acceptance checks would prove the change is done?",
            ]
            if constraints:
                questions.append(
                    "- Do the stated constraints still apply: " + "; ".join(constraints[:3]) + "?"
                )
            sections = [
                f"# {name}",
                "",
                "## Questions",
                *questions,
                "",
                "## Blocking unknowns",
                "- Acceptance criteria are not yet pinned.",
                "- Scope boundaries are incomplete.",
                "",
                "## Partial outcome",
                outcome,
                "",
                "## Recommended next pack",
                "none — human clarification required before investigation or planning",
                "",
            ]
            return "\n".join(sections) + "\n"

        name = document_name or "CHANGE_BRIEF.md"
        constraint_lines = [f"- {c}" for c in constraints] or [
            "- Stay within the existing repository conventions."
        ]
        finding_lines = [f"- inference: {f.summary}" for f in findings[:5]]
        sections = [
            f"# {name}",
            "",
            "## Outcome",
            outcome,
            "",
            "## Scope",
            "Implement the requested change within the named repository surfaces.",
            "",
            "## Non-goals",
            "- Unrelated refactors",
            "- New live research or discovery plane work",
            "",
            "## Acceptance criteria",
            "- The stated outcome is observable in the repository.",
            "- Existing tests relevant to the change still pass.",
            "- No secrets are introduced.",
            "",
            "## Constraints",
            *constraint_lines,
            "",
            "## Risks",
            "- Mis-scoped acceptance if operator intent was incomplete.",
            *finding_lines,
            "",
            "## Assumptions",
            "- Request text and optional pinned dossier are authoritative for framing.",
            "",
            "## Unknowns",
            "- Residual edge cases not named in the request.",
            "",
            "## Recommended next pack",
            "technical_plan",
            "",
        ]
        return "\n".join(sections) + "\n"

    @staticmethod
    def inherited_findings(dependency_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Findings produced upstream, deduplicated by id in dependency order."""
        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        for prior in dependency_outputs:
            for finding in prior.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                finding_id = str(finding.get("id") or "")
                if finding_id and finding_id in seen:
                    continue
                if finding_id:
                    seen.add(finding_id)
                collected.append(finding)
        return collected

    @staticmethod
    def scoped_paths(dependency_outputs: list[dict[str, Any]]) -> list[str]:
        """Repository paths an upstream scoping task actually listed.

        Read from the `repository-analysis.json` excerpt so a plan cites files a
        reviewer can open, ordered tests-first because those carry the coverage
        signal a quality gate reports on.
        """
        paths: list[str] = []
        for prior in dependency_outputs:
            for excerpt in prior.get("artifact_excerpts") or []:
                if excerpt.get("logical_name") != "repository-analysis.json":
                    continue
                try:
                    payload = json.loads(excerpt.get("content") or "{}")
                except json.JSONDecodeError:
                    continue
                for key in ("tests", "entry_points", "configuration", "files"):
                    for path in payload.get(key) or []:
                        path_s = str(path).strip()
                        if path_s and path_s not in paths:
                            paths.append(path_s)
                # Excerpted paths are the reliable signal: the listing is empty
                # whenever a read-only task's glob matched nothing.
                for item in payload.get("relevant_excerpts") or []:
                    if isinstance(item, dict) and item.get("path"):
                        path_s = str(item["path"]).strip()
                        if path_s and path_s not in paths:
                            paths.append(path_s)
        return paths

    @staticmethod
    def evidence_paths(findings: list[dict[str, Any]]) -> list[str]:
        """Path-like evidence scopes cited by findings, in first-seen order."""
        paths: list[str] = []
        for finding in findings:
            for ref in finding.get("evidence_refs") or []:
                if not isinstance(ref, dict):
                    continue
                scope = str(ref.get("scope") or "").strip()
                if not scope or scope in {"patch", "review_input"}:
                    continue
                if scope not in paths:
                    paths.append(scope)
        return paths

    def compose_quality_document(
        self,
        *,
        role: str,
        request: RunRequest,
        dependency_outputs: list[dict[str, Any]],
        document_name: str,
    ) -> str:
        """Deterministic quality-gate deliverable for one land-map role (P4.E).

        Each document carries the sections its pack declares and cites the
        evidence paths that upstream tasks actually recorded, so a findings report
        never asserts a defect without a path a reviewer can open.
        """
        inherited = self.inherited_findings(dependency_outputs)
        scoped_paths = self.scoped_paths(dependency_outputs)
        evidence_paths = self.evidence_paths(inherited) or scoped_paths[:5] or ["README.md"]
        objective = request.request_text.strip() or "Repository quality review"
        commands = self.resolve_validation_command_ids(request)

        if role == ROLE_TEST_PLAN:
            ranked = (scoped_paths or evidence_paths)[:10]
            risk_lines = [f"- `{path}` — exercised by the checks below" for path in ranked]
            case_lines = [
                f"- Verify behavior covered by `{path}` against its acceptance criteria"
                for path in ranked
            ]
            if commands:
                case_lines.extend(
                    f"- Registered validation command `{command}`" for command in commands
                )
            sections = [
                f"# {document_name}",
                "",
                "## Summary",
                objective,
                "",
                "## Risk areas",
                *risk_lines,
                "",
                "## Test cases",
                *case_lines,
                "",
                "## Coverage gaps",
                *(
                    ["- No registered validation commands were configured for this run."]
                    if not commands
                    else ["- Paths outside the reviewed scope remain unverified."]
                ),
                "",
            ]
            return "\n".join(sections) + "\n"

        if role == ROLE_SECURITY_EVIDENCE:
            security = [
                finding
                for finding in inherited
                if str(finding.get("category") or "").lower() == "security"
            ]
            finding_lines = [
                f"- {finding.get('summary') or 'Security observation'} "
                f"({finding.get('severity') or 'minor'})"
                for finding in security
            ] or ["- No security-specific defects were identified in the reviewed scope."]
            sections = [
                f"# {document_name}",
                "",
                "## Summary",
                objective,
                "",
                "## Checks performed",
                "- Secret patterns scanned across composed deliverables.",
                "- Repository read-only inspection of the paths cited below.",
                "",
                "## Findings",
                *finding_lines,
                "",
                "## Evidence",
                *[f"- `{path}`" for path in evidence_paths],
                "",
            ]
            return "\n".join(sections) + "\n"

        blocking = [finding for finding in inherited if str(finding.get("severity")) == "blocking"]
        finding_lines = [
            f"- [{finding.get('severity') or 'minor'}] "
            f"{finding.get('summary') or 'Finding'} — see "
            f"`{(self.evidence_paths([finding]) or evidence_paths)[0]}`"
            for finding in inherited
        ] or ["- No defects were identified in the reviewed scope."]
        action_lines = [
            f"- {finding.get('recommended_action') or 'Review and triage'}"
            for finding in inherited
            if finding.get("recommended_action")
        ] or ["- No action required from this gate."]
        sections = [
            f"# {document_name}",
            "",
            "## Summary",
            objective,
            "",
            f"Blocking findings: {len(blocking)}. Total findings: {len(inherited)}.",
            "",
            "## Findings",
            *finding_lines,
            "",
            "## Evidence",
            *[f"- `{path}`" for path in evidence_paths],
            "",
            "## Recommended actions",
            *action_lines,
            "",
        ]
        return "\n".join(sections) + "\n"
