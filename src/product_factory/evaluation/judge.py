"""LLM judge contracts and implementations."""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from product_factory.domain.usage import UsageMetrics
from product_factory.evaluation.cases import RUBRIC_DIMENSIONS, EvalCase
from product_factory.evaluation.subjects import SubjectArtifact
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest


class DimensionScore(BaseModel):
    name: str
    score: int = Field(ge=1, le=5)
    rationale: str = ""
    uncertain: bool = False


class JudgeVerdict(BaseModel):
    model_config = {"extra": "forbid"}

    overall: int = Field(ge=1, le=5)
    dimensions: list[DimensionScore]
    summary: str = ""
    evidence_notes: list[str] = Field(default_factory=list)
    pairwise_preference: Literal["a", "b", "tie"] | None = None
    uncertain: bool = False


class JudgeResult(BaseModel):
    verdict: JudgeVerdict
    model_profile: str
    provider: str = ""
    prompt_hash: str = ""
    response_hash: str = ""
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    is_mock: bool = False


class PairwiseVerdict(BaseModel):
    model_config = {"extra": "forbid"}

    preference: Literal["a", "b", "tie"]
    rationale: str
    uncertain: bool = False


JUDGE_SYSTEM = """You are an independent evaluation judge for software engineering artifacts.
Score each rubric dimension from 1–5:
1 Incorrect/unusable, 2 Major changes required, 3 Usable with moderate corrections,
4 Strong minor corrections only, 5 Ready for intended MVP purpose.
Dimensions: correctness, completeness, maintainability, architectural_quality,
security_awareness, test_quality, evidence_quality, scope_discipline.
For architecture artifacts: require request-specific design detail. Penalize
generic templates, empty section stubs, and missing must_cover / reference_hints
topics. Do not award usable overall scores (≥3) for boilerplate section shells.
Do not invent evidence. Mark uncertain=true when unsure.
Deterministic validation failures already reported must not be ignored.
Return ONLY JSON matching the provided schema.
"""


class Judge(ABC):
    @abstractmethod
    def score(
        self,
        *,
        case: EvalCase,
        artifact: SubjectArtifact,
        deterministic_summary: str,
    ) -> JudgeResult:
        raise NotImplementedError

    def pairwise(
        self,
        *,
        case: EvalCase,
        artifact_a: SubjectArtifact,
        artifact_b: SubjectArtifact,
        deterministic_summary: str,
        rng: random.Random | None = None,
    ) -> tuple[JudgeResult, dict[str, str]]:
        """Blind pairwise: returns verdict with preference in labeled space + label map."""
        rng = rng or random.Random()
        swap = rng.choice([True, False])
        left, right = (artifact_b, artifact_a) if swap else (artifact_a, artifact_b)
        label_map = {"a": left.subject_id, "b": right.subject_id}
        # Default implementation: score both and prefer higher overall.
        left_res = self.score(case=case, artifact=left, deterministic_summary=deterministic_summary)
        right_res = self.score(
            case=case, artifact=right, deterministic_summary=deterministic_summary
        )
        if left_res.verdict.overall > right_res.verdict.overall:
            pref: Literal["a", "b", "tie"] = "a"
        elif right_res.verdict.overall > left_res.verdict.overall:
            pref = "b"
        else:
            pref = "tie"
        merged = left_res.model_copy(deep=True)
        merged.verdict.pairwise_preference = pref
        merged.verdict.summary = (
            f"Pairwise {pref}: A={left_res.verdict.overall} B={right_res.verdict.overall}"
        )
        return merged, label_map


class MockJudge(Judge):
    """Deterministic judge for CI smoke tests."""

    def __init__(self, *, base_score: int = 4) -> None:
        self.base_score = max(1, min(5, base_score))

    def score(
        self,
        *,
        case: EvalCase,
        artifact: SubjectArtifact,
        deterministic_summary: str,
    ) -> JudgeResult:
        hard_fail = "FAIL" in deterministic_summary or artifact.error
        overall = 1 if hard_fail else self.base_score
        dims = [
            DimensionScore(
                name=d,
                score=overall,
                rationale="mock",
                uncertain=False,
            )
            for d in RUBRIC_DIMENSIONS
        ]
        # Weight architecture vs coding lightly via artifact kind
        if artifact.artifact_kind == "architecture" and not hard_fail:
            for dim in dims:
                if dim.name == "architectural_quality":
                    dim.score = min(5, overall + 0)
        verdict = JudgeVerdict(
            overall=overall,
            dimensions=dims,
            summary="MockJudge verdict",
            evidence_notes=[deterministic_summary[:200]],
            uncertain=False,
        )
        body = verdict.model_dump_json()
        return JudgeResult(
            verdict=verdict,
            model_profile="mock_judge",
            provider="mock",
            prompt_hash=hashlib.sha256(case.id.encode()).hexdigest(),
            response_hash=hashlib.sha256(body.encode()).hexdigest(),
            usage=UsageMetrics(estimated_cost_usd=Decimal("0")),
            is_mock=True,
        )


class LLMJudge(Judge):
    def __init__(
        self,
        gateway: ModelGateway,
        *,
        model_profile: str = "grok_judge",
        max_cost_usd: float | None = None,
    ) -> None:
        self.gateway = gateway
        self.model_profile = model_profile
        self.max_cost_usd = max_cost_usd

    def score(
        self,
        *,
        case: EvalCase,
        artifact: SubjectArtifact,
        deterministic_summary: str,
    ) -> JudgeResult:
        payload = {
            "case_id": case.id,
            "request": case.request,
            "acceptance_criteria": case.acceptance_criteria,
            "reference_hints": case.reference_hints,
            "must_cover": case.must_cover,
            "deterministic_validation": deterministic_summary,
            "artifact_kind": artifact.artifact_kind,
            "artifact_excerpt": artifact.artifact_text[:12_000],
            "changed_files": artifact.changed_files,
            "rubric_weights": case.rubric_weights,
        }
        schema = JudgeVerdict.model_json_schema()
        prompt = json.dumps(payload, indent=2, default=str)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        req = ModelRequest(
            request_id=f"judge-{uuid.uuid4().hex[:10]}",
            run_id=f"bench-{case.id}",
            task_id="judge",
            session_id=f"pf:bench:judge:{case.id}",
            model_profile=self.model_profile,
            messages=[
                CanonicalMessage(role="system", content=JUDGE_SYSTEM),
                CanonicalMessage(role="user", content=prompt),
            ],
            output_schema=schema,
            max_output_tokens=4000,
            temperature=0.0,
            seed=int(case.metadata.get("benchmark_seed", 0)),
            max_cost_usd=self.max_cost_usd,
        )
        resp = self.gateway.complete(req)
        if resp.structured_data:
            verdict = JudgeVerdict.model_validate(resp.structured_data)
        elif resp.text:
            verdict = JudgeVerdict.model_validate(json.loads(resp.text))
        else:
            verdict = JudgeVerdict(
                overall=1,
                dimensions=[
                    DimensionScore(name=d, score=1, rationale="judge failed", uncertain=True)
                    for d in RUBRIC_DIMENSIONS
                ],
                summary=f"Judge failed: {resp.status}",
                uncertain=True,
            )
        return JudgeResult(
            verdict=verdict,
            model_profile=self.model_profile,
            provider=resp.provider,
            prompt_hash=prompt_hash,
            response_hash=resp.response_hash,
            usage=resp.usage,
            is_mock=False,
        )

    def pairwise(
        self,
        *,
        case: EvalCase,
        artifact_a: SubjectArtifact,
        artifact_b: SubjectArtifact,
        deterministic_summary: str,
        rng: random.Random | None = None,
    ) -> tuple[JudgeResult, dict[str, str]]:
        rng = rng or random.Random()
        swap = rng.choice([True, False])
        left, right = (artifact_b, artifact_a) if swap else (artifact_a, artifact_b)
        label_map = {"a": left.subject_id, "b": right.subject_id}
        payload = {
            "request": case.request,
            "acceptance_criteria": case.acceptance_criteria,
            "deterministic_validation": deterministic_summary,
            "artifact_a": left.artifact_text[:12_000],
            "artifact_b": right.artifact_text[:12_000],
        }
        prompt = json.dumps(payload, indent=2, default=str)
        response = self.gateway.complete(
            ModelRequest(
                request_id=f"pairwise-{uuid.uuid4().hex[:10]}",
                run_id=f"bench-{case.id}",
                task_id="pairwise_judge",
                session_id=f"pf:bench:pairwise:{case.id}",
                model_profile=self.model_profile,
                messages=[
                    CanonicalMessage(
                        role="system",
                        content=(
                            "Blindly compare two software-engineering artifacts for the request. "
                            "Prefer the artifact that is more correct, complete, maintainable, "
                            "well-tested, and scoped. Do not infer author identity. Return JSON."
                        ),
                    ),
                    CanonicalMessage(role="user", content=prompt),
                ],
                output_schema=PairwiseVerdict.model_json_schema(),
                max_output_tokens=1200,
                temperature=0.0,
                seed=int(case.metadata.get("benchmark_seed", 0)),
                max_cost_usd=self.max_cost_usd,
            )
        )
        raw = response.structured_data or (json.loads(response.text) if response.text else None)
        verdict = (
            PairwiseVerdict.model_validate(raw)
            if raw is not None
            else PairwiseVerdict(
                preference="tie",
                rationale=f"Pairwise judge failed: {response.status}",
                uncertain=True,
            )
        )
        judge_verdict = JudgeVerdict(
            overall=3,
            dimensions=[
                DimensionScore(
                    name=name,
                    score=3,
                    rationale="Pairwise comparison only",
                    uncertain=verdict.uncertain,
                )
                for name in RUBRIC_DIMENSIONS
            ],
            summary=verdict.rationale,
            pairwise_preference=verdict.preference,
            uncertain=verdict.uncertain,
        )
        return (
            JudgeResult(
                verdict=judge_verdict,
                model_profile=self.model_profile,
                provider=response.provider,
                prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                response_hash=response.response_hash,
                usage=response.usage,
            ),
            label_map,
        )
