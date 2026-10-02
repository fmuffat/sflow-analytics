# Changelog

## [0.15.0] - 2026-10-02 — HTTPS certificate, Administration in tabs

### Added
- **HTTPS certificate** management (Administration → HTTPS certificate): current certificate
  (issuer, names, expiry, fingerprint); install a PFX/P12 file or PEM certificate + key +
  chain, validated first (key matches, dates, names; previous files kept as .bak); generate a
  CSR whose private key stays on the appliance, then install the signed certificate without key;
  back to a self-signed certificate. The private key is never returned. nginx reloads itself
  within ~10 s when the files change (no downtime; an invalid file is not loaded).
  API `/admin/certificate` (GET, POST `/pem`, `/pfx`, `/csr`, `/self-signed`).

### Changed
- **Administration in tabs**: System, Users & sign-ins, Integrations (RUCKUS One / SmartZone and
  reverse DNS), Notifications, HTTPS certificate, My account (`/admin?tab=...`). "Users &
  sign-ins" left the navigation (old link redirects); reverse DNS settings moved from Hosts.

## [0.14.1] - 2026-10-02

### Changed
- First sign-in to the web interface: **admin / sflow**, a new password is required at once
  (installer and appliance; sign in before exposing the appliance).

### Fixed
- OVA: the data disk is declared without file and created empty by the hypervisor (the empty
  VMDK was rejected by the ESXi upload: "postNFCData failed / Error on read").
- OVA setup assistant: host name and time zone written directly (systemd-hostnamed/timedated
  refused the change on first boot); the data disk detection ignores floppy/CD drives.
- Installer: no "tr: write error: Broken pipe" message; wait for the web interface configurable
  (`SFLOW_WAIT`, 20 minutes on the appliance).

## [0.14.0] - 2026-10-01 — VMware appliance (OVA)

### Added
- **OVA appliance** (`appliance/build-ova.sh`): Ubuntu 24.04 LTS, 4 vCPU, 8 GB, system disk 30 GB
  and data disk 100 GB (thin), PVSCSI, VMXNET3, ESXi 6.7+. At first boot a console assistant asks
  network (DHCP/static), host name, time zone, NTP and the password of the system account
  "sflow", formats the data disk (Docker data on /data), installs the application from the
  bundled package and shows the URL and the initial admin password. Console menu
  `sudo sflow-console`: status, network change, initial/reset admin password, restart, reboot.
  Unattended setup with /etc/sflow/setup.conf. No default password is shipped.
- User-oriented README with screenshots (demo data); GitHub Release workflow attaching the
  offline package.
- "A new version of sFlow Analytics is available — Reload" banner: each build publishes
  `/version.json`; open pages check it every 2 minutes and when the window regains focus.

## [0.13.1] - 2026-10-01 — Installation package (internal beta)

### Added
- **Offline installation package** `sflow-analytics-<version>.tar.gz` (images included, ~310 MB),
  built with `scripts/build-package.sh`: `sudo ./install.sh` installs Docker if needed (Ubuntu /
  Debian packages), loads the images, generates the configuration (random database password,
  certificate for the host IP, time zone), tunes UDP buffers, starts and prints the URL, the
  initial admin password and the ICX sFlow configuration. Re-running it upgrades and keeps data.
  `--demo` starts 5 synthetic switches. `uninstall.sh` (data kept, or `--purge`), `backup.sh`,
  QUICKSTART.md (EN/FR). Ports configurable (HTTPS_PORT, HTTP_PORT, SFLOW_PORT).

### Fixed
- Worker: on a fresh installation the first job runs failed while the collector was creating
  the ClickHouse schema; the worker now waits for the schema (up to 5 minutes).

## [0.13.0] - 2026-10-01 — Alerts

### Added
- **Alerting**: rules evaluated every minute by the worker; alerts open, can be acknowledged,
  and close by themselves when the condition ends (hysteresis: below 90 % of the threshold);
  history kept 180 days.
- Rule kinds: port utilization, discards/errors, broadcast storm, switch silent (no sFlow),
  traffic of a host / subnet / IP group / interface group; scope by switch and interface group;
  severity critical / warning / info. Four default rules at first start.
- **Notifications**: e-mail (SMTP with STARTTLS/SSL), Microsoft Teams (Workflows webhook,
  adaptive card), Slack/Mattermost, generic JSON webhook, syslog UDP; per-rule channels and
  recipients, "also when resolved"; test buttons; links to the appliance (public URL setting).
  SMTP password and webhook URL encrypted at rest and never returned.
- **Alerts** page (active, history, rules editor) and a badge in the navigation.
- Read-only accounts see alerts and rules (without recipients) but cannot acknowledge or edit.

## [0.12.0] - 2026-10-01 — Read-only accounts

### Added
- **Roles**: `admin` and `viewer` (read-only). A read-only account sees every analytics page
  (dashboard, explorer, trends, devices, interfaces, hosts, groups, layer 2, collector health)
  but cannot change anything (names, aliases, groups...) nor open Administration; the API
  enforces it (403), the UI hides edit controls and shows a "read-only" badge. Read-only users
  change their password under "My account".
- **Users & sign-ins** page (administrators): create an account with a role (random password
  shown once, to change at first sign-in), change role (closes the user's sessions), new
  password, delete; at least one administrator is always kept.
- **Sign-in log**: every attempt (user name, address, result), kept 180 days; failures in
  the last 24 hours.
- CLI: `app.users create --role viewer`, `app.users set-role`.

### Security
- nginx now overwrites `X-Forwarded-For` with the real client address: a forged header could
  previously bypass the login throttling (5 failures / 5 min per address) once exposed to the
  Internet.

## [0.11.1] - 2026-10-01

### Changed
- Default `RETENTION_DAYS` is now **90** (was 30); "90d" added to the time range selector.
  Sizing table for 90 days and disk growth procedure (docs/architecture.md, docs/deployment.md).

## [0.11.0] - 2026-10-01 — Trends: day / week / month / year comparisons

### Added
- **Long-term interface history**: hourly summary of interface counters per interface
  (exact volume, peak and 95th percentile of the 20 s rates, discards, errors, broadcast,
  multicast) in `interface_hourly`, **kept 3 years** while raw data keeps `RETENTION_DAYS`.
  Computed every 5 minutes by the worker job `history-rollup`; the first run backfills the raw
  retention. Not affected by the disk guard. Collector migration 005.
- **Trends** page (Monitor): day, week, month or year compared with the previous period, the
  same day last week or a year before; ‹ › navigation; every interface with volume, change %,
  average, peak and peak change; click a row to chart it; filter by interface group (a group
  is charted as the sum of its interfaces). Current period vs reference period chart
  (average, peak or volume; in, out or both).
- "Trends" panel on each interface page.
- Applications and protocols donuts on device and interface pages.
- Time format preference 24 h / 12 h (bottom of the navigation, per browser).
- API `/history/compare`, `/history/interfaces`, `/history/status`.

### Notes
- Periods follow the viewer's time zone. While a period is running, changes compare the same
  elapsed time of the reference period (e.g. October 1-15 vs September 1-15).

## [0.10.0] - 2026-09-30 — M8 (part 1): groups, layer 2, broadcast storms

### Added
- **Groups**: named IP groups (IPs and subnets) and interface groups; page Inventory →
  Groups; filters `src_group`, `dst_group`, `group`, `if_group` on every traffic endpoint
  and in the Traffic Explorer; top source/destination groups and a group-to-group matrix
  (Explorer tab "Groups"); flow map levels "source group → (service →) destination group".
  API `/groups`, `/traffic/top-groups`, `/traffic/group-matrix`. Collector migration 004.
- **Layer 2** page: top source/destination MACs with vendor, alias, IP (when the MAC carries
  one address) or "gateway" badge, VLANs; traffic per EtherType (ARP, LLDP, STP/LLC, LACP,
  HomePlug, LLTD...). API `/traffic/top-macs`, `/traffic/top-ethertypes`.
- **Broadcast / multicast storms** from interface counters (exact): per port average and
  peak broadcast/multicast packets/s, broadcast share, time of the peak, number of 20 s polls
  above a configurable threshold (default 1000 pps); chart on each interface page.
  API `/utilization/broadcast`; broadcast/multicast rates added to `/utilization/timeseries`.

### Verified
- Lab: a one-minute broadcast burst (~1000 pps per port on every port of a switch) detected
  among 24 h of normal traffic (6–7 pps per port).

## [0.9.1] - 2026-09-30 — MAC vendors

### Added
- **MAC vendor (OUI)** from the IEEE registries (MA-L, MA-M, MA-S; 54 079 prefixes), bundled
  (`api/app/data/oui.tsv.gz`, refreshed with `scripts/update-oui.py`): shown under IPs in top
  sources/destinations and conversations, and for MACs in flow samples; randomized
  (locally administered) and multicast MACs are labelled as such. API `/mac/{mac}`,
  `/hosts/{ip}` now returns the MAC and vendor.

### Fixed
- A router/gateway MAC (seen with more than 16 IPs in a day) no longer names or identifies
  the hosts behind it: MAC aliases and vendors apply only to the device's own IPs.

## [0.9.0] - 2026-09-30 — Host names: aliases and reverse DNS

### Added
- **Manual aliases** on an IP, a MAC (follows the device across DHCP changes; MAC learned
  from the controller or recent flows) or a subnet (fallback name); page "Hosts & aliases"
  with add/edit/delete and CSV import/export; ✎ button next to every IP in the tables.
- **Reverse DNS** resolved by the worker (`reverse-dns` job, every minute, most active IPs
  of the last hour first; cache 24 h, 6 h for no PTR, 1 h for errors; parallel lookups with a
  2 s timeout); configurable resolvers (e.g. the internal DNS), on/off, lookups per run.
- Single name resolution everywhere, in priority order: IP alias › MAC alias › controller
  client name › reverse DNS › subnet alias.
- Host search by name in the Traffic Explorer (aliases, controller clients, DNS);
  API `/aliases`, `/aliases/import`, `/aliases/export`, `/hosts?q=`, `/hosts/{ip}`, `/admin/dns`.
- Collector migration 003 (`host_aliases`, `dns_cache`).

## [0.8.0] - 2026-09-30 — SmartZone connector

### Added
- **SmartZone / vSZ connector** (read-only administrator): service-ticket login and logoff,
  API version negotiated from `apiInfo` (v13_0 → v11_0), switches, ports (user port name
  parsed from SmartZone's "(name) Interface" format), LLDP table (`/switch/clients`),
  optional TLS verification for self-signed certificates. Same tables, ifIndex mapping and
  UI as RUCKUS One; the source is chosen in Administration → Enrichment.

### Verified
- Against a real vSZ (API v13_0): 16 switches, 322 ports, 11 LLDP neighbors in 8.7 s.

### Fixed
- Default interface names returned by controllers ("GigabitEthernet1/1/1",
  "2.5GigabitEthernet1/1/2") are no longer shown as port names.

### Changed
- Only the controller currently selected feeds the inventory (switching source no longer
  mixes RUCKUS One and SmartZone data).

## [0.7.2] - 2026-09-29

### Added
- Endpoint names next to IPs (top sources/destinations, conversations, bidirectional
  conversations, flow samples, flow map), from the clients the controller sees on switch
  ports (RUCKUS One). Manual aliases and reverse DNS will come on top (TODO M7).

### Fixed
- Controller port names kept trailing spaces.

### Verified
- ICX ifIndex numbering confirmed on two switches (ICX8200, ICX7150): 1/1/x, 1/2/x → 65+,
  1/3/x → 129+, LAG1 → 3073; all 17 mapped ports agree on speed with sFlow counters.

## [0.7.1] - 2026-09-29

### Added
- **Flow map** (Sankey, inspired by ElastiFlow): top traffic paths across 2–3 dimensions —
  source → destination, source → service → destination, ingress → egress port,
  VLAN → service, source → service; bytes / packets / samples; coverage of the shown
  paths; click a node or band to drill down. API `/traffic/sankey`. Tab in the Traffic
  Explorer; ingress → egress ports map on each device page; "who talks to whom" map on
  each interface page (traffic entering or leaving that port).

## [0.7.0] - 2026-09-29 — M7 (part 1): RUCKUS One enrichment

### Added
- RUCKUS One connector (read-only, OAuth2 client credentials; regions North America,
  Europe, Asia): switches, ports with LLDP neighbors, clients seen on switch ports.
- Worker job `controller-sync` (interval configurable, retry after 5 min on failure);
  ClickHouse tables `ctrl_switches`, `ctrl_ports`, `ctrl_clients` (collector migration 002).
- Administration → Enrichment: source (none / RUCKUS One / SmartZone — coming), region,
  tenant, client ID, write-only encrypted secret, "Test connection", "Sync now", last sync.
- Exporters matched to controller switches by management IP: name, model, serial,
  firmware, venue. Interfaces mapped with the ICX ifIndex numbering
  (`(unit-1)*256 + (module-1)*64 + port`, LAG `3072+n`), cross-checked with the sFlow
  port speed (mismatch = "mapping uncertain"): port id, description, VLAN, LLDP neighbor.
  Manual names keep priority.

### Fixed
- nginx kept a stale upstream address after the API or UI container was recreated (502):
  service names are now resolved at request time.

## [0.6.0] - 2026-09-29 — M6: platform foundations

### Added
- **Authentication**: local accounts, Argon2id password hashing, server-side sessions in an
  HttpOnly / Secure / SameSite=Strict cookie, idle (60 min) and absolute (12 h) timeouts,
  logout, login throttling, forced password change at first login, `X-Requested-With`
  check on mutations. Every API route except `/health` requires a session.
- Initial admin created at first start (password from `ADMIN_PASSWORD`, or random: API/worker
  log and `initial-admin-password` on the config volume).
- User CLI: `python -m app.users list|create|reset-password|delete` (forgotten password recovery).
- **Configuration store**: SQLite on the new `app-config` volume (users, sessions, settings,
  job state); secrets encrypted at rest (Fernet key from `SECRET_KEY` or generated per install).
- **Worker service** (`python -m app.worker`, same image as the API) running scheduled jobs,
  with status, duration and errors recorded; first job purges expired sessions.
- **Administration** page: effective configuration, password change, storage, collector,
  background jobs, diagnostics bundle download (`/admin/diagnostics`).
- Web UI: login page, forced password change, user menu with sign-out.

## [0.5.1] - 2026-09-29


### Added
- GitHub-ready: CI workflow (secret scan, Go tests + fuzz smoke, collector↔ClickHouse,
  API tests against ClickHouse, web UI typecheck/tests/build, image builds), release
  workflow publishing multi-arch images to GHCR on tags, `docker-compose.release.yml`,
  Dependabot, SECURITY.md, CONTRIBUTING.md, issue and PR templates.

## [0.5.0] - 2026-09-29 — M5: polish

### Added
- Stacked traffic timeline split by service, source, destination, protocol, VLAN or exporter
  (top 5 + Other): API `timeseries?group_by=…&top=…`, selector on every timeline.
- Bidirectional conversations (A↔B with per-direction volumes): `top-conversations?by=bidir`,
  "One-way / Both directions" toggle.
- CSV export of every top-N table and of flow samples (`format=csv`, "CSV" buttons).
- Utilization chart auto-scales when the load is low.

### Changed
- Lab-specific addresses removed from versioned docs (moved to the ignored `docs/local/`).

## [0.4.0] - 2026-09-29 — Phase 4: web UI

### Added
- Web UI (React + TypeScript + Vite, Recharts, TanStack Query): Dashboard, Traffic Explorer
  (filters in the URL), Devices, Device detail, Interfaces, Interface detail, Collector Health.
  Drill-down from every top-N row, drag-to-zoom on charts, 30 s auto-refresh on live windows.
- nginx reverse proxy: HTTPS with a self-signed certificate generated on first start,
  HTTP→HTTPS redirect, `/api` proxy; `frontend` and `nginx` services in docker-compose.
- API `/traffic/summary` (current rate, active conversations, sources, destinations).
- **Port utilization** from sFlow interface counters (exact): API `/utilization/interfaces`
  (avg / p95 / peak %, discards, errors) and `/utilization/timeseries` (avg and peak per bucket);
  dashboard widget, Interfaces columns, Interface page charts with an 80 % threshold.
- Ruckus-inspired colors (orange accent, charcoal navigation), service and protocol donuts.
- `scripts/ui-screenshots.sh`: headless Chromium screenshots of every page, fails on console errors.

## [0.3.0] - 2026-09-29 — Phase 3: API

### Added
- FastAPI service `api` (Python 3.12, clickhouse-connect), `/api/v1`, OpenAPI docs at `/api/v1/docs`.
- Health, collector status (proxied) and system status (services, retention, storage).
- Exporters list/detail/rename, interfaces list/detail/manual naming; names stored in
  `exporter_settings` / `interface_settings` and used in analytics results.
- Traffic: timeseries (auto step, zero-filled), top sources, destinations, conversations
  (pair or 5-tuple), services, protocols, VLANs, exporters, interfaces (ingress/egress), flows.
- Combinable filters on every traffic endpoint (time, IP/CIDR, exporter, protocol, ports,
  service, VLAN, ifIndex in/out/either); all values bound as ClickHouse parameters.
- Port-based service catalog (no DPI).
- 77 API tests (unit + against ClickHouse with a Python oracle), `scripts/api-test.sh`.

### Verified
- Real ICX data: 24 h queries answer in 20–80 ms.

## [0.2.0] - 2026-09-28 — Phase 2: persistence

### Added
- ClickHouse 25.8 service (`clickhouse-data` volume, config for small VMs, bounded system logs).
- Schema embedded in the collector and applied at startup: `flow_records`,
  `interface_counters` (daily partitions, TTL), `exporters`, `interfaces`,
  `exporter_settings`, `interface_settings`.
- Batching ClickHouse sink: 5000 rows or 1 s, non-blocking, bounded retry queue
  with backoff, poison-batch protection, full accounting of inserted/pending/dropped rows.
- Retention: `RETENTION_DAYS`, default **30 days**, applied at startup without rewriting data.
- Disk guard: drops the oldest day above `DISK_MAX_USAGE_PERCENT` (85 %), suspends
  inserts at +10 points; storage statistics (disk, DB size, oldest record, estimated
  retention) in `/v1/status`.
- Exporter and interface inventory persisted and restored at startup; interface
  speed and oper status from counter samples.
- Integration tests against real ClickHouse (`scripts/integration-test.sh`).

### Changed
- Flow logging is now off by default (`SFLOW_LOG_FLOWS=false`); flows go to ClickHouse.
- Go 1.25 required (clickhouse-go).
- `scripts/dev-up.sh` generates a random ClickHouse password in `.env`.

### Verified
- Real ICX flows stored and queried; inventory restored after a collector restart.
- 500 synthetic exporters / 80 000 samples/s inserted without loss (collector ~1.1 core, ClickHouse ~0.85 core).
- ~24 bytes per flow record on disk (synthetic traffic).

### Fixed
- 802.3/LLC frames (e.g. STP BPDUs) no longer report their length as EtherType.

## [0.1.0] - 2026-09-28 — Phase 1: collector proof of concept

### Added
- Go collector: UDP/6343 listener, bounded queue, decode worker pool.
- sFlow v5 decoding (samples via goflow2 v2.2.6; datagram envelope walked
  locally so that one bad sample does not drop the datagram).
- Normalized flow records: exporter/agent, ifIndexes, MACs, EtherType, VLAN
  (802.1Q/QinQ or extended switch), IPv4/IPv6, protocol, ports, TCP flags,
  sampled size, sampling rate, estimated bytes/packets.
- Generic interface counter decoding.
- Automatic exporter discovery (source IP + agent IP + sub-agent ID),
  active/inactive status, samples/s, sequence-gap loss detection.
- Collector counters and internal HTTP status endpoint (`/healthz`, `/v1/status`, `/v1/exporters`).
- Structured JSON logs; rate-limited flow logging and error logging.
- `sflow-gen`: synthetic multi-exporter traffic, malformed injection, pcap/bin replay, fixture writer.
- Fixtures with golden outputs, unit tests, UDP end-to-end test, decoder fuzz test.
- Dockerfile (distroless), docker-compose with optional generator profile.

### Verified
- Real Ruckus ICX decoded with zero errors: IPv4/IPv6, VLANs, ifIndexes, STP BPDUs.
- 500 synthetic exporters / 80k samples/s received without loss on the dev VM (log sink).
