import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Exporter, Interface, InterfaceTrafficItem } from "../api/types";
import { useExporter, useTop, useUpdateExporter, useUpdateInterface } from "../hooks/queries";
import { useFilters } from "../hooks/useFilters";
import { TimeRange } from "../components/TimeRange";
import { TimelinePanel } from "../components/Timeline";
import { FlowMap } from "../components/FlowMap";
import { ProtocolsDonut, ServicesDonut } from "../components/Donut";
import { explorerLink, interfaceLink, TopConversations, TopInterfaces, TopIps, TopServices } from "../components/TopPanels";
import { DataTable } from "../components/DataTable";
import { EditableText, Panel, QueryView, StatusBadge, Tabs } from "../components/ui";
import { LldpCell } from "../components/Lldp";
import { formatAgo, formatBytes, formatCount, formatRate, formatSpeed, formatTime } from "../lib/format";

type Tab = "overview" | "interfaces" | "stats" | "settings";

export function DeviceDetail() {
  const id = decodeURIComponent(useParams().id ?? "");
  const q = useExporter(id);
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <QueryView q={q}>
      {(e) => (
        <>
          <div className="page-head">
            <h1>{e.name}</h1>
            <StatusBadge status={e.status} />
            <span className="sub">
              agent <span className="mono">{e.agent_ip}</span> · last sFlow {formatAgo(e.last_seen)} ·{" "}
              {formatRate(e.samples_per_second)} samples/s · first seen {formatTime(e.first_seen)}
              {e.controller && <> · {e.controller.model} · S/N {e.controller.serial} · FW {e.controller.firmware}{e.controller.venue ? ` · ${e.controller.venue}` : ""}</>}
            </span>
          </div>
          <Tabs
            tabs={[["overview", "Overview"], ["interfaces", "Interfaces"], ["stats", "Collector stats"], ["settings", "Settings"]]}
            value={tab}
            onChange={setTab}
          />
          {tab === "overview" && <Overview e={e} />}
          {tab === "interfaces" && <Interfaces e={e} />}
          {tab === "stats" && <Stats e={e} />}
          {tab === "settings" && <Settings e={e} />}
        </>
      )}
    </QueryView>
  );
}

function Overview({ e }: { e: Exporter }) {
  const { filters: page, set, replace } = useFilters();
  const filters = { ...page, exporter: e.id };
  return (
    <>
      <div className="page-head">
        <TimeRange filters={page} onRange={(r) => set("range", r)} onWindow={(from, to) => replace({ ...page, from, to, range: undefined })} />
        <span className="spacer" />
        <Link to={explorerLink(filters, {})}>Open in Traffic Explorer →</Link>
      </div>
      <TimelinePanel title="Traffic" filters={filters} onZoom={(from, to) => replace({ ...page, from, to, range: undefined })} />
      <div className="grid two" style={{ marginTop: 12 }}>
        <ServicesDonut filters={filters} />
        <ProtocolsDonut filters={filters} />
      </div>
      <div className="grid two" style={{ marginTop: 12 }}>
        <TopIps direction="src" filters={filters} />
        <TopIps direction="dst" filters={filters} />
      </div>
      <div className="grid two">
        <TopConversations filters={filters} />
        <TopServices filters={filters} />
      </div>
      <TopInterfaces filters={filters} showExporter={false} />
      <div style={{ marginTop: 12 }}>
        <FlowMap filters={filters} preset="input_if,output_if" height={360} compact title="Flow map (ingress → egress ports)" />
      </div>
    </>
  );
}

function InterfaceName({ i }: { i: Interface }) {
  const m = useUpdateInterface(i.exporter_id, i.ifindex);
  return <EditableText value={i.name} placeholder="e.g. 1/1/48" maxLength={64} onSave={(v) => m.mutateAsync({ name: v })} />;
}

function InterfaceDescription({ i }: { i: Interface }) {
  const m = useUpdateInterface(i.exporter_id, i.ifindex);
  const fallback = i.controller_port && !i.controller_port.mapping_uncertain ? i.controller_port.name : null;
  return <EditableText value={i.manual_description ?? null} placeholder={fallback ?? "e.g. Uplink core"} maxLength={64} onSave={(v) => m.mutateAsync({ description: v })} />;
}

function Interfaces({ e }: { e: Exporter }) {
  const { filters } = useFilters();
  const traffic = useTop<InterfaceTrafficItem>("top-interfaces", { ...filters, exporter: e.id }, 1000);
  const byIf = new Map(traffic.data?.items.map((t) => [t.ifindex, t]) ?? []);
  const rows = e.interfaces ?? [];
  return (
    <Panel title="Interfaces" note={`seen in sFlow samples · traffic over ${filters.range ?? "the selected window"}`} flush>
      <DataTable<Interface>
        rows={rows}
        rowKey={(r) => r.id}
        empty="No interface seen yet."
        initialSort={{ key: "ifindex", desc: false }}
        columns={[
          { key: "ifindex", label: "ifIndex", render: (r) => <Link to={interfaceLink(r.exporter_id, r.ifindex, filters)}>{r.ifindex}</Link>, sort: (r) => r.ifindex, num: true },
          { key: "name", label: "Name", render: (r) => <InterfaceName i={r} />, sort: (r) => r.name ?? "" },
          { key: "port", label: "Port", render: (r) => r.port ?? "–", sort: (r) => r.port ?? "" },
          { key: "desc", label: "Description", render: (r) => <InterfaceDescription i={r} />, sort: (r) => r.description ?? "" },
          { key: "lldp", label: "LLDP neighbor", render: (r) => <LldpCell i={r} />, sort: (r) => r.controller_port?.lldp_neighbor ?? "" },
          { key: "speed", label: "Speed", render: (r) => formatSpeed(r.speed_bps), sort: (r) => r.speed_bps, num: true },
          { key: "oper", label: "Oper", render: (r) => (r.oper_up === null ? "–" : r.oper_up ? <span className="badge ok">up</span> : <span className="badge bad">down</span>) },
          { key: "in", label: "Est. in", render: (r) => formatBytes(byIf.get(r.ifindex)?.in_bytes ?? 0), sort: (r) => byIf.get(r.ifindex)?.in_bytes ?? 0, num: true },
          { key: "out", label: "Est. out", render: (r) => formatBytes(byIf.get(r.ifindex)?.out_bytes ?? 0), sort: (r) => byIf.get(r.ifindex)?.out_bytes ?? 0, num: true },
          { key: "seen", label: "Last seen", render: (r) => formatAgo(r.last_seen), sort: (r) => r.last_seen },
        ]}
      />
    </Panel>
  );
}

function Stats({ e }: { e: Exporter }) {
  return (
    <Panel title="Collector statistics" note={e.status_source === "collector" ? "live, since the collector started" : "collector unreachable: last known state"}>
      <dl className="kv">
        <dt>Exporter id</dt><dd className="mono">{e.id}</dd>
        <dt>Source IP (UDP)</dt><dd className="mono">{e.exporter_ip}</dd>
        <dt>Agent IP / sub-agent</dt><dd className="mono">{e.agent_ip} / {e.agent_sub_id}</dd>
        <dt>Status</dt><dd><StatusBadge status={e.status} /></dd>
        <dt>Last sFlow packet</dt><dd>{formatTime(e.last_seen, true)} ({formatAgo(e.last_seen)})</dd>
        <dt>Samples/s</dt><dd>{formatRate(e.samples_per_second)}</dd>
        <dt>Datagrams</dt><dd>{formatCount(e.datagrams)}</dd>
        <dt>Decode errors</dt><dd>{formatCount(e.errors)}</dd>
        <dt>Lost datagrams</dt><dd>{formatCount(e.lost_datagrams)} <span className="est">(sequence gaps)</span></dd>
        <dt>Sampling rate</dt><dd>1:{formatCount(e.sample_rate)}</dd>
        {e.controller && (
          <>
            <dt>{e.controller.source === "snmp" ? "Source" : "Controller"}</dt><dd>{({ ruckusone: "RUCKUS One", smartzone: "SmartZone", snmp: "SNMP" } as Record<string, string>)[e.controller.source] ?? e.controller.source} · {e.controller.source === "snmp" ? "polled" : "synced"} {formatTime(e.controller.synced_at)}</dd>
            <dt>Name / model</dt><dd>{e.controller.name} · {e.controller.model}</dd>
            <dt>Serial / firmware</dt><dd>{e.controller.serial} · {e.controller.firmware}</dd>
            <dt>Status / venue</dt><dd>{e.controller.status} · {e.controller.venue}</dd>
          </>
        )}
      </dl>
    </Panel>
  );
}

function Settings({ e }: { e: Exporter }) {
  const m = useUpdateExporter(e.id);
  const [name, setName] = useState(e.display_name ?? "");
  const [notes, setNotes] = useState(e.notes ?? "");
  const [icxNumbering, setIcxNumbering] = useState(e.icx_numbering ?? false);
  return (
    <Panel title="Settings">
      <div style={{ display: "grid", gap: 10, maxWidth: 520 }}>
        <label className="filters" style={{ display: "flex", flexDirection: "column" }}>
          <span className="muted">Display name (the discovered IP {e.agent_ip} is kept)</span>
          <input value={name} maxLength={64} placeholder={e.agent_ip} onChange={(ev) => setName(ev.target.value)} />
        </label>
        <label style={{ display: "flex", flexDirection: "column" }}>
          <span className="muted">Notes</span>
          <textarea rows={4} maxLength={2000} value={notes} onChange={(ev) => setNotes(ev.target.value)} />
        </label>
        <label className="pick">
          <input type="checkbox" checked={icxNumbering} onChange={(ev) => setIcxNumbering(ev.target.checked)} />
          RUCKUS ICX switch: name the ports from the ifIndex (1/1/8, 1/2/1, LAG3)
        </label>
        <span className="muted" style={{ marginTop: -6, fontSize: 12 }}>Not needed when the switch is known by RUCKUS One, SmartZone or SNMP.</span>
        <span className="inline-edit">
          <button className="primary" disabled={m.isPending} onClick={() => m.mutate({ display_name: name.trim(), notes, icx_numbering: icxNumbering })}>Save</button>
          {m.isSuccess && <span className="muted">Saved.</span>}
          {m.isError && <span className="error">{(m.error as Error).message}</span>}
        </span>
      </div>
    </Panel>
  );
}
