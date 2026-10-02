import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useFilters } from "../hooks/useFilters";
import { useTop } from "../hooks/queries";
import { TimeRange } from "../components/TimeRange";
import { ActiveFilters } from "../components/FilterBar";
import { DataTable, PercentBar } from "../components/DataTable";
import { CsvLink, explorerLink, interfaceLink } from "../components/TopPanels";
import { Panel, QueryView } from "../components/ui";
import type { Filters } from "../lib/filters";
import { formatBytes, formatCount, formatRate, formatSpeed, formatTime } from "../lib/format";

interface MacItem { mac: string; vendor: string | null; name: string | null; ip: string | null; ips: number; gateway: boolean; vlans: number[]; bytes: number; packets: number; percent: number }
interface EtherItem { ether_type: number | null; ether_type_hex: string | null; name: string; bytes: number; packets: number; samples: number; percent: number }
interface BcastItem {
  exporter_id: string; exporter_name: string; ifindex: number; interface_id: string; label: string; speed_bps: number | null;
  in_bcast_avg_pps: number; in_bcast_max_pps: number; out_bcast_avg_pps: number; out_bcast_max_pps: number;
  in_mcast_avg_pps: number; in_mcast_max_pps: number; out_mcast_avg_pps: number; out_mcast_max_pps: number;
  in_bcast_share: number | null; out_bcast_share: number | null; storm: boolean; bcast_peak_at: string; storm_polls: number;
}

/** Layer 2: MAC addresses, EtherTypes, broadcast/multicast per port. */
export function Layer2() {
  const { filters, set, replace } = useFilters();
  return (
    <>
      <div className="page-head">
        <h1>Layer 2</h1>
        <span className="sub">MAC addresses, non-IP traffic, broadcast and multicast</span>
        <span className="spacer" />
        <TimeRange filters={filters} onRange={(r) => set("range", r)} onWindow={(from, to) => replace({ ...filters, from, to, range: undefined })} />
      </div>
      <ActiveFilters filters={filters} set={set} />
      <Broadcast filters={filters} />
      <div className="grid two" style={{ marginTop: 12 }}>
        <Macs filters={filters} direction="src" />
        <Macs filters={filters} direction="dst" />
      </div>
      <EtherTypes filters={filters} />
    </>
  );
}

function Macs(props: { filters: Filters; direction: "src" | "dst" }) {
  const q = useTop<MacItem>("top-macs", props.filters, 15, { direction: props.direction });
  return (
    <Panel title={props.direction === "src" ? "Top source MACs" : "Top destination MACs"} note="estimated"
      actions={<CsvLink path="top-macs" filters={props.filters} extra={{ direction: props.direction }} />} flush>
      <QueryView q={q}>
        {(d) => (
          <DataTable<MacItem>
            rows={d.items} rowKey={(r) => r.mac} initialSort={{ key: "bytes", desc: true }}
            columns={[
              { key: "mac", label: "MAC", render: (r) => (
                <span className="host">
                  {r.name && <span className="host-name">{r.name}</span>}
                  <span className="mono">{r.mac}{r.vendor && <span className="vendor"> · {r.vendor}</span>}</span>
                </span>), sort: (r) => r.mac },
              { key: "ips", label: "IPs", render: (r) => r.gateway ? <span className="badge warn" title="MAC seen with many IPs: router or gateway">gateway · {r.ips}</span>
                  : r.ip ? <Link className="mono" to={explorerLink(props.filters, { ip: r.ip })}>{r.ip}</Link> : formatCount(r.ips), sort: (r) => r.ips, num: true },
              { key: "vlans", label: "VLAN", render: (r) => r.vlans.join(", ") || "–" },
              { key: "bytes", label: "Est. traffic", render: (r) => formatBytes(r.bytes), sort: (r) => r.bytes, num: true },
              { key: "percent", label: "Share", render: (r) => <PercentBar percent={r.percent} />, sort: (r) => r.percent },
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

function EtherTypes(props: { filters: Filters }) {
  const q = useTop<EtherItem>("top-ethertypes", props.filters, 20);
  return (
    <Panel title="EtherTypes" note="IPv4, IPv6 and non-IP protocols (ARP, LLDP, STP, LACP...)" flush>
      <QueryView q={q}>
        {(d) => (
          <DataTable<EtherItem>
            rows={d.items} rowKey={(r) => String(r.ether_type)} initialSort={{ key: "bytes", desc: true }}
            columns={[
              { key: "name", label: "Protocol", render: (r) => r.name, sort: (r) => r.name },
              { key: "hex", label: "EtherType", render: (r) => <span className="mono">{r.ether_type_hex ?? "length (802.3)"}</span> },
              { key: "samples", label: "Samples", render: (r) => formatCount(r.samples), sort: (r) => r.samples, num: true },
              { key: "packets", label: "Est. packets", render: (r) => formatCount(r.packets), sort: (r) => r.packets, num: true },
              { key: "bytes", label: "Est. traffic", render: (r) => formatBytes(r.bytes), sort: (r) => r.bytes, num: true },
              { key: "percent", label: "Share", render: (r) => <PercentBar percent={r.percent} />, sort: (r) => r.percent },
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

function Broadcast(props: { filters: Filters }) {
  const [threshold, setThreshold] = useState(1000);
  const f: Filters = { range: props.filters.range, from: props.filters.from, to: props.filters.to, exporter: props.filters.exporter };
  const q = useQuery({
    queryKey: ["bcast", f, threshold],
    queryFn: () => api.get<{ storms: number; items: BcastItem[] }>("/utilization/broadcast", f, { threshold_pps: threshold, limit: 200 }),
    refetchInterval: 30_000,
  });
  const pps = (avg: number, max: number) => <span title={`average ${formatRate(avg)} pps`}>{formatRate(max)}</span>;
  return (
    <Panel
      title="Broadcast & multicast per port"
      note={q.data ? (q.data.storms ? `${q.data.storms} port(s) above ${formatCount(threshold)} broadcast pps` : "no broadcast storm") + " · exact, from interface counters · peak packets/s (hover: average)" : undefined}
      actions={<label className="inline-edit"><span className="muted">storm ≥</span>
        <input type="number" min={1} value={threshold} onChange={(e) => setThreshold(Math.max(1, Number(e.target.value)))} style={{ width: 90 }} />
        <span className="muted">pps</span></label>}
      flush
    >
      <QueryView q={q}>
        {(d) => (
          <DataTable<BcastItem>
            rows={d.items} rowKey={(r) => r.interface_id} initialSort={{ key: "bin", desc: true }}
            empty="No interface counters in this window."
            columns={[
              { key: "storm", label: "", render: (r) => r.storm ? <span className="badge bad">storm</span> : "" , sort: (r) => (r.storm ? 1 : 0) },
              { key: "exp", label: "Exporter", render: (r) => r.exporter_name, sort: (r) => r.exporter_name },
              { key: "if", label: "Interface", render: (r) => <Link to={interfaceLink(r.exporter_id, r.ifindex, props.filters)}>{r.label}</Link>, sort: (r) => r.label },
              { key: "speed", label: "Speed", render: (r) => formatSpeed(r.speed_bps), num: true },
              { key: "bin", label: "Bcast in", render: (r) => pps(r.in_bcast_avg_pps, r.in_bcast_max_pps), sort: (r) => r.in_bcast_max_pps, num: true },
              { key: "bout", label: "Bcast out", render: (r) => pps(r.out_bcast_avg_pps, r.out_bcast_max_pps), sort: (r) => r.out_bcast_max_pps, num: true },
              { key: "min", label: "Mcast in", render: (r) => pps(r.in_mcast_avg_pps, r.in_mcast_max_pps), sort: (r) => r.in_mcast_max_pps, num: true },
              { key: "mout", label: "Mcast out", render: (r) => pps(r.out_mcast_avg_pps, r.out_mcast_max_pps), sort: (r) => r.out_mcast_max_pps, num: true },
              { key: "peak", label: "Peak at", render: (r) => r.in_bcast_max_pps + r.out_bcast_max_pps > 0 ? formatTime(r.bcast_peak_at, true) : "–", sort: (r) => r.bcast_peak_at },
              { key: "dur", label: "Storm polls", render: (r) => r.storm_polls ? <span title="20 s polls above the threshold">{r.storm_polls} × ~20 s</span> : "–", sort: (r) => r.storm_polls, num: true },
              { key: "share", label: "Bcast share in/out", render: (r) => `${r.in_bcast_share ?? "–"} % / ${r.out_bcast_share ?? "–"} %`, sort: (r) => Math.max(r.in_bcast_share ?? 0, r.out_bcast_share ?? 0), num: true },
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}
