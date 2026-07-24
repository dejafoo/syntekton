"""Unit tests for product-factory.host/v1 envelope."""

from __future__ import annotations

import json

from product_factory.host.protocol import (
    HOST_PROTOCOL,
    HostError,
    HostResponse,
    HostSubscription,
)


def test_host_response_success_envelope_fields() -> None:
    response = HostResponse.success(
        run_id="run-abc",
        status="queued",
        subscription=HostSubscription(
            sse_url="http://127.0.0.1:8765/api/v1/runs/run-abc/events/stream?after_seq=0",
            cli_tail="product-factory host tail run-abc",
        ),
        plan_summary={"objective": "demo", "task_count": 2},
        artifacts=[{"logical_name": "plan.json"}],
        events=[{"seq": 1, "type": "run.started"}],
        data={"request_id": "req-1"},
    )
    payload = json.loads(response.model_dump_json())
    assert payload["protocol"] == HOST_PROTOCOL == "product-factory.host/v1"
    assert payload["ok"] is True
    assert payload["run_id"] == "run-abc"
    assert payload["status"] == "queued"
    assert payload["subscription"]["cli_tail"].endswith("run-abc")
    assert payload["error"] is None
    # Round-trip validates schema
    restored = HostResponse.model_validate(payload)
    assert restored.subscription is not None
    assert restored.plan_summary is not None


def test_host_response_failure_envelope() -> None:
    response = HostResponse.failure(
        code="not_found",
        message="Unknown run",
        run_id="run-missing",
        details={"hint": "submit first"},
    )
    payload = json.loads(response.model_dump_json())
    assert payload["ok"] is False
    assert payload["protocol"] == HOST_PROTOCOL
    assert payload["error"] == {
        "code": "not_found",
        "message": "Unknown run",
        "details": {"hint": "submit first"},
    }
    HostError.model_validate(payload["error"])
