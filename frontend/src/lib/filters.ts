// Traffic filters. Keys are exactly the API query parameters, so the URL of
// a page is also its API query: views can be bookmarked and shared.

export const FILTER_KEYS = [
  "range", "from", "to", "exporter", "src_ip", "dst_ip", "ip", "protocol",
  "src_port", "dst_port", "port", "service", "vlan", "input_ifindex", "output_ifindex", "ifindex",
  "src_group", "dst_group", "group", "if_group",
] as const;

export type FilterKey = (typeof FILTER_KEYS)[number];
export type Filters = Partial<Record<FilterKey, string>>;

export const RANGES = ["5m", "15m", "1h", "6h", "24h", "7d", "30d", "90d"] as const;
export const DEFAULT_RANGE = "1h";

export const FILTER_LABELS: Record<FilterKey, string> = {
  range: "Range", from: "From", to: "To", exporter: "Exporter", src_ip: "Source IP", dst_ip: "Destination IP",
  ip: "IP (either)", protocol: "Protocol", src_port: "Source port", dst_port: "Destination port",
  port: "Port (either)", service: "Service", vlan: "VLAN", input_ifindex: "Ingress ifIndex",
  output_ifindex: "Egress ifIndex", ifindex: "ifIndex (either)",
  src_group: "Source group", dst_group: "Destination group", group: "Group (either)", if_group: "Interface group",
};

/** Reads filters from URL search params, ignoring unknown and empty keys. */
export function fromSearchParams(sp: URLSearchParams): Filters {
  const f: Filters = {};
  for (const k of FILTER_KEYS) {
    const v = sp.get(k)?.trim();
    if (v) f[k] = v;
  }
  if (!f.from && !f.range) f.range = DEFAULT_RANGE;
  return f;
}

/** Serializes filters in a stable key order (used for URLs and API calls). */
export function toQuery(f: Record<string, string | undefined>): string {
  const sp = new URLSearchParams();
  const keys = Object.keys(f).sort((a, b) => order(a) - order(b) || a.localeCompare(b));
  for (const k of keys) {
    const v = f[k];
    if (v !== undefined && v !== "") sp.set(k, v);
  }
  return sp.toString();
}

function order(k: string): number {
  const i = (FILTER_KEYS as readonly string[]).indexOf(k);
  return i === -1 ? 999 : i;
}

/** Returns a copy with `key` set (or removed when value is empty). Choosing a
 * relative range clears an absolute window and vice versa. */
export function withFilter(f: Filters, key: FilterKey, value: string | undefined): Filters {
  const next: Filters = { ...f };
  if (value === undefined || value === "") delete next[key];
  else next[key] = value;
  if (key === "range" && value) {
    delete next.from;
    delete next.to;
  }
  if ((key === "from" || key === "to") && value) delete next.range;
  return next;
}

/** Only the time part of a filter set (to carry the window across pages). */
export function timeOnly(f: Filters): Filters {
  const t: Filters = {};
  for (const k of ["range", "from", "to"] as const) if (f[k]) t[k] = f[k];
  return t;
}

/** True when the window is relative to "now" (auto-refresh makes sense). */
export const isLive = (f: Filters) => !f.from && !f.to;

/** Filters other than time, for chips display. */
export function activeFilters(f: Filters): [FilterKey, string][] {
  return (Object.entries(f) as [FilterKey, string][]).filter(([k]) => !["range", "from", "to"].includes(k));
}
