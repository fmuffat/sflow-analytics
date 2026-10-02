import { useMemo, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, Legend, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { GroupedTimeseries } from "../api/types";
import type { Filters } from "../lib/filters";
import { formatBps, formatBytes, formatTick, formatTime } from "../lib/format";
import { useGroupedTimeseries, useTimeseries } from "../hooks/queries";
import { TrafficChart } from "./TrafficChart";
import { Panel, QueryView } from "./ui";

export const GROUPS = [
  ["", "Total"], ["service", "by service"], ["src_ip", "by source"], ["dst_ip", "by destination"],
  ["protocol", "by protocol"], ["vlan", "by VLAN"], ["exporter", "by exporter"],
] as const;
export type Group = (typeof GROUPS)[number][0];

const COLORS = Array.from({ length: 9 }, (_, i) => `var(--chart-${i + 1})`);
const STORE = "sflow.timeline.group";

function loadGroup(): Group {
  try {
    const g = localStorage.getItem(STORE);
    return (GROUPS.some(([k]) => k === g) ? g : "") as Group;
  } catch {
    return "";
  }
}

/** Traffic timeline with a "split by" selector (stacked top 5 + Other). */
export function TimelinePanel(props: { filters: Filters; onZoom?: (from: string, to: string) => void; title?: string; note?: string }) {
  const [group, setGroup] = useState<Group>(loadGroup);
  const total = useTimeseries(props.filters, group === "");
  const grouped = useGroupedTimeseries(props.filters, group, 5);
  const choose = (g: Group) => {
    setGroup(g);
    try { localStorage.setItem(STORE, g); } catch { /* ignore */ }
  };
  return (
    <Panel
      title={props.title ?? "Traffic timeline"}
      note={props.note ?? "estimated from sampled packets"}
      actions={
        <select value={group} onChange={(e) => choose(e.target.value as Group)} aria-label="Split timeline">
          {GROUPS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      }
    >
      {group === "" ? (
        <QueryView q={total}>
          {(d) => <TrafficChart series={[{ name: "Traffic", data: d, color: "var(--series-in)" }]} onZoom={props.onZoom} />}
        </QueryView>
      ) : (
        <QueryView q={grouped}>{(d) => <StackedChart data={d} onZoom={props.onZoom} />}</QueryView>
      )}
    </Panel>
  );
}

export function StackedChart(props: { data: GroupedTimeseries; onZoom?: (from: string, to: string) => void }) {
  const { data } = props;
  const [sel, setSel] = useState<{ a?: string; b?: string }>({});
  const span = data.items.length * data.step_seconds;
  const rows = useMemo(() => data.items.map((i) => ({ t: i.t, ...i.bps })), [data]);
  if (data.series.length === 0) return <div className="empty">No traffic sampled in this window.</div>;

  const finish = () => {
    if (sel.a && sel.b && sel.a !== sel.b && props.onZoom) {
      const [x, y] = [sel.a, sel.b].sort();
      props.onZoom(x, new Date(new Date(y).getTime() + data.step_seconds * 1000).toISOString().replace(".000Z", "Z"));
    }
    setSel({});
  };
  return (
    <div>
      <div className="chart">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}
            onMouseDown={(e) => e?.activeLabel && setSel({ a: String(e.activeLabel) })}
            onMouseMove={(e) => sel.a && e?.activeLabel && setSel({ a: sel.a, b: String(e.activeLabel) })}
            onMouseUp={finish}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => formatTick(t, span)} minTickGap={40} stroke="var(--muted)" fontSize={11} />
            <YAxis tickFormatter={(v) => formatBps(v)} width={78} stroke="var(--muted)" fontSize={11} />
            <Tooltip
              contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", fontSize: 12 }}
              labelFormatter={(t) => formatTime(String(t), true)}
              formatter={(v, n) => [formatBps(Number(v)), String(n)]}
              itemSorter={(i) => -Number(i.value)}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {data.series.map((s, i) => (
              <Area
                key={s.name} type="monotone" dataKey={s.name} stackId="1" isAnimationActive={false} dot={false}
                name={`${s.name} (${formatBytes(s.bytes)})`}
                stroke={s.name === "Other" ? "var(--chart-8)" : COLORS[i % COLORS.length]}
                fill={s.name === "Other" ? "var(--chart-8)" : COLORS[i % COLORS.length]} fillOpacity={0.5}
              />
            ))}
            {sel.a && sel.b && <ReferenceArea x1={sel.a} x2={sel.b} fill="var(--accent)" fillOpacity={0.15} />}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-hint">
        Estimated throughput, top 5 {data.group_by.replace("_ip", "s").replace("src", "source").replace("dst", "destination")} + Other,{" "}
        {data.step_seconds >= 60 ? `${data.step_seconds / 60} min` : `${data.step_seconds} s`} buckets
        {props.onZoom ? " · drag across the chart to zoom" : ""}
      </div>
    </div>
  );
}
