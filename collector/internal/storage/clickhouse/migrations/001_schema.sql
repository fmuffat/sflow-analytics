-- sFlow analytics schema. Every statement must be idempotent: migrations run
-- at each collector start. {{DB}} and {{RETENTION_DAYS}} are substituted.
-- IP addresses are stored as IPv6; IPv4 values are IPv4-mapped (::ffff:a.b.c.d).

CREATE DATABASE IF NOT EXISTS {{DB}};

-- One row per sFlow flow sample. Volumes are ESTIMATES (size x sampling rate).
CREATE TABLE IF NOT EXISTS {{DB}}.flow_records
(
    timestamp            DateTime64(3, 'UTC') CODEC(DoubleDelta, ZSTD(1)),
    exporter_ip          IPv6,
    agent_ip             IPv6,
    agent_sub_id         UInt32,
    sample_sequence      UInt32 CODEC(Delta, ZSTD(1)),
    source_id_type       UInt8,
    source_id_index      UInt32,
    input_ifindex        Nullable(UInt32),
    output_ifindex       Nullable(UInt32),
    src_mac              LowCardinality(Nullable(String)),
    dst_mac              LowCardinality(Nullable(String)),
    ether_type           Nullable(UInt16),
    vlan                 Nullable(UInt16),
    ip_version           Nullable(UInt8),
    src_ip               Nullable(IPv6),
    dst_ip               Nullable(IPv6),
    ip_protocol          Nullable(UInt8),
    src_port             Nullable(UInt16),
    dst_port             Nullable(UInt16),
    tcp_flags            Nullable(UInt8),
    sampled_packet_size  UInt32,
    sampling_rate        UInt32,
    estimated_bytes      UInt64,
    estimated_packets    UInt64
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (agent_ip, timestamp)
TTL toDateTime(timestamp) + INTERVAL {{RETENTION_DAYS}} DAY DELETE
SETTINGS ttl_only_drop_parts = 1;

-- Generic interface counters (sFlow counter samples).
CREATE TABLE IF NOT EXISTS {{DB}}.interface_counters
(
    timestamp     DateTime64(3, 'UTC') CODEC(DoubleDelta, ZSTD(1)),
    exporter_ip   IPv6,
    agent_ip      IPv6,
    agent_sub_id  UInt32,
    ifindex       UInt32,
    if_type       UInt32,
    speed_bps     UInt64,
    direction     UInt8,
    admin_up      Bool,
    oper_up       Bool,
    in_octets     UInt64,
    in_ucast      UInt32,
    in_multicast  UInt32,
    in_broadcast  UInt32,
    in_discards   UInt32,
    in_errors     UInt32,
    out_octets    UInt64,
    out_ucast     UInt32,
    out_multicast UInt32,
    out_broadcast UInt32,
    out_discards  UInt32,
    out_errors    UInt32
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (agent_ip, ifindex, timestamp)
TTL toDateTime(timestamp) + INTERVAL {{RETENTION_DAYS}} DAY DELETE
SETTINGS ttl_only_drop_parts = 1;

-- Discovered exporters, written periodically by the collector. Query with FINAL.
CREATE TABLE IF NOT EXISTS {{DB}}.exporters
(
    id            String,
    exporter_ip   IPv6,
    agent_ip      IPv6,
    agent_sub_id  UInt32,
    first_seen    DateTime64(3, 'UTC'),
    last_seen     DateTime64(3, 'UTC'),
    sample_rate   UInt32,
    updated_at    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY id;

-- Interfaces seen per exporter (ifIndex), written by the collector.
CREATE TABLE IF NOT EXISTS {{DB}}.interfaces
(
    exporter_id  String,
    ifindex      UInt32,
    first_seen   DateTime64(3, 'UTC'),
    last_seen    DateTime64(3, 'UTC'),
    speed_bps    UInt64,
    oper_up      Nullable(Bool),
    updated_at   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (exporter_id, ifindex);

-- User-defined names, written by the API (never by the collector), so they
-- survive restarts and upgrades.
CREATE TABLE IF NOT EXISTS {{DB}}.exporter_settings
(
    id            String,
    display_name  String,
    notes         String,
    updated_at    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY id;

CREATE TABLE IF NOT EXISTS {{DB}}.interface_settings
(
    exporter_id  String,
    ifindex      UInt32,
    name         String,
    description  String,
    updated_at   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (exporter_id, ifindex);

-- Retention changes (RETENTION_DAYS) are applied by the collector with
-- ALTER ... MODIFY TTL only when the configured value differs.
