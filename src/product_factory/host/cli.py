"""CLI group: `product-factory host` (machine JSON protocol)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import typer
import yaml

from product_factory.config.loader import PoliciesConfig, load_config
from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.budgets import run_budget_from_policy
from product_factory.domain.errors import ProductFactoryError
from product_factory.domain.runs import ArtifactOverride, RunRequest
from product_factory.host.protocol import HostResponse
from product_factory.host.registry import get_host_service
from product_factory.host.service import HostService
from product_factory.host_mcp.factory import resolve_mcp_config_root
from product_factory.workflows.inputs import parse_pack_input_option

host_app = typer.Typer(
    name="host",
    help=(
        "Machine host protocol (product-factory.host/v1 compatibility; "
        "prefer host/v2 via /api/v2). JSON output by default."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _parse_validation_commands(
    validation_command: list[str], validation_commands: str | None
) -> list[str]:
    ids = list(validation_command)
    if validation_commands:
        ids.extend(v.strip() for v in validation_commands.split(",") if v.strip())
    seen: set[str] = set()
    result: list[str] = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            result.append(cid)
    return result


def _parse_artifact_overrides(
    artifact_override: list[str], artifact_name: list[str]
) -> dict[str, ArtifactOverride]:
    """`--artifact-override ROLE=docs/x.md` / `--artifact-name ROLE=x.md`."""
    overrides: dict[str, ArtifactOverride] = {}

    def _split(entry: str, flag: str) -> tuple[str, str]:
        role, sep, value = entry.partition("=")
        if not sep or not role.strip() or not value.strip():
            raise typer.BadParameter(f"Expected {flag} ROLE=VALUE, got {entry!r}")
        return role.strip(), value.strip()

    for entry in artifact_override:
        role, dest = _split(entry, "--artifact-override")
        current = overrides.get(role) or ArtifactOverride()
        overrides[role] = current.model_copy(update={"dest_path": dest})
    for entry in artifact_name:
        role, logical = _split(entry, "--artifact-name")
        current = overrides.get(role) or ArtifactOverride()
        overrides[role] = current.model_copy(update={"logical_name": logical})
    return overrides


def _load_config_with_policy_override(policy: Path | None):
    # Same cwd-independent resolution as MCP so OpenCode plugin hosts can set
    # PRODUCT_FACTORY_ROOT when the project cwd is not the PF checkout.
    config = load_config(resolve_mcp_config_root())
    if policy is not None:
        raw = yaml.safe_load(policy.read_text(encoding="utf-8")) or {}
        config = config.model_copy(update={"policies": PoliciesConfig.model_validate(raw)})
    return config


def _emit(response: HostResponse, *, exit_on_error: bool = True) -> None:
    sys.stdout.write(response.model_dump_json() + "\n")
    sys.stdout.flush()
    if exit_on_error and not response.ok:
        raise typer.Exit(1)


def _service(
    *,
    mock: bool = False,
    policy: Path | None = None,
    data_dir: Path | None = None,
) -> HostService:
    config = _load_config_with_policy_override(policy)
    if data_dir is None and os.environ.get("PRODUCT_FACTORY_DATA_DIR"):
        data_dir = Path(os.environ["PRODUCT_FACTORY_DATA_DIR"]).expanduser()
    force_mock = mock or bool(os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"))
    return get_host_service(
        config=config,
        data_dir=data_dir,
        force_mock=force_mock,
    )


@host_app.command("submit")
def host_submit_cmd(
    request: Path = typer.Option(..., "--request", exists=True, dir_okay=False),
    repo: Path | None = typer.Option(None, "--repo"),
    workflow: str = typer.Option("code_change", "--workflow"),
    profile: str = typer.Option("local-target", "--profile"),
    budget_usd: float = typer.Option(3.0, "--budget-usd"),
    max_wall_clock_seconds: int | None = typer.Option(None, "--max-wall-clock-seconds"),
    validation_command: list[str] = typer.Option([], "--validation-command"),
    validation_commands: str | None = typer.Option(None, "--validation-commands"),
    artifact_override: list[str] = typer.Option(
        [],
        "--artifact-override",
        help=(
            "Land a deliverable at a chosen repository path: "
            "ROLE=PATH (e.g. architecture_document=docs/integration_testing_architecture.md)"
        ),
    ),
    artifact_name: list[str] = typer.Option(
        [],
        "--artifact-name",
        help="Name a deliverable without changing its directory: ROLE=FILENAME",
    ),
    pack_input: str | None = typer.Option(
        None,
        "--pack-input",
        help="Typed pack payload as inline JSON or @file.json; validated against the pack",
    ),
    handoff_refs: str | None = typer.Option(
        None,
        "--handoff-refs",
        help="Handoff ref list as inline JSON array or @file.json",
    ),
    policy: Path | None = typer.Option(None, "--policy"),
    mock: bool = typer.Option(False, "--mock"),
    inline: bool = typer.Option(
        False,
        "--inline",
        help="Run worker in a daemon thread (tests); default spawns a subprocess",
    ),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Run worker in-process before returning (debug only)",
    ),
) -> None:
    """Submit a curated request and return run_id + subscription immediately."""
    try:
        pack_input_payload = parse_pack_input_option(pack_input)
    except ProductFactoryError as exc:
        _emit(
            HostResponse.failure(
                code="invalid_pack_input",
                message=exc.message,
                details=exc.details,
            )
        )
        return
    handoff_payload: list[HandoffRef] = []
    if handoff_refs:
        import json as _json
        from pathlib import Path as _Path

        text = handoff_refs.strip()
        if text.startswith("@"):
            path = _Path(text[1:]).expanduser()
            if not path.is_file():
                _emit(
                    HostResponse.failure(
                        code="invalid_handoff",
                        message=f"handoff_refs file not found: {path}",
                    )
                )
                return
            text = path.read_text(encoding="utf-8")
        try:
            parsed = _json.loads(text)
        except Exception as exc:
            _emit(
                HostResponse.failure(
                    code="invalid_handoff",
                    message=f"handoff_refs is not valid JSON: {exc}",
                )
            )
            return
        if not isinstance(parsed, list):
            _emit(
                HostResponse.failure(
                    code="invalid_handoff",
                    message="handoff_refs must be a JSON array",
                )
            )
            return
        try:
            handoff_payload = [HandoffRef.model_validate(item) for item in parsed]
        except Exception as exc:
            _emit(
                HostResponse.failure(
                    code="invalid_handoff",
                    message=f"Invalid handoff_refs: {exc}",
                )
            )
            return
    service = _service(mock=mock, policy=policy)
    run_request = RunRequest(
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        workflow_type=workflow,  # type: ignore[arg-type]
        request_text=request.read_text(encoding="utf-8"),
        repository_path=repo.resolve() if repo else None,
        model_profile_set=profile,
        validation_commands=_parse_validation_commands(validation_command, validation_commands),
        artifact_overrides=_parse_artifact_overrides(artifact_override, artifact_name),
        pack_input=pack_input_payload,
        handoff_refs=handoff_payload,
        budget=run_budget_from_policy(
            max_cost_usd=Decimal(str(budget_usd)),
            budgets=service.config.policies.budgets,
            max_wall_clock_seconds=max_wall_clock_seconds,
        ),
    )
    response = service.submit(
        run_request,
        mock=mock,
        detach=not inline and not sync,
        inline_thread=inline and not sync,
    )
    _emit(response)


@host_app.command("worker")
def host_worker_cmd(
    run_id: str = typer.Option(..., "--run-id"),
    mock: bool = typer.Option(False, "--mock"),
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Override .product-factory data root"
    ),
) -> None:
    """Internal: execute a queued host submit (spawned by submit)."""
    from product_factory.domain.errors import RunCancelledError

    service = _service(mock=mock, data_dir=data_dir)
    try:
        manifest = service.run_worker(run_id)
    except RunCancelledError as exc:
        _emit(
            HostResponse.success(
                run_id=run_id,
                status="cancelled",
                data={"cancelled": True, "message": exc.message},
            )
        )
        return
    except ProductFactoryError as exc:
        _emit(
            HostResponse.failure(
                code=exc.__class__.__name__,
                message=exc.message,
                run_id=run_id,
                details=exc.details,
            )
        )
        return
    _emit(
        HostResponse.success(
            run_id=manifest.run_id,
            status=manifest.final_status,
            data={"usage": json.loads(manifest.usage.model_dump_json())},
        )
    )


@host_app.command("status")
def host_status_cmd(run_id: str = typer.Argument(...)) -> None:
    _emit(_service().status(run_id))


@host_app.command("inspect")
def host_inspect_cmd(run_id: str = typer.Argument(...)) -> None:
    _emit(_service().inspect(run_id))


@host_app.command("artifacts")
def host_artifacts_cmd(run_id: str = typer.Argument(...)) -> None:
    _emit(_service().artifacts(run_id))


@host_app.command("approve")
def host_approve_cmd(
    run_id: str = typer.Argument(...),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    _emit(_service().approve(run_id, apply=apply))


@host_app.command("reject")
def host_reject_cmd(run_id: str = typer.Argument(...)) -> None:
    _emit(_service().reject(run_id))


@host_app.command("cancel")
def host_cancel_cmd(run_id: str = typer.Argument(...)) -> None:
    _emit(_service().cancel(run_id))


@host_app.command("revise")
def host_revise_cmd(
    run_id: str = typer.Argument(...),
    note: str = typer.Option("", "--note"),
) -> None:
    _emit(_service().revise(run_id, note=note))


@host_app.command("export-bundle")
def host_export_bundle_cmd(run_id: str = typer.Argument(...)) -> None:
    _emit(_service().export_bundle(run_id))


@host_app.command("materialize")
def host_materialize_cmd(
    run_id: str = typer.Argument(...),
    artifact: str = typer.Option(
        ...,
        "--artifact",
        help="Logical artifact name (e.g. ARCHITECTURE.md) or sha256",
    ),
    to_path: str = typer.Option(
        ...,
        "--to",
        help="Destination path under the run repository_path",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace an existing destination file",
    ),
) -> None:
    """Land a run artifact into the target repository (host-mediated copy)."""
    _emit(
        _service().materialize(
            run_id,
            artifact=artifact,
            dest_path=to_path,
            overwrite=overwrite,
        )
    )


@host_app.command("materialize-all")
def host_materialize_all_cmd(
    run_id: str = typer.Argument(...),
    role: list[str] = typer.Option(
        [],
        "--role",
        help="Limit to these deliverable roles (default: every landable role)",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace existing destination files",
    ),
) -> None:
    """Land every resolved deliverable of a run at its suggested destination."""
    _emit(
        _service().materialize_all(
            run_id,
            roles=list(role) or None,
            overwrite=overwrite,
        )
    )


def _tail_cmd(
    run_id: str,
    *,
    after_seq: int,
    follow: bool,
    once: bool,
) -> None:
    service = _service()
    for batch in service.tail(
        run_id,
        after_seq=after_seq,
        follow=follow and not once,
        max_idle_polls=1 if once else None,
        stop_when_terminal=True,
    ):
        _emit(batch, exit_on_error=False)
        if once:
            break
        if batch.events:
            # Keep streaming; when terminal idle heartbeat arrives, iterator stops.
            continue


@host_app.command("tail")
def host_tail_cmd(
    run_id: str = typer.Argument(...),
    after_seq: int = typer.Option(0, "--after-seq"),
    follow: bool = typer.Option(True, "--follow/--no-follow"),
    once: bool = typer.Option(False, "--once", help="Emit one batch and exit"),
) -> None:
    """Stream events by after_seq; uses observe HTTP then durable SQLite."""
    _tail_cmd(run_id, after_seq=after_seq, follow=follow, once=once)


@host_app.command("attach")
def host_attach_cmd(
    run_id: str = typer.Argument(...),
    after_seq: int = typer.Option(0, "--after-seq"),
    follow: bool = typer.Option(True, "--follow/--no-follow"),
    once: bool = typer.Option(False, "--once"),
) -> None:
    """Alias for `host tail`."""
    _tail_cmd(run_id, after_seq=after_seq, follow=follow, once=once)
