import { type ReactElement, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useExporters } from "../hooks/queries";
import { useCanEdit } from "../hooks/role";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView, Tabs } from "../components/ui";
import { useGroups } from "./Groups";
import { formatAgo, formatDuration, formatTime } from "../lib/format";

type Severity = "critical" | "warning" | "info";
type Kind = "utilization" | "discards" | "broadcast" | "exporter_silent" | "traffic";
type Channel = "email" | "webhook" | "syslog";

export interface Alert {
  id: number; rule_id: number; rule: string; kind: Kind; object_key: string; subject: string; link: string | null;
  state: "open" | "acknowledged" | "closed"; severity: Severity; message: string; value: number | null; peak: number | null;
  threshold: number | null; opened_at: string; last_seen_at: string; closed_at: string | null;
  acked_by: string | null; acked_at: string | null; notified: Record<string, { at: string; result: Record<string, string> }>;
}
export interface Counts { open: number; acknowledged: number; critical: number; warning: number; info: number }
interface Rule {
  id: number; name: string; kind: Kind; enabled: boolean; severity: Severity;
  params: Record<string, string | number>; scope: { exporter?: string; if_group?: string };
  notify: { channels?: Channel[]; recipients?: string[]; notify_resolved?: boolean };
}
interface RulesResponse { items: Rule[]; kinds: Record<Kind, Record<string, string | number>> }

export const useAlertCounts = () =>
  useQuery({ queryKey: ["alert-counts"], queryFn: () => api.get<Counts>("/alerts/counts"), refetchInterval: 30_000 });

const KIND_LABEL: Record<Kind, string> = {
  utilization: "Port utilization", discards: "Discards / errors", broadcast: "Broadcast storm",
  exporter_silent: "Switch silent", traffic: "Traffic of a host or group",
};

export function SeverityBadge({ s }: { s: Severity }) {
  return <span className={`badge ${s === "critical" ? "bad" : s === "warning" ? "warn" : "muted"}`}>{s}</span>;
}

function condition(r: Rule): string {
  const p = r.params;
  const scope = [r.scope.exporter && `switch ${r.scope.exporter}`, r.scope.if_group && `group ${r.scope.if_group}`].filter(Boolean).join(", ");
  const s = scope ? ` · ${scope}` : "";
  switch (r.kind) {
    case "utilization": return `average ≥ ${p.threshold_pct} % (${p.direction}) for ${p.window_min} min${s}`;
    case "discards": return `${p.metric} ≥ ${Number(p.threshold).toLocaleString()} in ${p.window_min} min${s}`;
    case "broadcast": return `broadcast ≥ ${Number(p.threshold_pps).toLocaleString()} pps for ${p.window_min} min${s}`;
    case "exporter_silent": return `no sFlow for ${p.silent_min} min${s}`;
    case "traffic": return `${p.target === "ip" ? "host" : p.target === "ip_group" ? "IP group" : "interface group"} ${p.value} (${p.direction}) ≥ ${(Number(p.threshold_bps) / 1e6).toLocaleString()} Mb/s for ${p.window_min} min${s}`;
  }
}

/** Alerts: active ones (open, acknowledged), history, and the rules. */
export function Alerts() {
  const [tab, setTab] = useState<"active" | "closed" | "rules">("active");
  const counts = useAlertCounts();
  return (
    <>
      <div className="page-head">
        <h1>Alerts</h1>
        <span className="sub">rules evaluated every minute by the worker</span>
        <span className="spacer" />
        {counts.data && <span className="inline-edit">
          {counts.data.critical > 0 && <span className="badge bad">{counts.data.critical} critical</span>}
          {counts.data.warning > 0 && <span className="badge warn">{counts.data.warning} warning</span>}
          {counts.data.acknowledged > 0 && <span className="badge muted">{counts.data.acknowledged} acknowledged</span>}
          {counts.data.open + counts.data.acknowledged === 0 && <span className="badge ok">no active alert</span>}
        </span>}
      </div>
      <Tabs tabs={[["active", "Active"], ["closed", "History"], ["rules", "Rules"]]} value={tab} onChange={setTab} />
      {tab === "rules" ? <Rules /> : <AlertList state={tab} />}
    </>
  );
}

function AlertList({ state }: { state: "active" | "closed" }) {
  const qc = useQueryClient();
  const canEdit = useCanEdit();
  const q = useQuery({ queryKey: ["alerts", state], queryFn: () => api.get<{ items: Alert[] }>("/alerts", undefined, { state, limit: 500 }), refetchInterval: 30_000 });
  const act = async (a: Alert, what: "ack" | "close") => {
    await api.post(`/alerts/${a.id}/${what}`);
    qc.invalidateQueries({ queryKey: ["alerts"] });
    qc.invalidateQueries({ queryKey: ["alert-counts"] });
  };
  const dur = (a: Alert) => formatDuration((new Date(a.closed_at ?? Date.now()).getTime() - new Date(a.opened_at).getTime()) / 1000);
  return (
    <Panel flush title={state === "active" ? "Active alerts" : "History"} note={state === "closed" ? "closed alerts, kept 180 days" : "open and acknowledged; they close by themselves when the condition ends"}>
      <QueryView q={q}>
        {(d) => (
          <DataTable<Alert>
            rows={d.items} rowKey={(a) => String(a.id)}
            empty={state === "active" ? "No active alert." : "No alert in the history."}
            columns={[
              { key: "sev", label: "Severity", render: (a) => <SeverityBadge s={a.severity} />, sort: (a) => ["critical", "warning", "info"].indexOf(a.severity) },
              ...(state === "active" ? [{ key: "state", label: "State", render: (a: Alert) => a.state === "open"
                  ? <span className="badge bad">open</span>
                  : <span className="badge muted" title={a.acked_at ? `by ${a.acked_by} at ${formatTime(a.acked_at)}` : ""}>acknowledged · {a.acked_by}</span> }] : []),
              { key: "rule", label: "Rule", render: (a) => a.rule, sort: (a) => a.rule },
              { key: "subject", label: "On", render: (a) => a.link ? <Link to={a.link}>{a.subject}</Link> : a.subject, sort: (a) => a.subject },
              { key: "msg", label: "Details", render: (a) => <span className="muted">{a.message}</span> },
              { key: "since", label: state === "active" ? "Since" : "Opened", render: (a) => <span title={formatTime(a.opened_at)}>{state === "active" ? formatAgo(a.opened_at) : formatTime(a.opened_at)}</span>, sort: (a) => a.opened_at },
              { key: "dur", label: "Duration", render: dur, sort: (a) => new Date(a.closed_at ?? Date.now()).getTime() - new Date(a.opened_at).getTime(), num: true },
              { key: "notif", label: "Notified", render: (a) => {
                  const n = a.notified?.open?.result;
                  if (!n) return <span className="muted">–</span>;
                  const bad = Object.entries(n).filter(([, v]) => v !== "ok");
                  return bad.length ? <span className="badge warn" title={bad.map(([k, v]) => `${k}: ${v}`).join("\n")}>failed</span>
                    : <span className="muted">{Object.keys(n).join(", ")}</span>;
                } },
              ...(state === "active" && canEdit ? [{ key: "act", label: "", render: (a: Alert) => (
                <span className="inline-edit">
                  {a.state === "open" && <button onClick={() => act(a, "ack")}>Acknowledge</button>}
                  <button onClick={() => act(a, "close")} title="Closes it now; it reopens if the condition is still true">Close</button>
                </span>) }] : []),
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

const EMPTY_RULE: Omit<Rule, "id"> = {
  name: "", kind: "utilization", enabled: true, severity: "warning",
  params: { threshold_pct: 80, window_min: 5, direction: "either" }, scope: {}, notify: { channels: [], recipients: [], notify_resolved: true },
};

function Rules() {
  const qc = useQueryClient();
  const canEdit = useCanEdit();
  const q = useQuery({ queryKey: ["alert-rules"], queryFn: () => api.get<RulesResponse>("/alerts/rules") });
  const [edit, setEdit] = useState<(Omit<Rule, "id"> & { id?: number }) | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["alert-rules"] }); qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["alert-counts"] }); };
  const toggle = async (r: Rule) => { await api.put(`/alerts/rules/${r.id}`, { ...r, enabled: !r.enabled }); refresh(); };
  const remove = async (r: Rule) => { if (window.confirm(`Delete the rule "${r.name}" and its alerts?`)) { await api.del(`/alerts/rules/${r.id}`); refresh(); } };
  const evaluate = useMutation({
    mutationFn: () => api.post<{ rules: number; opened: number; closed: number; active: number; errors: Record<string, string> }>("/alerts/evaluate"),
    onSuccess: (r) => { setMsg(`${r.rules} rule(s) evaluated: ${r.opened} opened, ${r.closed} closed, ${r.active} active` + (Object.keys(r.errors).length ? ` · errors: ${Object.entries(r.errors).map(([k, v]) => `${k}: ${v}`).join("; ")}` : "")); refresh(); },
    onError: (e) => setMsg((e as Error).message),
  });
  return (
    <>
      {edit && q.data && <RuleForm rule={edit} kinds={q.data.kinds} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); refresh(); }} />}
      <Panel
        flush title="Rules" note={msg ?? "notifications: set the channels in Administration → Notifications"}
        actions={canEdit ? <span className="inline-edit">
          <button onClick={() => evaluate.mutate()} disabled={evaluate.isPending}>Evaluate now</button>
          <button className="primary" onClick={() => setEdit({ ...EMPTY_RULE })}>New rule</button>
        </span> : undefined}
      >
        <QueryView q={q}>
          {(d) => (
            <DataTable<Rule>
              rows={d.items} rowKey={(r) => String(r.id)} initialSort={{ key: "name", desc: false }} empty="No rule."
              columns={[
                { key: "on", label: "Enabled", render: (r) => canEdit
                    ? <input type="checkbox" checked={r.enabled} onChange={() => toggle(r)} />
                    : r.enabled ? "yes" : "no" },
                { key: "name", label: "Name", render: (r) => <b>{r.name}</b>, sort: (r) => r.name.toLowerCase() },
                { key: "kind", label: "Type", render: (r) => KIND_LABEL[r.kind], sort: (r) => r.kind },
                { key: "cond", label: "Condition", render: (r) => <span className="muted">{condition(r)}</span> },
                { key: "sev", label: "Severity", render: (r) => <SeverityBadge s={r.severity} /> },
                { key: "ch", label: "Notify", render: (r) => r.notify.channels?.length ? r.notify.channels.join(", ") : <span className="muted">page only</span> },
                ...(canEdit ? [{ key: "act", label: "", render: (r: Rule) => (
                  <span className="inline-edit"><button onClick={() => setEdit(r)}>Edit</button><button onClick={() => remove(r)}>Delete</button></span>) }] : []),
              ]}
            />
          )}
        </QueryView>
      </Panel>
    </>
  );
}

function RuleForm(props: { rule: Omit<Rule, "id"> & { id?: number }; kinds: RulesResponse["kinds"]; onClose: () => void; onSaved: () => void }) {
  const [r, setR] = useState(props.rule);
  const [recipients, setRecipients] = useState((props.rule.notify.recipients ?? []).join(", "));
  const [err, setErr] = useState<string | null>(null);
  const exporters = useExporters();
  const groups = useGroups();
  const ifGroups = groups.data?.items.filter((g) => g.kind === "if") ?? [];
  const ipGroups = groups.data?.items.filter((g) => g.kind === "ip") ?? [];
  const p = r.params;
  const setP = (k: string, v: string | number) => setR({ ...r, params: { ...p, [k]: v } });
  const setKind = (kind: Kind) => setR({ ...r, kind, params: { ...props.kinds[kind] } });
  const channels = new Set(r.notify.channels ?? []);
  const toggleCh = (c: Channel) => { const n = new Set(channels); if (n.has(c)) n.delete(c); else n.add(c); setR({ ...r, notify: { ...r.notify, channels: [...n] } }); };
  const save = async () => {
    setErr(null);
    const body = { ...r, notify: { ...r.notify, recipients: recipients.split(/[\s,;]+/).filter(Boolean) } };
    try {
      if (r.id) await api.put(`/alerts/rules/${r.id}`, body); else await api.post("/alerts/rules", body);
      props.onSaved();
    } catch (e) { setErr((e as Error).message); }
  };
  const num = (k: string, label: string, unit?: string, step = 1) => (
    <label key={k}>{label}{unit && <span className="muted"> ({unit})</span>}
      <input type="number" step={step} value={p[k] ?? ""} onChange={(e) => setP(k, e.target.value === "" ? "" : Number(e.target.value))} />
    </label>
  );
  const sel = (k: string, label: string, opts: [string, string][]) => (
    <label key={k}>{label}<select value={String(p[k] ?? "")} onChange={(e) => setP(k, e.target.value)}>{opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
  );
  const fields: Record<Kind, ReactElement[]> = {
    utilization: [num("threshold_pct", "Threshold", "%"), num("window_min", "For", "minutes"), sel("direction", "Direction", [["either", "in or out"], ["in", "in"], ["out", "out"]])],
    discards: [num("threshold", "Threshold", "packets"), num("window_min", "In", "minutes"), sel("metric", "Count", [["discards", "discards"], ["errors", "errors"], ["both", "discards + errors"]])],
    broadcast: [num("threshold_pps", "Threshold", "broadcast packets/s"), num("window_min", "For", "minutes")],
    exporter_silent: [num("silent_min", "No sFlow for", "minutes")],
    traffic: [
      sel("target", "Target", [["ip", "host / subnet"], ["ip_group", "IP group"], ["if_group", "interface group"]]),
      p.target === "ip" ? <label key="value">IP or subnet<input value={String(p.value ?? "")} onChange={(e) => setP("value", e.target.value)} placeholder="192.168.1.30" /></label>
        : sel("value", "Group", [["", "—"], ...(p.target === "ip_group" ? ipGroups : ifGroups).map((g) => [g.name, g.name] as [string, string])]),
      <label key="mbps">Threshold <span className="muted">(Mb/s, estimated)</span>
        <input type="number" value={Number(p.threshold_bps ?? 0) / 1e6} onChange={(e) => setP("threshold_bps", Number(e.target.value) * 1e6)} /></label>,
      num("window_min", "For", "minutes"),
      ...(p.target !== "if_group" ? [sel("direction", "Direction", [["either", "sent or received"], ["src", "sent (source)"], ["dst", "received (destination)"]])] : []),
    ],
  };
  return (
    <div style={{ marginBottom: 12 }}>
      <Panel title={r.id ? `Edit rule` : "New rule"} actions={<button onClick={props.onClose}>Cancel</button>}>
        <div className="filters" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
          <label style={{ gridColumn: "span 2" }}>Name<input maxLength={80} value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} placeholder="Uplink saturation" /></label>
          <label>Type<select value={r.kind} onChange={(e) => setKind(e.target.value as Kind)}>
            {(Object.keys(KIND_LABEL) as Kind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}</select></label>
          <label>Severity<select value={r.severity} onChange={(e) => setR({ ...r, severity: e.target.value as Severity })}>
            <option value="critical">critical</option><option value="warning">warning</option><option value="info">info</option></select></label>
          {fields[r.kind]}
          <label>Only switch<select value={r.scope.exporter ?? ""} onChange={(e) => setR({ ...r, scope: { ...r.scope, exporter: e.target.value || undefined } })}>
            <option value="">all switches</option>
            {exporters.data?.items.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
          {r.kind !== "exporter_silent" && r.kind !== "traffic" && (
            <label>Only interface group<select value={r.scope.if_group ?? ""} onChange={(e) => setR({ ...r, scope: { ...r.scope, if_group: e.target.value || undefined } })}>
              <option value="">all interfaces</option>
              {ifGroups.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}</select></label>)}
        </div>
        <div className="inline-edit" style={{ marginTop: 10, flexWrap: "wrap" }}>
          <span className="muted">Notify by</span>
          {(["email", "webhook", "syslog"] as Channel[]).map((c) => (
            <label key={c} className="pick"><input type="checkbox" checked={channels.has(c)} onChange={() => toggleCh(c)} /> {c === "webhook" ? "Teams / Slack (webhook)" : c}</label>))}
          <label className="pick"><input type="checkbox" checked={r.notify.notify_resolved ?? true} onChange={(e) => setR({ ...r, notify: { ...r.notify, notify_resolved: e.target.checked } })} /> also when resolved</label>
          <label className="pick"><input type="checkbox" checked={r.enabled} onChange={(e) => setR({ ...r, enabled: e.target.checked })} /> enabled</label>
        </div>
        {channels.has("email") && (
          <label style={{ display: "block", marginTop: 8 }}>E-mail recipients <span className="muted">(empty = default recipients of the e-mail channel)</span>
            <input style={{ width: "100%" }} value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="noc@example.com, it@example.com" />
          </label>)}
        <div className="inline-edit" style={{ marginTop: 10 }}>
          <button className="primary" disabled={!r.name.trim()} onClick={save}>Save rule</button>
          {err && <span className="error" style={{ padding: 0 }}>{err}</span>}
        </div>
      </Panel>
    </div>
  );
}
