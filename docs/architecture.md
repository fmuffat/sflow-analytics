# Architecture

## Collector (Phases 1–2)

```
ICX / sflow-gen ──UDP/6343──▶ UDP reader ──queue──▶ N decode workers ──▶ Fanout sink
                               (1 goroutine)  (bounded,   │                 ├─ ClickHouse sink ──▶ ClickHouse
                                              drop+count  │                 │   (batches, retry)     ▲
                                              when full)  │                 └─ log sink (optional)   │
                                                          ├──▶ exporter registry ──every 30 s──────────┤
                                                          └──▶ metrics                                │
                                                                   │              disk guard (1 min) ─┘
                                          HTTP :8081 (/healthz, /v1/status, /v1/exporters)
```

- **UDP reader**: copies each datagram and enqueues it; never blocks on
  decoding. When the queue is full the datagram is dropped and
  `datagrams_dropped` increases.
- **Workers** (`SFLOW_WORKERS`, default = CPUs): decode, normalize, update the
  exporter registry and counters, then call the sink. Panics are recovered.
- **Exporter registry**: in memory, keyed by (UDP source IP, agent IP,
  sub-agent ID). Tracks first/last seen, status (inactive after
  `EXPORTER_INACTIVE_TIMEOUT`), sampling rate, samples/s, lost datagrams,
  interfaces (speed and oper status from counter samples). Saved to ClickHouse
  every `INVENTORY_SAVE_INTERVAL` and at shutdown; restored at startup.
- **ClickHouse sink**: non-blocking writes into a bounded queue
  (`CLICKHOUSE_QUEUE_ROWS`); a single writer sends batches of
  `CLICKHOUSE_BATCH_SIZE` rows or every `CLICKHOUSE_FLUSH_INTERVAL`. Failed
  batches are retried with exponential backoff (1 s → 30 s) and kept up to
  `CLICKHOUSE_MAX_PENDING_ROWS`; beyond that the oldest are dropped. Batches
  the client cannot encode are dropped instead of blocking the queue. Every
  loss is counted (`db_rows_dropped`, `db_insert_failures`).
- **Startup**: the UDP listener starts immediately. In the background the
  collector waits for ClickHouse, applies migrations and retention, restores
  the inventory, measures the disk, then enables inserts.
- **Disk guard**: see [db/README.md](../db/README.md#retention).
- **Logging**: JSON via `log/slog`, `service` attribute on every line. No
  per-packet logging except the optional rate-limited flow log sink; errors are
  rate-limited and always counted.

## API (Phase 3)

FastAPI (`api/`), stateless, 2 uvicorn workers. Every traffic endpoint shares
one filter model (`app/filters.py`) that renders a WHERE clause with all user
values bound as ClickHouse query parameters. Service, protocol name and
exporter id are computed in SQL (`app/catalog.py`), so they can be filtered
and grouped on. Queries run on the raw `flow_records` table; 1-minute
aggregates will be added only if latency on 30 days of data requires it.
Live exporter status and storage figures come from the collector's internal
endpoint.

Multi-switch note: a packet crossing several sampled switches is sampled by
each of them, so totals across exporters can count it more than once. Filter
on one exporter (or one ingress interface) for exact per-device views.

## Docker notes

- Published UDP ports keep the exporter's source IP for traffic coming from
  the network (DNAT). Traffic sent to 127.0.0.1 goes through docker-proxy and
  shows the Docker gateway as source — the agent IP still identifies the exporter.
- The kernel caps the UDP receive buffer at `net.core.rmem_max`. The dev VM
  sets it to 32 MiB (`/etc/sysctl.d/99-sflow-collector.conf`); the appliance
  must do the same.

## Measured performance (dev VM: 4 vCPU / 8 GB)

Synthetic generator on the same host, 20 datagrams/s per exporter, 8 flow
samples per datagram. CPU is a percentage of one core.

Phase 2, with ClickHouse inserts (60 s per step):

| Exporters | Samples/s | Rows inserted | Dropped | Insert failures | Collector CPU / RSS | ClickHouse CPU / RSS |
|-----------|-----------|---------------|---------|-----------------|---------------------|----------------------|
| 100       | 16 000    | 100 %         | 0       | 0               | ~37 % / 110 MiB     | ~25 % / 314 MiB      |
| 500       | 80 000    | 100 %         | 0       | 0               | ~108 % / 146 MiB    | ~84 % / 453 MiB      |

Phase 1 (log sink only): up to 500 exporters / 80 000 samples/s without loss,
~88 % CPU, 14 MiB RSS.

## Storage sizing

Measured on synthetic traffic: **~24 bytes per flow record** on disk
(compressed). Real traffic compresses somewhat less; re-measure on customer
data. Required disk for flows:

```
samples/s × 86 400 × retention_days × ~25 bytes
```

| Sustained samples/s | Per day  | 30 days  | 90 days (default) |
|---------------------|----------|----------|-------------------|
| 100                 | 0.2 GB   | 6.5 GB   | 19 GB             |
| 1 000               | 2.2 GB   | 65 GB    | 194 GB            |
| 5 000               | 11 GB    | 324 GB   | 972 GB            |
| 10 000              | 22 GB    | 648 GB   | 1.9 TB            |

Interface counters add ~36 bytes per poll: with 20 s polling, ~155 kB per
interface and day (2 400 interfaces: ~0.4 GB/day, ~33 GB for 90 days). The
hourly history used by Trends is negligible (~0.5 MB per interface per year,
kept 3 years). Lab measurement (3 switches, ~20 000 samples/day): 1.7 MB/day.

With the 100 GB appliance disk and the 85 % budget, 90 days are guaranteed up
to roughly 400 samples/s (30 days: ~1 300 samples/s); a larger site needs a
larger disk (see docs/deployment.md "Growing the disk") or a shorter
`RETENTION_DAYS`; above that the disk guard shortens retention
automatically (visible as `estimated_retention_days` in `/v1/status`).
The samples/s rate depends on traffic and on the sampling rate configured on
the switches — see [sflow.md](sflow.md#sampling-rate).
