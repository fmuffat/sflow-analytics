# Local sFlow Collector & Analytics Appliance
## Product + Technical Specification for Claude Code

**Status:** Draft v0.1  
**Target:** Local standalone sFlow analytics platform  
**Primary telemetry source:** sFlow v5  
**Primary target infrastructure:** Ruckus ICX switches  
**Initial deployment:** Docker Compose for development, then packaged as an OVA appliance  
**Goal:** Build a simple, self-contained local traffic analytics solution able to receive sFlow, store and aggregate telemetry, and expose useful network troubleshooting views.

---

# 1. Product Vision

Build a local sFlow analytics appliance that can be deployed inside a customer network and immediately provide visibility into the traffic traversing ICX switches.

The appliance should answer questions such as:

- What is consuming bandwidth?
- Which endpoint is generating the traffic?
- Where is the traffic going?
- Which protocols or services are involved?
- Which switch saw the traffic?
- Which ingress or egress interface is involved?
- Which VLAN is involved?
- What happened during a specific time window?
- Which conversations were responsible for a traffic spike?
- How has traffic evolved over time?

The first release is intentionally focused on **sFlow only**.

The longer-term idea is to use this application as a local proof-of-concept for a future architecture where Ruckus Edge could collect ICX sFlow and Ruckus One could process the resulting telemetry.

---

# 2. Core Product Principle

The application must be designed for **network troubleshooting**, not simply for displaying raw sFlow packets.

The preferred user journey is:

```text
Dashboard
    ↓
Traffic spike / high utilization
    ↓
Switch
    ↓
Interface
    ↓
Top Conversation
    ↓
Source / Destination / Service
```

The user should never need to understand raw sFlow structures or interface indexes unless they explicitly want to inspect them.

---

# 3. MVP Scope

The MVP must provide:

## Collection

- sFlow v5 listener
- UDP port 6343
- support for multiple exporters
- automatic exporter discovery
- flow sample decoding
- counter sample decoding where practical
- graceful handling of malformed packets
- collector statistics

## Traffic Information

Extract, where available:

- timestamp
- exporter / agent IP
- source IP
- destination IP
- source MAC
- destination MAC
- source port
- destination port
- IP protocol
- Ethernet type
- VLAN
- input interface index
- output interface index
- sampled packet size
- sampling rate
- estimated traffic volume

## Storage

Store:

- raw normalized flow records for short-term analysis
- aggregated traffic statistics
- exporter inventory
- interface metadata
- system configuration

## Analytics

Provide:

- Traffic timeline
- Top Sources
- Top Destinations
- Top Conversations
- Top Services / Applications
- Top Protocols
- Top VLANs
- Top Exporters
- Top Interfaces

## Filtering

Users must be able to filter by:

- time range
- source IP
- destination IP
- subnet
- protocol
- source port
- destination port
- service/application
- exporter
- ingress interface
- egress interface
- VLAN

Filters must be combinable.

## Web UI

Provide:

- Dashboard
- Traffic Explorer
- Devices
- Device Detail
- Interfaces
- Interface Detail
- Administration
- Collector Health

---

# 4. Non-Goals for MVP

Do NOT implement the following in the first version:

- NetFlow
- IPFIX
- packet capture
- DPI
- IDS / IPS
- threat detection
- SIEM functionality
- multi-tenancy
- cloud sync
- Ruckus One integration
- machine learning
- AI assistant
- full topology discovery
- SNMPv3
- Active Directory / SAML
- advanced RBAC
- custom report builder

The goal is to first build a stable and useful sFlow collector.

---

# 5. High-Level Architecture

```text
                 Customer Network

      ICX Switches / sFlow Exporters
                    │
                    │ UDP/6343
                    ▼
        ┌─────────────────────────┐
        │     sFlow Collector     │
        │                         │
        │ Receive                 │
        │ Decode                  │
        │ Validate                │
        │ Normalize               │
        │ Estimate traffic        │
        └────────────┬────────────┘
                     │
                     ▼
        ┌─────────────────────────┐
        │       ClickHouse        │
        │                         │
        │ Flow records            │
        │ Aggregations            │
        │ Time-series queries     │
        └────────────┬────────────┘
                     │
                     ▼
        ┌─────────────────────────┐
        │          API            │
        │                         │
        │ Search                  │
        │ Filtering               │
        │ Top-N                   │
        │ Devices                 │
        │ Interfaces              │
        │ Health                  │
        └────────────┬────────────┘
                     │
                     ▼
        ┌─────────────────────────┐
        │        Web UI           │
        │                         │
        │ Dashboard               │
        │ Traffic Explorer        │
        │ Devices / Interfaces    │
        │ Administration          │
        └─────────────────────────┘
```

---

# 6. Recommended Technology Stack

The initial implementation should favor simplicity, maintainability and performance.

## Collector

**Go**

Reasons:

- excellent UDP performance
- low memory footprint
- easy static binary
- good concurrency model
- appropriate for a future appliance
- easy containerization

The collector may use a mature sFlow v5 decoding library if one is available and actively maintained.

Avoid implementing an entire sFlow decoder from scratch unless necessary.

## Database

**ClickHouse**

Reasons:

- designed for high-volume analytical data
- excellent time-series aggregation
- very fast Top-N queries
- high compression
- suitable for flow telemetry

## Backend API

Preferred:

**FastAPI / Python**

Reasons:

- fast development
- simple REST API
- excellent integration with frontend development

Alternative:

Go API if a single-language backend is preferred later.

For MVP, collector and API should be separate services.

## Frontend

**React + TypeScript + Vite**

Use a clean network-operations-oriented UI.

Charts can use an established React visualization library.

## Reverse Proxy

**Nginx**

Responsibilities:

- HTTPS termination
- frontend delivery
- API proxying

## Development Deployment

**Docker Compose**

Services:

```text
collector
api
frontend
clickhouse
nginx
```

Do not introduce Redis, Kafka or a message bus in MVP unless load testing proves that it is required.

---

# 7. Suggested Repository Structure

```text
sflow-analytics/
│
├── README.md
├── docker-compose.yml
├── .env.example
│
├── collector/
│   ├── cmd/
│   ├── internal/
│   │   ├── decoder/
│   │   ├── normalize/
│   │   ├── storage/
│   │   ├── metrics/
│   │   └── config/
│   ├── tests/
│   ├── Dockerfile
│   └── go.mod
│
├── api/
│   ├── app/
│   │   ├── main.py
│   │   ├── routes/
│   │   ├── models/
│   │   ├── services/
│   │   └── config/
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── api/
│   │   ├── hooks/
│   │   └── types/
│   ├── package.json
│   └── Dockerfile
│
├── db/
│   ├── migrations/
│   ├── schema/
│   └── init/
│
├── nginx/
│   ├── nginx.conf
│   └── certificates/
│
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── sflow.md
│   └── deployment.md
│
└── scripts/
    ├── dev-up.sh
    ├── dev-down.sh
    ├── backup.sh
    └── generate-test-traffic.sh
```

---

# 8. sFlow Collector Requirements

## Listener

Default:

```text
UDP/6343
```

Must be configurable using environment variables.

Example:

```env
SFLOW_LISTEN_ADDRESS=0.0.0.0
SFLOW_PORT=6343
```

## Supported Version

Initial:

```text
sFlow v5
```

Unsupported versions should:

- not crash the collector
- increment an error counter
- optionally log the exporter and version

## Exporter Identification

Each exporter must be identified by:

- source IP of the UDP datagram
- sFlow agent address
- agent sub-ID where applicable

The collector must automatically create an exporter record when a new exporter is seen.

## Collector Metrics

Track:

- datagrams received
- datagrams/sec
- samples received
- samples/sec
- flow samples
- counter samples
- malformed datagrams
- unsupported records
- database insert failures
- active exporters
- last packet timestamp per exporter

---

# 9. Flow Normalization

The collector should convert decoded sFlow records into an internal normalized record.

Suggested structure:

```json
{
  "timestamp": "2026-09-28T14:20:31.123Z",
  "exporter_ip": "10.0.0.10",
  "agent_ip": "10.0.0.10",
  "agent_sub_id": 0,

  "input_ifindex": 24,
  "output_ifindex": 48,

  "src_mac": "00:11:22:33:44:55",
  "dst_mac": "00:aa:bb:cc:dd:ee",

  "src_ip": "10.20.30.42",
  "dst_ip": "10.40.50.20",

  "ip_protocol": 6,
  "src_port": 53214,
  "dst_port": 443,

  "vlan": 120,

  "sampled_packet_size": 1514,
  "sampling_rate": 1024,
  "estimated_bytes": 1550336,
  "estimated_packets": 1024
}
```

Not every record will contain every field.

Nullable values must be supported.

---

# 10. Traffic Estimation

sFlow is sampled telemetry.

The application must never present sampled observations as exact packet counts.

Estimated traffic should be calculated from:

```text
sampled packet size × sampling rate
```

or the most appropriate information available in the sFlow sample.

The UI should internally treat this as **estimated traffic volume**.

Avoid misleading wording such as "exact bytes transferred".

---

# 11. ClickHouse Data Model

## Main Flow Table

Suggested table:

```text
flow_records
```

Suggested columns:

```text
timestamp
exporter_ip
agent_ip
agent_sub_id

input_ifindex
output_ifindex

src_mac
dst_mac

src_ip
dst_ip

ip_protocol
src_port
dst_port

vlan

sampled_packet_size
sampling_rate
estimated_bytes
estimated_packets
```

Recommended partitioning:

```text
PARTITION BY toDate(timestamp)
```

Recommended ordering concept:

```text
ORDER BY (
  timestamp,
  exporter_ip,
  src_ip,
  dst_ip
)
```

Final ClickHouse tuning should be validated with generated traffic.

---

# 12. Aggregation Strategy

Do not perform every UI query directly against millions of raw records if avoidable.

Add materialized or aggregated views for common analytics.

Potential aggregation windows:

- 1 minute
- 5 minutes
- 1 hour

Possible dimensions:

```text
timestamp bucket
exporter
input interface
output interface
source IP
destination IP
protocol
service
VLAN
```

Metrics:

```text
estimated bytes
estimated packets
sample count
```

MVP can start with raw queries and introduce materialized views once baseline functionality is working.

---

# 13. Retention

Retention must be configurable.

Default MVP:

```text
7 days
```

Possible presets:

- 1 day
- 7 days
- 14 days
- 30 days

Use ClickHouse TTL where practical.

The application must expose:

- total disk space
- database size
- estimated retention
- oldest available flow record

The application must never allow telemetry to silently consume the complete disk.

---

# 14. Service / Application Identification

MVP application identification is intentionally simple.

Use protocol + known destination/source ports.

Examples:

```text
TCP/80     HTTP
TCP/443    HTTPS
TCP/22     SSH
TCP/25     SMTP
TCP/587    SMTP Submission
TCP/993    IMAPS
UDP/53     DNS
TCP/53     DNS
UDP/67-68  DHCP
UDP/123    NTP
TCP/445    SMB
TCP/3389   RDP
```

Do NOT call this DPI.

UI terminology should preferably use:

```text
Service
```

rather than claiming full application identification.

A service mapping table should be editable later.

---

# 15. Exporter Inventory

Create an exporter/device inventory.

Fields:

```text
id
display_name
exporter_ip
agent_ip
agent_sub_id
first_seen
last_seen
status
sample_rate
samples_per_second
notes
```

Status:

```text
Active
Inactive
```

Default inactive timeout:

```text
5 minutes
```

The timeout should be configurable.

---

# 16. Manual Device Naming

The user should be able to rename an automatically discovered exporter.

Example:

```text
10.10.10.12
```

becomes:

```text
ICX8200-Core-01
```

Keep both:

- discovered IP
- user-defined display name

The user-defined name must survive service restarts and upgrades.

---

# 17. Interface Mapping

sFlow provides interface indexes.

The application must initially support:

```text
ifIndex 24
ifIndex 48
```

The user must be able to manually assign friendly names if no enrichment is available.

Example:

```text
24 → 1/1/24
48 → 1/1/48
```

Future enrichment can use SNMP, but SNMP is not mandatory for the first functional collector.

Interface data model:

```text
exporter_id
ifindex
name
description
first_seen
last_seen
```

---

# 18. Optional SNMP Enrichment – Post Core MVP

Once basic sFlow collection is stable, add optional SNMP enrichment.

Initial support:

```text
SNMPv2c
```

Use SNMP to retrieve:

- sysName
- sysDescr
- sysObjectID
- ifName
- ifAlias
- ifSpeed / ifHighSpeed
- ifOperStatus

sFlow must continue working when SNMP is:

- disabled
- unreachable
- misconfigured

SNMP is enrichment only.

It must never be a dependency for flow collection.

---

# 19. API Requirements

Base path:

```text
/api/v1
```

Suggested endpoints:

## Health

```text
GET /api/v1/health
GET /api/v1/collector/status
GET /api/v1/system/status
```

## Exporters

```text
GET    /api/v1/exporters
GET    /api/v1/exporters/{id}
PATCH  /api/v1/exporters/{id}
```

## Interfaces

```text
GET /api/v1/interfaces
GET /api/v1/interfaces/{id}
```

## Traffic

```text
GET /api/v1/traffic/timeseries
GET /api/v1/traffic/top-sources
GET /api/v1/traffic/top-destinations
GET /api/v1/traffic/top-conversations
GET /api/v1/traffic/top-services
GET /api/v1/traffic/top-protocols
GET /api/v1/traffic/top-vlans
GET /api/v1/traffic/top-interfaces
GET /api/v1/traffic/flows
```

All analytics endpoints must support appropriate filters.

Example:

```text
?from=
&to=
&exporter=
&src_ip=
&dst_ip=
&protocol=
&port=
&vlan=
&input_ifindex=
&output_ifindex=
&limit=
```

---

# 20. Dashboard

The default dashboard should provide immediate network visibility.

## Header Metrics

Display:

- Current estimated traffic
- sFlow samples/sec
- Active exporters
- Active conversations
- Database size

## Traffic Timeline

Default:

```text
Last 1 hour
```

Selectable:

- 5 min
- 15 min
- 1 hour
- 6 hours
- 24 hours
- 7 days
- custom

## Top Sources

Show:

```text
Source IP
Estimated traffic
Percentage
```

## Top Destinations

Show:

```text
Destination IP
Estimated traffic
Percentage
```

## Top Conversations

Show:

```text
Source
Destination
Service
Estimated traffic
```

## Top Services

Show:

```text
HTTPS
DNS
SMB
SSH
Unknown
...
```

## Top Interfaces

Show:

```text
Exporter
Interface
Estimated traffic
```

---

# 21. Traffic Explorer

This is the core troubleshooting screen.

Layout idea:

```text
+----------------------------------------------------+
| Time Range | Exporter | VLAN | Protocol | Service |
+----------------------------------------------------+
| Source IP                                          |
| Destination IP                                     |
| Source Port                                        |
| Destination Port                                   |
| Input Interface                                    |
| Output Interface                                   |
+----------------------------------------------------+

Traffic Timeline

Top Sources     Top Destinations     Top Services

Flow / Conversation Table
```

Filters must update the complete page.

Filters should also be reflected in the URL so that views can be bookmarked.

---

# 22. Conversation Model

A conversation is defined by a 5-tuple:

```text
source IP
destination IP
source port
destination port
protocol
```

For analytics, also retain:

- exporter
- input interface
- output interface
- VLAN

Display:

```text
Source
Destination
Service
First Seen
Last Seen
Estimated Traffic
Exporter
Input Interface
Output Interface
VLAN
```

---

# 23. Device Page

Example:

```text
ICX8200-Core-01

Management / Exporter IP: 10.10.10.12
Status: Active
Last sFlow packet: 2 sec ago
Samples/sec: 1,420
First seen: ...
```

Tabs:

```text
Overview
Traffic
Interfaces
Collector Stats
Settings
```

Overview:

- total traffic
- traffic history
- top conversations
- top sources
- top destinations
- top services

---

# 24. Interface Detail Page

Example:

```text
ICX8200-Core-01 / 1/1/48
```

Display:

- IfIndex
- Friendly name
- Description
- Current estimated traffic
- traffic history
- top sources
- top destinations
- top conversations
- top services
- VLANs observed

This view should be optimized for troubleshooting.

---

# 25. Collector Health Page

Show:

```text
Collector Status: Running

Listening:
0.0.0.0:6343/UDP

Datagrams:
1,284,223

Datagrams/sec:
2,314

Samples/sec:
8,510

Malformed:
12

DB Insert Errors:
0

Active Exporters:
14
```

Per exporter:

```text
Exporter
Last Seen
Samples/sec
Datagrams
Errors
```

---

# 26. Administration

MVP Administration pages:

## General

- application name
- timezone
- retention

## Collector

- listen address
- UDP port
- inactivity timeout

## Devices

- rename exporters
- interface mappings

## Storage

- database size
- disk usage
- oldest record
- retention

## Diagnostics

- application version
- service status
- download logs
- collector counters

---

# 27. Authentication

For the local MVP:

- local administrator account
- password hashing using a modern password hashing algorithm
- secure session cookie
- logout
- session timeout

Do not store passwords in clear text.

For local development only, authentication may initially be disabled behind a configuration flag.

Before appliance packaging, authentication must be enabled by default.

---

# 28. Configuration

Configuration should use environment variables and a persistent application configuration database/file.

Example:

```env
APP_NAME=sFlow Analytics
APP_TIMEZONE=Europe/Paris

SFLOW_LISTEN_ADDRESS=0.0.0.0
SFLOW_PORT=6343

CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=9000
CLICKHOUSE_DATABASE=sflow

RETENTION_DAYS=7

ADMIN_USERNAME=admin
```

Never commit secrets to Git.

Provide:

```text
.env.example
```

---

# 29. Logging

All services should produce structured logs.

Include:

```text
timestamp
service
severity
message
exporter_ip when relevant
```

Collector logging must avoid one log entry per packet under normal operation.

Use counters/metrics for high-frequency events.

---

# 30. Error Handling

The collector must never crash because of:

- malformed sFlow packets
- unknown record types
- unsupported address families
- database temporary unavailability

Unknown records should be skipped safely.

Database errors should:

- be logged
- increment metrics
- not permanently kill the collector

For MVP, a small in-memory retry queue is acceptable.

Do not introduce a distributed message broker initially.

---

# 31. Performance

The architecture must support continuous ingestion.

Initial test targets should include:

```text
10 exporters
50 exporters
100 exporters
```

Measure:

- UDP datagrams/sec
- flow samples/sec
- CPU usage
- memory usage
- ClickHouse insert rate
- disk growth
- API query latency

The code should batch ClickHouse inserts.

Do not insert one database row per network round trip.

Suggested initial batching:

```text
500–5000 records
or
flush every 1 second
```

Tune through testing.

---

# 32. Test Data

Development must not depend on having a physical ICX switch available at all times.

Provide a testing mechanism that can:

- replay saved sFlow datagrams
- generate synthetic sFlow traffic
- test multiple fake exporters

Add sample captures or generated fixtures under:

```text
collector/tests/fixtures/
```

Do not include customer-sensitive traffic captures.

---

# 33. Unit Tests

Collector tests should cover:

- valid sFlow v5 datagram
- multiple samples in one datagram
- IPv4 flow
- IPv6 flow where supported
- TCP traffic
- UDP traffic
- VLAN extraction
- interface index extraction
- malformed packet
- unsupported record
- sampling calculation
- exporter discovery

API tests should cover:

- time filtering
- IP filtering
- VLAN filtering
- protocol filtering
- Top-N queries
- empty datasets
- invalid parameters

---

# 34. Integration Tests

Create an integration test that:

1. starts ClickHouse
2. starts collector
3. sends a sample sFlow datagram
4. verifies that the normalized record is stored
5. queries it through the API
6. verifies the expected response

This test should be runnable locally and in CI.

---

# 35. Docker Compose

Development should start with:

```bash
docker compose up -d
```

Required services:

```text
clickhouse
collector
api
frontend
nginx
```

Persistent volumes:

```text
clickhouse-data
app-config
```

Expose:

```text
443/tcp
6343/udp
```

Development may expose additional ports directly.

Production should expose only required ports.

---

# 36. MVP User Stories

## US-001 – Receive sFlow

As a network administrator, I want an ICX switch to send sFlow to the application so that I can see its traffic.

Acceptance:

- UDP/6343 receives packets
- exporter appears automatically
- traffic records are stored
- dashboard updates

## US-002 – Find a Top Talker

As a network engineer, I want to see which source is consuming the most bandwidth.

Acceptance:

- Top Sources view exists
- selectable time range
- estimated traffic displayed
- clicking source filters Traffic Explorer

## US-003 – Investigate a Conversation

As a network engineer, I want to identify which source/destination pair caused a traffic spike.

Acceptance:

- Top Conversations view exists
- displays source/destination/service
- displays estimated traffic
- clicking opens filtered details

## US-004 – Investigate a Switch

As a network engineer, I want to see the traffic observed by a specific exporter.

Acceptance:

- exporter page exists
- traffic timeline
- Top Sources
- Top Destinations
- Top Conversations
- Top Interfaces

## US-005 – Investigate an Interface

As a network engineer, I want to understand what traffic is traversing a specific ICX interface.

Acceptance:

- filter by input/output ifIndex
- traffic timeline
- Top Sources
- Top Destinations
- Top Services
- Top Conversations

## US-006 – Historical Investigation

As a network engineer, I want to inspect what happened during a previous incident.

Acceptance:

- custom date/time range
- all Top-N analytics respect selected range
- flow table respects selected range

---

# 37. UI Design Principles

The interface should look like a modern network operations platform.

Priorities:

1. clarity
2. density of useful information
3. fast filtering
4. troubleshooting workflow
5. minimal unnecessary animation

Avoid:

- oversized marketing-style cards
- excessive gradients
- decorative visual noise
- charts without clear operational value

Tables should be readable and sortable.

Use responsive design, but desktop is the priority.

---

# 38. MVP Development Phases

## Phase 1 – Collector Proof of Concept

Build:

- UDP listener
- sFlow v5 decoding
- console output
- exporter discovery
- unit tests

Success:

An ICX can send sFlow and decoded records are visible.

## Phase 2 – Persistence

Build:

- ClickHouse
- normalized flow schema
- batched inserts
- basic retention
- test traffic generator

Success:

Flow records are stored and can be queried.

## Phase 3 – API

Build:

- health
- exporters
- flow search
- Top Sources
- Top Destinations
- Top Conversations
- timeline

Success:

Traffic data can be queried through REST.

## Phase 4 – Initial UI

Build:

- Dashboard
- Traffic Explorer
- Devices
- basic filters

Success:

The application can be used without CLI/database access.

## Phase 5 – Interface Analytics

Build:

- interface mapping
- interface page
- Top Interfaces
- ingress/egress filtering

## Phase 6 – Appliance Operations

Build:

- authentication
- retention configuration
- collector health
- disk monitoring
- diagnostics
- persistent configuration

## Phase 7 – OVA Packaging

Only after the Docker implementation is stable.

Create a minimal Linux VM containing:

- Docker / container runtime
- application stack
- persistent data volumes
- first-boot network configuration
- local console status page

Target appliance concept:

```text
4 vCPU
8 GB RAM
100 GB disk
1 NIC
```

Final sizing must be based on performance testing.

---

# 39. Definition of MVP Complete

The MVP is complete when the following scenario works end-to-end:

1. Start the application using Docker Compose.
2. Configure an ICX switch to send sFlow to the collector IP.
3. The switch automatically appears as an exporter.
4. Traffic appears on the dashboard.
5. The user can identify the Top Source.
6. The user can identify the Top Destination.
7. The user can identify a Top Conversation.
8. The user can see protocol/service information.
9. The user can identify ingress/egress interface indexes.
10. The user can filter by switch, VLAN, interface, IP and time.
11. Historical traffic can be queried.
12. Collector health is visible.
13. The system survives malformed sFlow datagrams.
14. Data retention prevents uncontrolled disk growth.
15. The complete stack restarts cleanly without losing persistent data.

---

# 40. Future Features

After the MVP is stable, consider:

- SNMP enrichment
- automatic ICX hostname/model discovery
- automatic ifIndex → interface name mapping
- DNS resolution
- IPv6 improvements
- GeoIP / ASN enrichment
- configurable service definitions
- saved searches
- bookmarked investigations
- alerts
- anomaly detection
- baselining
- traffic growth analysis
- capacity planning
- natural-language / AI investigation agent
- Ruckus Edge integration proof-of-concept
- Ruckus One telemetry export proof-of-concept

---

# 41. Future AI Use Cases

The data model should remain compatible with future AI-driven troubleshooting.

Examples:

```text
Why was ICX8200-01 uplink 1/1/48 congested between 10:30 and 10:45?
```

Potential answer:

```text
The interface reached 95% utilization.
68% of the observed traffic was associated with a conversation
between 10.20.30.42 and 10.40.50.20 over HTTPS.
```

Another example:

```text
What changed on VLAN 120?
```

Potential answer:

```text
Traffic is 4.3 times higher than the normal level for this time period.
The increase is mainly associated with three source endpoints
communicating with the same destination.
```

Capacity example:

```text
Which uplinks have shown the strongest traffic growth during the last 30 days?
```

The first MVP does not need to implement AI, but the API and data model should make these questions possible later.

---

# 42. Claude Code Implementation Rules

Claude Code should follow these rules while implementing the project:

1. Build incrementally.
2. Do not over-engineer the MVP.
3. Prefer simple services over distributed architecture.
4. Do not introduce Kafka, Redis or Kubernetes without a demonstrated need.
5. Keep collector, API and frontend independently testable.
6. Add tests with every core feature.
7. Never allow malformed telemetry to crash the collector.
8. Batch database inserts.
9. Treat sFlow traffic values as estimates.
10. Keep configuration externalized.
11. Keep all persistent data outside containers.
12. Update this document if the implemented architecture materially changes.
13. Maintain a `TODO.md` for deferred work.
14. Maintain a `CHANGELOG.md`.
15. Ensure `docker compose up -d` is sufficient to start a development environment.
16. Avoid mock UI data once the collector/API path is functional.
17. Every dashboard number must come from the API.
18. Every API metric must come from actual stored or collector data.
19. Favor working vertical slices over large unfinished subsystems.
20. Do not start OVA packaging until the Docker-based MVP works reliably.

---

# 43. First Task for Claude Code

Start with **Phase 1 only**.

Create the repository structure and implement:

- Docker-compatible Go collector
- UDP listener on port 6343
- sFlow v5 decoding
- basic normalized flow structure
- exporter discovery
- collector counters
- structured logs
- unit tests
- synthetic/replay test capability

For the first milestone, decoded samples may be written to structured logs.

Do **not** build the frontend yet.

Do **not** build ClickHouse integration until the collector can reliably decode test sFlow traffic.

The first deliverable should demonstrate:

```text
ICX / synthetic sFlow sender
          │
          ▼
      UDP/6343
          │
          ▼
     Go Collector
          │
          ▼
Decoded + normalized record
          │
          ▼
Structured log output
```

Once Phase 1 is stable, proceed to Phase 2 and add ClickHouse persistence.
