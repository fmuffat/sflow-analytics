import { useState } from "react";
import type { FlowItem } from "../api/types";
import { useFlows, useSummary } from "../hooks/queries";
import { useFilters } from "../hooks/useFilters";
import { FilterBar } from "../components/FilterBar";
import { TimelinePanel } from "../components/Timeline";
import { FlowMap } from "../components/FlowMap";
import { GroupViews } from "../components/GroupViews";
import { CsvLink, Host } from "../components/TopPanels";
import { TopConversations, TopIps, TopServices, TopVlans } from "../components/TopPanels";
import { DataTable } from "../components/DataTable";
import { Panel, QueryView, Tabs } from "../components/ui";
import { formatBps, formatBytes, formatCount, formatTime } from "../lib/format";
import { toQuery } from "../lib/filters";

const PAGE = 100;

export function Explorer() {
  const { filters, set, replace } = useFilters();
  const summary = useSummary(filters);
  const [tab, setTab] = useState<"conversations" | "map" | "groups" | "flows">("conversations");

  return (
    <>
      <div className="page-head">
        <h1>Traffic Explorer</h1>
        <span className="sub">All filters combine; the URL can be bookmarked.</span>
      </div>
      <Panel>
        <FilterBar filters={filters} onChange={replace} set={set} />
      </Panel>

      <div style={{ marginTop: 12 }}>
        <TimelinePanel
          filters={filters}
          note={summary.data ? `${formatBytes(summary.data.summary.bytes)} estimated · average ${formatBps(summary.data.summary.bps)} · ${formatCount(summary.data.summary.samples)} samples · ${formatCount(summary.data.summary.conversations)} conversations` : undefined}
          onZoom={(from, to) => replace({ ...filters, from, to, range: undefined })}
        />
      </div>

      <div className="grid three" style={{ marginTop: 12 }}>
        <TopIps direction="src" filters={filters} />
        <TopIps direction="dst" filters={filters} />
        <TopServices filters={filters} />
      </div>

      <Tabs tabs={[["conversations", "Conversations"], ["map", "Flow map"], ["groups", "Groups"], ["flows", "Flow samples"]]} value={tab} onChange={setTab} />
      {tab === "groups" ? (
        <GroupViews filters={filters} />
      ) : tab === "map" ? (
        <FlowMap filters={filters} />
      ) : tab === "conversations" ? (
        <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 1fr)" }}>
          <TopConversations filters={filters} limit={50} detailed title="Conversations" />
          <TopVlans filters={filters} />
        </div>
      ) : (
        <FlowTable key={toQuery(filters)} />
      )}
    </>
  );
}

function FlowTable() {
  const { filters } = useFilters();
  const [offset, setOffset] = useState(0);
  const q = useFlows(filters, PAGE, offset);
  const port = (p: number | null) => (p === null ? "" : `:${p}`);
  return (
    <Panel
      title="Flow samples"
      note="individual sFlow samples, newest first"
      actions={
        <span className="inline-edit">
          <CsvLink path="flows" filters={filters} />
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>‹ Newer</button>
          <span className="muted">{offset + 1}–{offset + (q.data?.items.length ?? 0)}</span>
          <button disabled={(q.data?.items.length ?? 0) < PAGE} onClick={() => setOffset(offset + PAGE)}>Older ›</button>
        </span>
      }
      flush
    >
      <QueryView q={q}>
        {(d) => (
          <DataTable<FlowItem>
            rows={d.items}
            rowKey={(r, i) => r.timestamp + i}
            columns={[
              { key: "t", label: "Time", render: (r) => formatTime(r.timestamp, true) },
              { key: "exp", label: "Exporter", render: (r) => r.exporter_name },
              { key: "if", label: "In → out", render: (r) => `${r.input_ifindex ?? "–"} → ${r.output_ifindex ?? "–"}` },
              { key: "vlan", label: "VLAN", render: (r) => r.vlan ?? "–", num: true },
              { key: "src", label: "Source", render: (r) => r.src_ip ? <Host ip={r.src_ip + port(r.src_port)} name={r.src_name} /> : <span className="mono" title={r.src_mac_vendor ?? ""}>{r.src_mac}{r.src_mac_vendor ? ` · ${r.src_mac_vendor}` : ""}</span> },
              { key: "dst", label: "Destination", render: (r) => r.dst_ip ? <Host ip={r.dst_ip + port(r.dst_port)} name={r.dst_name} /> : <span className="mono" title={r.dst_mac_vendor ?? ""}>{r.dst_mac}{r.dst_mac_vendor ? ` · ${r.dst_mac_vendor}` : ""}</span> },
              { key: "mac", label: "MAC (src → dst)", render: (r) => <span className="mono" style={{ fontSize: 11 }} title={`${r.src_mac_vendor ?? "?"} → ${r.dst_mac_vendor ?? "?"}`}>{r.src_mac_vendor ?? r.src_mac ?? "–"} → {r.dst_mac_vendor ?? r.dst_mac ?? "–"}</span> },
              { key: "proto", label: "Protocol", render: (r) => r.protocol },
              { key: "svc", label: "Service", render: (r) => r.service },
              { key: "size", label: "Size", render: (r) => formatCount(r.sampled_packet_size) + " B", num: true },
              { key: "rate", label: "1:N", render: (r) => formatCount(r.sampling_rate), num: true },
              { key: "est", label: "Est. traffic", render: (r) => formatBytes(r.estimated_bytes), num: true },
            ]}
          />
        )}
      </QueryView>
    </Panel>
  );
}
