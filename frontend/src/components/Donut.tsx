import { Link } from "react-router-dom";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { ProtocolItem, ServiceItem } from "../api/types";
import type { Filters } from "../lib/filters";
import { formatBytes, formatPercent } from "../lib/format";
import { useTop } from "../hooks/queries";
import { explorerLink } from "./TopPanels";
import { Panel, QueryView } from "./ui";

const COLORS = Array.from({ length: 9 }, (_, i) => `var(--chart-${i + 1})`);

interface Slice {
  name: string;
  bytes: number;
  percent: number;
  link?: string;
}

/** Donut + legend; everything beyond the top 8 is grouped as "Other". */
export function Donut(props: { slices: Slice[]; totalBytes: number }) {
  const top = props.slices.slice(0, 8);
  const rest = props.totalBytes - top.reduce((s, x) => s + x.bytes, 0);
  const data = rest > 0 ? [...top, { name: "Other", bytes: rest, percent: (rest * 100) / props.totalBytes }] : top;
  if (data.length === 0) return <div className="empty">No data in this window.</div>;
  return (
    <div className="donut">
      <div style={{ height: 170 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="bytes" nameKey="name" innerRadius={48} outerRadius={78} paddingAngle={1} isAnimationActive={false} stroke="var(--panel)">
              {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
            </Pie>
            <Tooltip
              contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", fontSize: 12 }}
              formatter={(v, n) => [formatBytes(Number(v)), String(n)]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">
        {data.map((s, i) => (
          <div className="item" key={s.name}>
            <span className="sw" style={{ background: COLORS[i % COLORS.length] }} />
            <span className="name" title={s.name}>{"link" in s && s.link ? <Link to={s.link}>{s.name}</Link> : s.name}</span>
            <span className="num">{formatBytes(s.bytes)}</span>
            <span className="num">{formatPercent(s.percent)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ServicesDonut(props: { filters: Filters; title?: string }) {
  const q = useTop<ServiceItem>("top-services", props.filters, 8);
  return (
    <Panel title={props.title ?? "Applications (services)"} note="protocol + port">
      <QueryView q={q}>
        {(d) => (
          <Donut
            totalBytes={d.total?.bytes ?? 0}
            slices={d.items.map((s) => ({ name: s.service, bytes: s.bytes, percent: s.percent, link: explorerLink(props.filters, { service: s.service }) }))}
          />
        )}
      </QueryView>
    </Panel>
  );
}

export function ProtocolsDonut(props: { filters: Filters }) {
  const q = useTop<ProtocolItem>("top-protocols", props.filters, 8);
  return (
    <Panel title="Protocols">
      <QueryView q={q}>
        {(d) => (
          <Donut
            totalBytes={d.total?.bytes ?? 0}
            slices={d.items.map((s) => ({
              name: s.protocol, bytes: s.bytes, percent: s.percent,
              link: s.ip_protocol === null ? undefined : explorerLink(props.filters, { protocol: String(s.ip_protocol) }),
            }))}
          />
        )}
      </QueryView>
    </Panel>
  );
}
