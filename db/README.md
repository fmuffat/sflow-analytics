# Database

ClickHouse 25.8 (LTS), service `clickhouse` in docker-compose, data in the
`clickhouse-data` volume.

- **Schema**: `collector/internal/storage/clickhouse/migrations/*.sql`, embedded
  in the collector and applied at every start (idempotent `CREATE ... IF NOT EXISTS`).
  New migrations are added as new numbered files.
- **Server settings**: `clickhouse/config.d/sflow.xml` (memory limits for a small
  VM, bounded system logs, IPv4 listen).

## Tables

| Table                | Engine              | Written by | Content                                  |
|----------------------|---------------------|------------|------------------------------------------|
| `flow_records`       | MergeTree, daily partitions, TTL | collector | one row per flow sample (estimates) |
| `interface_counters` | MergeTree, daily partitions, TTL | collector | sFlow generic interface counters     |
| `exporters`          | ReplacingMergeTree  | collector  | discovered exporters (query with `FINAL`) |
| `interfaces`         | ReplacingMergeTree  | collector  | ifIndexes seen per exporter, speed, status |
| `exporter_settings`  | ReplacingMergeTree  | API        | user-defined display name, notes         |
| `interface_settings` | ReplacingMergeTree  | API        | user-defined interface name, description |

IP addresses are `IPv6`; IPv4 values are IPv4-mapped. Display them with
`replaceRegexpOne(IPv6NumToString(ip), '^::ffff:', '')`, filter with
`toIPv6('10.1.2.3')`.

## Retention

`RETENTION_DAYS` (default 90) sets the TTL of `flow_records` and
`interface_counters`. The collector changes the TTL at startup when the value
differs, without rewriting existing data; expired days are removed whole
(`ttl_only_drop_parts`).

Independently, the collector's disk guard (every minute) drops the oldest day
early when disk usage exceeds `DISK_MAX_USAGE_PERCENT` (default 85 %), and
suspends inserts above that value + 10 points (max 98 %).

## Ad-hoc queries (dev)

```bash
set -a; . ./.env; set +a
docker compose exec clickhouse clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -d sflow
```
