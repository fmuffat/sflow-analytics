import { Link } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { UtilizationItem, UtilizationSeries } from "../api/types";
import type { Filters } from "../lib/filters";
import { formatBps, formatCount, formatSpeed, formatTick, formatTime } from "../lib/format";
import { useUtilizationTop } from "../hooks/queries";
import { DataTable } from "./DataTable";
import { interfaceLink } from "./TopPanels";
import { Panel, QueryView } from "./ui";

export const WARN_PCT = 50;
export const HIGH_PCT = 80;

export const levelOf = (pct: number | null | undefined) =>
  pct === null || pct === undefined ? "ok" : pct >= HIGH_PCT ? "bad" : pct >= WARN_PCT ? "warn" : "ok";

const pctText = (p: number | null | undefined) => (p === null || p === undefined ? "–" : `${p < 10 ? p.toFixed(1) : p.toFixed(0)} %`);

/** In/out utilization bars, coloured by threshold (green < 50 %, amber < 80 %, red). */
export function UtilBars(props: { inPct: number | null; outPct: number | null }) {
  return (
    <div className="util">
      {([["In", props.inPct], ["Out", props.outPct]] as const).map(([k, v]) => (
        <div className="util-row" key={k}>
          <span className="k">{k}</span>
          <div className="track">
            <div className={"fill " + levelOf(v)} style={{ width: `${Math.min(100, v ?? 0)}%` }} />
          </div>
          <span className="v">{pctText(v)}</span>
        </div>
      ))}
    </div>
  );
}

/** Most utilized interfaces (from interface counters, exact). */
export function UtilizationTable(props: { filters: Filters; limit?: number; title?: string; showExporter?: boolean }) {
  const q = useUtilizationTop(props.filters, props.limit ?? 10, "peak");
  return (
    <Panel
      title={props.title ?? "Interface utilization"}
      note={`from interface counters · peak = highest ${"polling interval"} average · red ≥ ${HIGH_PCT} %`}
      flush
    >
      <QueryView q={q}>
        {(d) => (
          <DataTable<UtilizationItem>
            rows={d.items}
            rowKey={(r) => r.interface_id}
            empty="No interface counters in this window (check `sflow polling-interval` on the switch)."
            initialSort={{ key: "peak", desc: true }}
            columns={[
              ...(props.showExporter === false ? [] : [{ key: "exp", label: "Exporter", render: (r: UtilizationItem) => r.exporter_name, sort: (r: UtilizationItem) => r.exporter_name }]),
              { key: "if", label: "Interface", render: (r) => <Link to={interfaceLink(r.exporter_id, r.ifindex, props.filters)}>{r.label}</Link>, sort: (r) => r.label },
              { key: "speed", label: "Speed", render: (r) => formatSpeed(r.speed_bps), sort: (r) => r.speed_bps, num: true },
              { key: "avg", label: "Average", render: (r) => <UtilBars inPct={r.in_avg_pct} outPct={r.out_avg_pct} />, sort: (r) => Math.max(r.in_avg_pct ?? 0, r.out_avg_pct ?? 0) },
              { key: "p95", label: "95th pct", render: (r) => <UtilBars inPct={r.in_p95_pct} outPct={r.out_p95_pct} />, sort: (r) => Math.max(r.in_p95_pct ?? 0, r.out_p95_pct ?? 0) },
              { key: "peak", label: "Peak", render: (r) => <UtilBars inPct={r.in_max_pct} outPct={r.out_max_pct} />, sort: (r) => Math.max(r.in_max_pct ?? 0, r.out_max_pct ?? 0) },
              { key: "disc", label: "Discards in/out", render: (r) => `${formatCount(r.in_discards)} / ${formatCount(r.out_discards)}`, sort: (r) => r.in_discards + r.out_discards, num: true },
              { key: "err", label: "Errors in/out", render: (r) => `${formatCount(r.in_errors)} / ${formatCount(r.out_errors)}`, sort: (r) => r.in_errors + r.out_errors, num: true },
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

const tooltipStyle = { background: "var(--panel)", border: "1px solid var(--border)", fontSize: 12 };

/** Utilization % over time: average (solid) and peak (dashed) per bucket, in and out. */
export function UtilizationChart(props: { data: UtilizationSeries }) {
  const { data } = props;
  const span = data.items.length * data.step_seconds;
  const hasData = data.items.some((p) => p.in_avg_pct !== null);
  if (!data.speed_bps) {
    return <div className="empty">Interface speed unknown: no counter samples for this interface in this window.</div>;
  }
  return (
    <div>
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data.items} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => formatTick(t, span)} minTickGap={40} stroke="var(--muted)" fontSize={11} />
            {/* Auto-scale: a 0.3 % load on a 10G port must stay readable; full scale from 50 %. */}
            <YAxis domain={[0, (max: number) => (max >= 50 ? 100 : max >= 10 ? Math.ceil((max * 1.3) / 10) * 10 : Math.max(0.5, Math.ceil(max * 13) / 10))]}
              allowDataOverflow tickFormatter={(v) => `${v} %`} width={52} stroke="var(--muted)" fontSize={11} />
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(t) => formatTime(String(t), true)}
              formatter={(v, name) => [v === null ? "no data" : `${Number(v).toFixed(1)} %  (${formatBps((Number(v) / 100) * data.speed_bps!)})`, String(name)]}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine ifOverflow="discard" y={HIGH_PCT} stroke="var(--bad)" strokeDasharray="4 4" label={{ value: `${HIGH_PCT} %`, fill: "var(--bad)", fontSize: 10, position: "right" }} />
            <Line name="In avg" dataKey="in_avg_pct" stroke="var(--series-in)" dot={false} strokeWidth={1.8} isAnimationActive={false} connectNulls={false} />
            <Line name="In peak" dataKey="in_max_pct" stroke="var(--series-in)" strokeDasharray="3 3" dot={false} strokeWidth={1} isAnimationActive={false} />
            <Line name="Out avg" dataKey="out_avg_pct" stroke="var(--series-out)" dot={false} strokeWidth={1.8} isAnimationActive={false} />
            <Line name="Out peak" dataKey="out_max_pct" stroke="var(--series-out)" strokeDasharray="3 3" dot={false} strokeWidth={1} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-hint">
        {hasData ? "" : "No counter samples in this window. "}
        Exact values from interface counters · {formatSpeed(data.speed_bps)} port ·{" "}
        {data.step_seconds >= 60 ? `${data.step_seconds / 60} min` : `${data.step_seconds} s`} buckets (average and peak of the polls in each bucket)
      </div>
    </div>
  );
}

/** Discards and errors per bucket (hints of congestion invisible in averages). */
export function DiscardsChart(props: { data: UtilizationSeries }) {
  const span = props.data.items.length * props.data.step_seconds;
  const total = props.data.items.reduce((s, p) => s + (p.in_discards ?? 0) + (p.out_discards ?? 0) + (p.in_errors ?? 0) + (p.out_errors ?? 0), 0);
  if (total === 0) return <div className="empty">No discards or errors in this window.</div>;
  return (
    <div className="chart" style={{ height: 160 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={props.data.items} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="t" tickFormatter={(t) => formatTick(t, span)} minTickGap={40} stroke="var(--muted)" fontSize={11} />
          <YAxis allowDecimals={false} width={48} stroke="var(--muted)" fontSize={11} />
          <Tooltip contentStyle={tooltipStyle} labelFormatter={(t) => formatTime(String(t), true)} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar name="In discards" dataKey="in_discards" stackId="a" fill="var(--chart-5)" isAnimationActive={false} />
          <Bar name="Out discards" dataKey="out_discards" stackId="a" fill="var(--bad)" isAnimationActive={false} />
          <Bar name="Errors" dataKey={(p) => (p.in_errors ?? 0) + (p.out_errors ?? 0)} stackId="a" fill="var(--chart-4)" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Broadcast and multicast packets/s over time (peak per bucket). */
export function BroadcastChart(props: { data: UtilizationSeries }) {
  const span = props.data.items.length * props.data.step_seconds;
  const any = props.data.items.some((p) => (p.in_bcast_max_pps ?? 0) + (p.in_mcast_max_pps ?? 0) + (p.out_bcast_max_pps ?? 0) + (p.out_mcast_max_pps ?? 0) > 0);
  if (!any) return <div className="empty">No broadcast or multicast counted in this window.</div>;
  return (
    <div className="chart" style={{ height: 180 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={props.data.items} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="t" tickFormatter={(t) => formatTick(t, span)} minTickGap={40} stroke="var(--muted)" fontSize={11} />
          <YAxis width={52} stroke="var(--muted)" fontSize={11} tickFormatter={(v) => `${v}`} />
          <Tooltip contentStyle={tooltipStyle} labelFormatter={(t) => formatTime(String(t), true)} formatter={(v, n) => [v === null ? "no data" : `${Number(v).toFixed(1)} pps`, String(n)]} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line name="Broadcast in (peak)" dataKey="in_bcast_max_pps" stroke="var(--bad)" dot={false} isAnimationActive={false} />
          <Line name="Broadcast out (peak)" dataKey="out_bcast_max_pps" stroke="var(--bad)" strokeDasharray="3 3" dot={false} isAnimationActive={false} />
          <Line name="Multicast in (peak)" dataKey="in_mcast_max_pps" stroke="var(--chart-4)" dot={false} isAnimationActive={false} />
          <Line name="Multicast out (peak)" dataKey="out_mcast_max_pps" stroke="var(--chart-4)" strokeDasharray="3 3" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
