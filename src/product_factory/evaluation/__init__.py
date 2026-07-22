"""Evaluation package exports."""

from product_factory.evaluation.bench import BenchmarkRunner, build_judge
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.compare import ComparisonReport
from product_factory.evaluation.deterministic import EvaluationScore, merge_scores
from product_factory.evaluation.judge import JudgeVerdict, LLMJudge, MockJudge
from product_factory.evaluation.lessons import LessonCandidate
from product_factory.evaluation.loader import load_eval_cases
from product_factory.evaluation.runner import run_evaluation

__all__ = [
    "BenchmarkRunner",
    "ComparisonReport",
    "EvalCase",
    "EvaluationScore",
    "JudgeVerdict",
    "LLMJudge",
    "LessonCandidate",
    "MockJudge",
    "build_judge",
    "load_eval_cases",
    "merge_scores",
    "run_evaluation",
]
