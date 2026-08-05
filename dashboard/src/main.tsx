import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrowserRouter, Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  api,
  eventIdentity,
  eventItems,
  projectionsForEvent,
  statusClass,
  streamRunEvents,
  taskColumn,
  type ContentRef,
  type ContentView,
  type Dict,
  type RunSummary,
  type StreamEvent,
  type TaskSummary,
} from "./api";
import "./styles.css";

type Row = Dict & { [key: string]: unknown };
type QueryName = "run" | "tasks" | "plan" | "lineage" | "costs" | "invocations" | "tools" | "validations" | "artifacts" | "prompts";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });
const money = (value: unknown) => `$${Number(value ?? 0).toFixed(4)}`;
const date = (value: unknown) => typeof value === "string" && value ? new Date(value).toLocaleString() : "—";
const text = (value: unknown, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
const object = (value: unknown): Dict => value && typeof value === "object" && !Array.isArray(value) ? value as Dict : {};
const number = (value: unknown) => typeof value === "number" ? value : Number(value ?? 0);
const hasStatus = (status: string, choices: string[]) => choices.includes(status.toLowerCase());

function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api<RunSummary[]>("/runs"),
    refetchInterval: 2_000,
    refetchIntervalInBackground: false,
  });
}

function RunList() {
  const { data: runs = [], isLoading, error } = useRuns();
  const [status, setStatus] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [liveness, setLiveness] = useState("");
  const visible = runs.filter((run) =>
    (!status || run.status === status)
    && (!workflow || run.workflow_type === workflow)
    && (!liveness || run.liveness === liveness),
  );
  const values = (field: keyof RunSummary) => [...new Set(runs.map((run) => run[field]).filter(Boolean))].map(String);

  return <main className="page">
    <header>
      <p className="eyebrow">LOCAL · MONITOR ONLY</p>
      <h1>Product Factory</h1>
      <p>Durable orchestration runs. Mutations remain in the host CLI, MCP, or API control surface.</p>
    </header>
    <div className="filters">
      <Filter label="Status" value={status} values={values("status")} onChange={setStatus} />
      <Filter label="Workflow" value={workflow} values={values("workflow_type")} onChange={setWorkflow} />
      <Filter label="Liveness" value={liveness} values={values("liveness")} onChange={setLiveness} />
    </div>
    {isLoading && <p>Loading runs…</p>}
    {error && <p className="error">Could not load runs: {String(error)}</p>}
    {!isLoading && !visible.length && <section className="empty">No runs yet. Start a run from the host CLI or MCP, then this list will refresh automatically.</section>}
    <section className="run-list">
      {visible.map((run) => {
        const usage = object(run.usage);
        return <Link className="run-card" to={`/dashboard/runs/${encodeURIComponent(run.run_id)}`} key={run.run_id}>
          <div><strong>{run.run_id}</strong><span>{run.workflow_type}</span></div>
          <span className={statusClass(run.status)}>{run.status}</span>
          <dl>
            <Metric name="Tasks" value={Object.values(run.task_counts ?? {}).reduce((total, value) => total + number(value), 0)} />
            <Metric name="Cloud cost" value={money(usage.reported_cost_usd ?? usage.estimated_cost_usd)} />
            <Metric name="Liveness" value={text(run.liveness)} />
            <Metric name="Active" value={text(run.active_operation)} />
          </dl>
          <small>{date(run.updated_at)} · {number(run.error_count)} errors</small>
        </Link>;
      })}
    </section>
  </main>;
}

function Filter({ label, value, values, onChange }: { label: string; value: string; values: string[]; onChange: (value: string) => void }) {
  return <label>{label}<select value={value} onChange={(event) => onChange(event.target.value)}><option value="">All</option>{values.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>;
}

function useLiveRun(runId: string, latestSeq: number | undefined) {
  const client = useQueryClient();
  const [state, setState] = useState<"live" | "stale" | "reconnecting">("stale");
  const last = useRef(0);
  const seen = useRef(new Set<string>());
  const ready = latestSeq !== undefined;

  useEffect(() => {
    if (!ready) return;
    // The durable run projection is read before opening the stream.  Do not
    // restart whenever that projection's latest_seq advances; the stream
    // cursor below is the single owner of the live cursor.
    last.current = latestSeq ?? 0;
    seen.current.clear();
    let closed = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    let attempt = 0;

    const invalidate = (projection: string) => {
      const name = projection as QueryName;
      client.invalidateQueries({ queryKey: [name, runId] });
    };
    const receive = (event: StreamEvent) => {
      setState("live");
      if (typeof event.seq === "number") last.current = Math.max(last.current, event.seq);
      if (event.type === "heartbeat") return;
      const identity = eventIdentity(event);
      if (seen.current.has(identity)) return;
      seen.current.add(identity);
      client.setQueryData<StreamEvent[]>(["events", runId], (previous = []) =>
        [...previous, event].filter((item, index, all) => all.findIndex((candidate) => eventIdentity(candidate) === eventIdentity(item)) === index)
          .sort((left, right) => number(left.seq) - number(right.seq)),
      );
      for (const projection of projectionsForEvent(event.type)) invalidate(projection);
    };
    const connect = async () => {
      if (closed) return;
      setState(attempt ? "reconnecting" : "stale");
      controller = new AbortController();
      try {
        await streamRunEvents(runId, last.current, controller.signal, receive, () => setState("live"));
        if (!closed) throw new Error("The event stream ended.");
      } catch {
        if (closed || controller.signal.aborted) return;
        setState("stale");
        const delay = Math.min(10_000, 250 * 2 ** attempt);
        attempt += 1;
        timer = window.setTimeout(() => { void connect(); }, delay);
      }
    };
    void connect();
    return () => { closed = true; controller?.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [client, ready, runId]);

  return state;
}

function graph(tasks: TaskSummary[], selected: string | null): { nodes: Node[]; edges: Edge[] } {
  const tasksById = new Map(tasks.map((task) => [task.task_id, task]));
  const levels = new Map<string, number>();
  const resolving = new Set<string>();
  const levelFor = (taskId: string): number => {
    if (levels.has(taskId)) return levels.get(taskId)!;
    if (resolving.has(taskId)) return 0;
    resolving.add(taskId);
    const task = tasksById.get(taskId);
    const level = task ? Math.max(0, ...(task.dependencies ?? []).map((dependency) => levelFor(dependency) + 1)) : 0;
    resolving.delete(taskId);
    levels.set(taskId, level);
    return level;
  };
  const positions = new Map<number, number>();
  const nodes = tasks.map((task) => {
    const layer = levelFor(task.task_id);
    const row = positions.get(layer) ?? 0;
    positions.set(layer, row + 1);
    const column = taskColumn(task);
    return {
      id: task.task_id,
      position: { x: layer * 265, y: row * 122 },
      data: { label: <div><b>{task.task_id}</b><br /><small>{task.title || task.capability}</small><br /><span className={statusClass(task.status)}>{task.status}</span></div> },
      className: [selected === task.task_id ? "selected-node" : "", column === "repairing" ? "repair-node" : ""].filter(Boolean).join(" "),
    } satisfies Node;
  });
  const edges = tasks.flatMap((task) => (task.dependencies ?? []).filter((dependency) => tasksById.has(dependency)).map((dependency) => ({
    id: `${dependency}-${task.task_id}`,
    source: dependency,
    target: task.task_id,
    animated: taskColumn(task) === "running" || taskColumn(task) === "repairing",
  } satisfies Edge)));
  return { nodes, edges };
}

function Detail() {
  const { runId = "" } = useParams();
  const [tab, setTab] = useState("Plan");
  const [selected, setSelected] = useState<string | null>(null);
  const encodedId = encodeURIComponent(runId);
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api<RunSummary>(`/runs/${encodedId}`) });
  const tasks = useQuery({ queryKey: ["tasks", runId], queryFn: () => api<TaskSummary[]>(`/runs/${encodedId}/tasks`) });
  const plan = useQuery({ queryKey: ["plan", runId], queryFn: () => api<Row>(`/runs/${encodedId}/plan`) });
  const lineage = useQuery({ queryKey: ["lineage", runId], queryFn: () => api<Row>(`/runs/${encodedId}/lineage`) });
  const costs = useQuery({ queryKey: ["costs", runId], queryFn: () => api<Row>(`/runs/${encodedId}/costs`) });
  const events = useQuery({ queryKey: ["events", runId], queryFn: async () => eventItems(await api<{ items: StreamEvent[] }>(`/runs/${encodedId}/events?limit=500`)) });
  const invocations = useQuery({ queryKey: ["invocations", runId], queryFn: () => api<Row[]>(`/runs/${encodedId}/model-invocations`) });
  const tools = useQuery({ queryKey: ["tools", runId], queryFn: () => api<Row[]>(`/runs/${encodedId}/tool-calls`) });
  const validations = useQuery({ queryKey: ["validations", runId], queryFn: () => api<Row[]>(`/runs/${encodedId}/validations`) });
  const artifacts = useQuery({ queryKey: ["artifacts", runId], queryFn: () => api<Row[]>(`/runs/${encodedId}/artifacts`) });
  const prompts = useQuery({ queryKey: ["prompts", runId], queryFn: () => api<Row[]>(`/runs/${encodedId}/prompts`) });
  const stream = useLiveRun(runId, run.data?.latest_seq);
  const selectedTask = tasks.data?.find((task) => task.task_id === selected);
  const flow = useMemo(() => graph(tasks.data ?? [], selected), [tasks.data, selected]);

  if (run.error) return <main className="page"><p className="error">Run could not be found or read.</p><Link to="/dashboard/">Back to runs</Link></main>;
  if (!run.data) return <main className="page"><Link className="back" to="/dashboard/">← Runs</Link><p>Loading run…</p></main>;

  const currentRun = run.data;
  return <main className="page">
    <Link className="back" to="/dashboard/">← Runs</Link>
    <header className="run-header">
      <div><p className="eyebrow">{currentRun.workflow_type}</p><h1>{runId}</h1><p>{currentRun.active_operation || "No active operation"} · {date(currentRun.updated_at)}</p></div>
      <span className={`${statusClass(currentRun.status)} ${stream}`}>{stream === "live" ? "live · " : ""}{currentRun.status}</span>
    </header>
    <LifecycleNotice status={currentRun.status} nextAction={currentRun.next_action} />
    <nav className="tabs">{["Plan", "Execution", "Timeline", "Evidence", "Costs"].map((name) => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{name}</button>)}</nav>
    {tab === "Plan" && <section className="split">
      <div className="graph"><ReactFlow nodes={flow.nodes} edges={flow.edges} onNodeClick={(_, node) => setSelected(node.id)} fitView><Background /><MiniMap /><Controls /></ReactFlow></div>
      <TaskDetail task={selectedTask} repair={repairForTask(lineage.data, selected)} />
      <Kanban tasks={tasks.data ?? []} selected={selected} onSelect={setSelected} />
    </section>}
    {tab === "Execution" && <Execution tasks={tasks.data ?? []} invocations={invocations.data ?? []} tools={tools.data ?? []} validations={validations.data ?? []} selected={selected} onSelect={setSelected} />}
    {tab === "Timeline" && <Timeline events={events.data ?? []} stream={stream} />}
    {tab === "Evidence" && <Evidence runId={runId} artifacts={artifacts.data ?? []} prompts={prompts.data ?? []} events={events.data ?? []} invocations={invocations.data ?? []} plan={plan.data} lineage={lineage.data} />}
    {tab === "Costs" && <Costs data={costs.data} />}
  </main>;
}

function LifecycleNotice({ status, nextAction }: { status: string; nextAction?: string | null }) {
  const normal = hasStatus(status, ["running", "executing", "revising", "succeeded", "success", "completed", "failed", "blocked", "queued", "planning", "initializing"]);
  if (nextAction) return <p className="notice">{nextAction}</p>;
  if (hasStatus(status, ["awaiting_approval", "awaiting-approval"])) return <p className="notice">This run is awaiting approval. Resolve it with the host CLI, MCP, or API control surface; this dashboard is monitor-only.</p>;
  if (hasStatus(status, ["cancelled", "canceled", "cancel_requested"])) return <p className="notice">Cancellation state is reported by the host integration. Use the host CLI or MCP to inspect or take the next action.</p>;
  if (!normal) return <p className="notice">Host lifecycle state: <code>{status}</code>. Its durable projections are shown below.</p>;
  return null;
}

function Kanban({ tasks, selected, onSelect }: { tasks: TaskSummary[]; selected: string | null; onSelect: (id: string) => void }) {
  const groups: Record<ReturnType<typeof taskColumn>, TaskSummary[]> = {
    queued: [], running: [], repairing: [], "awaiting approval": [], succeeded: [], "failed / blocked": [],
  };
  tasks.forEach((task) => groups[taskColumn(task)].push(task));
  return <div className="kanban">{Object.entries(groups).map(([name, items]) => <div key={name}><h3>{name}</h3>{items.map((task) => <button className={`task-chip ${selected === task.task_id ? "selected" : ""}`} onClick={() => onSelect(task.task_id)} key={task.task_id}>{task.task_id}<small>{task.title || task.capability}</small></button>)}</div>)}</div>;
}

function repairForTask(lineage: Row | undefined, taskId: string | null): Row | undefined {
  if (!taskId || !Array.isArray(lineage?.repairs)) return undefined;
  return lineage.repairs.find((item): item is Row => Boolean(item && typeof item === "object") && (item.task_id === taskId || item.replacement_task_id === taskId || item.repair_task_id === taskId));
}

function TaskDetail({ task, repair }: { task?: TaskSummary; repair?: Row }) {
  if (!task) return <aside className="detail"><p>Select a task in the graph, kanban, or execution table.</p></aside>;
  const policy = object(task.effective_policy);
  const grants = Array.isArray(policy.allowed_tool_names) ? policy.allowed_tool_names as string[] : [];
  return <aside className="detail"><h2>{task.task_id}</h2><p className={statusClass(task.status)}>{task.status}</p><p>{task.summary || task.title || "No terminal summary yet."}</p>
    {task.next_action && <p className="notice">{task.next_action}</p>}
    <dl>
      <Metric name="Capability" value={task.capability} />
      <Metric name="Model profile" value={text(task.primary_model_profile ?? task.model_profile)} />
      <Metric name="Route" value={text(task.route_class, task.legacy_policy ? "legacy" : "—")} />
      <Metric name="Fallback" value={task.fallback_eligible ? text(task.fallback_model_profile, "eligible") : "disabled"} />
      <Metric name="Stack profile" value={text(task.stack_profile_digest)} />
      <Metric name="Liveness" value={text(task.liveness)} />
      <Metric name="Dependencies" value={(task.dependencies ?? []).join(", ") || "—"} />
    </dl>
    {task.legacy_policy && <p className="warn">No persisted effective policy on this task (legacy run). Grants and route identity are unavailable.</p>}
    {grants.length > 0 && <><h3>Granted tools</h3><p><code>{grants.join(", ")}</code></p></>}
    {repair && <><h3>Repair lineage</h3><p>Origin: {text(repair.origin_task_id ?? repair.source_task_id)}</p><p>{text(repair.reason ?? repair.validation_reason ?? repair.finding_reason, "No persisted reason")}</p><p>Inherited patch: {text(repair.inherited_patch_fingerprint)}</p><p>Supersedes: {text(repair.superseded_task_id)}</p></>}
  </aside>;
}

function Execution({ tasks, invocations, tools, validations, selected, onSelect }: { tasks: TaskSummary[]; invocations: Row[]; tools: Row[]; validations: Row[]; selected: string | null; onSelect: (id: string) => void }) {
  return <section className="stack"><h2>Tasks</h2><table><thead><tr><th>Task</th><th>Status</th><th>Model</th><th>Route</th><th>Duration</th><th>Liveness</th><th>Summary</th></tr></thead><tbody>{tasks.map((task) => <tr className={selected === task.task_id ? "selected-row" : ""} key={task.task_id} onClick={() => onSelect(task.task_id)}><td>{task.task_id}</td><td><span className={statusClass(task.status)}>{task.status}</span></td><td>{text(task.primary_model_profile ?? task.model_profile)}</td><td>{text(task.route_class, task.legacy_policy ? "legacy" : "—")}</td><td>{duration(task.started_at, task.ended_at)}</td><td>{text(task.liveness)}</td><td>{text(task.summary)}</td></tr>)}</tbody></table>
    <h2>Model invocations</h2>
    <table><thead><tr><th>Request</th><th>Task</th><th>Route</th><th>Provider</th><th>Model</th><th>Fallback</th><th>Cost</th><th>Status</th></tr></thead>
      <tbody>{invocations.length ? invocations.map((row) => <tr key={text(row.request_id)}><td>{text(row.request_id)}</td><td>{text(row.task_id)}</td><td>{text(row.route)}</td><td>{text(row.provider)}</td><td>{text(row.resolved_model_id ?? row.model_profile)}</td><td>{row.fallback_reason ? `${text(row.fallback_reason)} → ${text(row.fallback_profile)}` : "—"}</td><td>{row.cost_usd ? `${text(row.cost_basis)} ${money(row.cost_usd)}` : "—"}</td><td>{text(row.status)}</td></tr>) : <tr><td colSpan={8}>None recorded.</td></tr>}</tbody></table>
    <h2>Tool calls</h2><JsonTable rows={tools} /><h2>Validator results</h2><JsonTable rows={validations} /></section>;
}

function duration(startedAt: string | null | undefined, endedAt: string | null | undefined) {
  return startedAt && endedAt ? `${Math.max(0, Math.round((Date.parse(endedAt) - Date.parse(startedAt)) / 1000))}s` : "—";
}

function Timeline({ events, stream }: { events: StreamEvent[]; stream: "live" | "stale" | "reconnecting" }) {
  const [type, setType] = useState("");
  const [task, setTask] = useState("");
  const [severity, setSeverity] = useState("");
  const visible = events.filter((event) => (!type || event.type.includes(type)) && (!task || event.task_id?.includes(task)) && (!severity || event.severity === severity));
  const severities = [...new Set(events.map((event) => event.severity).filter(Boolean))] as string[];
  return <section className="stack"><p className={stream === "live" ? "ok" : "warn"}>{stream === "live" ? "Live stream connected" : "Stream stale or reconnecting; durable projections remain authoritative."}</p>
    <div className="filters compact"><label>Type<input value={type} onChange={(event) => setType(event.target.value)} placeholder="task.started" /></label><label>Task<input value={task} onChange={(event) => setTask(event.target.value)} placeholder="task id" /></label><Filter label="Severity" value={severity} values={severities} onChange={setSeverity} /></div>
    {visible.map((event) => <article className="event" key={eventIdentity(event)}><time>#{text(event.seq)} · {date(event.recorded_at ?? event.occurred_at)}</time><strong>{event.type}</strong><span>{event.task_id || "run"}</span><p>{event.summary}</p><pre>{JSON.stringify(event.payload ?? {}, null, 2)}</pre>{(event.content_refs ?? []).map((ref) => <small key={ref.sha256}>capture {ref.capture_level ?? "metadata"}: {ref.logical_name || ref.sha256}</small>)}</article>)}
    {!visible.length && <p className="empty">No events match these filters.</p>}
  </section>;
}

type EvidenceItem = { id: string; kind: "artifact" | "content" | "prompt" | "projection"; label: string; detail: string; sha256?: string; value?: unknown };

function Evidence({ runId, artifacts, prompts, events, invocations, plan, lineage }: { runId: string; artifacts: Row[]; prompts: Row[]; events: StreamEvent[]; invocations: Row[]; plan?: Row; lineage?: Row }) {
  const [selected, setSelected] = useState<EvidenceItem | null>(null);
  const content = useQuery({
    queryKey: ["evidence-content", runId, selected?.kind, selected?.sha256],
    enabled: Boolean(selected?.sha256 && (selected.kind === "artifact" || selected.kind === "content")),
    queryFn: () => api<ContentView>(selected?.kind === "artifact" ? `/runs/${encodeURIComponent(runId)}/artifacts/${selected.sha256}/content` : `/runs/${encodeURIComponent(runId)}/content/${selected?.sha256}`),
  });
  const items = useMemo(() => evidenceItems(artifacts, prompts, events, invocations, plan, lineage), [artifacts, prompts, events, invocations, plan, lineage]);
  return <section className="split evidence"><div><h2>Evidence</h2>{items.length ? items.map((item) => <button className={`artifact ${selected?.id === item.id ? "selected" : ""}`} onClick={() => setSelected(item)} key={item.id}>{item.label}<small>{item.detail}</small></button>) : <p className="empty">No artifacts or captures recorded.</p>}</div><aside className="detail"><h2>Inspector</h2><EvidenceInspector item={selected} content={content.data} loading={content.isLoading} error={content.error} /></aside></section>;
}

function evidenceItems(artifacts: Row[], prompts: Row[], events: StreamEvent[], invocations: Row[], plan?: Row, lineage?: Row): EvidenceItem[] {
  const items: EvidenceItem[] = artifacts.map((artifact) => ({
    id: `artifact:${text(artifact.sha256)}`,
    kind: "artifact",
    sha256: text(artifact.sha256),
    label: text(artifact.logical_name),
    detail: `${text(artifact.media_type)} · ${number(artifact.size_bytes)} B · ${text(artifact.visibility, artifact.legacy ? "legacy_unknown" : "available")}`,
  }));
  if (plan) items.push({ id: "projection:plan", kind: "projection", label: "Compiled plan", detail: "durable projection", value: plan });
  if (lineage) items.push({ id: "projection:lineage", kind: "projection", label: "Task lineage", detail: "durable projection", value: lineage });
  for (const prompt of prompts) items.push({ id: `prompt:${text(prompt.package_hash)}`, kind: "prompt", label: `Prompt manifest · ${text(prompt.task_id)}`, detail: text(prompt.package_hash), value: prompt });
  const references: Array<[string, ContentRef]> = [];
  for (const event of events) for (const ref of event.content_refs ?? []) references.push([`event ${event.type}`, ref]);
  for (const invocation of invocations) for (const ref of Array.isArray(invocation.content_refs) ? invocation.content_refs as ContentRef[] : []) references.push([`model ${text(invocation.request_id)}`, ref]);
  for (const prompt of prompts) for (const ref of Array.isArray(prompt.content_refs) ? prompt.content_refs as ContentRef[] : []) references.push([`prompt ${text(prompt.task_id)}`, ref]);
  for (const [origin, ref] of references) {
    if (!ref.sha256 || items.some((item) => item.id === `content:${ref.sha256}`)) continue;
    items.push({ id: `content:${ref.sha256}`, kind: "content", sha256: ref.sha256, label: ref.logical_name || `Captured content · ${ref.sha256.slice(0, 12)}`, detail: `${origin} · ${ref.capture_level ?? "metadata"}` });
  }
  return items;
}

function EvidenceInspector({ item, content, loading, error }: { item: EvidenceItem | null; content?: ContentView; loading: boolean; error: unknown }) {
  if (!item) return <p>Select an authorized artifact, capture, manifest, or durable projection.</p>;
  if (item.kind === "projection" || item.kind === "prompt") return <pre>{JSON.stringify(item.value, null, 2)}</pre>;
  if (loading) return <p>Loading…</p>;
  if (error) return <p className="error">This item is no longer available for this run.</p>;
  if (!content?.available) {
    return <p>
      This capture is unavailable
      {content?.visibility ? <> (<code>{content.visibility}</code>)</> : null}
      {" "}under its stored <code>{content?.capture_level ?? "unknown"}</code> capture policy
      {content?.reason ? <> — {content.reason}</> : null}
      {content?.legacy ? <> · legacy artifact without an instance row</> : null}.
      The dashboard does not request or reconstruct unavailable content.
    </p>;
  }
  return <>
    <p><small>{content.media_type} · {number(content.byte_count)} B · {content.capture_level}{content.visibility ? ` · ${content.visibility}` : ""}{content.content_class ? ` · ${content.content_class}` : ""}</small></p>
    {content.redacted && <p className="warn">Stored content is redacted under capture policy.</p>}
    {content.truncated && <p className="warn">Stored content was truncated before it reached this dashboard.</p>}
    <pre>{typeof content.payload === "string" ? content.payload : JSON.stringify(content.payload, null, 2)}</pre>
  </>;
}

function Costs({ data }: { data?: Row }) {
  if (!data) return <p>Loading costs…</p>;
  const total = object(data.total);
  const reported = total.reported_cost_usd;
  const estimated = total.estimated_cost_usd;
  const spent = data.basis === "estimated" ? estimated : reported ?? estimated;
  const byRoute = Array.isArray(data.by_route) ? data.by_route as Row[] : [];
  return <section className="stack"><div className="metrics"><Metric name="Spend" value={money(spent)} /><Metric name="Remaining" value={money(total.remaining_budget_usd)} /><Metric name="Basis" value={text(data.basis)} /><Metric name="Latency" value={`${number(total.latency_ms)} ms`} /></div>
    {data.basis === "mixed" && <p className="notice">Mixed basis: reported cloud charges and estimated costs both contribute to this run.</p>}
    <h2>Per route (local vs cloud)</h2><JsonTable rows={byRoute} />
    <h2>Per task</h2><JsonTable rows={Array.isArray(data.by_task) ? data.by_task as Row[] : []} /><h2>Per model</h2><JsonTable rows={Array.isArray(data.by_model) ? data.by_model as Row[] : []} /><h2>Budget ledger</h2><pre>{JSON.stringify({ budget: data.budget, ledger: data.ledger }, null, 2)}</pre>
  </section>;
}

function Metric({ name, value }: { name: string; value: unknown }) { return <div className="metric"><dt>{name}</dt><dd>{text(value, "0")}</dd></div>; }

function JsonTable({ rows }: { rows: Row[] }) {
  if (!rows.length) return <p className="empty">None recorded.</p>;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((key) => !["content_refs", "usage", "result", "record_json"].includes(key));
  return <table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={text(row.id ?? row.request_id ?? row.tool_call_id, String(index))}>{columns.map((column) => <td key={column}>{typeof row[column] === "object" ? JSON.stringify(row[column]) : text(row[column])}</td>)}</tr>)}</tbody></table>;
}

function App() { return <Routes><Route path="/dashboard/" element={<RunList />} /><Route path="/dashboard/runs/:runId" element={<Detail />} /><Route path="*" element={<Navigate to="/dashboard/" replace />} /></Routes>; }

createRoot(document.getElementById("root")!).render(<QueryClientProvider client={queryClient}><BrowserRouter><App /></BrowserRouter></QueryClientProvider>);
