import { Link } from "react-router-dom";
import type { Interface, InterfaceTrafficItem } from "../api/types";
import { useExporters, useInterfaces, useTop, useUtilizationTop } from "../hooks/queries";
import { UtilBars } from "../components/Utilization";
import { LldpCell } from "../components/Lldp";
import { useFilters } from "../hooks/useFilters";
import { TimeRange } from "../components/TimeRange";
import { DataTable } from "../components/DataTable";
import { interfaceLink } from "../components/TopPanels";
import { Panel, QueryView } from "../components/ui";
import { formatAgo, formatBytes, formatSpeed } from "../lib/format";

export function Interfaces() {
  const { filters, set, replace } = useFilters();
  const exporters = useExporters();
  const list = useInterfaces(filters.exporter);
  const traffic = useTop<InterfaceTrafficItem>("top-interfaces", filters, 1000);
  const byId = new Map(traffic.data?.items.map((t) => [t.interface_id, t]) ?? []);
  const util = useUtilizationTop(filters, 1000);
  const utilById = new Map(util.data?.items.map((u) => [u.interface_id, u]) ?? []);
  const names = new Map(exporters.data?.items.map((e) => [e.id, e.name]) ?? []);

  return (
    <>
      <div className="page-head">
        <h1>Interfaces</h1>
        <select value={filters.exporter ?? ""} onChange={(e) => set("exporter", e.target.value || undefined)}>
          <option value="">All exporters</option>
          {exporters.data?.items.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
        <span className="spacer" />
        <TimeRange filters={filters} onRange={(r) => set("range", r)} onWindow={(from, to) => replace({ ...filters, from, to, range: undefined })} />
      </div>
      <Panel flush note="ifIndexes seen in sFlow; name them to replace raw indexes everywhere" title="Interfaces">
        <QueryView q={list}>
          {(d) => (
            <DataTable<Interface>
              rows={d.items}
              rowKey={(r) => r.id}
              initialSort={{ key: "total", desc: true }}
              columns={[
                { key: "exp", label: "Exporter", render: (r) => names.get(r.exporter_id) ?? r.exporter_id, sort: (r) => names.get(r.exporter_id) ?? r.exporter_id },
                { key: "label", label: "Interface", render: (r) => <Link to={interfaceLink(r.exporter_id, r.ifindex, filters)}>{r.label}</Link>, sort: (r) => r.label },
                { key: "ifindex", label: "ifIndex", render: (r) => r.ifindex, sort: (r) => r.ifindex, num: true },
                { key: "lldp", label: "LLDP neighbor", render: (r) => <LldpCell i={r} />, sort: (r) => r.controller_port?.lldp_neighbor ?? "" },
                { key: "speed", label: "Speed", render: (r) => formatSpeed(r.speed_bps), sort: (r) => r.speed_bps, num: true },
                { key: "uavg", label: "Avg utilization", render: (r) => utilById.has(r.id) ? <UtilBars inPct={utilById.get(r.id)!.in_avg_pct} outPct={utilById.get(r.id)!.out_avg_pct} /> : <span className="muted">no counters</span>,
                  sort: (r) => Math.max(utilById.get(r.id)?.in_avg_pct ?? -1, utilById.get(r.id)?.out_avg_pct ?? -1) },
                { key: "upeak", label: "Peak utilization", render: (r) => utilById.has(r.id) ? <UtilBars inPct={utilById.get(r.id)!.in_max_pct} outPct={utilById.get(r.id)!.out_max_pct} /> : "",
                  sort: (r) => Math.max(utilById.get(r.id)?.in_max_pct ?? -1, utilById.get(r.id)?.out_max_pct ?? -1) },
                { key: "in", label: "Est. in", render: (r) => formatBytes(byId.get(r.id)?.in_bytes ?? 0), sort: (r) => byId.get(r.id)?.in_bytes ?? 0, num: true },
                { key: "out", label: "Est. out", render: (r) => formatBytes(byId.get(r.id)?.out_bytes ?? 0), sort: (r) => byId.get(r.id)?.out_bytes ?? 0, num: true },
                { key: "total", label: "Total", render: (r) => formatBytes(byId.get(r.id)?.bytes ?? 0), sort: (r) => byId.get(r.id)?.bytes ?? 0, num: true },
                { key: "seen", label: "Last seen", render: (r) => formatAgo(r.last_seen), sort: (r) => r.last_seen },
              ]}
            />
          )}
        </QueryView>
      </Panel>
    </>
  );
}
