-- Long-term interface history: one row per interface and hour, computed from
-- interface_counters by the worker job "history-rollup". Raw counters and flows
-- expire after RETENTION_DAYS; this table is kept 3 years for day / week /
-- month / year comparisons (about 50 bytes per interface and hour compressed,
-- i.e. ~0.5 MB per interface per year). Not touched by the disk guard.
-- The current hour is recomputed until complete: ReplacingMergeTree keeps the
-- latest version (read with FINAL).
CREATE TABLE IF NOT EXISTS {{DB}}.interface_hourly
(
    hour            DateTime('UTC'),
    exporter_id     LowCardinality(String),
    ifindex         UInt32,
    speed_bps       UInt64,
    polls           UInt32,
    covered_seconds UInt32,
    in_bytes        UInt64,
    out_bytes       UInt64,
    in_max_bps      Float64,
    out_max_bps     Float64,
    in_p95_bps      Float64,
    out_p95_bps     Float64,
    in_discards     UInt64,
    out_discards    UInt64,
    in_errors       UInt64,
    out_errors      UInt64,
    in_broadcast    UInt64,
    out_broadcast   UInt64,
    in_multicast    UInt64,
    out_multicast   UInt64,
    updated_at      DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(hour)
ORDER BY (exporter_id, ifindex, hour)
TTL hour + INTERVAL 1096 DAY DELETE;
