"""Architecture compose uses profile output limits and recovers from truncation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget, TaskBudget
from product_factory.domain.errors import RuntimeFailureError
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.canonical_messages import ModelRequest, ModelResponse
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import (
    RunCoordinator,
    append_markdown_continuation,
    output_was_truncated,
)


def test_output_was_truncated_by_finish_reason() -> None:
    assert output_was_truncated(
        finish_reason="length",
        output_tokens=100,
        max_output_tokens=8_000,
    )
    assert output_was_truncated(
        finish_reason="max_tokens",
        output_tokens=1,
        max_output_tokens=8_000,
    )
    assert not output_was_truncated(
        finish_reason="stop",
        output_tokens=100,
        max_output_tokens=8_000,
    )


def test_output_was_truncated_by_token_cap() -> None:
    assert output_was_truncated(
        finish_reason="stop",
        output_tokens=12_000,
        max_output_tokens=12_000,
    )
    assert output_was_truncated(
        finish_reason=None,
        output_tokens=8_000,
        max_output_tokens=8_000,
    )
    assert not output_was_truncated(
        finish_reason=None,
        output_tokens=7_999,
        max_output_tokens=8_000,
    )


def test_append_markdown_continuation() -> None:
    assert (
        append_markdown_continuation(
            "https://www.sqlite.org/docs.html (",
            "official docs).",
        )
        == "https://www.sqlite.org/docs.html (official docs)."
    )
    assert append_markdown_continuation("## A\n", "more") == "## A\nmore"
    assert append_markdown_continuation("Done.", "## B") == "Done.\n## B"
    assert append_markdown_continuation("## A\n", "  ") == "## A\n"


def _task() -> TaskSpec:
    return TaskSpec(
        id="task_composition",
        title="Compose",
        capability="composition",
        objective="Write the architecture document",
        expected_output_schema="composition_result.v1",
        acceptance_criteria=[
            AcceptanceCriterion(id="ac", description="doc", verification="artifact_check")
        ],
        budget=TaskBudget(
            max_input_tokens=8_000,
            max_output_tokens=8_000,
            max_tool_calls=5,
            max_repair_attempts=1,
            max_wall_clock_seconds=120,
        ),
    )


def _request() -> RunRequest:
    return RunRequest(
        request_id="req-compose",
        workflow_type="technical_plan",
        request_text="Design local lesson storage with citations.",
        budget=RunBudget(max_cost_usd=Decimal("2.00")),
        approval_policy="none",
    )


def _response(
    request: ModelRequest,
    *,
    text: str,
    finish_reason: str,
    output_tokens: int,
) -> ModelResponse:
    return ModelResponse(
        request_id=request.request_id,
        provider="mock",
        provider_model_id="mock/local-model",
        resolved_model_id="mock/local-model",
        status="success",
        text=text,
        usage=UsageMetrics(
            input_tokens=10,
            output_tokens=output_tokens,
            estimated_cost_usd=Decimal("0"),
        ),
        finish_reason=finish_reason,
    )


def test_generate_architecture_uses_profile_max_output_tokens(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    assert config.models.profiles["supervisor"].max_output_tokens == 12_000

    captured: list[ModelRequest] = []

    def responder(request: ModelRequest) -> ModelResponse:
        captured.append(request)
        return _response(
            request,
            text="# ARCHITECTURE.md\n\n## Objective\nDone.\n",
            finish_reason="stop",
            output_tokens=20,
        )

    coord = RunCoordinator(
        config=config,
        gateway=MockGateway(responder=responder),
        data_dir=tmp_path / ".product-factory",
    )
    text, usage = coord._generate_architecture_document(
        request=_request(),
        task=_task(),
        ctx_messages=[],
        run_id="run-test",
        profile="supervisor",
        dependency_outputs=[],
    )
    assert text.startswith("# ARCHITECTURE.md")
    assert usage.output_tokens == 20
    assert len(captured) == 1
    assert captured[0].max_output_tokens == 12_000


def test_generate_architecture_continues_after_length_truncation(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    calls: list[ModelRequest] = []

    def responder(request: ModelRequest) -> ModelResponse:
        calls.append(request)
        if len(calls) == 1:
            return _response(
                request,
                text="# ARCHITECTURE.md\n\n### Citations\n- SQLite: https://www.sqlite.org/docs.html (",
                finish_reason="length",
                output_tokens=request.max_output_tokens,
            )
        return _response(
            request,
            text="official docs).\n- DuckDB: https://duckdb.org/docs/",
            finish_reason="stop",
            output_tokens=40,
        )

    coord = RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(responder=responder),
        data_dir=tmp_path / ".product-factory",
    )
    text, usage = coord._generate_architecture_document(
        request=_request(),
        task=_task(),
        ctx_messages=[],
        run_id="run-cont",
        profile="supervisor",
        dependency_outputs=[],
    )
    assert len(calls) == 2
    assert calls[0].max_output_tokens == 12_000
    assert calls[1].request_id.startswith("arch-cont-")
    assert "https://www.sqlite.org/docs.html (official docs)." in text
    assert "https://duckdb.org/docs/" in text
    assert usage.output_tokens == 12_000 + 40


def test_generate_architecture_fails_after_exhausted_continuations(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]

    def responder(request: ModelRequest) -> ModelResponse:
        return _response(
            request,
            text="truncated draft",
            finish_reason="length",
            output_tokens=request.max_output_tokens,
        )

    coord = RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(responder=responder),
        data_dir=tmp_path / ".product-factory",
    )
    with pytest.raises(RuntimeFailureError, match="truncated after"):
        coord._generate_architecture_document(
            request=_request(),
            task=_task(),
            ctx_messages=[],
            run_id="run-fail",
            profile="supervisor",
            dependency_outputs=[],
        )
