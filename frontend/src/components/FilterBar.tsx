import { useEffect, useState } from "react";
import type { FilterKey, Filters } from "../lib/filters";
import { activeFilters, FILTER_LABELS } from "../lib/filters";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useExporters } from "../hooks/queries";
import { TimeRange } from "./TimeRange";
import { useGroups } from "../pages/Groups";

const TEXT_FIELDS: [FilterKey, string][] = [
  ["src_ip", "10.1.1.0/24"], ["dst_ip", "10.2.2.20"], ["ip", "either direction"],
  ["protocol", "tcp, udp, icmp"], ["service", "HTTPS, DNS, TCP/8443"], ["vlan", "10,20"],
  ["src_port", ""], ["dst_port", "443"], ["port", "either direction"],
  ["input_ifindex", ""], ["output_ifindex", ""], ["ifindex", "either direction"],
];

/** Full filter editor for the Traffic Explorer. Values apply on Enter or blur. */
export function FilterBar(props: { filters: Filters; onChange: (f: Filters) => void; set: (k: FilterKey, v?: string) => void }) {
  const { filters, set } = props;
  const exporters = useExporters();
  const [draft, setDraft] = useState<Filters>(filters);
  useEffect(() => setDraft(filters), [filters]);

  const commit = (k: FilterKey) => {
    if ((draft[k] ?? "") !== (filters[k] ?? "")) set(k, draft[k]);
  };

  return (
    <div>
      <div className="page-head" style={{ marginBottom: 8 }}>
        <TimeRange
          filters={filters}
          onRange={(r) => set("range", r)}
          onWindow={(from, to) => props.onChange({ ...filters, from, to, range: undefined })}
        />
        <HostSearch onPick={(ip) => set("ip", ip)} />
        <GroupSelect label="Src group" value={filters.src_group} kind="ip" onChange={(v) => set("src_group", v)} />
        <GroupSelect label="Dst group" value={filters.dst_group} kind="ip" onChange={(v) => set("dst_group", v)} />
        <GroupSelect label="If group" value={filters.if_group} kind="if" onChange={(v) => set("if_group", v)} />
        <label className="inline-edit">
          <span className="muted">Exporter</span>
          <select value={filters.exporter ?? ""} onChange={(e) => set("exporter", e.target.value || undefined)}>
            <option value="">All</option>
            {exporters.data?.items.map((e) => (
              <option key={e.id} value={e.id}>{e.name}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="filters">
        {TEXT_FIELDS.map(([k, ph]) => (
          <label key={k}>
            {FILTER_LABELS[k]}
            <input
              value={draft[k] ?? ""}
              placeholder={ph}
              onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
              onBlur={() => commit(k)}
              onKeyDown={(e) => e.key === "Enter" && commit(k)}
            />
          </label>
        ))}
      </div>
      <ActiveFilters filters={filters} set={set} />
    </div>
  );
}

function GroupSelect(props: { label: string; kind: "ip" | "if"; value?: string; onChange: (v?: string) => void }) {
  const groups = useGroups(props.kind);
  if (!groups.data?.items.length) return null;
  return (
    <label className="inline-edit">
      <span className="muted">{props.label}</span>
      <select value={props.value ?? ""} onChange={(e) => props.onChange(e.target.value || undefined)}>
        <option value="">All</option>
        {groups.data.items.map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
      </select>
    </label>
  );
}

/** Search a host by name (alias, controller, DNS) and filter on its IP. */
function HostSearch(props: { onPick: (ip: string) => void }) {
  const [q, setQ] = useState("");
  const hits = useQuery({
    queryKey: ["hosts", q],
    queryFn: () => api.get<{ items: { ip: string; name: string; source: string }[] }>("/hosts", undefined, { q }),
    enabled: q.trim().length >= 2,
    staleTime: 30_000,
  });
  const pick = (value: string) => {
    const hit = hits.data?.items.find((h) => `${h.name} (${h.ip})` === value);
    if (hit) { props.onPick(hit.ip); setQ(""); }
  };
  return (
    <label className="inline-edit">
      <span className="muted">Host</span>
      <input list="host-hits" value={q} placeholder="name…" style={{ width: 180 }}
        onChange={(e) => { setQ(e.target.value); pick(e.target.value); }} />
      <datalist id="host-hits">
        {hits.data?.items.map((h) => <option key={h.ip} value={`${h.name} (${h.ip})`}>{h.source}</option>)}
      </datalist>
    </label>
  );
}

/** Removable chips for the non-time filters. */
export function ActiveFilters(props: { filters: Filters; set: (k: FilterKey, v?: string) => void }) {
  const exporters = useExporters();
  const chips = activeFilters(props.filters);
  if (chips.length === 0) return null;
  const label = (k: FilterKey, v: string) =>
    k === "exporter" ? exporters.data?.items.find((e) => e.id === v)?.name ?? v : v;
  return (
    <div className="chips">
      {chips.map(([k, v]) => (
        <span key={k} className="chip">
          {FILTER_LABELS[k]}: {label(k, v)}
          <button title="Remove filter" onClick={() => props.set(k, undefined)}>×</button>
        </span>
      ))}
    </div>
  );
}
