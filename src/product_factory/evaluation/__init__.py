"""Evaluation package exports."""

from product_factory.evaluation.bench import BenchmarkRunner, build_judge
from product_factory.evaluation.cases import (
    SD6_CORPUS_CATEGORIES,
    SD6_FOUNDATION_CASE_IDS,
    EvalCase,
)
from product_factory.evaluation.compare import ComparisonReport
from product_factory.evaluation.corpus import (
    CorpusSnapshot,
    Sd6CorpusCatalog,
    build_corpus_snapshot,
    build_sd6_corpus_catalog,
)
from product_factory.evaluation.deterministic import EvaluationScore, merge_scores
from product_factory.evaluation.experiments import (
    HARNESS_SCHEMA_VERSION,
    SCORECARD_SCHEMA_VERSION,
    ExperimentManifest,
    ExperimentRegistry,
    HarnessManifest,
    ScorecardMetrics,
    ScorecardRecord,
    build_deferred_promotion_record,
)
from product_factory.evaluation.gates import GateResult, GateThresholds, evaluate_regression_gate
from product_factory.evaluation.judge import JudgeVerdict, LLMJudge, MockJudge
from product_factory.evaluation.lessons import LessonCandidate
from product_factory.evaluation.loader import load_eval_cases
from product_factory.evaluation.promotion import (
    COMPARISON_ARMS,
    ArmMetrics,
    PromotionGateResult,
    PromotionRecord,
    RollbackRecord,
    Sd6PromotionConfig,
    evaluate_local_first_promotion,
    evaluate_skill_promotion,
    load_sd6_promotion_config,
)
from product_factory.evaluation.runner import run_evaluation

__all__ = [
    "COMPARISON_ARMS",
    "HARNESS_SCHEMA_VERSION",
    "SCORECARD_SCHEMA_VERSION",
    "SD6_CORPUS_CATEGORIES",
    "SD6_FOUNDATION_CASE_IDS",
    "ArmMetrics",
    "BenchmarkRunner",
    "ComparisonReport",
    "CorpusSnapshot",
    "EvalCase",
    "EvaluationScore",
    "ExperimentManifest",
    "ExperimentRegistry",
    "GateResult",
    "GateThresholds",
    "HarnessManifest",
    "JudgeVerdict",
    "LLMJudge",
    "LessonCandidate",
    "MockJudge",
    "PromotionGateResult",
    "PromotionRecord",
    "RollbackRecord",
    "Sd6CorpusCatalog",
    "Sd6PromotionConfig",
    "ScorecardMetrics",
    "ScorecardRecord",
    "build_corpus_snapshot",
    "build_deferred_promotion_record",
    "build_judge",
    "build_sd6_corpus_catalog",
    "evaluate_local_first_promotion",
    "evaluate_regression_gate",
    "evaluate_skill_promotion",
    "load_eval_cases",
    "load_sd6_promotion_config",
    "merge_scores",
    "run_evaluation",
]
