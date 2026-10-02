# TODO — roadmap

Ordered by development dependency: each milestone only relies on earlier ones.
Priority: **P1** = needed for a convincing customer PoC, **P2** = strong value, **P3** = later.

Done: Phase 1 collector · Phase 2 ClickHouse (30-day retention, disk guard) · Phase 3 API ·
Phase 4 web UI + nginx HTTPS · port utilization from interface counters · Ruckus colors (first pass).

---

## M5 — Finish and polish what exists (no new dependency)
- [x] P1 Update CHANGELOG / README / docs for Phase 4, utilization and the Ruckus theme
- [x] P1 Utilization chart: auto-scale the Y axis when usage is low (0–100 % hides 0.3 % on 10G)
- [x] P1 **Stacked traffic over time** by top N services / sources / destinations / interfaces + "Other"
      (API: timeseries `group_by`; UI: stacked area on dashboard, explorer, device and interface pages)
- [x] P1 **Bidirectional conversations**: merge A→B and B→A into one row with both directions' volumes
- [x] P1 **CSV export** of every table (API `format=csv` on top-N and flows endpoints + UI button)
- [x] P1 "New version available — reload" banner: an open tab keeps the old UI after an update
      (poll the app version / index.html ETag and prompt the user)
- [ ] P2 Continue the NetFlow Analyzer / SolarWinds-style layout (widget density, breadcrumbs, drill-downs)
- [ ] P2 Separate ClickHouse users: read-only for analytics, write only on `*_settings` tables
- [ ] P2 Forget exporters/interfaces not seen for longer than the retention period
- [ ] P2 Measure query latency on 30 days × 1 000 samples/s; add 1-minute aggregates if needed

## GH — GitHub-ready (continuous, from now on; publication itself only when the user decides)
The repository must be publishable at any time without cleanup. No remote/push until asked.
- [x] P1 Local git repository (no remote) with meaningful commits from now on; tags per milestone (v0.4.0…)
- [x] P1 **No secrets and no lab data in the repo** (lab notes in ignored docs/local/; gitleaks in CI): `.env` ignored (done), secret scan (gitleaks) in CI,
      move lab-specific values (192.168.x IPs, ESXi host, lab ICX notes in docs/deployment.md, docs/sflow.md)
      to an ignored `docs/local/` or generic examples; `vm-seed/` stays outside the repo
- [ ] P1 **One-command start for newcomers**: `git clone` → `scripts/dev-up.sh` → https://localhost,
      with the synthetic generator profile so the UI shows data without a switch
- [x] P1 **CI with GitHub Actions** (validated with actionlint; runs once pushed): Go tests + race + vet, fuzz smoke, pytest (with ClickHouse service),
      vitest + typecheck, Docker builds, collector↔ClickHouse integration test; status badges in README
- [x] P1 **Published images**: multi-arch builds pushed to GHCR on tags; a `docker-compose.release.yml`
      using those images (no local build needed)
- [ ] P1 **One-line installer for published images**: `install.sh` attached to each GitHub Release
      (checks Docker, downloads a release bundle: compose files + .env template; generates passwords,
      sets `net.core.rmem_max`, pulls GHCR images, starts, prints URL and initial admin password).
      Bake the ClickHouse config into an image or ship it in the bundle (release compose must not need
      the repository). Update via `docker compose pull && up -d`.
- [ ] P1 README "Getting started" for end users: prerequisites, install, ICX sFlow config, first login
- [ ] P2 OVA appliance (spec Phase 7): Ubuntu cloud image + first-boot console (IP/DNS/NTP/timezone),
      stack preinstalled, initial password on the console; published as a Release asset
- [x] P2 LICENSE — all rights reserved with a free right to use the published releases; SECURITY.md, CONTRIBUTING.md,
      issue/PR templates done; CODEOWNERS once the GitHub account is known
- [ ] P2 README for GitHub: screenshots, architecture diagram, ICX configuration, sizing, FAQ;
      trademark note (Ruckus/ICX/SmartZone/RUCKUS One are trademarks of their owners; no logos used)
- [x] P2 Dependabot for Go, Python, npm and Docker base images
- [ ] P3 Versioned documentation site (GitHub Pages) and CHANGELOG-driven releases

## M6 — Platform foundations (required by M7–M10)
- [x] P1 **Authentication** (spec §27): local admin, hashed passwords, secure session cookie, timeout, logout
- [x] P1 **Configuration store** on the `app-config` volume (settings, groups, alert rules, connector
      credentials — secrets encrypted at rest) + **Administration** pages (spec §26: general, retention,
      collector, devices, storage, diagnostics/log download)
- [x] P1 **Worker service** (Python, same image as the API): scheduled jobs with status in the UI —
      used by DNS resolution, SmartZone/R1 polling, alert evaluation and reports

## M7 — Enrichment (needs M6: worker + config store)
- [x] P1 **SmartZone OR RUCKUS One connector** (RUCKUS One v0.7.0, SmartZone v0.8.0 — SmartZone to
      validate against a real vSZ with managed ICX) — implement both, the user chooses one
      (`ENRICHMENT_SOURCE=none|smartzone|ruckusone`), never both at once. Read-only account.
      Optional: sFlow keeps working without it.
  - [x] switch name, model, serial, firmware → exporter display (manual names still override)
  - [x] interface name + description → automatic ifIndex mapping (manual names still override)
  - [x] **LLDP neighbors** per port (neighbor name, port, type) on interface and device pages
  - [x] client names known by the controller shown next to IPs (v0.7.2)
- [x] P1 **Reverse DNS** of source/destination IPs: asynchronous resolution by the worker, cache
      (TTL, negative cache, rate limit, timeout), configurable DNS servers, on/off; hostname shown next
      to IPs and searchable. Priority of names: controller client name > DNS > IP
- [x] P1 **Host aliases** for endpoints when there is no internal DNS: IP → name, subnet → name,
      and MAC → name (follows DHCP address changes); edit inline from any table (click an IP →
      rename), CSV import/export; names shown next to IPs everywhere and usable as filters.
      Name priority everywhere: **manual alias > controller client name (SZ/R1) > reverse DNS > IP**.
      (Switches and interfaces are already renameable.)
- [x] P2 **MAC vendor (OUI)** lookup from a bundled IEEE OUI list (no network access needed)

## M8 — Grouping and L2/L3 insight (needs M6 config store; uses M7 names)
- [x] P1 **Named IP groups** (subnets/ranges: "Servers", "Guest Wi-Fi", "Site Lyon") and **interface
      groups** ("Uplinks", "Access ports"): new filters `src_group`, `dst_group`, `if_group`;
      top groups and group-to-group matrix ("Guest → Servers")
- [x] P1 **Layer-2 view**: top MACs with vendor, non-IP traffic, per VLAN
- [x] P1 **Broadcast / multicast per port** from interface counters (already stored) — storm view
- [x] P2 **Period comparison** (v0.11.0): long-term hourly interface history (3 years), Trends page
      and interface panel: day / week / month / year vs previous period, same day last week or a year before
- [ ] P2 Comparison for flows (services, top talkers per period): needs a daily flow rollup table;
      baseline for the anomaly alert in M9
- [ ] P2 **QoS / DSCP** breakdown: collector stores ToS/DSCP from sampled headers (schema migration),
      top DSCP classes, filter by DSCP
- [ ] P2 **Switch health**: decode sFlow processor counters (CPU, memory) and show them per device

## M9 — Alerting (needs M6 worker, uses M8 groups/baseline and utilization)
- [x] (v0.13.0) Rule engine, rules below, notifications e-mail / Teams-Slack webhook / syslog, Alerts page and badge
- [ ] P1 Rule engine evaluated by the worker, with state (open/acknowledged/closed) and history
- [ ] P1 Rules: port utilization > X % for N min · discards/errors increasing · exporter silent ·
      broadcast/multicast storm · traffic of a group/host above threshold
- [ ] P2 Rule: traffic anomaly vs baseline (M8)
- [ ] P1 Notifications: e-mail (SMTP), syslog, webhook (Teams/Slack); per-rule recipients
- [ ] P1 Alerts page + badge in the navigation; alert → link to the filtered explorer/interface view

## M10 — Reporting (needs M6 worker, uses M8 groups)
- [ ] P2 Scheduled PDF/CSV reports (daily/weekly) per site, group or uplink: top talkers, services,
      utilization with 95th percentile; e-mailed or downloadable
- [ ] P3 Saved views and customizable dashboards (widgets chosen per user)

## M11 — Later (P3)
- [ ] GeoIP and ASN enrichment for internet traffic (country, provider)
- [ ] Real-time view (2–5 s refresh) for live troubleshooting
- [ ] Capacity forecast ("uplink reaches 80 % in 3 months")
- [ ] Integrations: Grafana data source, flow/alert forwarding to a SIEM
- [ ] Editable service catalog (table + API)
- [ ] SNMPv2c enrichment for sites without SmartZone/R1 (spec §18)
- [ ] OVA appliance packaging (spec Phase 7) — once the Docker stack is stable
- [ ] Out of scope unless requested: scan/DDoS detection (spec non-goals)

## Technical backlog
- [ ] Re-measure bytes/row on real customer traffic and update the sizing table
- [ ] Drop samples (sFlow format 5): decode reason and store
- [ ] Extended router/gateway records (next hop, AS)
- [ ] Optional raw datagram capture mode in the collector (replay without tcpdump)
- [ ] pcapng support in replay
- [ ] Wire exporter display names into collector logs (optional)
- [x] Multiple users with roles (read-only viewer vs admin), user management page and sign-in log (v0.12.0)
- [ ] Editable settings from the Administration page (today: `.env` + restart)
