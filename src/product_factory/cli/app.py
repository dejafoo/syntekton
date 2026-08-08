"""CLI application."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from product_factory import __version__
from product_factory.config.loader import PoliciesConfig, load_config
from product_factory.delivery import LandingAdapter, LandingError, LandingReceipt
from product_factory.domain import export_json_schemas
from product_factory.domain.budgets import run_budget_from_policy
from product_factory.domain.errors import ProductFactoryError
from product_factory.domain.runs import RunRequest
from product_factory.gateway.factory import gateway_from_config
from product_factory.gateway.mock import MockGateway
from product_factory.host.cli import host_app
from product_factory.host.registry import get_host_service
from product_factory.host.service import HostService
from product_factory.observability.logging import setup_logging
from product_factory.remote.cli import remote_app
from product_factory.workflows.inputs import parse_pack_input_option

app = typer.Typer(
    name="product-factory",
    help="Multi-agent product factory CLI",
    no_args_is_help=True,
    add_completion=False,
)
models_app = typer.Typer(help="Model catalogue commands")
app.add_typer(models_app, name="models")
bench_app = typer.Typer(help="LLM-judge benchmark commands")
app.add_typer(bench_app, name="bench")
lessons_app = typer.Typer(help="Human-gated lesson triage and promotion (ADR-007)")
app.add_typer(lessons_app, name="lessons")
observe_app = typer.Typer(help="Observability API commands")
app.add_typer(observe_app, name="observe")
ops_app = typer.Typer(
    help=(
        "Administrative database/backup/retention commands (NOT run semantics). "
        "Mutations go through HostService via run/host/MCP/HTTP — not ops."
    )
)
app.add_typer(ops_app, name="ops")
handoff_app = typer.Typer(help="Durable cross-run handoff operations (via HostService)")
app.add_typer(handoff_app, name="handoff")
app.add_typer(host_app, name="host")
app.add_typer(remote_app, name="remote")
console = Console()


def _gateway_from_config(config, *, force_mock: bool = False):
    return gateway_from_config(config, force_mock=force_mock)


def _local_host_service(*, mock: bool = False, policy: Path | None = None) -> HostService:
    """Sole application-service entry for local CLI mutations (SD4.A)."""
    config = _load_config_with_policy_override(policy)
    return get_host_service(config=config, force_mock=mock)


def _exit_code_for_host_error(code: str | None) -> int:
    """Map HostResponse error codes (exception class names) to CLI exit codes."""
    from product_factory.domain import errors as domain_errors

    if not code:
        return 4
    cls = getattr(domain_errors, code, None)
    if isinstance(cls, type) and issubclass(cls, ProductFactoryError):
        return int(cls.exit_code)
    return 4


def _print_host_failure(response) -> int:
    err = response.error
    message = err.message if err else "request failed"
    code = err.code if err else "error"
    console.print(f"[red]{code}:[/red] {message}")
    return _exit_code_for_host_error(code)


@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging()
    if verbose:
        console.print(f"[dim]product-factory {__version__}[/dim]")


@app.command("init")
def init_cmd(
    path: Path = typer.Argument(Path("."), help="Project directory"),
) -> None:
    """Initialize .product-factory layout and copy default configs."""
    root = path.resolve()
    pf = root / ".product-factory"
    for sub in ("config", "skills", "data", "runs"):
        (pf / sub).mkdir(parents=True, exist_ok=True)
    src_config = Path(__file__).resolve().parents[3] / "config"
    # When installed editable, config lives at project root.
    project_config = root / "config"
    if not project_config.exists() and src_config.exists():
        shutil.copytree(src_config, project_config)
    elif project_config.exists():
        for name in ("models.yaml", "policies.yaml", "workflows.yaml"):
            target = pf / "config" / name
            if not target.exists() and (project_config / name).exists():
                shutil.copy(project_config / name, target)
    schemas = export_json_schemas(pf / "schemas")
    console.print(f"Initialized {pf}")
    console.print(f"Exported {len(schemas)} JSON schemas")


@app.command("doctor")
def doctor_cmd() -> None:
    """Validate environment and configuration."""
    try:
        config = load_config()
        console.print(f"[green]Config OK[/green]: {config.root}")
        console.print(f"Profiles: {', '.join(config.models.profiles)}")
    except ProductFactoryError as exc:
        console.print(f"[red]Config error:[/red] {exc.message}")
        raise typer.Exit(exc.exit_code) from exc
    import shutil as sh

    if not sh.which("git"):
        console.print("[red]git not found on PATH[/red]")
        raise typer.Exit(2)
    console.print("[green]git OK[/green]")
    console.print(f"Python package version: {__version__}")


@models_app.command("list")
def models_list() -> None:
    config = load_config()
    table = Table("Profile", "Adapter", "Model")
    for name, profile in config.models.profiles.items():
        table.add_row(name, profile.provider_adapter, profile.model)
    console.print(table)


@models_app.command("refresh")
def models_refresh() -> None:
    config = load_config()
    gateway = _gateway_from_config(config)
    payload = gateway.refresh_catalog()
    service = get_host_service(
        config=config, gateway=gateway, force_mock=isinstance(gateway, MockGateway)
    )
    service.coord.db.cache_model_catalog(payload)
    console.print(f"Refreshed {len(payload.get('models', []))} models")


@app.command("plan")
def plan_cmd(
    request: Path = typer.Option(..., "--request", exists=True),
    workflow: str = typer.Option("code_change", "--workflow"),
    repo: Path | None = typer.Option(None, "--repo"),
    mock: bool = typer.Option(False, "--mock"),
) -> None:
    """Generate and compile a plan without full execution."""
    text = request.read_text(encoding="utf-8")
    service = _local_host_service(mock=mock)
    response = service.plan_preview(text, workflow_type=workflow)
    if not response.ok:
        console.print(f"[red]{response.error.message if response.error else 'plan failed'}[/red]")
        raise typer.Exit(3)
    console.print_json(data=response.model_dump(mode="json"))
    compiler = (response.data or {}).get("compiler") or {}
    if not compiler.get("ok", True):
        raise typer.Exit(3)


def _parse_validation_commands(
    validation_command: list[str], validation_commands: str | None
) -> list[str]:
    """Merge repeatable `--validation-command` and comma-separated `--validation-commands`."""
    ids = list(validation_command)
    if validation_commands:
        ids.extend(v.strip() for v in validation_commands.split(",") if v.strip())
    # De-dupe, preserve order.
    seen: set[str] = set()
    result = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            result.append(cid)
    return result


def _load_config_with_policy_override(policy: Path | None):
    config = load_config()
    if policy is not None:
        raw = yaml.safe_load(policy.read_text(encoding="utf-8")) or {}
        config = config.model_copy(update={"policies": PoliciesConfig.model_validate(raw)})
    return config


@app.command("run")
def run_cmd(
    request: Path = typer.Option(..., "--request", exists=True, dir_okay=False),
    repo: Path | None = typer.Option(None, "--repo"),
    workflow: str = typer.Option("code_change", "--workflow"),
    profile: str = typer.Option("local-target", "--profile"),
    budget_usd: float = typer.Option(3.0, "--budget-usd"),
    max_wall_clock_seconds: int | None = typer.Option(
        None, "--max-wall-clock-seconds", help="Override RunBudget.max_wall_clock_seconds"
    ),
    validation_command: list[str] = typer.Option(
        [],
        "--validation-command",
        help="Registered command id to run as behavioral validation (repeatable)",
    ),
    validation_commands: str | None = typer.Option(
        None,
        "--validation-commands",
        help="Comma-separated registered command ids for behavioral validation",
    ),
    pack_input: str | None = typer.Option(
        None,
        "--pack-input",
        help="Typed pack payload as inline JSON or @file.json; validated against the pack",
    ),
    policy: Path | None = typer.Option(
        None, "--policy", help="Override policies.yaml path (registered_commands, etc.)"
    ),
    mock: bool = typer.Option(False, "--mock"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Execute a product-factory run through the shared HostService."""
    try:
        pack_input_payload = parse_pack_input_option(pack_input)
    except ProductFactoryError as exc:
        console.print(f"[red]{exc.__class__.__name__}:[/red] {exc.message}")
        raise typer.Exit(exc.exit_code) from exc
    service = _local_host_service(mock=mock, policy=policy)
    # profile retained for CLI compatibility; HostService/lifecycle emit deprecation.
    _ = profile
    budget_kwargs: dict[str, Any] = {}
    if max_wall_clock_seconds is not None:
        budget_kwargs["max_wall_clock_seconds"] = max_wall_clock_seconds
    run_request = RunRequest(
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        workflow_type=workflow,
        request_text=request.read_text(encoding="utf-8"),
        repository_path=repo.resolve() if repo else None,
        model_profile_set=profile,
        validation_commands=_parse_validation_commands(validation_command, validation_commands),
        pack_input=pack_input_payload,
        budget=run_budget_from_policy(
            max_cost_usd=Decimal(str(budget_usd)),
            budgets=service.config.policies.budgets,
            **budget_kwargs,
        ),
    )
    try:
        submitted = service.submit(run_request, mock=mock, detach=False, inline_thread=False)
    except ProductFactoryError as exc:
        console.print(f"[red]{exc.__class__.__name__}:[/red] {exc.message}")
        raise typer.Exit(exc.exit_code) from exc
    if not submitted.ok:
        raise typer.Exit(_print_host_failure(submitted))
    assert submitted.run_id is not None
    final = service.status(submitted.run_id)
    status = final.status or "unknown"
    usage = ((final.data or {}).get("usage") if final.data else None) or {}
    if json_out:
        console.print_json(
            data={
                "run_id": submitted.run_id,
                "final_status": status,
                "usage": usage,
            }
        )
    else:
        console.print(f"Run [bold]{submitted.run_id}[/bold] → {status}")
        if usage.get("estimated_cost_usd") is not None:
            console.print(f"Cost: ${usage['estimated_cost_usd']}")
    if status == "budget_exhausted":
        console.print("[red]BudgetExhaustedError:[/red] run exhausted its budget")
        raise typer.Exit(6)
    if status in {"failed", "plan_rejected", "blocked"}:
        raise typer.Exit(4)


@app.command("status")
def status_cmd(run_id: str | None = typer.Argument(None)) -> None:
    service = _local_host_service()
    if run_id:
        response = service.status(run_id)
        if not response.ok:
            console.print(f"Unknown run {run_id}")
            raise typer.Exit(1)
        console.print_json(data=response.model_dump(mode="json"))
    else:
        listed = service.list_runs()
        for row in (listed.data or {}).get("runs", []):
            console.print(f"{row['run_id']}\t{row['status']}\t{row['workflow_type']}")


@app.command("inspect")
def inspect_cmd(run_id: str = typer.Argument(...)) -> None:
    response = _local_host_service().inspect(run_id)
    if not response.ok:
        console.print("Manifest not found")
        raise typer.Exit(1)
    console.print_json(data=response.model_dump(mode="json"))


@app.command("resume")
def resume_cmd(
    run_id: str = typer.Argument(...),
    mock: bool = typer.Option(False, "--mock"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Resume an interrupted product-factory run via HostService."""
    service = _local_host_service(mock=mock)
    try:
        response = service.resume(run_id)
    except ProductFactoryError as exc:
        console.print(f"[red]{exc.__class__.__name__}:[/red] {exc.message}")
        raise typer.Exit(exc.exit_code) from exc
    if not response.ok:
        raise typer.Exit(_print_host_failure(response))
    if json_out:
        console.print_json(
            data={
                "run_id": response.run_id,
                "final_status": response.status,
                "usage": (response.data or {}).get("usage") or {},
            }
        )
    else:
        console.print(f"Run [bold]{response.run_id}[/bold] → {response.status}")
        usage = (response.data or {}).get("usage") or {}
        if usage.get("estimated_cost_usd") is not None:
            console.print(f"Cost: ${usage['estimated_cost_usd']}")
    if response.status == "budget_exhausted":
        raise typer.Exit(6)
    if response.status in {"failed", "plan_rejected", "blocked"}:
        raise typer.Exit(4)


@app.command("approve")
def approve_cmd(
    run_id: str = typer.Argument(...),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    response = _local_host_service().approve(run_id, apply=apply)
    if not response.ok:
        raise typer.Exit(_print_host_failure(response))
    console.print_json(
        data=(response.data or {}).get("approval") or response.model_dump(mode="json")
    )


@handoff_app.command("approve")
def handoff_approve_cmd(handoff_id: str = typer.Argument(...)) -> None:
    """Promote one evidence-complete handoff after explicit operator confirmation."""
    if not typer.confirm(f"Approve handoff {handoff_id}?"):
        raise typer.Abort()
    response = _local_host_service().approve_handoff(handoff_id, actor="local_cli_operator")
    if not response.ok:
        raise typer.Exit(_print_host_failure(response))
    console.print_json(
        data=(response.data or {}).get("handoff") or response.model_dump(mode="json")
    )


@handoff_app.command("supersede")
def handoff_supersede_cmd(
    handoff_id: str = typer.Argument(...),
    successor_handoff_id: str | None = typer.Option(None, "--successor"),
) -> None:
    """Terminally supersede an approved handoff after confirmation."""
    if not typer.confirm(f"Supersede handoff {handoff_id}?"):
        raise typer.Abort()
    response = _local_host_service().supersede_handoff(
        handoff_id,
        successor_handoff_id=successor_handoff_id,
        actor="local_cli_operator",
    )
    if not response.ok:
        raise typer.Exit(_print_host_failure(response))
    console.print_json(
        data=(response.data or {}).get("handoff") or response.model_dump(mode="json")
    )


@app.command("reject")
def reject_cmd(run_id: str = typer.Argument(...)) -> None:
    response = _local_host_service().reject(run_id)
    if not response.ok:
        raise typer.Exit(_print_host_failure(response))
    console.print_json(
        data=(response.data or {}).get("approval") or response.model_dump(mode="json")
    )


@app.command("apply")
def apply_cmd(run_id: str = typer.Argument(...)) -> None:
    response = _local_host_service().apply(run_id)
    if not response.ok:
        raise typer.Exit(_print_host_failure(response))
    console.print_json(data=(response.data or {}).get("apply") or response.model_dump(mode="json"))


@app.command("land")
def land_cmd(
    run_id: str = typer.Argument(..., help="Approved remote run id"),
    workspace: Path = typer.Option(Path("."), "--workspace", "-C"),
    remote_url: str | None = typer.Option(None, "--remote-url"),
    token: str | None = typer.Option(None, "--token"),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Fetch and hash-verify a remote delivery, then land it in a local Git workspace."""
    from product_factory.remote.client import PfRemoteError, RemotePfClient

    try:
        with RemotePfClient(base_url=remote_url, token=token) as client:
            manifest = client.delivery(run_id)
            result = LandingAdapter().land(
                manifest,
                workspace_root=workspace,
                blob_loader=lambda digest: client.delivery_blob(run_id, digest),
                overwrite=overwrite,
            )
            receipt = client.record_landing(
                run_id,
                LandingReceipt(
                    manifest_sha256=result.manifest_sha256,
                    base_revision=result.base_revision,
                    status="landed",
                    landed_paths=list(result.landed_paths),
                    client="product-factory-cli",
                ),
            )
    except (LandingError, PfRemoteError) as exc:
        console.print(f"[red]Landing failed:[/red] {exc}")
        raise typer.Exit(8) from exc
    console.print_json(
        data={
            "run_id": run_id,
            "landed_paths": list(result.landed_paths),
            "manifest_sha256": result.manifest_sha256,
            "receipt": receipt,
        }
    )


@app.command("eval")
def eval_cmd(
    cases_dir: Path = typer.Option(Path("tests/eval_cases"), "--cases"),
    config_name: str = typer.Option("multi_agent", "--config"),
    mock: bool = typer.Option(True, "--mock/--live"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    from product_factory.evaluation.runner import run_evaluation

    config = load_config()
    gateway = _gateway_from_config(config, force_mock=mock)
    report = run_evaluation(
        cases_dir=cases_dir,
        app_config=config,
        gateway=gateway,
        config_name=config_name,
        limit=limit,
        use_mock=mock,
    )
    console.print_json(data=report)


@bench_app.command("run")
def bench_run_cmd(
    cases_dir: Path = typer.Option(Path("tests/eval_cases"), "--cases"),
    suite: str = typer.Option("local", "--suite"),
    subjects: str = typer.Option(
        "full_orchestration,single_agent_baseline,agent_isolation",
        "--subjects",
        help="Comma-separated subject ids",
    ),
    judge_profile: str = typer.Option("grok_judge", "--judge"),
    limit: int = typer.Option(10, "--limit"),
    oracle_budget_usd: float = typer.Option(5.0, "--oracle-budget-usd"),
    mock: bool = typer.Option(True, "--mock/--live"),
    seeds: int = typer.Option(1, "--seeds", min=1, help="Independent runs per case/subject"),
    case_ids: str | None = typer.Option(None, "--case-ids", help="Comma-separated exact case ids"),
    resume: str | None = typer.Option(
        None,
        "--resume",
        help="Resume an interrupted bench id (skips already-scored case/subject pairs)",
    ),
    progress_log: Path | None = typer.Option(
        None,
        "--progress-log",
        help="Append-only progress log path (flushed after each subject)",
    ),
) -> None:
    """Run LLM-judge benchmark across subjects."""
    from product_factory.evaluation.bench import BenchmarkRunner, build_judge

    config = load_config()
    gateway = _gateway_from_config(config, force_mock=mock)
    judge = build_judge(gateway, judge_profile=judge_profile, force_mock=mock)
    runner = BenchmarkRunner(
        app_config=config,
        gateway=gateway,
        judge=judge,
        use_deterministic_planner=mock,
    )
    subject_list = [s.strip() for s in subjects.split(",") if s.strip()]
    report = runner.run(
        cases_dir=cases_dir,
        subjects=subject_list,
        limit=limit,
        suite=suite,
        oracle_budget_usd=Decimal(str(oracle_budget_usd)),
        resume_bench_id=resume,
        progress_log=progress_log,
        seeds=seeds,
        case_ids=[value.strip() for value in case_ids.split(",") if value.strip()]
        if case_ids
        else None,
    )
    console.print(f"Bench [bold]{report.bench_id}[/bold]")
    console.print(
        f"Cases={len(report.case_ids)} subjects={report.subjects} "
        f"oracle=${report.oracle_cost_usd} judge=${report.judge_cost_usd}"
    )
    console.print_json(data=json.loads(report.model_dump_json()))


@bench_app.command("compare")
def bench_compare_cmd(run_id: str = typer.Argument(..., help="Bench id")) -> None:
    from product_factory.evaluation.compare import report_to_markdown
    from product_factory.evaluation.store import EvalStore
    from product_factory.persistence.database import Database

    config = load_config()
    db = Database(config.root / ".product-factory" / "data" / "product_factory.sqlite")
    store = EvalStore(db)
    raw = store.get_bench(run_id)
    if not raw:
        console.print(f"[red]Unknown bench {run_id}[/red]")
        raise typer.Exit(1)
    from product_factory.evaluation.compare import ComparisonReport

    report = ComparisonReport.model_validate(raw)
    console.print(report_to_markdown(report))


@bench_app.command("lessons")
def bench_lessons_cmd(run_id: str = typer.Argument(..., help="Bench id")) -> None:
    """Dump raw lesson JSON for a bench (legacy). Prefer `product-factory lessons list`."""
    config = load_config()
    lesson_dir = config.root / ".product-factory" / "lessons" / "candidates" / run_id
    if not lesson_dir.exists():
        console.print(f"[yellow]No lessons for {run_id}[/yellow]")
        raise typer.Exit(0)
    index = lesson_dir / "index.jsonl"
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            if line.strip():
                console.print_json(data=json.loads(line))
    else:
        for path in sorted(lesson_dir.glob("lesson-*.json")):
            console.print_json(data=json.loads(path.read_text(encoding="utf-8")))


@lessons_app.command("list")
def lessons_list_cmd(
    bench: str = typer.Option(..., "--bench", help="Bench id"),
    orch_only: bool = typer.Option(
        True, "--orch-only/--all", help="Default: actionable orch subjects"
    ),
    status: str | None = typer.Option(None, "--status", help="proposed|accepted|rejected|promoted"),
    theme: str | None = typer.Option(None, "--theme"),
) -> None:
    from product_factory.evaluation.lessons import list_lessons

    config = load_config()
    pf_root = config.root / ".product-factory"
    lessons = list_lessons(
        pf_root,
        bench_id=bench,
        orch_only=orch_only,
        status=status,  # type: ignore[arg-type]
        theme=theme,
    )
    if not lessons:
        console.print("[yellow]No matching lessons[/yellow]")
        raise typer.Exit(0)
    for lesson in lessons:
        console.print(
            f"{lesson.id} [{lesson.status}] {lesson.theme} "
            f"{lesson.subject_id}/{lesson.case_id}: {lesson.summary}"
        )


@lessons_app.command("summarize")
def lessons_summarize_cmd(
    bench: str = typer.Option(..., "--bench", help="Bench id"),
    orch_only: bool = typer.Option(True, "--orch-only/--all"),
) -> None:
    from product_factory.evaluation.lessons import list_lessons, summarize_lessons

    config = load_config()
    pf_root = config.root / ".product-factory"
    lessons = list_lessons(pf_root, bench_id=bench, orch_only=orch_only)
    console.print_json(data=summarize_lessons(lessons))


@lessons_app.command("accept")
def lessons_accept_cmd(
    lesson_id: str = typer.Argument(...),
    note: str = typer.Option("", "--note"),
    bench: str | None = typer.Option(None, "--bench"),
) -> None:
    from product_factory.evaluation.lessons import update_lesson_status

    config = load_config()
    lesson = update_lesson_status(
        config.root / ".product-factory",
        lesson_id,
        status="accepted",
        note=note,
        bench_id=bench,
    )
    console.print(f"[green]accepted[/green] {lesson.id}")


@lessons_app.command("reject")
def lessons_reject_cmd(
    lesson_id: str | None = typer.Argument(None),
    note: str = typer.Option("", "--note"),
    bench: str | None = typer.Option(None, "--bench"),
    filter_name: str | None = typer.Option(
        None,
        "--filter",
        help="Bulk reject: baseline|non_orch (requires --bench)",
    ),
) -> None:
    from product_factory.evaluation.lessons import reject_lessons_matching, update_lesson_status

    config = load_config()
    pf_root = config.root / ".product-factory"
    if filter_name:
        if not bench:
            console.print("[red]--bench required with --filter[/red]")
            raise typer.Exit(2)
        updated = reject_lessons_matching(
            pf_root, bench_id=bench, filter_name=filter_name, note=note
        )
        console.print(f"[yellow]rejected {len(updated)} lessons[/yellow] filter={filter_name}")
        return
    if not lesson_id:
        console.print("[red]lesson id or --filter required[/red]")
        raise typer.Exit(2)
    lesson = update_lesson_status(pf_root, lesson_id, status="rejected", note=note, bench_id=bench)
    console.print(f"[yellow]rejected[/yellow] {lesson.id}")


@lessons_app.command("promote")
def lessons_promote_cmd(
    lesson_ids: str = typer.Option(..., "--lesson-ids", help="Comma-separated lesson ids"),
    files: str = typer.Option(..., "--files", help="Comma-separated human-authored file paths"),
    bump_skill: str = typer.Option(
        "",
        "--bump-skill",
        help="Comma-separated skill ids to bump (manifest.yaml version)",
    ),
    note: str = typer.Option("", "--note"),
    bench: str | None = typer.Option(None, "--bench"),
) -> None:
    """Promote accepted lessons after human edits (never auto-writes skill text)."""
    from product_factory.evaluation.lessons import promote_lessons

    config = load_config()
    ledger = promote_lessons(
        config.root / ".product-factory",
        lesson_ids=[x.strip() for x in lesson_ids.split(",") if x.strip()],
        files=[Path(x.strip()) for x in files.split(",") if x.strip()],
        bump_skill_ids=[x.strip() for x in bump_skill.split(",") if x.strip()],
        project_root=config.root,
        note=note,
        bench_id=bench,
    )
    console.print("[green]promoted[/green]")
    console.print_json(data=ledger)


def _serve_api(
    *,
    host: str,
    port: int,
    data_dir: Path | None,
    cors: str,
) -> None:
    try:
        from product_factory.api.app import serve
    except ImportError as exc:
        console.print(
            "[red]Observability API dependencies missing.[/red] "
            "Install with: uv sync --extra observability"
        )
        raise typer.Exit(2) from exc

    root = data_dir
    if root is None and os.environ.get("PRODUCT_FACTORY_DATA_DIR"):
        root = Path(os.environ["PRODUCT_FACTORY_DATA_DIR"]).expanduser()
    if root is None:
        try:
            config = load_config()
            root = config.root / ".product-factory"
        except ProductFactoryError:
            root = Path(".product-factory")
    origins = [o.strip() for o in cors.split(",") if o.strip()] or None
    console.print(f"Control + observability API on http://{host}:{port} (data={root.resolve()})")
    serve(root, host=host, port=port, cors_origins=origins)


@observe_app.command("serve")
def observe_serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Product-factory data root (contains data/ and runs/). Defaults to .product-factory",
    ),
    cors: str = typer.Option(
        "",
        "--cors",
        help="Comma-separated CORS origins (empty = disabled)",
    ),
) -> None:
    """Serve the local observability + host control REST/SSE API."""
    _serve_api(host=host, port=port, data_dir=data_dir, cors=cors)


def _resolve_data_dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return data_dir.expanduser().resolve()
    env = os.environ.get("PRODUCT_FACTORY_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    try:
        return load_config().root / ".product-factory"
    except ProductFactoryError:
        return Path(".product-factory").resolve()


@ops_app.command("backup")
def ops_backup_cmd(
    dest: Path = typer.Option(..., "--dest", help="Output .tar.gz path"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Administrative: create a SQLite + runs/ops snapshot (not a HostService mutation)."""
    from product_factory.persistence.backup import create_backup

    root = _resolve_data_dir(data_dir)
    manifest = create_backup(root, dest)
    console.print(f"[green]backup written[/green] {dest.resolve()}")
    console.print_json(data=manifest.model_dump(mode="json"))


@ops_app.command("restore")
def ops_restore_cmd(
    archive: Path = typer.Option(..., "--archive", exists=True, dir_okay=False),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Move aside a non-empty target data directory before restore",
    ),
) -> None:
    """Administrative: restore a backup archive (not a HostService mutation)."""
    from product_factory.persistence.backup import restore_backup

    root = _resolve_data_dir(data_dir)
    result = restore_backup(archive, root, replace=replace)
    console.print(f"[green]restored[/green] {result.target_data_dir}")
    console.print_json(data=result.model_dump(mode="json"))


@ops_app.command("backup-status")
def ops_backup_status_cmd(
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Administrative: summarize the local data root for backup planning."""
    from product_factory.persistence.backup import backup_status

    console.print_json(data=backup_status(_resolve_data_dir(data_dir)))


@ops_app.command("maintain")
def ops_maintain_cmd(
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Dry-run first (default)"),
    prune_run: list[str] = typer.Option(None, "--prune-run", help="Explicit run ID to prune"),
    max_age_days: float | None = typer.Option(None, "--max-age-days"),
    backup_ref: Path | None = typer.Option(None, "--backup-ref", help="Eligible backup archive"),
    vacuum: bool = typer.Option(False, "--vacuum", help="VACUUM after checkpoint (execute only)"),
) -> None:
    """Dry-run-first retention/maintenance inventory and optional prune/GC (SD3.D)."""
    from product_factory.persistence.database import Database
    from product_factory.persistence.retention import MaintenanceService

    root = _resolve_data_dir(data_dir)
    db = Database(root / "data" / "product_factory.sqlite")
    try:
        svc = MaintenanceService(data_dir=root, db=db)
        plan = svc.plan(
            dry_run=dry_run,
            prune_run_ids=list(prune_run or []),
            max_age_days=max_age_days,
            backup_ref=str(backup_ref) if backup_ref else None,
        )
        if vacuum:
            plan.notes.append("vacuum:requested")
        result = svc.execute(plan, require_backup=True)
        console.print_json(data=result.to_dict())
    finally:
        db.close()


@ops_app.command("pin")
def ops_pin_cmd(
    target_id: str = typer.Argument(...),
    kind: str = typer.Option("run", "--kind"),
    reason: str = typer.Option("", "--reason"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Pin a run or experiment so retention cannot prune it."""
    from product_factory.persistence.database import Database
    from product_factory.persistence.retention import MaintenanceService

    root = _resolve_data_dir(data_dir)
    db = Database(root / "data" / "product_factory.sqlite")
    try:
        MaintenanceService(data_dir=root, db=db).pin(
            target_kind=kind, target_id=target_id, reason=reason
        )
        console.print(f"[green]pinned[/green] {kind}:{target_id}")
    finally:
        db.close()


@ops_app.command("unpin")
def ops_unpin_cmd(
    target_id: str = typer.Argument(...),
    kind: str = typer.Option("run", "--kind"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
) -> None:
    """Remove a retention pin."""
    from product_factory.persistence.database import Database
    from product_factory.persistence.retention import MaintenanceService

    root = _resolve_data_dir(data_dir)
    db = Database(root / "data" / "product_factory.sqlite")
    try:
        MaintenanceService(data_dir=root, db=db).unpin(target_kind=kind, target_id=target_id)
        console.print(f"[green]unpinned[/green] {kind}:{target_id}")
    finally:
        db.close()


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Product-factory data root (contains data/ and runs/). Defaults to .product-factory",
    ),
    cors: str = typer.Option(
        "",
        "--cors",
        help="Comma-separated CORS origins (empty = disabled)",
    ),
) -> None:
    """Alias for `observe serve` (control + observability API)."""
    _serve_api(host=host, port=port, data_dir=data_dir, cors=cors)


@app.command("mcp")
def mcp_cmd(
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Force mock gateway for tools that submit runs",
    ),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help="Override .product-factory data root",
    ),
) -> None:
    """Run the Product Factory MCP server on stdio (OpenCode / Cursor / Claude Code)."""
    import sys

    from product_factory.host_mcp.server import run_stdio

    # MCP uses stdout for JSON-RPC; keep Rich/typer noise off the wire.
    try:
        run_stdio(mock=mock, data_dir=data_dir)
    except ProductFactoryError as exc:
        sys.stderr.write(f"product-factory mcp failed: {exc.message}\n")
        raise SystemExit(1) from None
    except Exception as exc:  # noqa: BLE001 — last-resort stdio safety
        sys.stderr.write(f"product-factory mcp failed: {exc}\n")
        raise SystemExit(1) from None


@app.command("costs")
def costs_cmd(run_id: str | None = typer.Argument(None)) -> None:
    service = _local_host_service()
    if run_id:
        row = service.coord.db.get_run(run_id)
        rows = [row] if row else []
    else:
        rows = list(service.coord.db.list_runs())
    total = Decimal("0")
    for row in rows:
        if not row:
            continue
        usage = json.loads(row.get("usage_json") or "{}")
        cost = Decimal(str(usage.get("estimated_cost_usd", "0")))
        total += cost
        console.print(f"{row['run_id']}: ${cost}")
    console.print(f"Total: ${total}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
