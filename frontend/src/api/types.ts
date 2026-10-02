// Response shapes of the sFlow Analytics API (/api/v1). Volumes are estimates.

export interface Totals {
  bytes: number;
  packets: number;
  samples: number;
}

export interface Envelope<T> {
  query: Record<string, unknown>;
  estimated: true;
  total?: Totals;
  items: T[];
}

export interface Volume extends Totals {
  percent: number;
}

export interface IpItem extends Volume {
  ip: string;
  name?: string | null;
  vendor?: string | null;
  mac?: string | null;
}

export interface ServiceItem extends Volume {
  service: string;
}

export interface ProtocolItem extends Volume {
  protocol: string;
  ip_protocol: number | null;
}

export interface VlanItem extends Volume {
  vlan: number | null;
}

export interface ExporterRef {
  id: string;
  name: string;
}

export interface ConversationItem extends Volume {
  src_ip: string;
  dst_ip: string;
  src_name?: string | null;
  dst_name?: string | null;
  src_vendor?: string | null;
  dst_vendor?: string | null;
  protocol: string;
  service: string;
  src_port?: number | null;
  dst_port?: number | null;
  first_seen: string;
  last_seen: string;
  exporters: ExporterRef[];
  input_ifindexes: number[];
  output_ifindexes: number[];
  vlans: number[];
}

export interface InterfaceTrafficItem extends Volume {
  exporter_id: string;
  exporter_name: string;
  ifindex: number;
  interface_id: string;
  interface_name: string | null;
  label: string;
  speed_bps: number | null;
  in_bytes: number;
  out_bytes: number;
}

export interface ExporterTrafficItem extends Volume {
  exporter_id: string;
  name: string;
  agent_ip: string | null;
}

export interface TimePoint {
  t: string;
  bytes: number;
  packets: number;
  samples: number;
  bps: number;
  pps: number;
}

export interface Timeseries extends Envelope<TimePoint> {
  step_seconds: number;
}

export interface Summary {
  bytes: number;
  packets: number;
  samples: number;
  bps: number;
  pps: number;
  conversations: number;
  sources: number;
  destinations: number;
  exporters: number;
  last_sample: string | null;
}

export interface FlowItem {
  timestamp: string;
  exporter_id: string;
  exporter_name: string;
  agent_ip: string;
  input_ifindex: number | null;
  output_ifindex: number | null;
  src_mac: string | null;
  dst_mac: string | null;
  ether_type: number | null;
  vlan: number | null;
  ip_version: number | null;
  src_ip: string | null;
  dst_ip: string | null;
  src_name?: string | null;
  dst_name?: string | null;
  src_mac_vendor?: string | null;
  dst_mac_vendor?: string | null;
  ip_protocol: number | null;
  protocol: string;
  src_port: number | null;
  dst_port: number | null;
  tcp_flags: number | null;
  service: string;
  sampled_packet_size: number;
  sampling_rate: number;
  estimated_bytes: number;
  estimated_packets: number;
}

export interface ControllerPort {
  port_id: string;
  name: string | null;
  status: string | null;
  admin_status: string | null;
  speed: string | null;
  vlan_untagged: string | null;
  lag_name: string | null;
  lldp_neighbor: string | null;
  lldp_mac: string | null;
  lldp_port_mac: string | null;
  mapping_uncertain: boolean;
}

export interface ControllerSwitch {
  source: string;
  serial: string;
  name: string;
  model: string;
  firmware: string;
  mac: string;
  venue: string;
  status: string;
  synced_at: string;
}

export interface Interface {
  id: string;
  controller_port?: ControllerPort | null;
  exporter_id: string;
  ifindex: number;
  first_seen: string;
  last_seen: string;
  speed_bps: number | null;
  oper_up: boolean | null;
  name: string | null;
  description: string | null;
  label: string;
}

export interface Exporter {
  id: string;
  exporter_ip: string;
  agent_ip: string;
  agent_sub_id: number;
  first_seen: string;
  last_seen: string;
  sample_rate: number;
  display_name: string | null;
  notes: string | null;
  name: string;
  status: "active" | "inactive";
  status_source: "collector" | "database";
  samples_per_second?: number | null;
  datagrams?: number | null;
  lost_datagrams?: number | null;
  errors?: number | null;
  interfaces?: Interface[];
  controller?: ControllerSwitch | null;
}

export interface StorageStatus {
  enabled?: boolean;
  ready?: boolean;
  available: boolean;
  disk_total_bytes: number;
  disk_free_bytes: number;
  disk_used_percent: number;
  disk_max_usage_percent: number;
  database_bytes: number;
  flow_records_bytes: number;
  flow_records_rows: number;
  oldest_flow_record: string | null;
  retention_days: number;
  disk_capacity_days: number | null;
  estimated_retention_days: number | null;
  partitions_dropped_by_disk_guard: number;
  inserts_suspended: boolean;
}

export interface CollectorCounters {
  started_at: string;
  uptime_seconds: number;
  last_packet_at: string | null;
  datagrams_received: number;
  datagrams_per_second: number;
  bytes_received: number;
  datagrams_dropped: number;
  malformed_datagrams: number;
  unsupported_version_datagrams: number;
  truncated_datagrams: number;
  samples_received: number;
  samples_per_second: number;
  flow_samples: number;
  counter_samples: number;
  unsupported_samples: number;
  unsupported_records: number;
  sample_errors: number;
  db_insert_failures: number;
  db_rows_inserted: number;
  db_rows_dropped: number;
  db_rows_pending: number;
  exporters_total: number;
  exporters_active: number;
}

export interface CollectorStatus {
  status: string;
  version: string;
  listen_address: string;
  counters: CollectorCounters;
  storage: StorageStatus | null;
}

export interface SystemStatus {
  app_name: string;
  version: string;
  timezone: string;
  retention_days: number;
  services: Record<string, string>;
  collector_listen_address: string | null;
  storage: StorageStatus | null;
}

export interface UtilizationItem {
  exporter_id: string;
  exporter_name: string;
  ifindex: number;
  interface_id: string;
  label: string;
  speed_bps: number | null;
  polls: number;
  in_avg_bps: number | null;
  in_max_bps: number | null;
  out_avg_bps: number | null;
  out_max_bps: number | null;
  in_avg_pct: number | null;
  in_p95_pct: number | null;
  in_max_pct: number | null;
  out_avg_pct: number | null;
  out_p95_pct: number | null;
  out_max_pct: number | null;
  in_discards: number;
  out_discards: number;
  in_errors: number;
  out_errors: number;
}

export interface UtilizationPoint {
  t: string;
  in_avg_bps: number | null;
  in_max_bps: number | null;
  out_avg_bps: number | null;
  out_max_bps: number | null;
  in_avg_pct: number | null;
  in_max_pct: number | null;
  out_avg_pct: number | null;
  out_max_pct: number | null;
  in_discards: number | null;
  out_discards: number | null;
  in_errors: number | null;
  out_errors: number | null;
  in_bcast_avg_pps?: number | null;
  in_bcast_max_pps?: number | null;
  out_bcast_avg_pps?: number | null;
  out_bcast_max_pps?: number | null;
  in_mcast_avg_pps?: number | null;
  in_mcast_max_pps?: number | null;
  out_mcast_avg_pps?: number | null;
  out_mcast_max_pps?: number | null;
}

export interface UtilizationSeries {
  exporter_id: string;
  ifindex: number;
  speed_bps: number | null;
  step_seconds: number;
  items: UtilizationPoint[];
}

export interface GroupedTimeseries {
  group_by: string;
  step_seconds: number;
  series: { name: string; bytes: number }[];
  items: { t: string; bytes: Record<string, number>; bps: Record<string, number> }[];
}

export interface BidirConversationItem extends Volume {
  host_a: string;
  host_b: string;
  host_a_name?: string | null;
  host_b_name?: string | null;
  host_a_vendor?: string | null;
  host_b_vendor?: string | null;
  protocol: string;
  service: string;
  a_to_b_bytes: number;
  b_to_a_bytes: number;
  first_seen: string;
  last_seen: string;
  vlans: number[];
}
