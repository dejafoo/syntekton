# Sandbox and durable resume (Phase 1: P1.D, P1.B)

Design note for the two runtime-truthfulness gaps called out in
[`handover_post_mvp.md`](../handover_post_mvp.md) §2/§4 Phase 1: process-level
sandboxing for command execution, and real coordinator resume from persisted
state. See [`docs/next-work-packages-phase1.md`](../next-work-packages-phase1.md)
for the full Phase 1 checklist and evidence.

## Sandbox (P1.D)

All registered-command execution — behavioral validation
(`validation/pipeline.py::validate_behavioral_commands`), deterministic smoke
commands (`evaluation/deterministic.py::_run_smoke_commands`), and the tool
broker's `run_command` — goes through a single choke point:
`tools/sandbox.py::run_sandboxed_command`. There is no other code path that
shells out to a registered command; this makes the sandbox mandatory rather
than opt-in.

Guarantees, regardless of platform:

- **Environment scrub.** Only an explicit allowlist (`PATH`, `HOME`, `LANG`,
  locale vars, `TMPDIR`/`TMP`/`TEMP`, `USER`/`LOGNAME`, and the `uv`/Python
  virtualenv vars needed to invoke project tooling) is copied from the parent
  process. Everything else — API keys, cloud credentials, arbitrary CI
  secrets — is absent by construction, plus a deny-list double-checks common
  secret-bearing names are dropped even if an allowlist entry were widened
  later.
- **Hard timeout.** Every registered command carries a `timeout_seconds`; the
  subprocess is killed and reported as `returncode=124` on expiry, both
  before dispatch (`BudgetLedger.check_before_command`, P1.A) and via
  `subprocess.run(..., timeout=...)`.
- **Worktree-confined cwd.** The command always runs with `cwd` set to the
  task's isolated worktree (or a scratch copy for behavioral validation), so
  path traversal outside the assigned scope requires an explicit escape
  (e.g. `bwrap` closes this further — see below).
- **Bounded output.** stdout/stderr are captured and truncated (last 8000/4000
  chars) before being persisted as validator evidence, so a runaway command
  cannot balloon run storage.

Platform behavior (`sandbox_info()` reports which mode is active):

- **Darwin (default dev/CI target today):** restricted subprocess only — env
  scrub + cwd confinement as above. There is no OS-level namespace/mount
  sandbox on macOS available to an unprivileged process without extra
  tooling, so this is the practical floor for Phase 1.
- **Linux:** prefers `bubblewrap` (`bwrap`) when present on `PATH`
  (`shutil.which("bwrap")`). When available, the command runs inside a
  minimal namespace: private `/tmp`, network fully unshared
  (`--unshare-net`, i.e. no outbound sockets at all), the worktree bind-mounted
  read-write, and `/usr`, `/bin`, `/lib`, `/lib64`, `/opt`, and `~/.local`
  bind-mounted read-only so the toolchain itself cannot be tampered with.
  `--die-with-parent` prevents orphaned sandboxed processes. If `bwrap` is
  not installed, Linux silently falls back to the same restricted-subprocess
  mode as Darwin — sandboxing degrades, it never fails a run.

Explicitly out of scope for Phase 1 (tracked for a later phase, not silently
assumed): filesystem-level read restrictions beyond the worktree bind mount
when *not* running under `bwrap`, CPU/memory cgroup limits, and syscall
filtering (seccomp). These require either `bwrap` support on Linux or a
heavier-weight container runtime and are not required to make the current
safety claims (no ambient secrets, no ambient network *when bwrap is
available*, hard timeout) true.

Tests: `tests/security/test_sandbox.py` — secret env vars are not visible to
the sandboxed process, only allowlisted vars pass through, a hung command is
killed at its timeout, `sandbox_info()` correctly reports `restricted` vs
`bwrap`, and (skipped when `bwrap` is absent, e.g. on Darwin CI) a `bwrap`
sandboxed command cannot reach the network.

## Durable resume (P1.B)

Resume is coordinator + SQLite + run dir — deliberately not a separate
checkpoint store (e.g. LangGraph `MemorySaver`). Everything needed to resume
already gets written during a normal run:

- `runs.request_json` / `runs.base_commit` — the original `RunRequest` and the
  commit the run's worktrees are based on.
- `runs.budget_json` — a `BudgetLedger.snapshot()` after every task/wave, so a
  resumed run inherits cumulative cost/tokens/tool-calls/command-seconds and
  cannot reset a budget by crashing and restarting.
- `tasks` (one row per task, keyed by `(run_id, task_id)`) — status, spec,
  result, attempt count, timestamps. `list_tasks_in_creation_order` replays
  tasks (including dynamically-added repair tasks) in the order they were
  first seen, which is what lets `resume()` rebuild a faithful `CompiledPlan`.
  `attempt` counts crash-retries: `RunCoordinator.resume()` treats a task
  still marked `"running"` as a mid-task crash, bumps `attempt`, and resets it
  to `"pending"` for exactly one retry.
- `.product-factory/runs/<run_id>/worktrees/<task_id>` on disk —
  `WorktreeManager.reattach()` re-registers an existing worktree directory
  without re-running `git worktree add`, so a resumed writer/composition task
  continues from the same on-disk state rather than starting from a fresh
  clone of `base_commit`.

`RunCoordinator.resume(run_id)`:

1. Loads the run row; fails closed (`ConfigurationError`) for an unknown
   `run_id` or a run whose `status` is already terminal
   (`completed`/`failed`/`budget_exhausted`/etc.) — resume is only valid for
   an interrupted/in-flight run.
2. Reconstructs the `RunRequest` from `request_json` and recompiles the
   original plan proposal (`plan.json` artifact) plus any repair `TaskSpec`s
   recovered from `tasks.spec_json`, producing the same `CompiledPlan` the
   original process had.
3. Restores the `BudgetLedger` via `BudgetLedger.restore(budget, snapshot)`:
   cumulative usage/tool-calls/command-seconds carry over, and
   `started_monotonic` is shifted backward by the previously-elapsed
   wall-clock time so `max_wall_clock_seconds` spans the original process
   *and* the resumed one, not just the resumed one.
4. Walks tasks in creation order: `success`/`skipped` tasks contribute their
   persisted `TaskResult` to the resumed in-memory state with **no** new
   model or tool call — this is the load-bearing guarantee (see
   `tests/graph/test_resume.py`); a task caught mid-crash (`"running"`) is
   retried once; everything else stays `"pending"`.
5. Reattaches worktrees for any task whose directory still exists on disk.
6. Calls the same `_execute()` used by a fresh run, seeded with the
   reconstructed `initial_task_status` / `initial_results` /
   `initial_patch_text` / `initial_architecture_md`, so the wave loop, budget
   enforcement, concurrency, and approval gate are identical code paths for a
   fresh run and a resumed one.

The CLI's `product-factory resume <run_id>` calls this directly (the previous
LangGraph checkpoint demo is still reachable behind `--graph-demo` for
comparison, but is no longer the default).

Tests: `tests/graph/test_resume.py` — a task crashing mid-run is retried
exactly once on resume with no duplicate `model_invocations`/`tool_calls` rows
for already-completed tasks, resuming an unknown or already-terminal run fails
closed, and the approval gate (`approve`/`apply`) works against a coordinator
instance created after a simulated process restart.
