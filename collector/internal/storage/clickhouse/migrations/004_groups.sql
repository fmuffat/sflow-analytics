-- Named groups (written by the API). Query with FINAL.
--   kind = ip: members are IPs or CIDRs ("Servers", "Guest Wi-Fi", "Site Lyon")
--   kind = if: members are interface ids "<exporter_id>/<ifindex>" ("Uplinks", "Access ports")
CREATE TABLE IF NOT EXISTS {{DB}}.host_groups
(
    kind        LowCardinality(String),
    name        String,
    members     Array(String),
    notes       String,
    deleted     UInt8,
    updated_at  DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (kind, name);
