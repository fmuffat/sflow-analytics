-- Inventory enrichment from a controller (RUCKUS One or SmartZone), written by
-- the worker. Query with FINAL. Rows of a sync share `synced_at`; rows not seen
-- in the latest sync are stale (device removed from the controller).

CREATE TABLE IF NOT EXISTS {{DB}}.ctrl_switches
(
    source       LowCardinality(String),   -- ruckusone | smartzone
    serial       String,
    name         String,
    model        String,
    firmware     String,
    ip           String,
    mac          String,
    venue        String,
    status       String,
    synced_at    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(synced_at)
ORDER BY (source, serial);

CREATE TABLE IF NOT EXISTS {{DB}}.ctrl_ports
(
    source          LowCardinality(String),
    switch_serial   String,
    port_id         String,                -- e.g. 1/1/8
    ifindex         UInt32,                -- derived from port_id (ICX numbering), 0 if unknown
    name            String,                -- port name/description configured on the switch
    status          String,
    admin_status    String,
    speed           String,
    vlan_untagged   String,
    vlans           String,
    lag_id          String,
    lag_name        String,
    lldp_name       String,                -- LLDP neighbor system name
    lldp_mac        String,
    lldp_port_mac   String,
    synced_at       DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(synced_at)
ORDER BY (source, switch_serial, port_id);

CREATE TABLE IF NOT EXISTS {{DB}}.ctrl_clients
(
    source          LowCardinality(String),
    mac             String,
    ip              String,
    ipv6            String,
    name            String,                -- best name known by the controller (alias/hostname)
    device_type     String,
    vendor          String,
    switch_serial   String,
    port_id         String,
    vlan            String,
    synced_at       DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(synced_at)
ORDER BY (source, mac);
