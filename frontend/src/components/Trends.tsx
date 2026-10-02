import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { Panel, QueryView } from "./ui";
import { formatBps, formatBytes, formatTime, getClock } from "../lib/format";

// Period comparisons from the long-term hourly history (kept 3 years).

export type Period = "day" | "week" | "month" | "year";
export type Compare = "previous" | "week" | "year";

export interface TrendPoint {
  in_bytes: number; out_bytes: number; in_avg_bps: number | null; out_avg_bps: number | null;
  in_peak_bps: number; out_peak_bps: number; in_peak_pct: number | null; out_peak_pct: number | null;
  discards: number; errors: number;
}
interface Totals { in_bytes: number; out_bytes: number; in_avg_bps: number | null; out_avg_bps: number | null; in_peak_bps: number; out_peak_bps: number; has_data: boolean }
interface Coverage { first_hour: string | null; last_hour: string | null; rows: number; kept_days: number }
interface Side { from: string; to: string; label: string }
export interface CompareResponse {
  period: Period; compare: Compare; bucket: "hour" | "day" | "month";
  interfaces: { id: string; exporter_name: string; label: string }[];
  current: Side & { running: boolean; totals: Totals };
  reference: Side & { totals: Totals; totals_same_elapsed: Totals };
  delta_pct: Record<string, number | null>;
  delta_basis: string;
  navigation: { previous_at: string; next_at: string };
  coverage: Coverage;
  points: { index: number; label: string; current_start: string | null; reference_start: string | null; current: TrendPoint | null; reference: TrendPoint | null }[];
}

export const TZ = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
})();

export const PERIODS: [Period, string][] = [["day", "Day"], ["week", "Week"], ["month", "Month"], ["year", "Year"]];

export function compareOptions(period: Period): [Compare, string][] {
  const prev: Record<Period, string> = { day: "yesterday", week: "previous week", month: "previous month", year: "previous year" };
  const opts: [Compare, string][] = [["previous", prev[period]]];
  if (period === "day") opts.push(["week", "same day last week"]);
  if (period !== "year") opts.push(["year", "a year before"]);
  return opts;
}

export interface TrendState { period: Period; compare: Compare; at?: string }

/** Period selector, comparison choice and ‹ › navigation. */
export function TrendControls(props: { value: TrendState; onChange: (v: TrendState) => void; nav?: CompareResponse["navigation"]; label?: string }) {
  const v = props.value;
  const setPeriod = (period: Period) => {
    const ok = compareOptions(period).some(([c]) => c === v.compare);
    props.onChange({ period, compare: ok ? v.compare : "previous", at: v.at });
  };
  const today = new Date().toLocaleDateString("en-CA");
  return (
    <span className="inline-edit trend-controls">
      <span className="seg">
        {PERIODS.map(([p, l]) => <button key={p} type="button" className={v.period === p ? "on" : ""} onClick={() => setPeriod(p)}>{l}</button>)}
      </span>
      <span className="muted">vs</span>
      <select value={v.compare} onChange={(e) => props.onChange({ ...v, compare: e.target.value as Compare })}>
        {compareOptions(v.period).map(([c, l]) => <option key={c} value={c}>{l}</option>)}
      </select>
      <span className="seg">
        <button type="button" title="Previous period" disabled={!props.nav} onClick={() => props.nav && props.onChange({ ...v, at: props.nav.previous_at })}>‹</button>
        <button type="button" className="trend-label" onClick={() => props.onChange({ ...v, at: undefined })} title="Back to the current period">{props.label ?? "…"}</button>
        <button type="button" title="Next period" disabled={!props.nav || props.nav.next_at > today}
          onClick={() => props.nav && props.onChange({ ...v, at: props.nav.next_at })}>›</button>
      </span>
    </span>
  );
}

export function useCompare(sel: { interfaces?: string[]; ifGroup?: string }, s: TrendState) {
  const ids = sel.interfaces ?? [];
  return useQuery({
    queryKey: ["history-compare", ids, sel.ifGroup ?? "", s],
    enabled: ids.length > 0 || !!sel.ifGroup,
    queryFn: () => api.get<CompareResponse>("/history/compare", undefined, {
      period: s.period, compare: s.compare, tz: TZ, ...(s.at ? { at: s.at } : {}),
      ...(ids.length ? { interface: ids.join(",") } : {}), ...(sel.ifGroup ? { if_group: sel.ifGroup } : {}),
    }),
    refetchInterval: 300_000,
  });
}

export function Delta(props: { pct: number | null | undefined; title?: string }) {
  const p = props.pct;
  if (p === null || p === undefined) return <span className="muted" title={props.title ?? "nothing to compare with"}>–</span>;
  const cls = Math.abs(p) < 5 ? "delta flat" : p > 0 ? (p >= 50 ? "delta up big" : "delta up") : "delta down";
  return <span className={cls} title={props.title}>{p > 0 ? "▲" : p < 0 ? "▼" : "="} {p > 0 ? "+" : ""}{p.toFixed(Math.abs(p) >= 100 ? 0 : 1)} %</span>;
}

type Metric = "avg" | "peak" | "volume";
type Dir = "in" | "out" | "both";

function bucketLabel(iso: string | null, bucket: CompareResponse["bucket"], period: Period, fallback: string): string {
  if (!iso) return fallback;
  const d = new Date(iso);
  const hour = d.toLocaleTimeString(undefined, { hour: "2-digit", ...(getClock() === "12h" ? { hour12: true } : { hourCycle: "h23" as const }) });
  if (bucket === "hour") return period === "week" ? `${d.toLocaleDateString(undefined, { weekday: "short" })} ${hour}` : hour;
  if (bucket === "day") return String(d.getDate());
  return d.toLocaleDateString(undefined, { month: "short" });
}

/** Current period (solid, brand) over the reference period (dashed, grey). */
export function CompareChart(props: { data: CompareResponse; height?: number }) {
  const d = props.data;
  const [metric, setMetric] = useState<Metric>(d.period === "year" ? "volume" : "avg");
  const [dir, setDir] = useState<Dir>("both");
  const value = (p: TrendPoint | null): number | null => {
    if (!p) return null;
    const pick = (i: number | null, o: number | null) => (dir === "in" ? i : dir === "out" ? o : i === null && o === null ? null : (i ?? 0) + (o ?? 0));
    if (metric === "volume") return pick(p.in_bytes, p.out_bytes);
    if (metric === "peak") return dir === "both" ? Math.max(p.in_peak_bps, p.out_peak_bps) : pick(p.in_peak_bps, p.out_peak_bps);
    return pick(p.in_avg_bps, p.out_avg_bps);
  };
  const rows = d.points.map((p) => ({
    x: bucketLabel(p.current_start ?? p.reference_start, d.bucket, d.period, p.label),
    cur: value(p.current), ref: value(p.reference),
    curT: p.current_start, refT: p.reference_start,
  }));
  const dot = rows.length <= 31 ? { r: 2.5 } : false;
  const fmt = (v: number) => (metric === "volume" ? formatBytes(v) : formatBps(v));
  const curName = d.current.label + (d.current.running ? " (so far)" : "");
  const refName = d.reference.label;
  const tooltip = (
    <Tooltip
      contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", fontSize: 12 }}
      formatter={(v, name) => [v === null || v === undefined ? "no data" : fmt(Number(v)), String(name)]}
      labelFormatter={(_, payload) => {
        const r = payload?.[0]?.payload as { curT: string | null; refT: string | null } | undefined;
        return r ? `${r.curT ? formatTime(r.curT) : "–"}  vs  ${r.refT ? formatTime(r.refT) : "–"}` : "";
      }}
    />
  );
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
      <XAxis dataKey="x" minTickGap={d.bucket === "hour" ? 24 : 4} stroke="var(--muted)" fontSize={11} />
      <YAxis tickFormatter={(v) => fmt(Number(v))} width={78} stroke="var(--muted)" fontSize={11} />
      {tooltip}
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </>
  );
  return (
    <div>
      <div className="inline-edit" style={{ marginBottom: 6 }}>
        <span className="seg">
          {([["avg", "Average"], ["peak", "Peak"], ["volume", "Volume"]] as [Metric, string][]).map(([m, l]) => (
            <button key={m} type="button" className={metric === m ? "on" : ""} onClick={() => setMetric(m)}>{l}</button>))}
        </span>
        <span className="seg">
          {([["both", "In + Out"], ["in", "In"], ["out", "Out"]] as [Dir, string][]).map(([m, l]) => (
            <button key={m} type="button" className={dir === m ? "on" : ""} onClick={() => setDir(m)}>{l}</button>))}
        </span>
      </div>
      <div className="chart" style={{ height: props.height ?? 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          {metric === "volume" || d.period === "year" ? (
            <BarChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
              {axes}
              <Bar dataKey="ref" name={refName} fill="var(--muted)" fillOpacity={0.45} isAnimationActive={false} />
              <Bar dataKey="cur" name={curName} fill="var(--brand)" isAnimationActive={false} />
            </BarChart>
          ) : (
            <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
              {axes}
              <Line dataKey="ref" name={refName} stroke="var(--muted)" strokeDasharray="5 4" strokeWidth={1.5} dot={dot} isAnimationActive={false} connectNulls={false} />
              <Line dataKey="cur" name={curName} stroke="var(--brand)" strokeWidth={2} dot={dot} isAnimationActive={false} connectNulls={false} />
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Headline numbers: volume and average/peak rate, with deltas. */
export function CompareSummary(props: { data: CompareResponse }) {
  const d = props.data;
  const c = d.current.totals;
  const r = d.current.running ? d.reference.totals_same_elapsed : d.reference.totals;
  const basis = d.current.running ? `vs ${d.reference.label}, same elapsed time` : `vs ${d.reference.label}`;
  const cell = (label: string, cur: string, ref: string, pct: number | null | undefined) => (
    <div className="trend-cell">
      <div className="label">{label}</div>
      <div className="value">{cur} <Delta pct={pct} title={basis} /></div>
      <div className="muted">{ref} {basis}</div>
    </div>
  );
  const sum = (a: number | null, b: number | null) => (a === null && b === null ? null : (a ?? 0) + (b ?? 0));
  const pctOf = (a: number | null, b: number | null) => (a === null || !b ? null : Math.round(((a - b) / b) * 1000) / 10);
  return (
    <div className="trend-summary">
      {cell("Volume in", formatBytes(c.in_bytes), formatBytes(r.in_bytes), d.delta_pct.in_bytes)}
      {cell("Volume out", formatBytes(c.out_bytes), formatBytes(r.out_bytes), d.delta_pct.out_bytes)}
      {cell("Average in + out", fmtN(sum(c.in_avg_bps, c.out_avg_bps)), fmtN(sum(r.in_avg_bps, r.out_avg_bps)),
        pctOf(sum(c.in_avg_bps, c.out_avg_bps), sum(r.in_avg_bps, r.out_avg_bps)))}
      {cell("Peak (max of in/out)", formatBps(Math.max(c.in_peak_bps, c.out_peak_bps)), formatBps(Math.max(r.in_peak_bps, r.out_peak_bps)),
        pctOf(Math.max(c.in_peak_bps, c.out_peak_bps), Math.max(r.in_peak_bps, r.out_peak_bps)))}
    </div>
  );
}

const fmtN = (v: number | null) => (v === null ? "–" : formatBps(v));

export function CoverageNote(props: { data: CompareResponse }) {
  const cov = props.data.coverage;
  if (!cov.first_hour) return <span>No history yet: it is computed every 5 minutes from interface counters.</span>;
  const first = new Date(cov.first_hour);
  const refStart = new Date(props.data.reference.from);
  return (
    <span>
      exact, from interface counters · history since {formatTime(cov.first_hour)}, kept {Math.round(cov.kept_days / 365)} years
      {refStart < first && <span className="warn-text"> · reference period before the start of the history: partial or empty</span>}
    </span>
  );
}

/** Self-contained panel: controls + summary + chart, for one interface or an interface group. */
export function TrendsPanel(props: { interfaces?: string[]; ifGroup?: string; title?: string; initial?: Period }) {
  const [s, setS] = useState<TrendState>({ period: props.initial ?? "day", compare: "previous" });
  const q = useCompare({ interfaces: props.interfaces, ifGroup: props.ifGroup }, s);
  return (
    <Panel
      title={props.title ?? "Trends"}
      note={q.data ? <CoverageNote data={q.data} /> : undefined}
      actions={<TrendControls value={s} onChange={setS} nav={q.data?.navigation} label={q.data?.current.label} />}
    >
      <QueryView q={q}>
        {(d) => (
          <>
            <CompareSummary data={d} />
            <CompareChart data={d} />
          </>
        )}
      </QueryView>
    </Panel>
  );
}
