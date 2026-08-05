"""Evaluation package exports."""

from product_factory.evaluation.bench import BenchmarkRunner, build_judge
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.compare import ComparisonReport
from product_factory.evaluation.corpus import CorpusSnapshot, build_corpus_snapshot
from product_factory.evaluation.deterministic import EvaluationScore, merge_scores
from product_factory.evaluation.experiments import (
    ExperimentManifest,
    ExperimentRegistry,
    ScorecardMetrics,
    ScorecardRecord,
)
from product_factory.evaluation.gates import GateResult, GateThresholds, evaluate_regression_gate
from product_factory.evaluation.judge import JudgeVerdict, LLMJudge, MockJudge
from product_factory.evaluation.lessons import LessonCandidate
from product_factory.evaluation.loader import load_eval_cases
from product_factory.evaluation.runner import run_evaluation

__all__ = [
    "BenchmarkRunner",
    "ComparisonReport",
    "CorpusSnapshot",
    "EvalCase",
    "EvaluationScore",
    "ExperimentManifest",
    "ExperimentRegistry",
    "GateResult",
    "GateThresholds",
    "JudgeVerdict",
    "LLMJudge",
    "LessonCandidate",
    "MockJudge",
    "ScorecardMetrics",
    "ScorecardRecord",
    "build_corpus_snapshot",
    "build_judge",
    "evaluate_regression_gate",
    "load_eval_cases",
    "merge_scores",
    "run_evaluation",
]
