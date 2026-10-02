import { useCollectorStatus, useSummary, useSystemStatus } from "../hooks/queries";
import { useFilters } from "../hooks/useFilters";
import { TimeRange } from "../components/TimeRange";
import { TimelinePanel } from "../components/Timeline";
import { TopConversations, TopInterfaces, TopIps } from "../components/TopPanels";
import { ProtocolsDonut, ServicesDonut } from "../components/Donut";
import { UtilizationTable } from "../components/Utilization";
import { ActiveFilters } from "../components/FilterBar";
import { Stat } from "../components/ui";
import { formatBps, formatBytes, formatCount, formatRate } from "../lib/format";

const NOW_WINDOW = { range: "5m" };

export function Dashboard() {
  const { filters, set, replace } = useFilters();
  const current = useSummary({ ...NOW_WINDOW, exporter: filters.exporter });
  const collector = useCollectorStatus();
  const system = useSystemStatus();

  return (
    <>
      <div className="page-head">
        <h1>Dashboard</h1>
        <span className="spacer" />
        <TimeRange filters={filters} onRange={(r) => set("range", r)} onWindow={(from, to) => replace({ ...filters, from, to, range: undefined })} />
      </div>
      <ActiveFilters filters={filters} set={set} />

      <div className="grid stats" style={{ marginTop: 8 }}>
        <Stat
          label="Current estimated traffic"
          value={current.data ? formatBps(current.data.summary.bps) : "…"}
          hint="average over the last 5 min"
        />
        <Stat label="sFlow samples/s" value={collector.data ? formatRate(collector.data.counters.samples_per_second) : collector.isError ? "–" : "…"}
          hint={collector.data ? `${formatRate(collector.data.counters.datagrams_per_second)} datagrams/s` : "collector"} />
        <Stat label="Active exporters" value={collector.data ? formatCount(collector.data.counters.exporters_active) : "…"}
          hint={collector.data ? `${collector.data.counters.exporters_total} known` : undefined} />
        <Stat label="Active conversations" value={current.data ? formatCount(current.data.summary.conversations) : "…"}
          hint="source/destination pairs, last 5 min" />
        <Stat label="Database size" value={system.data?.storage ? formatBytes(system.data.storage.database_bytes) : "…"}
          hint={system.data?.storage ? `disk ${system.data.storage.disk_used_percent} % used · ${system.data.retention_days} d retention` : undefined} />
      </div>

      <TimelinePanel filters={filters} onZoom={(from, to) => replace({ ...filters, from, to, range: undefined })} />

      <div className="grid two" style={{ marginTop: 12 }}>
        <ServicesDonut filters={filters} />
        <ProtocolsDonut filters={filters} />
      </div>
      <UtilizationTable filters={filters} limit={8} title="Interface utilization (most loaded)" />
      <div className="grid two" style={{ marginTop: 12 }}>
        <TopIps direction="src" filters={filters} title="Top talkers (sources)" />
        <TopIps direction="dst" filters={filters} />
      </div>
      <div className="grid two">
        <TopConversations filters={filters} />
        <TopInterfaces filters={filters} title="Top interfaces by traffic" />
      </div>
    </>
  );
}
