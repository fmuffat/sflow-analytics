# Architecture

```
ICX switches ──sFlow v5 / UDP 6343──▶ collector (Go) ──▶ ClickHouse ◀── API (FastAPI) ◀── nginx HTTPS ◀── web UI (React)
                                                             ▲              │
RUCKUS One / SmartZone (HTTPS) ─┐                             │              │
switches (SNMP, read-only) ─────┼──▶ worker (same image as the API) ─────────┘ ──▶ SQLite configuration store
GitHub releases (version check)─┘        history, alerts, reports, DNS, sync, SNMP     (users, settings, secrets, jobs)
```

Services (Docker Compose): `collector`, `clickhouse`, `api`, `worker`, `frontend` (static files)
and `nginx` (TLS, the only exposed web port). On an installed appliance a host-side `updater`
(systemd path unit) applies in-app updates.

## Collector

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

## API

FastAPI (`api/`), stateless, 2 uvicorn workers. Every traffic endpoint shares
one filter model (`app/filters.py`) that renders a WHERE clause with all user
values bound as ClickHouse query parameters. Service, protocol name and
exporter id are computed in SQL (`app/catalog.py`), so they can be filtered
and grouped on. Queries run on the raw `flow_records` table; utilization comes
from `interface_counters` (deltas between 20 s polls) and the long-term
comparisons from `interface_hourly`. Live exporter status and storage figures
come from the collector's internal endpoint.

Multi-switch note: a packet crossing several sampled switches is sampled by
each of them, so totals across exporters can count it more than once. Filter
on one exporter (or one ingress interface) for exact per-device views.

### Configuration store

Small transactional data live in SQLite (`sflow.db` on the `app-config` volume, schema
migrations in `app/store.py`): users and sessions, sign-in log, settings, background jobs,
alert rules and alerts, report definitions and generated reports (files in
`app-config/reports/`). Secrets (controller, SMTP, webhook, SNMP) are encrypted with Fernet;
the key is `secret.key` on the same volume, so back both up together. Names used inside
analytics queries (aliases, groups, controller and SNMP inventory, interface names) are in
ClickHouse so that they can be joined and filtered on.

### Worker

`python -m app.worker` (same image as the API) runs the jobs below; each run is recorded in
the `jobs` table and shown in Administration → System. A failing job never stops the others.

| Job | Every | Purpose |
|-----|-------|---------|
| `purge-expired-sessions` | 10 min | expired sessions, sign-in log older than 180 days |
| `controller-sync` | 1 min (sync at the configured interval, 15 min) | RUCKUS One or SmartZone: switches, ports, LLDP neighbors, clients |
| `snmp-poll` | 1 min (poll at the configured interval, 15 min) | SNMP v2c/v3 for switches no controller knows: sysName, ifName, ifAlias, ENTITY serial, LLDP |
| `reverse-dns` | 1 min | PTR lookups of new IPs (rate-limited, cached 24 h) |
| `alerts` | 1 min | evaluate rules, open/close alerts, send notifications |
| `history-rollup` | 5 min | hourly interface history (`interface_hourly`, kept 3 years) |
| `reports` | 5 min | scheduled weekly/monthly reports, e-mail, 90-day purge |
| `update-check` | 1 h (GitHub at most once a day) | latest published release |

### Enrichment priority

Controller and SNMP data share the tables `ctrl_switches` / `ctrl_ports` (column `source`).
The selected controller wins over SNMP; SNMP polls only the switches the controller does not
know. Names shown everywhere: switch = manual > controller > SNMP `sysName` > agent IP; port =
manual > controller > SNMP `ifName` > RUCKUS ICX numbering (per switch option) > `ifIndex N`,
followed by the description (manual > controller / SNMP `ifAlias`).

### Reports

`app/reports/`: `data.py` collects one period and the previous one (and the same month a year
before for monthly reports, from `interface_hourly`), `pdf.py` renders `template.html` with
inline SVG charts through WeasyPrint, `xlsx.py` writes one sheet per section with openpyxl,
`service.py` handles definitions, schedule, e-mail and history.

### Updates

The API never touches the host. "Update now" writes the requested version to `updates/request`;
the host-side `packaging/updater.sh` (systemd path unit `sflow-update-<project>.path`) downloads
the release package, checks its SHA-256, refuses downgrades, runs `backup.sh` then the new
`install.sh`, and reports progress in `updates/status.json`.

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

With ClickHouse inserts (60 s per step):

| Exporters | Samples/s | Rows inserted | Dropped | Insert failures | Collector CPU / RSS | ClickHouse CPU / RSS |
|-----------|-----------|---------------|---------|-----------------|---------------------|----------------------|
| 100       | 16 000    | 100 %         | 0       | 0               | ~37 % / 110 MiB     | ~25 % / 314 MiB      |
| 500       | 80 000    | 100 %         | 0       | 0               | ~108 % / 146 MiB    | ~84 % / 453 MiB      |

Log sink only (no database): up to 500 exporters / 80 000 samples/s without loss,
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
