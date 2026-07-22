"""CLI application."""

from __future__ import annotations

import json
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from product_factory import __version__
from product_factory.config.loader import load_config
from product_factory.domain import export_json_schemas
from product_factory.domain.budgets import RunBudget
from product_factory.domain.errors import ProductFactoryError
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.openrouter import OpenRouterGateway
from product_factory.observability.logging import setup_logging
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.orchestration.graph import build_graph

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
observe_app = typer.Typer(help="Observability API commands")
app.add_typer(observe_app, name="observe")
console = Console()


def _gateway_from_config(config, *, force_mock: bool = False):
    if force_mock:
        return MockGateway()
    profiles = {
        name: {
            "model": p.model,
            "pricing": p.pricing,
            "provider": p.provider,
        }
        for name, p in config.models.profiles.items()
    }
    # Prefer OpenRouter when key present; else mock.
    import os

    if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"):
        return OpenRouterGateway(profile_models=profiles)
    return MockGateway()


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
    coord = RunCoordinator(config=config, gateway=gateway)
    coord.db.cache_model_catalog(payload)
    console.print(f"Refreshed {len(payload.get('models', []))} models")


@app.command("plan")
def plan_cmd(
    request: Path = typer.Option(..., "--request", exists=True),
    workflow: str = typer.Option("code_change", "--workflow"),
    repo: Path | None = typer.Option(None, "--repo"),
    mock: bool = typer.Option(False, "--mock"),
) -> None:
    """Generate and compile a plan without full execution."""
    from product_factory.orchestration.coordinator import (
        default_architecture_plan,
        default_code_change_plan,
    )
    from product_factory.planning.compiler import compile_plan

    text = request.read_text(encoding="utf-8")
    config = load_config()
    gateway = _gateway_from_config(config, force_mock=mock)
    RunCoordinator(config=config, gateway=gateway, use_deterministic_planner=mock)
    if workflow == "architecture":
        proposal = default_architecture_plan(text)
    else:
        proposal = default_code_change_plan(text)
    result = compile_plan(proposal)
    console.print_json(data=result.model_dump(mode="json"))
    if not result.ok:
        raise typer.Exit(3)


@app.command("run")
def run_cmd(
    request: Path = typer.Option(..., "--request", exists=True, dir_okay=False),
    repo: Path | None = typer.Option(None, "--repo"),
    workflow: str = typer.Option("code_change", "--workflow"),
    profile: str = typer.Option("local-target", "--profile"),
    budget_usd: float = typer.Option(3.0, "--budget-usd"),
    mock: bool = typer.Option(False, "--mock"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Execute a product-factory run."""
    config = load_config()
    gateway = _gateway_from_config(config, force_mock=mock)
    coord = RunCoordinator(
        config=config,
        gateway=gateway,
        use_deterministic_planner=mock or isinstance(gateway, MockGateway),
    )
    run_request = RunRequest(
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        workflow_type=workflow,  # type: ignore[arg-type]
        request_text=request.read_text(encoding="utf-8"),
        repository_path=repo.resolve() if repo else None,
        model_profile_set=profile,
        budget=RunBudget(max_cost_usd=Decimal(str(budget_usd))),
    )
    try:
        manifest = coord.run(run_request)
    except ProductFactoryError as exc:
        console.print(f"[red]{exc.__class__.__name__}:[/red] {exc.message}")
        raise typer.Exit(exc.exit_code) from exc
    if json_out:
        console.print_json(data=json.loads(manifest.model_dump_json()))
    else:
        console.print(f"Run [bold]{manifest.run_id}[/bold] → {manifest.final_status}")
        console.print(f"Cost: ${manifest.usage.estimated_cost_usd}")
    if manifest.final_status in {"failed", "plan_rejected", "budget_exhausted", "blocked"}:
        raise typer.Exit(4)


@app.command("status")
def status_cmd(run_id: str | None = typer.Argument(None)) -> None:
    config = load_config()
    coord = RunCoordinator(config=config, gateway=MockGateway())
    if run_id:
        row = coord.db.get_run(run_id)
        if not row:
            console.print(f"Unknown run {run_id}")
            raise typer.Exit(1)
        console.print_json(data=dict(row))
    else:
        for row in coord.db.list_runs():
            console.print(f"{row['run_id']}\t{row['status']}\t{row['workflow_type']}")


@app.command("inspect")
def inspect_cmd(run_id: str = typer.Argument(...)) -> None:
    config = load_config()
    run_dir = config.root / ".product-factory" / "runs" / run_id
    manifest = run_dir / "run-manifest.json"
    if not manifest.exists():
        # also search under pf root used by coordinator
        alt = Path(".product-factory") / "runs" / run_id / "run-manifest.json"
        manifest = alt if alt.exists() else manifest
    if not manifest.exists():
        console.print("Manifest not found")
        raise typer.Exit(1)
    console.print_json(data=json.loads(manifest.read_text(encoding="utf-8")))


@app.command("resume")
def resume_cmd(run_id: str = typer.Argument(...)) -> None:
    """Resume a checkpointed graph thread (graph-level demo)."""
    graph = build_graph()
    result = graph.invoke(
        {
            "run_id": run_id,
            "final_status": "executing",
            "workflow_type": "code_change",
            "compiler_errors": [],
            "validation_results": [],
            "plan_attempt": 1,
            "repair_count": 0,
            "task_results": [],
            "findings": [],
            "events": [],
        },
        config={"configurable": {"thread_id": run_id}},
    )
    console.print_json(data={"final_status": result.get("final_status"), "run_id": run_id})


@app.command("approve")
def approve_cmd(
    run_id: str = typer.Argument(...),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    config = load_config()
    coord = RunCoordinator(config=config, gateway=MockGateway())
    try:
        result = coord.approve(run_id, apply=apply)
    except ProductFactoryError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise typer.Exit(exc.exit_code) from exc
    console.print_json(data=result)


@app.command("reject")
def reject_cmd(run_id: str = typer.Argument(...)) -> None:
    config = load_config()
    coord = RunCoordinator(config=config, gateway=MockGateway())
    try:
        result = coord.reject(run_id)
    except ProductFactoryError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise typer.Exit(exc.exit_code) from exc
    console.print_json(data=result)


@app.command("apply")
def apply_cmd(run_id: str = typer.Argument(...)) -> None:
    config = load_config()
    coord = RunCoordinator(config=config, gateway=MockGateway())
    try:
        result = coord.apply_patch(run_id)
    except ProductFactoryError as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise typer.Exit(exc.exit_code) from exc
    console.print_json(data=result)


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
    case_ids: str | None = typer.Option(
        None, "--case-ids", help="Comma-separated exact case ids"
    ),
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
    """Serve the read-only observability REST/WebSocket API."""
    try:
        from product_factory.api.app import serve
    except ImportError as exc:
        console.print(
            "[red]Observability API dependencies missing.[/red] "
            "Install with: uv sync --extra observability"
        )
        raise typer.Exit(2) from exc

    root = data_dir
    if root is None:
        try:
            config = load_config()
            root = config.root / ".product-factory"
        except ProductFactoryError:
            root = Path(".product-factory")
    origins = [o.strip() for o in cors.split(",") if o.strip()] or None
    console.print(f"Observability API on http://{host}:{port} (data={root.resolve()})")
    serve(root, host=host, port=port, cors_origins=origins)


@app.command("costs")
def costs_cmd(run_id: str | None = typer.Argument(None)) -> None:
    config = load_config()
    coord = RunCoordinator(config=config, gateway=MockGateway())
    rows = [coord.db.get_run(run_id)] if run_id else coord.db.list_runs()
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
