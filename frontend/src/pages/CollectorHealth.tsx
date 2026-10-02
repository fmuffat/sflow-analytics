import { Link } from "react-router-dom";
import type { Exporter } from "../api/types";
import { useCollectorStatus, useExporters, useSystemStatus } from "../hooks/queries";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView, Stat, StatusBadge } from "../components/ui";
import { deviceLink } from "./Devices";
import { formatAgo, formatBytes, formatCount, formatDuration, formatRate, formatTime } from "../lib/format";

export function CollectorHealth() {
  const collector = useCollectorStatus();
  const system = useSystemStatus();
  const exporters = useExporters();

  return (
    <>
      <div className="page-head">
        <h1>Collector Health</h1>
        {system.data && Object.entries(system.data.services).map(([svc, st]) => (
          <span key={svc} className="muted">{svc} <StatusBadge status={st} /></span>
        ))}
      </div>

      <QueryView q={collector}>
        {(c) => {
          const k = c.counters;
          return (
            <>
              <div className="grid stats">
                <Stat label="Collector status" value={<StatusBadge status={c.status} />} hint={`listening on ${c.listen_address} · up ${formatDuration(k.uptime_seconds)}`} />
                <Stat label="Datagrams" value={formatCount(k.datagrams_received)} hint={`${formatRate(k.datagrams_per_second)}/s · last ${formatAgo(k.last_packet_at)}`} />
                <Stat label="Samples/s" value={formatRate(k.samples_per_second)} hint={`${formatCount(k.flow_samples)} flow · ${formatCount(k.counter_samples)} counter samples`} />
                <Stat label="Malformed" value={formatCount(k.malformed_datagrams)} hint={`${formatCount(k.unsupported_version_datagrams)} unsupported version · ${formatCount(k.sample_errors)} sample errors`} />
                <Stat label="DB insert errors" value={formatCount(k.db_insert_failures)} hint={`${formatCount(k.db_rows_inserted)} rows inserted · ${formatCount(k.db_rows_pending)} pending · ${formatCount(k.db_rows_dropped)} dropped`} />
                <Stat label="Active exporters" value={formatCount(k.exporters_active)} hint={`${formatCount(k.exporters_total)} known · ${formatCount(k.datagrams_dropped)} datagrams dropped (queue full)`} />
              </div>
              {c.storage && (
                <Panel title="Storage">
                  <dl className="kv">
                    <dt>Disk</dt>
                    <dd>{c.storage.disk_used_percent} % used of {formatBytes(c.storage.disk_total_bytes)} (budget {c.storage.disk_max_usage_percent} %)
                      {c.storage.inserts_suspended && <span className="badge bad" style={{ marginLeft: 8 }}>inserts suspended</span>}</dd>
                    <dt>Database size</dt><dd>{formatBytes(c.storage.database_bytes)} ({formatCount(c.storage.flow_records_rows)} flow records)</dd>
                    <dt>Oldest flow record</dt><dd>{formatTime(c.storage.oldest_flow_record)}</dd>
                    <dt>Retention</dt><dd>{c.storage.retention_days} days configured
                      {c.storage.estimated_retention_days !== null
                        ? ` · ${c.storage.estimated_retention_days} days achievable at the current rate`
                        : " · estimate available after 1 h of data"}</dd>
                    <dt>Partitions dropped by disk guard</dt><dd>{c.storage.partitions_dropped_by_disk_guard}</dd>
                  </dl>
                </Panel>
              )}
            </>
          );
        }}
      </QueryView>

      <div style={{ marginTop: 12 }}>
        <Panel title="Exporters" flush>
          <QueryView q={exporters}>
            {(d) => (
              <DataTable<Exporter>
                rows={d.items}
                rowKey={(r) => r.id}
                initialSort={{ key: "seen", desc: true }}
                columns={[
                  { key: "name", label: "Exporter", render: (r) => <Link to={deviceLink(r.id)}>{r.name}</Link>, sort: (r) => r.name },
                  { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
                  { key: "seen", label: "Last seen", render: (r) => formatAgo(r.last_seen), sort: (r) => r.last_seen },
                  { key: "sps", label: "Samples/s", render: (r) => formatRate(r.samples_per_second), sort: (r) => r.samples_per_second ?? null, num: true },
                  { key: "dg", label: "Datagrams", render: (r) => formatCount(r.datagrams), sort: (r) => r.datagrams ?? null, num: true },
                  { key: "err", label: "Errors", render: (r) => formatCount(r.errors), sort: (r) => r.errors ?? null, num: true },
                  { key: "lost", label: "Lost (seq. gaps)", render: (r) => formatCount(r.lost_datagrams), sort: (r) => r.lost_datagrams ?? null, num: true },
                ]}
              />
            )}
          </QueryView>
        </Panel>
      </div>
    </>
  );
}
