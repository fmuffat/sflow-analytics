import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Layer, Rectangle, ResponsiveContainer, Sankey, Tooltip } from "recharts";
import type { Filters } from "../lib/filters";
import { formatBytes, formatCount } from "../lib/format";
import { useTraffic } from "../hooks/queries";
import { explorerLink } from "./TopPanels";
import { Panel, QueryView } from "./ui";

interface SankeyNode { id: string; level: string; depth: number; value_key: string; label: string; value: number }
interface SankeyData {
  levels: string[];
  metric: "bytes" | "packets" | "samples";
  total: number;
  shown: number;
  coverage_percent: number;
  nodes: SankeyNode[];
  links: { source: string; target: string; value: number }[];
}

export const PRESETS: [string, string][] = [
  ["src_ip,dst_ip", "Source → Destination"],
  ["src_ip,service,dst_ip", "Source → Service → Destination"],
  ["input_if,output_if", "Ingress port → Egress port"],
  ["vlan,service", "VLAN → Service"],
  ["src_ip,service", "Source → Service"],
  ["src_group,dst_group", "Source group → Destination group"],
  ["src_group,service,dst_group", "Source group → Service → Destination group"],
];

const COLORS = Array.from({ length: 9 }, (_, i) => `var(--chart-${i + 1})`);

/** Filters that select one node of the flow map. */
function nodeFilters(n: SankeyNode): Filters | null {
  const v = n.value_key;
  switch (n.level) {
    case "src_ip": return { src_ip: v };
    case "dst_ip": return { dst_ip: v };
    case "service": return { service: v };
    case "protocol": return v === "Non-IP" ? null : { protocol: v };
    case "vlan": return v === "none" ? null : { vlan: v };
    case "exporter": return { exporter: v };
    case "src_group": return v === "Ungrouped" || v === "Non-IP" ? null : { src_group: v };
    case "dst_group": return v === "Ungrouped" || v === "Non-IP" ? null : { dst_group: v };
    case "input_if":
    case "output_if": {
      const [exp, idx] = [v.slice(0, v.lastIndexOf("#")), v.slice(v.lastIndexOf("#") + 1)];
      return { exporter: exp, [n.level === "input_if" ? "input_ifindex" : "output_ifindex"]: idx };
    }
  }
  return null;
}

/** Sankey diagram of the top traffic paths; click a node or a band to drill down. */
export function FlowMap(props: { filters: Filters; preset?: string; height?: number; title?: string; compact?: boolean }) {
  const navigate = useNavigate();
  const [levels, setLevels] = useState(props.preset ?? PRESETS[0][0]);
  const [metric, setMetric] = useState<"bytes" | "packets" | "samples">("bytes");
  const [top, setTop] = useState(props.compact ? 10 : 15);
  const q = useTraffic<SankeyData>("sankey", props.filters, { levels, top, metric });
  const fmt = (v: number) => (metric === "bytes" ? formatBytes(v) : formatCount(v) + (metric === "packets" ? " pkts" : " samples"));

  return (
    <Panel
      title={props.title ?? "Flow map"}
      note={q.data ? `top ${q.data.links.length ? top : 0} paths = ${q.data.coverage_percent} % of ${metric === "bytes" ? "estimated traffic" : metric} · click to drill down` : undefined}
      actions={
        <span className="inline-edit">
          <select value={levels} onChange={(e) => setLevels(e.target.value)} aria-label="Dimensions">
            {PRESETS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {!props.compact && (
            <>
              <span className="seg">
                {(["bytes", "packets", "samples"] as const).map((m) => (
                  <button key={m} className={metric === m ? "on" : ""} onClick={() => setMetric(m)}>{m}</button>
                ))}
              </span>
              <select value={top} onChange={(e) => setTop(Number(e.target.value))} aria-label="Paths">
                {[10, 15, 25, 40].map((n) => <option key={n} value={n}>top {n}</option>)}
              </select>
            </>
          )}
        </span>
      }
    >
      <QueryView q={q}>
        {(d) => d.links.length === 0 ? <div className="empty">No traffic sampled in this window.</div> : (
          <SankeyChart data={d} height={props.height ?? 460} fmt={fmt}
            onSelect={(f) => navigate(explorerLink(props.filters, f))} />
        )}
      </QueryView>
    </Panel>
  );
}

function SankeyChart(props: { data: SankeyData; height: number; fmt: (v: number) => string; onSelect: (f: Filters) => void }) {
  const { data } = props;
  const chart = useMemo(() => {
    const index = new Map(data.nodes.map((n, i) => [n.id, i]));
    return {
      nodes: data.nodes.map((n) => ({ ...n, name: n.label })),
      links: data.links.map((l) => ({ source: index.get(l.source)!, target: index.get(l.target)!, value: l.value })),
    };
  }, [data]);
  const colorOf = (i: number) => COLORS[i % COLORS.length];

  const Node = (p: { x: number; y: number; width: number; height: number; index: number; payload: SankeyNode & { name: string } }) => {
    // Sources: label on the left margin; last level: right margin; middle: right of the node.
    const outsideLeft = p.payload.depth === 0;
    const f = nodeFilters(p.payload);
    return (
      <Layer key={`n${p.index}`}>
        <Rectangle x={p.x} y={p.y} width={p.width} height={p.height} fill={colorOf(p.index)} fillOpacity={0.9}
          style={{ cursor: f ? "pointer" : "default" }} onClick={() => f && props.onSelect(f)} />
        <text x={outsideLeft ? p.x - 6 : p.x + p.width + 6} y={p.y + p.height / 2} textAnchor={outsideLeft ? "end" : "start"}
          dominantBaseline="middle" fontSize={11} fill="var(--text)" style={{ cursor: "default" }}>
          <title>{`${p.payload.name} — ${props.fmt(p.payload.value)}`}</title>
          {p.payload.name.length > 26 ? p.payload.name.slice(0, 25) + "…" : p.payload.name}
          <tspan fill="var(--muted)"> {props.fmt(p.payload.value)}</tspan>
        </text>
      </Layer>
    );
  };

  const Link = (p: {
    sourceX: number; sourceY: number; sourceControlX: number; targetX: number; targetY: number; targetControlX: number;
    linkWidth: number; index: number; payload: { source: SankeyNode; target: SankeyNode };
  }) => {
    const src = chart.nodes.indexOf(p.payload.source as never);
    const f = { ...(nodeFilters(p.payload.source) ?? {}), ...(nodeFilters(p.payload.target) ?? {}) };
    return (
      <path
        key={`l${p.index}`}
        d={`M${p.sourceX},${p.sourceY} C${p.sourceControlX},${p.sourceY} ${p.targetControlX},${p.targetY} ${p.targetX},${p.targetY}`}
        fill="none" stroke={colorOf(src >= 0 ? src : p.index)} strokeOpacity={0.35} strokeWidth={Math.max(1, p.linkWidth)}
        style={{ cursor: "pointer" }} onClick={() => props.onSelect(f)}
      />
    );
  };

  return (
    <div style={{ height: props.height }}>
      <ResponsiveContainer width="100%" height="100%">
        <Sankey data={chart} node={Node as never} link={Link as never} nodePadding={14} nodeWidth={10}
          margin={{ top: 8, right: 230, bottom: 8, left: 230 }} iterations={32}>
          <Tooltip
            contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", fontSize: 12 }}
            formatter={(v) => props.fmt(Number(v))}
          />
        </Sankey>
      </ResponsiveContainer>
    </div>
  );
}
