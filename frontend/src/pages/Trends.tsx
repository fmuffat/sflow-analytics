import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView } from "../components/ui";
import {
  CompareChart, CompareSummary, CoverageNote, Delta, TrendControls, TZ, useCompare, type TrendState,
} from "../components/Trends";
import { interfaceLink } from "../components/TopPanels";
import { useGroups } from "./Groups";
import { formatBps, formatBytes, formatSpeed } from "../lib/format";

interface Row {
  id: string; exporter_id: string; ifindex: number; exporter_name: string; label: string; speed_bps: number | null;
  current: { in_bytes: number; out_bytes: number; total_bytes: number; in_avg_bps: number | null; out_avg_bps: number | null; peak_bps: number; peak_pct: number | null; discards: number };
  reference: { total_bytes: number; peak_bps: number; peak_pct: number | null };
  delta_pct: number | null; peak_delta_pct: number | null;
}
interface TableResponse {
  current: { label: string; running: boolean }; reference: { label: string };
  delta_basis: string; navigation: { previous_at: string; next_at: string }; items: Row[];
}

/** Traffic evolution: day / week / month / year compared with the previous period or a year before. */
export function Trends() {
  const [s, setS] = useState<TrendState>({ period: "month", compare: "previous" });
  const [group, setGroup] = useState("");
  const [picked, setPicked] = useState<Row | null>(null);
  const groups = useGroups("if");
  const table = useQuery({
    queryKey: ["history-interfaces", s, group],
    queryFn: () => api.get<TableResponse>("/history/interfaces", undefined, {
      period: s.period, compare: s.compare, tz: TZ, limit: 200, ...(s.at ? { at: s.at } : {}), ...(group ? { if_group: group } : {}),
    }),
    refetchInterval: 300_000,
  });
  const first = table.data?.items[0];
  const chosen = picked && table.data?.items.some((r) => r.id === picked.id) ? picked : null;
  const sel = chosen ? { interfaces: [chosen.id] } : group ? { ifGroup: group } : first ? { interfaces: [first.id] } : {};
  const chart = useCompare(sel, s);
  const title = chosen ? `${chosen.exporter_name} · ${chosen.label}` : group ? `Group "${group}" (sum of its interfaces)` : first ? `${first.exporter_name} · ${first.label} (busiest)` : "";

  return (
    <>
      <div className="page-head">
        <h1>Trends</h1>
        <span className="sub">traffic evolution on interfaces · day, week, month, year</span>
        <span className="spacer" />
        <TrendControls value={s} onChange={(v) => setS(v)} nav={table.data?.navigation} label={table.data?.current.label} />
      </div>
      <Panel
        title={title || "Comparison"}
        note={chart.data ? <CoverageNote data={chart.data} /> : undefined}
        actions={
          <span className="inline-edit">
            <span className="muted">interface group</span>
            <select value={group} onChange={(e) => { setGroup(e.target.value); setPicked(null); }}>
              <option value="">all interfaces</option>
              {groups.data?.items.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
            </select>
            {chosen && <button type="button" onClick={() => setPicked(null)}>{group ? "Show the group" : "Busiest"}</button>}
          </span>
        }
      >
        {!sel.interfaces && !sel.ifGroup ? <div className="empty">No history for this period yet.</div> : (
          <QueryView q={chart}>{(d) => <><CompareSummary data={d} /><CompareChart data={d} height={300} /></>}</QueryView>
        )}
      </Panel>
      <div style={{ marginTop: 12 }}>
        <Panel
          title="Interfaces"
          note={table.data ? `${table.data.current.label}${table.data.current.running ? " (so far)" : ""} vs ${table.data.reference.label} · ${table.data.delta_basis} · click a row to chart it` : undefined}
          flush
        >
          <QueryView q={table}>
            {(d) => (
              <DataTable<Row>
                rows={d.items} rowKey={(r) => r.id} initialSort={{ key: "cur", desc: true }}
                empty="No interface history in these periods."
                onRowClick={(r) => { setPicked(r); window.scrollTo({ top: 0, behavior: "smooth" }); }}
                columns={[
                  { key: "if", label: "Interface", render: (r) => (
                    <span><b>{r.exporter_name}</b> · <Link to={interfaceLink(r.exporter_id, r.ifindex, {})} onClick={(e) => e.stopPropagation()}>{r.label}</Link>
                      {chosen?.id === r.id && <span className="badge ok" style={{ marginLeft: 6 }}>charted</span>}</span>),
                    sort: (r) => r.exporter_name + r.label },
                  { key: "speed", label: "Speed", render: (r) => formatSpeed(r.speed_bps), sort: (r) => r.speed_bps ?? 0, num: true },
                  { key: "cur", label: "Volume", render: (r) => formatBytes(r.current.total_bytes), sort: (r) => r.current.total_bytes, num: true },
                  { key: "ref", label: "Reference", render: (r) => <span className="muted">{formatBytes(r.reference.total_bytes)}</span>, sort: (r) => r.reference.total_bytes, num: true },
                  { key: "delta", label: "Change", render: (r) => <Delta pct={r.delta_pct} />, sort: (r) => r.delta_pct ?? -Infinity, num: true },
                  { key: "avg", label: "Avg in / out", render: (r) => `${r.current.in_avg_bps === null ? "–" : formatBps(r.current.in_avg_bps)} / ${r.current.out_avg_bps === null ? "–" : formatBps(r.current.out_avg_bps)}`, num: true },
                  { key: "peak", label: "Peak", render: (r) => <span title={`reference: ${formatBps(r.reference.peak_bps)}`}>{formatBps(r.current.peak_bps)}{r.current.peak_pct !== null ? ` · ${r.current.peak_pct} %` : ""}</span>, sort: (r) => r.current.peak_pct ?? 0, num: true },
                  { key: "pdelta", label: "Peak change", render: (r) => <Delta pct={r.peak_delta_pct} />, sort: (r) => r.peak_delta_pct ?? -Infinity, num: true },
                  { key: "disc", label: "Discards", render: (r) => r.current.discards ? r.current.discards.toLocaleString() : "–", sort: (r) => r.current.discards, num: true },
                ]}
              />
            )}
          </QueryView>
        </Panel>
      </div>
    </>
  );
}
