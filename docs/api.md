# API

REST API (FastAPI) on port 8000, base path **`/api/v1`**. Interactive
documentation: `/api/v1/docs` (OpenAPI: `/api/v1/openapi.json`).
In development it is published on `127.0.0.1:8000`; nginx fronts it from Phase 4.
Every route except `/health` requires a session: `POST /api/v1/auth/login`
(`{"username", "password"}`) sets an HttpOnly cookie; `POST /auth/logout`,
`GET /auth/me`, `POST /auth/password` (`{"current_password", "new_password"}`).
Mutating requests must send `X-Requested-With: sflow`. Scripts can use a cookie jar:

```bash
curl -sk -c jar -H 'Content-Type: application/json' -d '{"username":"admin","password":"..."}' https://host/api/v1/auth/login
curl -sk -b jar "https://host/api/v1/traffic/top-sources?range=24h"
```

Administration: `GET /admin/config`, `GET /admin/jobs`, `GET /admin/diagnostics` (JSON download).
Users (administrators): `GET|POST /admin/users` (create: random password returned once),
`PUT /admin/users/{name}/role`, `POST /admin/users/{name}/reset-password`, `DELETE /admin/users/{name}`;
sign-in log `GET /admin/logins?failed_only=`.

Roles: `admin` and `viewer`. A viewer gets 403 on every non-GET request (except its own
`/auth/password` and `/auth/logout`) and on every `/admin/*` path.

Enrichment (controller): `GET /admin/enrichment` (settings, never the secret; last sync),
`PUT /admin/enrichment` (`{"source": "ruckusone", "interval_minutes": 15, "ruckusone":
{"region": "eu", "tenant_id": "...", "client_id": "...", "client_secret": "..."}}` — an empty
secret keeps the stored one), `POST /admin/enrichment/test`, `POST /admin/enrichment/sync`.
Exporters then carry `controller` (serial, name, model, firmware, venue, status) and
interfaces `controller_port` (port_id, name, status, VLAN, LAG, LLDP neighbor, `mapping_uncertain`).

All traffic volumes are **estimates** (sampled packet size × sampling rate);
every traffic response carries `"estimated": true`.

## Filters

Every `/traffic/*` endpoint accepts the same filters. Values of one parameter
are comma-separated (OR); different parameters are combined (AND).

| Parameter        | Example                        | Meaning                                         |
|------------------|--------------------------------|-------------------------------------------------|
| `from`, `to`     | `2026-09-29T08:00:00Z`, `1790000000` | ISO 8601 (naive = UTC) or Unix seconds; `to` defaults to now |
| `range`          | `5m` `15m` `1h` `6h` `24h` `7d` `30d` | window ending at `to` when `from` is omitted (default `1h`) |
| `exporter`       | `10.0.0.1`, `192.0.2.2/192.0.2.2/1` | agent/exporter IP or exporter id        |
| `src_ip`, `dst_ip` | `10.1.1.10`, `10.1.0.0/16`, `2001:db8::/32` | IP or CIDR                          |
| `ip`             | `10.1.1.10`                    | source **or** destination                       |
| `protocol`       | `tcp`, `udp,icmp`, `47`        | name or number                                  |
| `src_port`, `dst_port`, `port` | `443`            | `port` = source or destination                  |
| `service`        | `HTTPS`, `DNS`, `TCP/8443`, `Unknown`, `Non-IP` | see *Services*             |
| `vlan`           | `10,20`                        | VLAN ID                                         |
| `input_ifindex`, `output_ifindex`, `ifindex` | `48` | ingress, egress, either                         |

Invalid values return **422** with a readable `detail`. The time range is
limited to the retention period.

## Endpoints

### Health and status
| Method | Path                | Description |
|--------|---------------------|-------------|
| GET    | `/health`           | `{"status":"ok","clickhouse":true}`; 503 when ClickHouse is unreachable |
| GET    | `/collector/status` | collector counters and storage (proxied); 502 when unreachable |
| GET    | `/system/status`    | app name, version, retention, service states, storage (disk, DB size, oldest record, estimated retention) |

### Exporters and interfaces
| Method | Path                               | Description |
|--------|------------------------------------|-------------|
| GET    | `/exporters`                       | discovered exporters, with live status from the collector (`status_source`) |
| GET    | `/exporters/{id}`                  | one exporter and its interfaces (`id` = `exporter_ip/agent_ip/sub_id`) |
| PATCH  | `/exporters/{id}`                  | `{"display_name": "ICX8200-Core-01", "notes": "..."}`; the discovered IPs are kept |
| GET    | `/interfaces[?exporter={id}]`      | interfaces (ifIndex, speed, oper status, name) |
| GET    | `/interfaces/{exporter_id}/{ifindex}` | one interface |
| PATCH  | `/interfaces/{exporter_id}/{ifindex}` | `{"name": "1/1/48", "description": "uplink"}` (manual ifIndex mapping) |

Names survive restarts and upgrades (stored in `exporter_settings` /
`interface_settings`). An empty string clears a name.

### Traffic
| Path                         | Items                                                        |
|------------------------------|--------------------------------------------------------------|
| `/traffic/timeseries`        | `t`, `bytes`, `packets`, `samples`, `bps`, `pps`; `step_seconds` automatic (~120 points) or `step=` |
| `/traffic/top-sources`       | `ip`, `bytes`, `packets`, `samples`, `percent`               |
| `/traffic/top-destinations`  | same                                                         |
| `/traffic/top-conversations` | `src_ip`, `dst_ip`, `protocol`, `service`, `first_seen`, `last_seen`, `exporters`, `input_ifindexes`, `output_ifindexes`, `vlans`, volumes; `by=pair` (default) or `by=5tuple` (adds ports) |
| `/traffic/top-services`      | `service`, volumes                                           |
| `/traffic/top-protocols`     | `protocol`, `ip_protocol`, volumes                           |
| `/traffic/top-vlans`         | `vlan` (null = untagged/unknown), volumes                    |
| `/traffic/top-exporters`     | `exporter_id`, `name`, `agent_ip`, volumes                   |
| `/traffic/top-interfaces`    | `exporter_id`, `exporter_name`, `ifindex`, `label`, `interface_name`, `speed_bps`, `in_bytes` (ingress), `out_bytes` (egress), volumes |
| `/traffic/flows`             | individual samples, newest first; `limit` (≤ 1000), `offset` |

Top-N endpoints take `limit` (default 10, max 1000) and return
`total` (bytes, packets, samples of the whole filtered window) so that
`percent` is relative to all matching traffic.

Response shape:

```json
{
  "query": {"from": "...", "to": "...", "vlans": [10]},
  "estimated": true,
  "total": {"bytes": 5551747072, "packets": 7176192, "samples": 1752},
  "items": [{"ip": "10.1.1.10", "bytes": 986890240, "packets": 240942, "samples": 186, "percent": 17.78}]
}
```

### History and period comparisons
Long-term interface history: the worker job `history-rollup` (every 5 min) summarizes interface
counters per interface and hour into `interface_hourly`, kept **3 years** (raw data: `RETENTION_DAYS`).
Periods are calendar periods in the viewer's time zone (`tz`, IANA name; weeks start on Monday).
While the current period is running, deltas compare the same elapsed time of the reference period.

| Method | Path                   | Description |
|--------|------------------------|-------------|
| GET    | `/history/compare`     | `period=day\|week\|month\|year`, `at` (local date in the period, default today), `tz`, `compare=previous\|week\|year` (`week`: same day a week before, day only; `year`: 52 weeks before for days/weeks, same month/previous year otherwise), `interface` (id(s), summed) or `if_group`. Aligned buckets (hours, hours, days, months) with volumes, average and peak rate, peak %, discards, errors; totals and deltas |
| GET    | `/history/interfaces`  | same period parameters + `if_group`, `exporter`, `limit`: every interface with current and reference volume, change %, average, peak and peak change |
| GET    | `/history/status`      | history coverage (first and last hour, rows) |

### Alerts
Rules are evaluated every minute by the worker (job `alerts`). An alert is one rule × one object
(interface, switch or traffic target): `open` → `acknowledged` (optional) → `closed` when the
condition ends (threshold rules close below 90 % of the threshold). Notifications are sent when an
alert opens and, optionally, when it closes. Closed alerts are kept 180 days.

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/alerts?state=active\|closed\|all` | alerts with rule name, object, link, message, value, notification results, and `counts` |
| GET    | `/alerts/counts` | open / acknowledged / open per severity (navigation badge) |
| POST   | `/alerts/{id}/ack`, `/alerts/{id}/close` | acknowledge, close by hand (reopens if still true) — administrators |
| GET    | `/alerts/rules` | rules, default parameters per kind |
| POST, PUT, DELETE | `/alerts/rules[/{id}]` | manage rules — administrators |
| POST   | `/alerts/evaluate` | evaluate now — administrators |
| GET, PUT | `/admin/notifications` | e-mail (SMTP), webhook (Teams Workflows adaptive card, Slack, generic JSON), syslog UDP; public URL for links; secrets write-only |
| POST   | `/admin/notifications/test/{email\|webhook\|syslog}` | send a test message |

Rule kinds: `utilization` (average % ≥ threshold over N min, in/out/either), `discards`
(discards, errors or both ≥ N in N min), `broadcast` (average broadcast pps ≥ threshold),
`exporter_silent` (no sFlow for N min, switches seen in the last 7 days), `traffic` (estimated
traffic of a host/subnet, IP group or interface group ≥ threshold). Scope: one switch and/or one
interface group. Four default rules are created at first start (utilization 80 %, discards,
broadcast storm, switch silent), without notification channels.

## Services

Port-based naming, not DPI: the destination port is looked up first, then the
source port (response direction), for TCP/UDP. Other IP protocols are named
after the protocol (ICMP, GRE, ESP...). Unmapped TCP/UDP flows whose lowest
port is below 49152 are labelled `<PROTO>/<port>` (e.g. `TCP/8443`); flows
between two ephemeral ports are `Unknown`; non-IP frames are `Non-IP`.
The table lives in `api/app/catalog.py` (to become editable).

## Collector status endpoint (internal)

The collector serves `/healthz`, `/v1/status`, `/v1/exporters` on port 8081
inside the Docker network (127.0.0.1:8081 on the host in development). The
API reads it for live exporter status and storage statistics; see the field
list in `/api/v1/docs` → `/collector/status`.
