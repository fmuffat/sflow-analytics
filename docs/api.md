# API

REST API (FastAPI), base path **`/api/v1`**, served behind nginx on HTTPS (port 443). Interactive
documentation: `/api/v1/docs` (OpenAPI: `/api/v1/openapi.json`); it lists every parameter and
response field. In the development stack the API is also published on `127.0.0.1:8000`.

## Authentication and roles

Every route except `/health` requires a session: `POST /auth/login` (`{"username", "password"}`)
sets an HttpOnly cookie; `POST /auth/logout`, `GET /auth/me`, `POST /auth/password`
(`{"current_password", "new_password"}`). Requests other than GET must send
`X-Requested-With: sflow`. Scripts can use a cookie jar:

```bash
curl -sk -c jar -H 'Content-Type: application/json' -d '{"username":"admin","password":"..."}' https://host/api/v1/auth/login
curl -sk -b jar "https://host/api/v1/traffic/top-sources?range=24h"
```

Roles: `admin` and `viewer`. A viewer gets 403 on every non-GET request (except its own
`/auth/password` and `/auth/logout`) and on every `/admin/*` and `/report-definitions` path; it
can read every analytics page and download reports.

All traffic volumes are **estimates** (sampled packet size × sampling rate); every traffic
response carries `"estimated": true`. Port utilization comes from interface counters and is exact.

## Filters

Every `/traffic/*` endpoint (and `/utilization/*` where it applies) accepts the same filters.
Values of one parameter are comma-separated (OR); different parameters are combined (AND).

| Parameter        | Example                        | Meaning                                         |
|------------------|--------------------------------|-------------------------------------------------|
| `from`, `to`     | `2026-09-29T08:00:00Z`, `1790000000` | ISO 8601 (naive = UTC) or Unix seconds; `to` defaults to now |
| `range`          | `5m` `15m` `1h` `6h` `24h` `7d` `30d` `90d` | window ending at `to` when `from` is omitted (default `1h`) |
| `exporter`       | `10.0.0.1`, `192.0.2.2/192.0.2.2/1` | agent/exporter IP or exporter id        |
| `src_ip`, `dst_ip` | `10.1.1.10`, `10.1.0.0/16`, `2001:db8::/32` | IP or CIDR                          |
| `ip`             | `10.1.1.10`                    | source **or** destination                       |
| `src_group`, `dst_group`, `group` | `Servers`     | IP group (source, destination, either)          |
| `if_group`       | `Uplinks`                      | interface group (ingress or egress port)        |
| `protocol`       | `tcp`, `udp,icmp`, `47`        | name or number                                  |
| `src_port`, `dst_port`, `port` | `443`            | `port` = source or destination                  |
| `service`        | `HTTPS`, `DNS`, `TCP/8443`, `Unknown`, `Non-IP` | see *Services*             |
| `vlan`           | `10,20`                        | VLAN ID                                         |
| `input_ifindex`, `output_ifindex`, `ifindex` | `48` | ingress, egress, either                         |

Invalid values return **422** with a readable `detail`. The time range is limited to the
retention period. Tables accept `format=csv` (up to 1000 rows).

## Endpoints

### Health and status
| Method | Path                | Description |
|--------|---------------------|-------------|
| GET    | `/health`           | `{"status":"ok","clickhouse":true}`; 503 when ClickHouse is unreachable (no session needed) |
| GET    | `/collector/status` | collector counters and storage (proxied); 502 when unreachable |
| GET    | `/system/status`    | app name, version, retention, service states, storage (disk, DB size, oldest record, achievable retention) |

### Switches and interfaces
| Method | Path                               | Description |
|--------|------------------------------------|-------------|
| GET    | `/exporters`                       | discovered switches, with live status from the collector (`status_source`) |
| GET    | `/exporters/{id}`                  | one switch and its interfaces (`id` = `exporter_ip/agent_ip/sub_id`) |
| PATCH  | `/exporters/{id}`                  | `{"display_name": "ICX8200-Core-01", "notes": "...", "icx_numbering": true}` |
| GET    | `/interfaces[?exporter={id}]`      | interfaces: ifIndex, speed, oper status, `label`, `port`, `description`, `controller_port` |
| GET    | `/interfaces/{exporter_id}/{ifindex}` | one interface |
| PATCH  | `/interfaces/{exporter_id}/{ifindex}` | `{"name": "1/1/48", "description": "Uplink"}`; an empty string clears a value |

Interface label = port + description, e.g. `1/1/8 Uplink`. Port: manual name > controller (RUCKUS
One / SmartZone) > SNMP `ifName` > RUCKUS ICX numbering derived from the ifIndex
(`icx_numbering`, 1/1/8, 1/2/1, LAG3) > `ifIndex N`. Description: manual > controller / SNMP
port name (`ifAlias`). Switch name: manual > controller > SNMP `sysName` > agent IP. Exporters
carry `controller` (`source` = `ruckusone`, `smartzone` or `snmp`; serial, name, model,
firmware, venue, status) and interfaces `controller_port` (port id, name, status, VLAN, LAG,
LLDP neighbor, `mapping_uncertain`).

### Traffic
| Path                         | Items                                                        |
|------------------------------|--------------------------------------------------------------|
| `/traffic/summary`           | headline figures: volume, rate, sources, destinations, conversations, samples, switches |
| `/traffic/timeseries`        | `t`, `bytes`, `packets`, `samples`, `bps`, `pps`; `step_seconds` automatic (~120 points) or `step=`; `group_by` + `top` for one series per service, protocol, host... |
| `/traffic/top-sources`, `/traffic/top-destinations` | `ip`, name, vendor, volumes, `percent`  |
| `/traffic/top-conversations` | `src_ip`, `dst_ip`, `protocol`, `service`, `first_seen`, `last_seen`, `exporters`, ifIndexes, `vlans`, volumes; `by=pair` (default), `by=5tuple` (adds ports) or `bidir` |
| `/traffic/top-services`      | `service`, volumes                                           |
| `/traffic/top-protocols`     | `protocol`, `ip_protocol`, volumes                           |
| `/traffic/top-vlans`         | `vlan` (null = untagged/unknown), volumes                    |
| `/traffic/top-exporters`     | `exporter_id`, `name`, `agent_ip`, volumes                   |
| `/traffic/top-interfaces`    | `exporter_id`, `exporter_name`, `ifindex`, `label`, `speed_bps`, `in_bytes` (ingress), `out_bytes` (egress), volumes |
| `/traffic/top-macs`          | MAC addresses (layer 2) with vendor; `direction=src\|dst`    |
| `/traffic/top-ethertypes`    | EtherType (IPv4, IPv6, ARP, LLDP, STP...)                    |
| `/traffic/top-groups`        | traffic per IP group; `direction=src\|dst`                   |
| `/traffic/group-matrix`      | traffic between IP groups (source group × destination group) |
| `/traffic/sankey`            | flow map: top paths across 2-3 dimensions (`levels=src_ip,service,dst_ip`, `top`, `metric`) |
| `/traffic/flows`             | individual samples, newest first; `limit` (≤ 1000), `offset` |

Top-N endpoints take `limit` (default 10, max 1000) and return `total` (bytes, packets, samples
of the whole filtered window) so that `percent` is relative to all matching traffic:

```json
{
  "query": {"from": "...", "to": "...", "vlans": [10]},
  "estimated": true,
  "total": {"bytes": 5551747072, "packets": 7176192, "samples": 1752},
  "items": [{"ip": "10.1.1.10", "bytes": 986890240, "packets": 240942, "samples": 186, "percent": 17.78}]
}
```

### Port utilization (interface counters, exact)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/utilization/interfaces` | every interface: average, 95th percentile and peak utilization in/out, discards, errors; `sort=peak\|p95\|avg\|discards` |
| GET | `/utilization/timeseries` | one interface over time (`exporter`, `ifindex`, `step`): average and peak per bucket |
| GET | `/utilization/broadcast` | broadcast and multicast packets/s per interface; `storm` when above `threshold_pps` (default 1000) |

### History and period comparisons
The worker job `history-rollup` (every 5 min) summarizes interface counters per interface and
hour into `interface_hourly`, kept **3 years** (raw data: `RETENTION_DAYS`). Periods are calendar
periods in the viewer's time zone (`tz`, IANA name; weeks start on Monday). While the current
period is running, deltas compare the same elapsed time of the reference period.

| Method | Path                   | Description |
|--------|------------------------|-------------|
| GET    | `/history/compare`     | `period=day\|week\|month\|year`, `at` (local date, default today), `tz`, `compare=previous\|week\|year`, `interface` (id(s), summed) or `if_group`: aligned buckets with volumes, average and peak rate, peak %, discards, errors; totals and deltas |
| GET    | `/history/interfaces`  | same period parameters + `if_group`, `exporter`, `limit`: every interface with current and reference volume, change %, peak |
| GET    | `/history/status`      | history coverage (first and last hour, rows) |

### Names and groups
| Method | Path | Description |
|--------|------|-------------|
| GET, PUT, DELETE | `/aliases` | manual names for an IP, a subnet or a MAC (`kind`, `key`, `name`, `notes`) |
| GET, POST | `/aliases/export`, `/aliases/import` | CSV `kind,key,name[,notes]` |
| GET | `/hosts?q=` | search hosts by name (aliases, controller clients, reverse DNS) |
| GET | `/hosts/{ip}` | name of one IP and where it comes from |
| GET | `/mac/{mac}` | vendor of a MAC address (IEEE registry, bundled) |
| GET, PUT, DELETE | `/groups` | IP groups (prefixes) and interface groups (`kind=ip\|if`, `name`, `members`) |

Name priority for hosts: manual alias > controller client name > reverse DNS > IP.

### Alerts
Rules are evaluated every minute by the worker (job `alerts`). An alert is one rule × one object
(interface, switch or traffic target): `open` → `acknowledged` (optional) → `closed` when the
condition ends (threshold rules close below 90 % of the threshold). Notifications are sent when an
alert opens and, optionally, when it closes. Closed alerts are kept 180 days.

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/alerts?state=active\|closed\|all` | alerts with rule name, object, link, message, value, notification results |
| GET    | `/alerts/counts` | open / acknowledged / open per severity (navigation badge) |
| POST   | `/alerts/{id}/ack`, `/alerts/{id}/close` | acknowledge, close by hand (reopens if still true) |
| GET    | `/alerts/rules` | rules, default parameters per kind |
| POST, PUT, DELETE | `/alerts/rules[/{id}]` | manage rules — administrators |
| POST   | `/alerts/evaluate` | evaluate now — administrators |

Rule kinds: `utilization` (average % ≥ threshold over N min, in/out/either), `discards`
(discards, errors or both ≥ N in N min), `broadcast` (average broadcast pps ≥ threshold),
`exporter_silent` (no sFlow for N min, switches seen in the last 7 days), `traffic` (estimated
traffic of a host/subnet, IP group or interface group ≥ threshold). Scope: one switch and/or one
interface group. Four default rules are created at first start (utilization 80 %, discards,
broadcast storm, switch silent), without notification channels.

### Reports
Weekly (previous Monday–Sunday, produced on Monday from 08:00) and monthly (previous month,
produced on the 1st from 08:00) PDF and Excel reports, in the appliance time zone (worker job
`reports`, every 5 minutes; a missed period is produced when the worker comes back). Files are
kept 90 days.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/reports` | generated reports: period, status, files, summary, e-mail result (recipients for administrators only) |
| GET | `/reports/{id}/{pdf\|xlsx}` | download |
| DELETE | `/reports/{id}` | delete — administrators |
| GET, POST | `/report-definitions` | definitions; `sections`, default options — administrators |
| PUT, DELETE | `/report-definitions/{id}` | update, delete (generated reports are kept) — administrators |
| POST | `/report-definitions/{id}/run` | "Report now": `{"period": "weekly\|monthly\|custom", "start": "2026-10-01", "end": "2026-10-07", "email": false}` (dates included); 202, generated in the background |

A definition: `{"name", "enabled", "period": "weekly|monthly", "options": {"sections": [...],
"exporters": [...], "monitored_groups": [...], "monitored_interfaces": [...], "busiest_ports": 10,
"top": 10, "warn_pct": 70}, "delivery": {"formats": ["pdf", "xlsx"], "email": true,
"recipients": [...]}}`. Sections: `summary`, `ports`, `applications`, `talkers`,
`conversations`, `trends`, `alerts`. E-mail goes through the SMTP channel of the notifications.

### Administration (administrators)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/config`, `/admin/jobs`, `/admin/diagnostics` | effective configuration (no secret), background jobs, diagnostics bundle (JSON download) |
| GET, POST | `/admin/users` | users; creation returns a random password once |
| PUT | `/admin/users/{name}/role` | `admin` or `viewer` |
| POST, DELETE | `/admin/users/{name}/reset-password`, `/admin/users/{name}` | reset (new random password), delete |
| GET | `/admin/logins?failed_only=` | sign-in log (kept 180 days) |
| GET, PUT | `/admin/notifications` | e-mail (SMTP), webhook (Teams Workflows, Slack, generic JSON), syslog UDP; public URL for links |
| POST | `/admin/notifications/test/{email\|webhook\|syslog}` | send a test message |
| GET, PUT | `/admin/enrichment` | controller source (`none`, `ruckusone`, `smartzone`), credentials, interval; last sync |
| POST | `/admin/enrichment/test`, `/admin/enrichment/sync` | test the controller connection, synchronize now |
| GET, PUT | `/admin/enrichment/snmp` | SNMP polling: `enabled`, `version` (`v2c`, `v3`), `community`, `v3` {`username`, `auth_protocol` SHA/SHA-256/SHA-512/MD5, `auth_key`, `priv_protocol` AES/AES-256/DES/none, `priv_key`}, `port`, `interval_minutes`; targets and last poll |
| POST | `/admin/enrichment/snmp/test`, `/admin/enrichment/snmp/poll` | test one switch (`{"ip": "..."}`, nothing written), poll now |
| GET, PUT | `/admin/dns` | reverse DNS: on/off, DNS servers, lookups per minute; cache statistics |
| POST | `/admin/dns/run` | resolve pending IPs now |
| GET | `/admin/certificate` | installed HTTPS certificate and pending CSR |
| POST | `/admin/certificate/csr`, `/pem`, `/pfx`, `/self-signed` | new key + CSR, install PEM (certificate + key) or PFX, back to self-signed; nginx reloads by itself |
| GET, PUT | `/admin/updates` | installed and latest published version, release notes; enable or disable the daily check |
| POST | `/admin/updates/check`, `/admin/updates/apply` | check GitHub now; install the latest release (backup first, the interface restarts) |
| GET | `/admin/updates/progress` | progress of the running (or last) update |

Secrets (controller, SMTP, webhook, SNMP) are write-only: an empty or missing value keeps the
stored one; they are encrypted at rest with the key `secret.key` of the `app-config` volume.

## Services

Port-based naming, not DPI: the destination port is looked up first, then the source port
(response direction), for TCP/UDP. Other IP protocols are named after the protocol (ICMP, GRE,
ESP...). Unmapped TCP/UDP flows whose lowest port is below 49152 are labelled `<PROTO>/<port>`
(e.g. `TCP/8443`); flows between two ephemeral ports are `Unknown`; non-IP frames are `Non-IP`.
The table lives in `api/app/catalog.py`.

## Collector status endpoint (internal)

The collector serves `/healthz`, `/v1/status`, `/v1/exporters` on port 8081 inside the Docker
network (127.0.0.1:8081 on the host in development). The API reads it for live exporter status
and storage statistics; see the field list in `/api/v1/docs` → `/collector/status`.
