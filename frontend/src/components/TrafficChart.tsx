import { useMemo, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, Legend, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Timeseries } from "../api/types";
import { formatBps, formatBytes, formatTick, formatTime } from "../lib/format";

export interface Series {
  name: string;
  data: Timeseries;
  color: string;
}

/**
 * Estimated throughput over time. Drag across the chart to zoom into a time
 * window (onZoom receives ISO bounds).
 */
export function TrafficChart(props: { series: Series[]; onZoom?: (from: string, to: string) => void; height?: number }) {
  const [sel, setSel] = useState<{ a?: string; b?: string }>({});
  const first = props.series[0]?.data;
  const span = first ? first.items.length * first.step_seconds : 3600;

  const rows = useMemo(() => {
    if (!first) return [];
    return first.items.map((p, i) => {
      const row: Record<string, number | string> = { t: p.t };
      for (const s of props.series) {
        const q = s.data.items[i];
        row[s.name] = q ? q.bps : 0;
        row[s.name + "_bytes"] = q ? q.bytes : 0;
      }
      return row;
    });
  }, [first, props.series]);

  if (!first) return null;
  const empty = props.series.every((s) => s.data.total?.bytes === 0);

  const finishZoom = () => {
    if (sel.a && sel.b && sel.a !== sel.b && props.onZoom) {
      const [x, y] = [sel.a, sel.b].sort();
      const end = new Date(new Date(y).getTime() + first.step_seconds * 1000).toISOString();
      props.onZoom(x, end.replace(".000Z", "Z"));
    }
    setSel({});
  };

  return (
    <div>
      <div className="chart" style={props.height ? { height: props.height } : undefined}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={rows}
            margin={{ top: 8, right: 16, left: 8, bottom: 0 }}
            onMouseDown={(e) => e?.activeLabel && setSel({ a: String(e.activeLabel) })}
            onMouseMove={(e) => sel.a && e?.activeLabel && setSel({ a: sel.a, b: String(e.activeLabel) })}
            onMouseUp={finishZoom}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(t) => formatTick(t, span)} minTickGap={40} stroke="var(--muted)" fontSize={11} />
            <YAxis tickFormatter={(v) => formatBps(v)} width={78} stroke="var(--muted)" fontSize={11} />
            <Tooltip
              contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", fontSize: 12 }}
              labelFormatter={(t) => formatTime(String(t), true)}
              formatter={(v, name, item) => [
                `${formatBps(Number(v))} (${formatBytes(Number((item.payload as Record<string, number>)[name + "_bytes"]))})`,
                String(name),
              ]}
            />
            {props.series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
            {props.series.map((s) => (
              <Area
                key={s.name} type="monotone" dataKey={s.name} stroke={s.color} fill={s.color} fillOpacity={0.18}
                strokeWidth={1.5} isAnimationActive={false} dot={false}
              />
            ))}
            {sel.a && sel.b && <ReferenceArea x1={sel.a} x2={sel.b} strokeOpacity={0.3} fill="var(--accent)" fillOpacity={0.15} />}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-hint">
        {empty ? "No traffic sampled in this window. " : ""}
        Estimated throughput, {first.step_seconds >= 60 ? `${first.step_seconds / 60} min` : `${first.step_seconds} s`} buckets
        {props.onZoom ? " · drag across the chart to zoom" : ""}
      </div>
    </div>
  );
}
