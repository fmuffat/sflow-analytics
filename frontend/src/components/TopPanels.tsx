import { useState } from "react";
import { Link } from "react-router-dom";
import { csvUrl } from "../api/client";
import type {
  BidirConversationItem, ConversationItem, InterfaceTrafficItem, IpItem, ServiceItem, VlanItem,
} from "../api/types";
import type { Filters } from "../lib/filters";
import { toQuery } from "../lib/filters";
import { formatBytes, formatCount, formatSpeed, formatTime } from "../lib/format";
import { useTop } from "../hooks/queries";
import { AliasButton } from "./Alias";
import { DataTable, PercentBar } from "./DataTable";
import type { Column } from "./DataTable";
import { Panel, QueryView } from "./ui";

/** Traffic Explorer URL: current filters refined with `extra`. */
export const explorerLink = (filters: Filters, extra: Filters) => "/explorer?" + toQuery({ ...filters, ...extra });

export const interfaceLink = (exporterId: string, ifindex: number, filters: Filters) =>
  `/interfaces/${encodeURIComponent(exporterId)}/${ifindex}?` + toQuery({ range: filters.range, from: filters.from, to: filters.to });

/** IP with the endpoint name when known (controller client name). */
export const Host = (props: { ip: string; name?: string | null; vendor?: string | null; editable?: boolean }) => {
  const bare = props.ip.replace(/:\d+$/, "").replace(/^\[|\]$/g, "");
  const ip = /^[\d.]+:\d+$/.test(props.ip) ? bare : props.ip; // strip ":port" from IPv4 only
  return (
    <span className="host-wrap">
      <span className="host" title={[props.name, props.ip, props.vendor].filter(Boolean).join(" · ")}>
        {props.name && <span className="host-name">{props.name}</span>}
        <span className={"mono truncate" + (props.name ? " muted" : "")}>
          {props.ip}{props.vendor && <span className="vendor"> · {props.vendor}</span>}
        </span>
      </span>
      {props.editable !== false && <AliasButton ip={ip} name={props.name} />}
    </span>
  );
};

const volumeCols = <T extends { bytes: number; packets: number; percent: number }>(): Column<T>[] => [
  { key: "bytes", label: "Est. traffic", render: (r) => formatBytes(r.bytes), sort: (r) => r.bytes, num: true },
  { key: "packets", label: "Est. packets", render: (r) => formatCount(r.packets), sort: (r) => r.packets, num: true },
  { key: "percent", label: "Share", render: (r) => <PercentBar percent={r.percent} />, sort: (r) => r.percent },
];

type TopProps = { filters: Filters; limit?: number; title?: string };

/** "CSV" download link for a panel. */
export const CsvLink = (props: { path: string; filters: Filters; extra?: Record<string, string> }) => (
  <a className="btn" href={csvUrl(props.path, props.filters, props.extra)} download title="Download as CSV">CSV</a>
);

export function TopIps(props: TopProps & { direction: "src" | "dst" }) {
  const path = props.direction === "src" ? "top-sources" : "top-destinations";
  const key = props.direction === "src" ? "src_ip" : "dst_ip";
  const q = useTop<IpItem>(path, props.filters, props.limit ?? 10);
  return (
    <Panel title={props.title ?? (props.direction === "src" ? "Top sources" : "Top destinations")} actions={<CsvLink path={path} filters={props.filters} />} flush>
      <QueryView q={q}>
        {(d) => (
          <DataTable
            rows={d.items}
            rowKey={(r) => r.ip}
            initialSort={{ key: "bytes", desc: true }}
            columns={[
              { key: "ip", label: props.direction === "src" ? "Source IP" : "Destination IP",
                render: (r) => <Link to={explorerLink(props.filters, { [key]: r.ip })}><Host ip={r.ip} name={r.name} vendor={r.vendor} /></Link>,
                sort: (r) => r.ip },
              ...volumeCols<IpItem>(),
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

export function TopServices(props: TopProps) {
  const q = useTop<ServiceItem>("top-services", props.filters, props.limit ?? 10);
  return (
    <Panel title={props.title ?? "Top services"} note="protocol + port, not DPI" actions={<CsvLink path="top-services" filters={props.filters} />} flush>
      <QueryView q={q}>
        {(d) => (
          <DataTable
            rows={d.items}
            rowKey={(r) => r.service}
            initialSort={{ key: "bytes", desc: true }}
            columns={[
              { key: "service", label: "Service",
                render: (r) => <Link to={explorerLink(props.filters, { service: r.service })}>{r.service}</Link>,
                sort: (r) => r.service },
              ...volumeCols<ServiceItem>(),
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

export function TopVlans(props: TopProps) {
  const q = useTop<VlanItem>("top-vlans", props.filters, props.limit ?? 10);
  return (
    <Panel title={props.title ?? "Top VLANs"} actions={<CsvLink path="top-vlans" filters={props.filters} />} flush>
      <QueryView q={q}>
        {(d) => (
          <DataTable
            rows={d.items}
            rowKey={(r) => String(r.vlan)}
            initialSort={{ key: "bytes", desc: true }}
            columns={[
              { key: "vlan", label: "VLAN",
                render: (r) => (r.vlan === null ? <span className="muted">none / unknown</span>
                  : <Link to={explorerLink(props.filters, { vlan: String(r.vlan) })}>{r.vlan}</Link>),
                sort: (r) => r.vlan },
              ...volumeCols<VlanItem>(),
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}

export function TopConversations(props: TopProps & { detailed?: boolean }) {
  const [bidir, setBidir] = useState(false);
  if (bidir) return <BidirConversations {...props} onOneWay={() => setBidir(false)} />;
  return <OneWayConversations {...props} onBidir={() => setBidir(true)} />;
}

function DirectionToggle(props: { bidir: boolean; onChange: () => void }) {
  return (
    <span className="seg">
      <button className={props.bidir ? "" : "on"} onClick={props.bidir ? props.onChange : undefined}>One-way</button>
      <button className={props.bidir ? "on" : ""} onClick={props.bidir ? undefined : props.onChange}>Both directions</button>
    </span>
  );
}

function BidirConversations(props: TopProps & { detailed?: boolean; onOneWay: () => void }) {
  const q = useTop<BidirConversationItem>("top-conversations", props.filters, props.limit ?? 10, { by: "bidir" });
  const cols: Column<BidirConversationItem>[] = [
    { key: "a", label: "Host A", render: (r) => <Host ip={r.host_a} name={r.host_a_name} vendor={r.host_a_vendor} />, sort: (r) => r.host_a_name ?? r.host_a },
    { key: "b", label: "Host B", render: (r) => <Host ip={r.host_b} name={r.host_b_name} vendor={r.host_b_vendor} />, sort: (r) => r.host_b_name ?? r.host_b },
    { key: "service", label: "Service", render: (r) => r.service, sort: (r) => r.service },
    { key: "ab", label: "A → B", render: (r) => formatBytes(r.a_to_b_bytes), sort: (r) => r.a_to_b_bytes, num: true },
    { key: "ba", label: "B → A", render: (r) => formatBytes(r.b_to_a_bytes), sort: (r) => r.b_to_a_bytes, num: true },
    { key: "bytes", label: "Est. total",
      render: (r) => <Link to={explorerLink(props.filters, { ip: `${r.host_a},${r.host_b}`, service: r.service })}>{formatBytes(r.bytes)}</Link>,
      sort: (r) => r.bytes, num: true },
    { key: "percent", label: "Share", render: (r) => <PercentBar percent={r.percent} />, sort: (r) => r.percent },
  ];
  if (props.detailed) {
    cols.push(
      { key: "first", label: "First seen", render: (r) => formatTime(r.first_seen), sort: (r) => r.first_seen },
      { key: "last", label: "Last seen", render: (r) => formatTime(r.last_seen), sort: (r) => r.last_seen },
      { key: "vlan", label: "VLAN", render: (r) => r.vlans.join(", ") || "–" },
    );
  }
  return (
    <Panel
      title={props.title ?? "Top conversations"}
      actions={<><DirectionToggle bidir onChange={props.onOneWay} /><CsvLink path="top-conversations" filters={props.filters} extra={{ by: "bidir" }} /></>}
      flush
    >
      <QueryView q={q}>
        {(d) => <DataTable rows={d.items} rowKey={(r) => `${r.host_a}|${r.host_b}|${r.service}`} columns={cols} initialSort={{ key: "bytes", desc: true }} />}
      </QueryView>
    </Panel>
  );
}

function OneWayConversations(props: TopProps & { detailed?: boolean; onBidir: () => void }) {
  const q = useTop<ConversationItem>("top-conversations", props.filters, props.limit ?? 10);
  const cols: Column<ConversationItem>[] = [
    { key: "src", label: "Source", render: (r) => <Host ip={r.src_ip} name={r.src_name} vendor={r.src_vendor} />, sort: (r) => r.src_name ?? r.src_ip },
    { key: "dst", label: "Destination", render: (r) => <Host ip={r.dst_ip} name={r.dst_name} vendor={r.dst_vendor} />, sort: (r) => r.dst_name ?? r.dst_ip },
    { key: "service", label: "Service", render: (r) => r.service, sort: (r) => r.service },
    { key: "bytes", label: "Est. traffic",
      render: (r) => <Link to={explorerLink(props.filters, { src_ip: r.src_ip, dst_ip: r.dst_ip, service: r.service })}>{formatBytes(r.bytes)}</Link>,
      sort: (r) => r.bytes, num: true },
    { key: "percent", label: "Share", render: (r) => <PercentBar percent={r.percent} />, sort: (r) => r.percent },
  ];
  if (props.detailed) {
    cols.push(
      { key: "first", label: "First seen", render: (r) => formatTime(r.first_seen), sort: (r) => r.first_seen },
      { key: "last", label: "Last seen", render: (r) => formatTime(r.last_seen), sort: (r) => r.last_seen },
      { key: "exp", label: "Exporter", render: (r) => r.exporters.map((e) => e.name).join(", ") },
      { key: "in", label: "In if", render: (r) => r.input_ifindexes.join(", ") || "–" },
      { key: "out", label: "Out if", render: (r) => r.output_ifindexes.join(", ") || "–" },
      { key: "vlan", label: "VLAN", render: (r) => r.vlans.join(", ") || "–" },
    );
  }
  return (
    <Panel
      title={props.title ?? "Top conversations"}
      actions={<><DirectionToggle bidir={false} onChange={props.onBidir} /><CsvLink path="top-conversations" filters={props.filters} /></>}
      flush
    >
      <QueryView q={q}>
        {(d) => <DataTable rows={d.items} rowKey={(r) => `${r.src_ip}|${r.dst_ip}|${r.service}`} columns={cols} initialSort={{ key: "bytes", desc: true }} />}
      </QueryView>
    </Panel>
  );
}

export function TopInterfaces(props: TopProps & { showExporter?: boolean }) {
  const q = useTop<InterfaceTrafficItem>("top-interfaces", props.filters, props.limit ?? 10);
  const cols: Column<InterfaceTrafficItem>[] = [];
  if (props.showExporter !== false) {
    cols.push({ key: "exp", label: "Exporter", render: (r) => r.exporter_name, sort: (r) => r.exporter_name });
  }
  cols.push(
    { key: "if", label: "Interface",
      render: (r) => <Link to={interfaceLink(r.exporter_id, r.ifindex, props.filters)}>{r.label}</Link>,
      sort: (r) => r.label },
    { key: "speed", label: "Speed", render: (r) => formatSpeed(r.speed_bps), sort: (r) => r.speed_bps, num: true },
    { key: "in", label: "Est. in", render: (r) => formatBytes(r.in_bytes), sort: (r) => r.in_bytes, num: true },
    { key: "out", label: "Est. out", render: (r) => formatBytes(r.out_bytes), sort: (r) => r.out_bytes, num: true },
    { key: "bytes", label: "Total", render: (r) => formatBytes(r.bytes), sort: (r) => r.bytes, num: true },
  );
  return (
    <Panel title={props.title ?? "Top interfaces"} note="in = entering the switch on that port" actions={<CsvLink path="top-interfaces" filters={props.filters} />} flush>
      <QueryView q={q}>
        {(d) => <DataTable rows={d.items} rowKey={(r) => r.interface_id} columns={cols} initialSort={{ key: "bytes", desc: true }} />}
      </QueryView>
    </Panel>
  );
}
