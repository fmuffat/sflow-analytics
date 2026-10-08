import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useExporters } from "../hooks/queries";
import { useCanEdit } from "../hooks/role";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView } from "../components/ui";
import { InterfacePicker, useGroups } from "./Groups";
import { formatBytes, formatTime } from "../lib/format";

type Period = "weekly" | "monthly";
type Format = "pdf" | "xlsx";
type Section = "summary" | "ports" | "applications" | "talkers" | "conversations" | "trends" | "alerts";

interface Options {
  sections: Section[]; exporters: string[]; monitored_groups: string[]; monitored_interfaces: string[];
  busiest_ports: number; top: number; warn_pct: number;
}
interface Delivery { formats: Format[]; email: boolean; recipients: string[] }
interface Definition {
  id: number; name: string; enabled: boolean; period: Period; options: Options; delivery: Delivery;
  last_period: string | null; next_run: string | null;
}
interface DefinitionsResponse { items: Definition[]; sections: Section[]; defaults: Options; timezone: string; send_hour: number }
interface Report {
  id: number; definition_id: number | null; title: string; period: Period | "custom"; label: string | null;
  period_start: string | null; period_end: string | null; trigger: "schedule" | "manual"; created_by: string | null;
  created_at: string; finished_at: string | null; status: "running" | "ok" | "error"; error: string | null;
  files: Partial<Record<Format, { size: number }>>;
  summary: { bytes?: number; bytes_change?: number | null; alerts?: number; storm_events?: number; hot_links?: string[] };
  email: { at: string; result: string; to?: string[] } | null; email_planned: boolean;
}

const SECTION_LABEL: Record<Section, string> = {
  summary: "Summary", ports: "Monitored links and busiest ports", applications: "Applications and protocols",
  talkers: "Top talkers", conversations: "Top conversations", trends: "Trends", alerts: "Alerts and broadcast storms",
};
const FORMAT_LABEL: Record<Format, string> = { pdf: "PDF", xlsx: "Excel" };

/** Reports: history for everyone (download), definitions and "Report now" for administrators. */
export function Reports() {
  const canEdit = useCanEdit();
  return (
    <>
      <div className="page-head">
        <h1>Reports</h1>
        <span className="sub">weekly and monthly PDF / Excel reports, e-mailed and kept 90 days</span>
      </div>
      {canEdit && <Definitions />}
      <History />
    </>
  );
}

function History() {
  const qc = useQueryClient();
  const canEdit = useCanEdit();
  const q = useQuery({
    queryKey: ["reports"],
    queryFn: () => api.get<{ items: Report[]; keep_days: number }>("/reports"),
    refetchInterval: (query) => (query.state.data?.items.some((r) => r.status === "running") ? 3000 : 60_000),
  });
  const remove = async (r: Report) => {
    if (window.confirm(`Delete the report "${r.title} · ${r.label}"?`)) { await api.del(`/reports/${r.id}`); qc.invalidateQueries({ queryKey: ["reports"] }); }
  };
  return (
    <Panel flush title="Generated reports" note={q.data ? `kept ${q.data.keep_days} days` : undefined}>
      <QueryView q={q}>
        {(d) => (
          <DataTable<Report>
            rows={d.items} rowKey={(r) => String(r.id)} initialSort={{ key: "created", desc: true }}
            empty={canEdit ? "No report yet: create a definition above, then use Report now." : "No report yet."}
            columns={[
              { key: "title", label: "Report", render: (r) => <><b>{r.title}</b><div className="muted">{r.label}</div></>, sort: (r) => r.title.toLowerCase() },
              { key: "period", label: "Period", render: (r) => r.period, sort: (r) => r.period },
              { key: "created", label: "Generated", sort: (r) => r.created_at, render: (r) => (
                <>{formatTime(r.created_at)}<div className="muted">{r.trigger === "schedule" ? "scheduled" : `by ${r.created_by ?? "?"}`}</div></>) },
              { key: "traffic", label: "Traffic", num: true, render: (r) => r.summary.bytes !== undefined ? <>
                {formatBytes(r.summary.bytes)}
                {r.summary.bytes_change != null && <div className="muted">{r.summary.bytes_change > 0 ? "+" : ""}{r.summary.bytes_change.toFixed(0)} %</div>}</> : "–" },
              { key: "status", label: "Status", render: (r) => <ReportStatus r={r} /> },
              { key: "files", label: "Download", render: (r) => (
                <span className="inline-edit">{(Object.keys(r.files) as Format[]).map((f) => (
                  <a key={f} className="btn" href={`/api/v1/reports/${r.id}/${f}`} download title={formatBytes(r.files[f]!.size)}>{FORMAT_LABEL[f]}</a>))}</span>) },
              ...(canEdit ? [{ key: "act", label: "", render: (r: Report) => <button onClick={() => remove(r)}>Delete</button> }] : []),
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

function ReportStatus({ r }: { r: Report }) {
  if (r.status === "running") return <span className="badge muted">generating…</span>;
  if (r.status === "error") return <span className="badge bad" title={r.error ?? ""}>failed</span>;
  const extra = [r.summary.alerts ? `${r.summary.alerts} alert${r.summary.alerts > 1 ? "s" : ""}` : "",
    r.summary.storm_events ? `${r.summary.storm_events} storm${r.summary.storm_events > 1 ? "s" : ""}` : "",
    r.summary.hot_links?.length ? `${r.summary.hot_links.length} port${r.summary.hot_links.length > 1 ? "s" : ""} above threshold` : ""].filter(Boolean);
  return (
    <>
      {r.email ? (r.email.result === "ok"
        ? <span className="badge ok" title={r.email.to?.join(", ") ?? ""}>e-mailed</span>
        : <span className="badge warn" title={r.email.result}>not e-mailed</span>)
        : <span className="badge ok">ready</span>}
      {extra.length > 0 && <div className="muted">{extra.join(" · ")}</div>}
      {r.error && r.email?.result !== "ok" && <div className="warn-text" style={{ fontSize: 11 }}>{r.email?.result}</div>}
    </>
  );
}

function schedule(d: Definition, sendHour: number): string {
  const h = `${String(sendHour).padStart(2, "0")}:00`;
  return d.period === "weekly" ? `Weekly · previous week, Monday ${h}` : `Monthly · previous month, 1st ${h}`;
}

function Definitions() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["report-definitions"], queryFn: () => api.get<DefinitionsResponse>("/report-definitions") });
  const [edit, setEdit] = useState<(Omit<Definition, "id" | "last_period" | "next_run"> & { id?: number }) | null>(null);
  const [run, setRun] = useState<Definition | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["report-definitions"] });
  const save = (d: Definition) => api.put(`/report-definitions/${d.id}`, d).then(refresh);
  const remove = async (d: Definition) => {
    if (window.confirm(`Delete the report definition "${d.name}"? Generated reports are kept.`)) { await api.del(`/report-definitions/${d.id}`); refresh(); }
  };
  const blank = (defaults: Options) => ({ name: "", enabled: true, period: "weekly" as Period, options: { ...defaults }, delivery: { formats: ["pdf", "xlsx"] as Format[], email: false, recipients: [] } });
  return (
    <>
      {edit && q.data && <DefinitionForm def={edit} sections={q.data.sections} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); refresh(); }} />}
      {run && <RunForm def={run} onClose={() => setRun(null)} />}
      <Panel
        flush title="Report definitions" note={q.data ? `times in ${q.data.timezone}; e-mail through Administration → Notifications` : undefined}
        actions={q.data && <button className="primary" onClick={() => setEdit(blank(q.data.defaults))}>New report</button>}
      >
        <QueryView q={q}>
          {(d) => (
            <DataTable<Definition>
              rows={d.items} rowKey={(r) => String(r.id)} empty="No report defined: New report to create a weekly or monthly report."
              columns={[
                { key: "on", label: "Enabled", render: (r) => <input type="checkbox" checked={r.enabled} onChange={() => save({ ...r, enabled: !r.enabled })} /> },
                { key: "name", label: "Name", render: (r) => <b>{r.name}</b>, sort: (r) => r.name.toLowerCase() },
                { key: "when", label: "Schedule", render: (r) => schedule(r, d.send_hour) },
                { key: "what", label: "Content", render: (r) => <span className="muted">
                  {r.options.sections.length} section{r.options.sections.length > 1 ? "s" : ""}
                  {r.options.monitored_groups.length + r.options.monitored_interfaces.length > 0 &&
                    ` · monitored: ${[...r.options.monitored_groups, r.options.monitored_interfaces.length ? `${r.options.monitored_interfaces.length} port(s)` : ""].filter(Boolean).join(", ")}`}
                  {r.options.exporters.length > 0 && ` · ${r.options.exporters.length} switch(es)`}</span> },
                { key: "out", label: "Delivery", render: (r) => <>{r.delivery.formats.map((f) => FORMAT_LABEL[f]).join(" + ")}
                  <div className="muted">{r.delivery.email ? `e-mail to ${r.delivery.recipients.length ? r.delivery.recipients.join(", ") : "default recipients"}` : "no e-mail"}</div></> },
                { key: "next", label: "Next report", render: (r) => r.next_run ? formatTime(r.next_run) : <span className="muted">disabled</span> },
                { key: "act", label: "", render: (r) => <span className="inline-edit">
                  <button onClick={() => setRun(r)}>Report now</button><button onClick={() => setEdit(r)}>Edit</button><button onClick={() => remove(r)}>Delete</button></span> },
              ]}
            />
          )}
        </QueryView>
      </Panel>
      <div style={{ height: 12 }} />
    </>
  );
}

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

function DefinitionForm(props: { def: Omit<Definition, "id" | "last_period" | "next_run"> & { id?: number }; sections: Section[]; onClose: () => void; onSaved: () => void }) {
  const [d, setD] = useState(props.def);
  const [recipients, setRecipients] = useState(props.def.delivery.recipients.join(", "));
  const [pick, setPick] = useState(props.def.options.monitored_interfaces.length > 0);
  const [err, setErr] = useState<string | null>(null);
  const exporters = useExporters();
  const groups = useGroups();
  const ifGroups = groups.data?.items.filter((g) => g.kind === "if") ?? [];
  const o = d.options;
  const setO = (patch: Partial<Options>) => setD({ ...d, options: { ...o, ...patch } });
  const setDl = (patch: Partial<Delivery>) => setD({ ...d, delivery: { ...d.delivery, ...patch } });
  const save = async () => {
    setErr(null);
    const body = { ...d, delivery: { ...d.delivery, recipients: recipients.split(/[\s,;]+/).filter(Boolean) } };
    try {
      if (d.id) await api.put(`/report-definitions/${d.id}`, body); else await api.post("/report-definitions", body);
      props.onSaved();
    } catch (e) { setErr((e as Error).message); }
  };
  const num = (k: "busiest_ports" | "top" | "warn_pct", label: string, unit?: string) => (
    <label>{label}{unit && <span className="muted"> ({unit})</span>}
      <input type="number" value={o[k]} onChange={(e) => setO({ [k]: Number(e.target.value) })} /></label>
  );
  return (
    <div style={{ marginBottom: 12 }}>
      <Panel title={d.id ? "Edit report" : "New report"} actions={<button onClick={props.onClose}>Cancel</button>}>
        <div className="filters" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
          <label style={{ gridColumn: "span 2" }}>Name (report title)<input maxLength={80} value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="Weekly network report" /></label>
          <label>Period<select value={d.period} onChange={(e) => setD({ ...d, period: e.target.value as Period })}>
            <option value="weekly">weekly (previous Monday–Sunday)</option><option value="monthly">monthly (previous month)</option></select></label>
          {num("warn_pct", "Highlight ports above", "% 95th percentile")}
          {num("busiest_ports", "Busiest ports", "0 = none")}
          {num("top", "Rows in top tables")}
        </div>

        <h4 style={{ margin: "12px 0 4px" }}>Sections</h4>
        <div className="inline-edit" style={{ flexWrap: "wrap" }}>
          {props.sections.map((s) => (
            <label key={s} className="pick"><input type="checkbox" checked={o.sections.includes(s)} onChange={() => setO({ sections: props.sections.filter((x) => x === s ? !o.sections.includes(s) : o.sections.includes(x)) })} /> {SECTION_LABEL[s]}</label>))}
        </div>

        <h4 style={{ margin: "12px 0 4px" }}>Monitored links <span className="muted" style={{ fontWeight: "normal" }}>always shown, even when quiet</span></h4>
        <div className="inline-edit" style={{ flexWrap: "wrap" }}>
          <span className="muted">Interface groups:</span>
          {ifGroups.length === 0 && <span className="muted">none defined (Inventory → Groups)</span>}
          {ifGroups.map((g) => (
            <label key={g.name} className="pick"><input type="checkbox" checked={o.monitored_groups.includes(g.name)} onChange={() => setO({ monitored_groups: toggle(o.monitored_groups, g.name) })} /> {g.name} <span className="muted">({g.members.length})</span></label>))}
          <label className="pick"><input type="checkbox" checked={pick} onChange={(e) => { setPick(e.target.checked); if (!e.target.checked) setO({ monitored_interfaces: [] }); }} /> select ports one by one{o.monitored_interfaces.length > 0 && ` (${o.monitored_interfaces.length})`}</label>
        </div>
        {pick && <div style={{ marginTop: 6 }}><InterfacePicker selected={o.monitored_interfaces} onChange={(m) => setO({ monitored_interfaces: m })} /></div>}

        <h4 style={{ margin: "12px 0 4px" }}>Scope</h4>
        <div className="inline-edit" style={{ flexWrap: "wrap" }}>
          <label className="pick"><input type="checkbox" checked={o.exporters.length === 0} onChange={() => setO({ exporters: [] })} /> all switches</label>
          {exporters.data?.items.map((e) => (
            <label key={e.id} className="pick"><input type="checkbox" checked={o.exporters.includes(e.id)} onChange={() => setO({ exporters: toggle(o.exporters, e.id) })} /> {e.name}</label>))}
        </div>

        <h4 style={{ margin: "12px 0 4px" }}>Delivery</h4>
        <div className="inline-edit" style={{ flexWrap: "wrap" }}>
          {(["pdf", "xlsx"] as Format[]).map((f) => (
            <label key={f} className="pick"><input type="checkbox" checked={d.delivery.formats.includes(f)} onChange={() => setDl({ formats: toggle(d.delivery.formats, f) })} /> {FORMAT_LABEL[f]}</label>))}
          <label className="pick"><input type="checkbox" checked={d.delivery.email} onChange={(e) => setDl({ email: e.target.checked })} /> send by e-mail</label>
          <label className="pick"><input type="checkbox" checked={d.enabled} onChange={(e) => setD({ ...d, enabled: e.target.checked })} /> enabled (scheduled)</label>
        </div>
        {d.delivery.email && (
          <label style={{ display: "block", marginTop: 8 }}>E-mail recipients <span className="muted">(empty = default recipients of the e-mail channel)</span>
            <input style={{ width: "100%" }} value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="noc@example.com, it@example.com" />
          </label>)}
        <div className="inline-edit" style={{ marginTop: 10 }}>
          <button className="primary" disabled={!d.name.trim() || o.sections.length === 0 || d.delivery.formats.length === 0} onClick={save}>Save report</button>
          {!d.id && <span className="muted">the first scheduled report covers the next {d.period === "weekly" ? "week" : "month"}; use Report now for the last one</span>}
          {err && <span className="error" style={{ padding: 0 }}>{err}</span>}
        </div>
      </Panel>
    </div>
  );
}

function RunForm({ def, onClose }: { def: Definition; onClose: () => void }) {
  const qc = useQueryClient();
  const [period, setPeriod] = useState<Period | "custom">(def.period);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [email, setEmail] = useState(false);
  const m = useMutation({
    mutationFn: () => api.post(`/report-definitions/${def.id}/run`, { period, email, ...(period === "custom" ? { start, end } : {}) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reports"] }); onClose(); },
  });
  return (
    <div style={{ marginBottom: 12 }}>
      <Panel title={`Report now · ${def.name}`} actions={<button onClick={onClose}>Cancel</button>}>
        <div className="inline-edit" style={{ flexWrap: "wrap" }}>
          <label className="pick"><input type="radio" checked={period === "weekly"} onChange={() => setPeriod("weekly")} /> last complete week</label>
          <label className="pick"><input type="radio" checked={period === "monthly"} onChange={() => setPeriod("monthly")} /> last complete month</label>
          <label className="pick"><input type="radio" checked={period === "custom"} onChange={() => setPeriod("custom")} /> custom period</label>
          {period === "custom" && <>
            <label className="pick">from <input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
            <label className="pick">to <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /> <span className="muted">(included)</span></label>
          </>}
          <label className="pick"><input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} /> also send by e-mail
            {email && <span className="muted"> to {def.delivery.recipients.length ? def.delivery.recipients.join(", ") : "the default recipients"}</span>}</label>
        </div>
        <div className="inline-edit" style={{ marginTop: 10 }}>
          <button className="primary" disabled={m.isPending || (period === "custom" && (!start || !end))} onClick={() => m.mutate()}>Generate</button>
          <span className="muted">generated in the background; it appears in the list below (usually a few seconds)</span>
          {m.isError && <span className="error" style={{ padding: 0 }}>{(m.error as Error).message}</span>}
        </div>
      </Panel>
    </div>
  );
}
