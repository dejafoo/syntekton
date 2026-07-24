import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrowserRouter, Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./styles.css";

type Dict = Record<string, any>;
const api = async <T,>(path: string): Promise<T> => {
  const response = await fetch(`/api/v1${path}`);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};
const money = (value: unknown) => `$${Number(value || 0).toFixed(4)}`;
const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : "—";
const statusClass = (status: string) => `status ${status.replaceAll("_", "-")}`;

function useRuns() {
  return useQuery({ queryKey: ["runs"], queryFn: () => api<Dict[]>("/runs"), refetchInterval: document.visibilityState === "visible" ? 2000 : false });
}

function RunList() {
  const { data: runs = [], isLoading, error } = useRuns();
  const [status, setStatus] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [liveness, setLiveness] = useState("");
  const visible = runs.filter(run => (!status || run.status === status) && (!workflow || run.workflow_type === workflow) && (!liveness || run.liveness === liveness));
  const values = (key: string) => [...new Set(runs.map(run => run[key]).filter(Boolean))];
  return <main className="page"><header><p className="eyebrow">LOCAL · MONITOR ONLY</p><h1>Product Factory</h1><p>Durable orchestration runs. Mutations remain in the CLI.</p></header>
    <div className="filters">
      {[["Status", status, setStatus, "status"], ["Workflow", workflow, setWorkflow, "workflow_type"], ["Liveness", liveness, setLiveness, "liveness"]].map(([label, selected, set, key]) => <label key={String(key)}>{String(label)}<select value={String(selected)} onChange={e => (set as any)(e.target.value)}><option value="">All</option>{values(String(key)).map(value => <option key={value}>{value}</option>)}</select></label>)}
    </div>
    {isLoading && <p>Loading runs…</p>}{error && <p className="error">Could not load runs: {String(error)}</p>}
    {!isLoading && !visible.length && <section className="empty">No runs yet. Start a run with <code>product-factory run …</code>, then refresh this page.</section>}
    <section className="run-list">{visible.map(run => <Link className="run-card" to={`/dashboard/runs/${run.run_id}`} key={run.run_id}><div><strong>{run.run_id}</strong><span>{run.workflow_type}</span></div><span className={statusClass(run.status)}>{run.status}</span><dl><div><dt>Tasks</dt><dd>{Object.values(run.task_counts || {}).reduce<number>((total, value) => total + Number(value), 0)}</dd></div><div><dt>Cloud cost</dt><dd>{money(run.usage?.reported_cost_usd ?? run.usage?.estimated_cost_usd)}</dd></div><div><dt>Liveness</dt><dd>{run.liveness}</dd></div><div><dt>Active</dt><dd>{run.active_operation || "—"}</dd></div></dl><small>{date(run.updated_at)} · {run.error_count || 0} errors</small></Link>)}</section>
  </main>;
}

function useLiveRun(runId: string, afterSeq: number) {
  const client = useQueryClient(); const [state, setState] = useState<"live" | "stale" | "reconnecting">("stale"); const seen = useRef(new Set<number>()); const last = useRef(afterSeq);
  useEffect(() => { last.current = afterSeq; seen.current.clear(); let source: EventSource | undefined; let timer = 0; let attempt = 0; let closed = false;
    const connect = () => { if (closed) return; setState(attempt ? "reconnecting" : "stale"); source = new EventSource(`/api/v1/runs/${encodeURIComponent(runId)}/events/stream?after_seq=${last.current}`); const receive = (event: MessageEvent) => { const item = JSON.parse(event.data); if (item.type === "heartbeat") { setState("live"); return; } if (typeof item.seq === "number" && !seen.current.has(item.seq)) { seen.current.add(item.seq); last.current = Math.max(last.current, item.seq); client.setQueryData<Dict[]>(["events", runId], old => [...(old || []), item]); client.invalidateQueries({ queryKey: ["run", runId] }); client.invalidateQueries({ queryKey: ["tasks", runId] }); client.invalidateQueries({ queryKey: ["costs", runId] }); } setState("live"); attempt = 0; }; source.onmessage = receive; ["run.started", "run.status_changed", "run.finished", "run.failed", "run.no_progress", "repository.snapshot", "plan.compiled", "plan.rejected", "task.started", "task.completed", "task.failed", "prompt.package_created", "model.request.started", "model.request.completed", "model.request.failed", "tool.call.started", "tool.call.completed", "tool.call.failed", "validation.completed", "artifact.created", "approval.required", "approval.decided", "budget.updated", "heartbeat", "observability.degraded", "repair.budget_exhausted", "workflow.pack_resolved"].forEach(type => source?.addEventListener(type, receive)); source.onerror = () => { source?.close(); setState("stale"); timer = window.setTimeout(connect, Math.min(10000, 250 * 2 ** attempt++)); }; };
    connect(); return () => { closed = true; source?.close(); window.clearTimeout(timer); };
  }, [runId, afterSeq, client]); return state;
}

function graph(tasks: Dict[], selected: string | null): { nodes: Node[]; edges: Edge[] } {
  const level = new Map<string, number>(); const getLevel = (task: Dict): number => { if (level.has(task.task_id)) return level.get(task.task_id)!; const value = Math.max(0, ...(task.dependencies || []).map((id: string) => getLevel(tasks.find(t => t.task_id === id) || { task_id: id, dependencies: [] }) + 1)); level.set(task.task_id, value); return value; };
  const layers = new Map<number, number>(); return { nodes: tasks.map(task => { const x = getLevel(task); const y = layers.get(x) || 0; layers.set(x, y + 1); return { id: task.task_id, position: { x: x * 260, y: y * 120 }, data: { label: <div><b>{task.task_id}</b><br /><small>{task.title || task.capability}</small><br /><span className={statusClass(task.status)}>{task.status}</span></div> }, className: selected === task.task_id ? "selected-node" : task.capability === "repair" ? "repair-node" : "" }; }), edges: tasks.flatMap(task => (task.dependencies || []).map((dependency: string) => ({ id: `${dependency}-${task.task_id}`, source: dependency, target: task.task_id, animated: task.status === "running" }))) };
}

function Detail() {
  const { runId = "" } = useParams(); const [tab, setTab] = useState("Plan"); const [selected, setSelected] = useState<string | null>(null);
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api<Dict>(`/runs/${runId}`) });
  const tasks = useQuery({ queryKey: ["tasks", runId], queryFn: () => api<Dict[]>(`/runs/${runId}/tasks`) });
  const plan = useQuery({ queryKey: ["plan", runId], queryFn: () => api<Dict>(`/runs/${runId}/plan`) });
  const lineage = useQuery({ queryKey: ["lineage", runId], queryFn: () => api<Dict>(`/runs/${runId}/lineage`) });
  const costs = useQuery({ queryKey: ["costs", runId], queryFn: () => api<Dict>(`/runs/${runId}/costs`) });
  const events = useQuery({ queryKey: ["events", runId], queryFn: async () => (await api<Dict>(`/runs/${runId}/events?limit=500`)).items });
  const invocations = useQuery({ queryKey: ["invocations", runId], queryFn: () => api<Dict[]>(`/runs/${runId}/model-invocations`) });
  const tools = useQuery({ queryKey: ["tools", runId], queryFn: () => api<Dict[]>(`/runs/${runId}/tool-calls`) });
  const validations = useQuery({ queryKey: ["validations", runId], queryFn: () => api<Dict[]>(`/runs/${runId}/validations`) });
  const artifacts = useQuery({ queryKey: ["artifacts", runId], queryFn: () => api<Dict[]>(`/runs/${runId}/artifacts`) });
  const stream = useLiveRun(runId, run.data?.latest_seq || 0); const selectedTask = tasks.data?.find(t => t.task_id === selected);
  const flow = useMemo(() => graph(tasks.data || [], selected), [tasks.data, selected]);
  if (run.error) return <main className="page"><p className="error">Run could not be found.</p><Link to="/dashboard/">Back to runs</Link></main>;
  if (!run.data) return <main className="page"><Link className="back" to="/dashboard/">← Runs</Link><p>Loading run…</p></main>;
  const currentRun = run.data;
  return <main className="page"><Link className="back" to="/dashboard/">← Runs</Link><><header className="run-header"><div><p className="eyebrow">{currentRun.workflow_type}</p><h1>{runId}</h1><p>{currentRun.active_operation || "No active operation"} · {date(currentRun.updated_at)}</p></div><span className={`${statusClass(currentRun.status)} ${stream}`}>{stream === "live" ? "live · " : ""}{currentRun.status}</span></header><nav className="tabs">{["Plan", "Execution", "Timeline", "Evidence", "Costs"].map(name => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{name}</button>)}</nav>
    {tab === "Plan" && <section className="split"><div className="graph"><ReactFlow nodes={flow.nodes} edges={flow.edges} onNodeClick={(_, node) => setSelected(node.id)} fitView><Background /><MiniMap /><Controls /></ReactFlow></div><TaskDetail task={selectedTask} repair={lineage.data?.repairs?.find((r: Dict) => r.task_id === selected)} /><Kanban tasks={tasks.data || []} onSelect={setSelected} /></section>}
    {tab === "Execution" && <Execution tasks={tasks.data || []} invocations={invocations.data || []} tools={tools.data || []} validations={validations.data || []} onSelect={setSelected} />}
    {tab === "Timeline" && <Timeline events={events.data || []} stream={stream} />}
    {tab === "Evidence" && <Evidence runId={runId} artifacts={artifacts.data || []} plan={plan.data} lineage={lineage.data} />}
    {tab === "Costs" && <Costs data={costs.data} />}</></main>;
}

function Kanban({ tasks, onSelect }: { tasks: Dict[]; onSelect: (id: string) => void }) { const groups: Dict = { queued: [], running: [], repairing: [], succeeded: [], "failed / blocked": [] }; tasks.forEach(task => groups[task.capability === "repair" && !["succeeded", "failed", "blocked"].includes(task.status) ? "repairing" : task.status === "pending" ? "queued" : task.status === "success" ? "succeeded" : ["failed", "blocked"].includes(task.status) ? "failed / blocked" : "running"].push(task)); return <div className="kanban">{Object.entries(groups).map(([name, items]: any) => <div key={name}><h3>{name}</h3>{items.map((task: Dict) => <button className="task-chip" onClick={() => onSelect(task.task_id)} key={task.task_id}>{task.task_id} {task.title}</button>)}</div>)}</div>; }
function TaskDetail({ task, repair }: { task?: Dict; repair?: Dict }) { return <aside className="detail">{task ? <><h2>{task.task_id}</h2><p className={statusClass(task.status)}>{task.status}</p><p>{task.summary || task.title || "No terminal summary yet."}</p><dl><div><dt>Model</dt><dd>{task.model_profile || "—"}</dd></div><div><dt>Usage</dt><dd>{JSON.stringify(task.usage || {})}</dd></div><div><dt>Dependencies</dt><dd>{(task.dependencies || []).join(", ") || "—"}</dd></div></dl>{repair && <><h3>Repair lineage</h3><p>Origin: {repair.origin_task_id || "not derivable from this durable run"}</p><p>{repair.reason}</p><p>Inherited patch: {repair.inherited_patch_fingerprint || "—"}</p></>}</> : <p>Select a task in the graph or kanban.</p>}</aside>; }
function Execution({ tasks, invocations, tools, validations, onSelect }: any) { return <section className="stack"><h2>Tasks</h2><table><thead><tr><th>Task</th><th>Status</th><th>Model</th><th>Duration</th><th>Summary</th></tr></thead><tbody>{tasks.map((task: Dict) => <tr key={task.task_id} onClick={() => onSelect(task.task_id)}><td>{task.task_id}</td><td><span className={statusClass(task.status)}>{task.status}</span></td><td>{task.model_profile || "—"}</td><td>{task.started_at && task.ended_at ? `${Math.round((Date.parse(task.ended_at) - Date.parse(task.started_at)) / 1000)}s` : "—"}</td><td>{task.summary}</td></tr>)}</tbody></table><h2>Model invocations</h2><JsonTable rows={invocations} /><h2>Tools</h2><JsonTable rows={tools} /><h2>Validator results</h2><JsonTable rows={validations} /></section>; }
function Timeline({ events, stream }: any) { const [filter, setFilter] = useState(""); return <section className="stack"><p className={stream === "live" ? "ok" : "warn"}>{stream === "live" ? "Live stream connected" : "Stream stale or reconnecting; projections remain durable."}</p><label>Event filter <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="task.started" /></label>{events.filter((event: Dict) => !filter || event.type.includes(filter) || event.task_id?.includes(filter)).map((event: Dict) => <article className="event" key={event.event_id}><time>#{event.seq} · {date(event.recorded_at)}</time><strong>{event.type}</strong><span>{event.task_id || "run"}</span><p>{event.summary}</p><pre>{JSON.stringify(event.payload, null, 2)}</pre>{event.content_refs?.map((ref: Dict) => <small key={ref.sha256}>capture {ref.capture_level}: {ref.logical_name || ref.sha256}</small>)}</article>)}</section>; }
function Evidence({ runId, artifacts, plan, lineage }: any) { const [selected, setSelected] = useState<Dict | null>(null); const content = useQuery({ queryKey: ["artifact-content", runId, selected?.sha256], enabled: !!selected, queryFn: () => api<Dict>(`/runs/${runId}/artifacts/${selected?.sha256}/content`) }); return <section className="split evidence"><div><h2>Artifacts</h2>{artifacts.map((item: Dict) => <button className="artifact" onClick={() => setSelected(item)} key={item.sha256}>{item.logical_name}<small>{item.media_type} · {item.size_bytes} B</small></button>)}<h2>Plan</h2><pre>{JSON.stringify(plan, null, 2)}</pre><h2>Lineage</h2><pre>{JSON.stringify(lineage, null, 2)}</pre></div><aside className="detail"><h2>Inspector</h2>{selected ? content.isLoading ? <p>Loading…</p> : content.data?.available ? <pre>{typeof content.data.payload === "string" ? content.data.payload : JSON.stringify(content.data.payload, null, 2)}</pre> : <p>This capture is unavailable under its stored capture policy.</p> : <p>Select an authorized run artifact.</p>}</aside></section>; }
function Costs({ data }: any) { if (!data) return <p>Loading costs…</p>; return <section className="stack"><div className="metrics"><Metric name="Spend" value={money(data.basis === "reported" ? data.total.reported_cost_usd : data.total.estimated_cost_usd)} /><Metric name="Remaining" value={money(data.total.remaining_budget_usd)} /><Metric name="Basis" value={data.basis} /><Metric name="Latency" value={`${data.total.latency_ms || 0} ms`} /></div><h2>Per task</h2><JsonTable rows={data.by_task} /><h2>Per model</h2><JsonTable rows={data.by_model} /><h2>Budget ledger</h2><pre>{JSON.stringify({ budget: data.budget, ledger: data.ledger }, null, 2)}</pre></section>; }
function Metric({ name, value }: any) { return <div className="metric"><small>{name}</small><strong>{value}</strong></div>; }
function JsonTable({ rows }: { rows: Dict[] }) { if (!rows.length) return <p className="empty">None recorded.</p>; const columns = Object.keys(rows[0]).filter(key => !["content_refs", "usage", "result", "record_json"].includes(key)); return <table><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={row.id || row.request_id || row.tool_call_id || index}>{columns.map(column => <td key={column}>{typeof row[column] === "object" ? JSON.stringify(row[column]) : String(row[column] ?? "—")}</td>)}</tr>)}</tbody></table>; }
function App() { return <Routes><Route path="/dashboard/" element={<RunList />} /><Route path="/dashboard/runs/:runId" element={<Detail />} /><Route path="*" element={<Navigate to="/dashboard/" replace />} /></Routes>; }
createRoot(document.getElementById("root")!).render(<React.StrictMode><QueryClientProvider client={new QueryClient()}><BrowserRouter><App /></BrowserRouter></QueryClientProvider></React.StrictMode>);
