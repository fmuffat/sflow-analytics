-- Endpoint naming: manual aliases (written by the API) and reverse DNS cache
-- (written by the worker). Query with FINAL.

CREATE TABLE IF NOT EXISTS {{DB}}.host_aliases
(
    kind        LowCardinality(String),   -- ip | mac | cidr
    key         String,                   -- normalized IP, MAC (aa:bb:..) or CIDR
    name        String,
    notes       String,
    deleted     UInt8,                    -- 1 = alias removed (latest row wins)
    updated_at  DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (kind, key);

CREATE TABLE IF NOT EXISTS {{DB}}.dns_cache
(
    ip           String,                  -- display form (IPv4 dotted, IPv6 compressed)
    name         String,                  -- PTR name without trailing dot, '' when none
    status       LowCardinality(String),  -- ok | nxdomain | error
    resolved_at  DateTime64(3, 'UTC'),
    expires_at   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(resolved_at)
ORDER BY ip
TTL toDateTime(expires_at) + INTERVAL 30 DAY DELETE;
