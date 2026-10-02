import { Link, useParams } from "react-router-dom";
import { useExporter, useInterface, useSummary, useTimeseries, useUpdateInterface, useUtilizationSeries } from "../hooks/queries";
import { BroadcastChart, DiscardsChart, UtilizationChart } from "../components/Utilization";
import { FlowMap } from "../components/FlowMap";
import { ProtocolsDonut, ServicesDonut } from "../components/Donut";
import { TrendsPanel } from "../components/Trends";
import { useFilters } from "../hooks/useFilters";
import { TimeRange } from "../components/TimeRange";
import { TrafficChart } from "../components/TrafficChart";
import { explorerLink, TopConversations, TopIps, TopServices, TopVlans } from "../components/TopPanels";
import { EditableText, Panel, QueryView, Stat } from "../components/ui";
import { deviceLink } from "./Devices";
import { formatAgo, formatBps, formatSpeed } from "../lib/format";

export function InterfaceDetail() {
  const params = useParams();
  const exporterId = decodeURIComponent(params.exporter ?? "");
  const ifindex = Number(params.ifindex);
  const { filters: page, set, replace } = useFilters();
  const exporter = useExporter(exporterId);
  const iface = useInterface(exporterId, ifindex);
  const rename = useUpdateInterface(exporterId, ifindex);
  const util = useUtilizationSeries(page, exporterId, ifindex);

  const base = { ...page, exporter: exporterId };
  const either = { ...base, ifindex: String(ifindex) };
  const tsIn = useTimeseries({ ...base, input_ifindex: String(ifindex) });
  const tsOut = useTimeseries({ ...base, output_ifindex: String(ifindex) });
  const nowIn = useSummary({ range: "5m", exporter: exporterId, input_ifindex: String(ifindex) });
  const nowOut = useSummary({ range: "5m", exporter: exporterId, output_ifindex: String(ifindex) });
  const speed = iface.data?.speed_bps ?? null;
  const utilText = (bps?: number) => (bps !== undefined && speed ? ` · ${((bps / speed) * 100).toFixed(1)} % of ${formatSpeed(speed)}` : "");

  return (
    <>
      <div className="page-head">
        <h1>
          <Link to={deviceLink(exporterId)}>{exporter.data?.name ?? exporterId}</Link> / {iface.data?.label ?? `ifIndex ${ifindex}`}
        </h1>
        <span className="spacer" />
        <TimeRange filters={page} onRange={(r) => set("range", r)} onWindow={(from, to) => replace({ ...page, from, to, range: undefined })} />
      </div>

      <div className="grid stats">
        <Stat label="Current in (est.)" value={nowIn.data ? formatBps(nowIn.data.summary.bps) : "…"} hint={"last 5 min, sampled" + utilText(nowIn.data?.summary.bps)} />
        <Stat label="Current out (est.)" value={nowOut.data ? formatBps(nowOut.data.summary.bps) : "…"} hint={"last 5 min, sampled" + utilText(nowOut.data?.summary.bps)} />
        <div className="panel stat">
          <div className="label">Interface</div>
          <QueryView q={iface}>
            {(i) => (
              <dl className="kv" style={{ marginTop: 4 }}>
                <dt>Name</dt>
                <dd><EditableText value={i.name} placeholder="e.g. 1/1/48" maxLength={64} onSave={(v) => rename.mutateAsync({ name: v })} /></dd>
                <dt>Description</dt>
                <dd><EditableText value={i.description} placeholder="none" maxLength={500} onSave={(v) => rename.mutateAsync({ description: v })} /></dd>
                <dt>ifIndex</dt><dd>{i.ifindex}</dd>
                {i.controller_port && (i.controller_port.mapping_uncertain ? (
                  <><dt>Controller port</dt><dd><span className="badge warn">mapping uncertain</span> {i.controller_port.port_id}</dd></>
                ) : (
                  <>
                    <dt>Port / status</dt><dd>{i.controller_port.port_id} · {i.controller_port.status ?? "–"}{i.controller_port.lag_name ? ` · LAG ${i.controller_port.lag_name}` : ""}</dd>
                    <dt>Untagged VLAN</dt><dd>{i.controller_port.vlan_untagged ?? "–"}</dd>
                    <dt>LLDP neighbor</dt><dd>{i.controller_port.lldp_neighbor ?? i.controller_port.lldp_mac ?? "none"}</dd>
                  </>
                ))}
                <dt>Speed / oper</dt><dd>{formatSpeed(i.speed_bps)} / {i.oper_up === null ? "–" : i.oper_up ? "up" : "down"}</dd>
                <dt>Last seen</dt><dd>{formatAgo(i.last_seen)}</dd>
              </dl>
            )}
          </QueryView>
        </div>
      </div>

      <Panel title="Utilization" note="exact, from interface counters">
        <QueryView q={util}>{(d) => <UtilizationChart data={d} />}</QueryView>
      </Panel>
      <div style={{ marginTop: 12 }}>
        <Panel title="Discards and errors" note="packets dropped by the switch on this port (congestion hint)">
          <QueryView q={util}>{(d) => <DiscardsChart data={d} />}</QueryView>
        </Panel>
      </div>
      <div style={{ marginTop: 12 }}>
        <Panel title="Broadcast & multicast" note="packets/s from interface counters (storm detection)">
          <QueryView q={util}>{(d) => <BroadcastChart data={d} />}</QueryView>
        </Panel>
      </div>
      <div style={{ marginTop: 12 }} />
      <Panel
        title="Traffic history (sampled flows)"
        note="in = entering the switch on this port, out = leaving it"
        actions={<Link to={explorerLink(either, {})}>Open in Traffic Explorer →</Link>}
      >
        {tsIn.data && tsOut.data ? (
          <TrafficChart
            series={[
              { name: "In", data: tsIn.data, color: "var(--series-in)" },
              { name: "Out", data: tsOut.data, color: "var(--series-out)" },
            ]}
            onZoom={(from, to) => replace({ ...page, from, to, range: undefined })}
          />
        ) : (
          <QueryView q={tsIn.isError ? tsIn : tsOut}>{() => null}</QueryView>
        )}
      </Panel>

      <div className="grid two" style={{ marginTop: 12 }}>
        <ServicesDonut filters={either} />
        <ProtocolsDonut filters={either} />
      </div>

      <div style={{ marginTop: 12 }}>
        <FlowMap filters={either} height={400} title="Who talks to whom on this interface" />
      </div>

      <div style={{ marginTop: 12 }}>
        <TrendsPanel interfaces={[`${exporterId}/${ifindex}`]} title="Trends (period comparison)" />
      </div>
      <div className="grid two" style={{ marginTop: 12 }}>
        <TopIps direction="src" filters={either} />
        <TopIps direction="dst" filters={either} />
      </div>
      <div className="grid two">
        <TopConversations filters={either} />
        <TopServices filters={either} />
      </div>
      <TopVlans filters={either} title="VLANs observed" />
    </>
  );
}
