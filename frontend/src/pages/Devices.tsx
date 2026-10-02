import { Link } from "react-router-dom";
import type { Exporter, ExporterTrafficItem } from "../api/types";
import { useExporters, useTop, useUpdateExporter } from "../hooks/queries";
import { DataTable } from "../components/DataTable";
import { EditableText, Panel, QueryView, StatusBadge } from "../components/ui";
import { formatAgo, formatBytes, formatCount, formatRate } from "../lib/format";

export const deviceLink = (id: string) => `/devices/${encodeURIComponent(id)}`;

function Rename({ e }: { e: Exporter }) {
  const m = useUpdateExporter(e.id);
  return <EditableText value={e.display_name} placeholder={e.agent_ip} maxLength={64} onSave={(v) => m.mutateAsync({ display_name: v })} />;
}

export function Devices() {
  const q = useExporters();
  const traffic = useTop<ExporterTrafficItem>("top-exporters", { range: "1h" }, 1000);
  const bytes = new Map(traffic.data?.items.map((t) => [t.exporter_id, t.bytes]) ?? []);

  return (
    <>
      <div className="page-head">
        <h1>Devices</h1>
        <span className="sub">sFlow exporters, discovered automatically</span>
      </div>
      <Panel flush>
        <QueryView q={q}>
          {(d) => (
            <DataTable<Exporter>
              rows={d.items}
              rowKey={(r) => r.id}
              empty="No exporter yet: configure a switch to send sFlow to this appliance on UDP/6343."
              initialSort={{ key: "name", desc: false }}
              columns={[
                { key: "name", label: "Name", render: (r) => <Link to={deviceLink(r.id)}>{r.name}</Link>, sort: (r) => r.name },
                { key: "rename", label: "", render: (r) => <Rename e={r} /> },
                { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} />, sort: (r) => r.status },
                { key: "model", label: "Model", render: (r) => r.controller?.model ?? "–", sort: (r) => r.controller?.model ?? "" },
                { key: "agent", label: "Agent IP", render: (r) => <span className="mono">{r.agent_ip}</span>, sort: (r) => r.agent_ip },
                { key: "exp", label: "Source IP", render: (r) => <span className="mono">{r.exporter_ip}</span> },
                { key: "sub", label: "Sub-agent", render: (r) => r.agent_sub_id, num: true },
                { key: "seen", label: "Last sFlow", render: (r) => formatAgo(r.last_seen), sort: (r) => r.last_seen },
                { key: "sps", label: "Samples/s", render: (r) => formatRate(r.samples_per_second), sort: (r) => r.samples_per_second ?? null, num: true },
                { key: "rate", label: "Sampling 1:N", render: (r) => formatCount(r.sample_rate), num: true },
                { key: "bytes", label: "Est. traffic 1 h", render: (r) => formatBytes(bytes.get(r.id) ?? 0), sort: (r) => bytes.get(r.id) ?? 0, num: true },
              ]}
            />
          )}
        </QueryView>
      </Panel>
    </>
  );
}
