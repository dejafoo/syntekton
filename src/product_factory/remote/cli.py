"""CLI group: `product-factory remote` (HTTPS host/v2 client; v1 explicit)."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

import typer

from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.errors import ProductFactoryError
from product_factory.host.protocol import HostResponse
from product_factory.host.protocol_v2 import HostResponseV2
from product_factory.remote.client import (
    PfProtocolError,
    PfRemoteError,
    ProtocolChoice,
    RemotePfClient,
)
from product_factory.workflows.inputs import parse_pack_input_option

remote_app = typer.Typer(
    name="remote",
    help=(
        "Remote HTTPS host protocol client. Defaults to product-factory.host/v2; "
        "pass --protocol v1 for the explicit compatibility adapter."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _emit(response: HostResponse | HostResponseV2, *, exit_on_error: bool = True) -> None:
    sys.stdout.write(response.model_dump_json() + "\n")
    sys.stdout.flush()
    if exit_on_error and not response.ok:
        raise typer.Exit(1)


def _client(
    *,
    remote_url: str | None,
    token: str | None,
    protocol: ProtocolChoice = "v2",
) -> RemotePfClient:
    try:
        return RemotePfClient(base_url=remote_url, token=token, protocol=protocol)
    except PfRemoteError as exc:
        _emit(HostResponse.failure(code="remote_config", message=str(exc)))
        raise typer.Exit(1) from exc


def _protocol_opt() -> Any:
    return typer.Option(
        "v2",
        "--protocol",
        help="host protocol: v2 (default), v1 (explicit compatibility), or auto (negotiate)",
    )


def _parse_handoff_refs(handoff_refs: str | None) -> list[HandoffRef]:
    if not handoff_refs:
        return []
    text = handoff_refs.strip()
    if text.startswith("@"):
        path = Path(text[1:]).expanduser()
        if not path.is_file():
            raise ProductFactoryError(f"handoff_refs file not found: {path}")
        text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise ProductFactoryError(f"handoff_refs is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ProductFactoryError("handoff_refs must be a JSON array")
    return [HandoffRef.model_validate(item) for item in parsed]


@remote_app.command("submit")
def remote_submit_cmd(
    request: Path = typer.Option(..., "--request", exists=True, dir_okay=False),
    workflow: str = typer.Option("code_change", "--workflow"),
    repository_id: str | None = typer.Option(None, "--repository-id"),
    profile: str = typer.Option("local-target", "--profile"),
    budget_usd: float = typer.Option(3.0, "--budget-usd"),
    pack_input: str | None = typer.Option(None, "--pack-input"),
    handoff_refs: str | None = typer.Option(None, "--handoff-refs"),
    mock: bool = typer.Option(False, "--mock"),
    sync: bool = typer.Option(False, "--sync"),
    protocol: Literal["auto", "v2", "v1"] = _protocol_opt(),
    remote_url: str | None = typer.Option(
        None, "--remote-url", help="Override PRODUCT_FACTORY_REMOTE_URL"
    ),
    token: str | None = typer.Option(None, "--token", help="Override OBSERVE_TOKEN / HOST_TOKEN"),
) -> None:
    """Submit a curated request to a remote Product Factory host."""
    try:
        pack_input_payload = parse_pack_input_option(pack_input)
        handoff_payload = _parse_handoff_refs(handoff_refs)
    except ProductFactoryError as exc:
        _emit(
            HostResponse.failure(
                code="invalid_input",
                message=exc.message,
                details=exc.details,
            )
        )
        return
    # mock/sync/profile/handoff_refs are v1 compatibility fields; refuse silent
    # downgrade when the operator asked for v2.
    if protocol == "v2" and (mock or sync or handoff_refs or profile != "local-target"):
        _emit(
            HostResponse.failure(
                code="v1_fields_on_v2",
                message=(
                    "host/v2 rejects --mock/--sync/--profile/--handoff-refs. "
                    "Pass --protocol v1 for the compatibility adapter, or omit "
                    "those flags (debug execution is server-config only on v2)."
                ),
            )
        )
        return
    with _client(remote_url=remote_url, token=token, protocol=protocol) as client:
        try:
            response = client.submit(
                request_text=request.read_text(encoding="utf-8"),
                workflow_type=workflow,
                repository_id=repository_id,
                model_profile_set=profile if protocol == "v1" else None,
                pack_input=pack_input_payload,
                handoff_refs=(
                    [ref.model_dump(mode="json") for ref in handoff_payload]
                    if protocol == "v1"
                    else None
                ),
                budget_usd=budget_usd,
                request_id=f"req-{uuid.uuid4().hex[:8]}",
                mock=mock if protocol == "v1" else False,
                sync=sync if protocol == "v1" else False,
            )
        except (PfRemoteError, PfProtocolError) as exc:
            _emit(HostResponse.failure(code=exc.__class__.__name__, message=str(exc)))
            return
    _emit(response)


@remote_app.command("status")
def remote_status_cmd(
    run_id: str = typer.Argument(...),
    protocol: Literal["auto", "v2", "v1"] = _protocol_opt(),
    remote_url: str | None = typer.Option(None, "--remote-url"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    with _client(remote_url=remote_url, token=token, protocol=protocol) as client:
        try:
            response = client.status(run_id)
        except (PfRemoteError, PfProtocolError) as exc:
            _emit(HostResponse.failure(code=exc.__class__.__name__, message=str(exc)))
            return
    _emit(response)


@remote_app.command("inspect")
def remote_inspect_cmd(
    run_id: str = typer.Argument(...),
    protocol: Literal["auto", "v2", "v1"] = _protocol_opt(),
    remote_url: str | None = typer.Option(None, "--remote-url"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    with _client(remote_url=remote_url, token=token, protocol=protocol) as client:
        try:
            response = client.inspect(run_id)
        except (PfRemoteError, PfProtocolError) as exc:
            _emit(HostResponse.failure(code=exc.__class__.__name__, message=str(exc)))
            return
    _emit(response)


@remote_app.command("wait")
def remote_wait_cmd(
    run_id: str = typer.Argument(...),
    after_seq: int = typer.Option(0, "--after-seq"),
    timeout: float = typer.Option(600.0, "--timeout"),
    protocol: Literal["auto", "v2", "v1"] = _protocol_opt(),
    remote_url: str | None = typer.Option(None, "--remote-url"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    with _client(remote_url=remote_url, token=token, protocol=protocol) as client:
        try:
            response = client.wait(run_id, after_seq=after_seq, timeout=timeout)
        except (PfRemoteError, PfProtocolError) as exc:
            _emit(HostResponse.failure(code=exc.__class__.__name__, message=str(exc)))
            return
    _emit(response)


@remote_app.command("reject")
def remote_reject_cmd(
    run_id: str = typer.Argument(...),
    protocol: Literal["auto", "v2", "v1"] = _protocol_opt(),
    remote_url: str | None = typer.Option(None, "--remote-url"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    with _client(remote_url=remote_url, token=token, protocol=protocol) as client:
        try:
            response = client.reject(run_id)
        except (PfRemoteError, PfProtocolError) as exc:
            _emit(HostResponse.failure(code=exc.__class__.__name__, message=str(exc)))
            return
    _emit(response)


@remote_app.command("cancel")
def remote_cancel_cmd(
    run_id: str = typer.Argument(...),
    protocol: Literal["auto", "v2", "v1"] = _protocol_opt(),
    remote_url: str | None = typer.Option(None, "--remote-url"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    with _client(remote_url=remote_url, token=token, protocol=protocol) as client:
        try:
            response = client.cancel(run_id)
        except (PfRemoteError, PfProtocolError) as exc:
            _emit(HostResponse.failure(code=exc.__class__.__name__, message=str(exc)))
            return
    _emit(response)


@remote_app.command("approve")
def remote_approve_cmd(
    run_id: str = typer.Argument(...),
    apply: bool = typer.Option(False, "--apply"),
    protocol: Literal["auto", "v2", "v1"] = _protocol_opt(),
    remote_url: str | None = typer.Option(None, "--remote-url"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    with _client(remote_url=remote_url, token=token, protocol=protocol) as client:
        try:
            response = client.approve(run_id, apply=apply)
        except (PfRemoteError, PfProtocolError) as exc:
            _emit(HostResponse.failure(code=exc.__class__.__name__, message=str(exc)))
            return
    _emit(response)
