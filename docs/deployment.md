# Deployment

## Development server

Any Linux host with Docker Engine and the Compose plugin (tested on Ubuntu Server
24.04 LTS, 4 vCPU / 8 GB / 100 GB). Go, Python and Node are not required on the
host: builds and tests run in containers.

Recommended kernel setting for the UDP receive buffer:

```bash
echo "net.core.rmem_max=33554432" | sudo tee /etc/sysctl.d/99-sflow-collector.conf
sudo sysctl --system
```

Site-specific notes (addresses, access) belong in `docs/local/`, which is not versioned.

## Running

```bash
cd ~/sflow-analytics
scripts/dev-up.sh            # creates .env and a random CLICKHOUSE_PASSWORD on first run
scripts/integration-test.sh  # collector <-> ClickHouse tests (Docker only)
scripts/api-test.sh          # API tests (Docker only)
```

`docker compose up -d` works once `.env` exists.

Persistent data (`docker compose down` keeps them, `down -v` deletes them):

| Volume            | Content                                                          |
|-------------------|------------------------------------------------------------------|
| `clickhouse-data` | flows, counters, exporter inventory, device/interface names      |
| `app-config`      | users, sessions, settings, encrypted secrets and their key, jobs |
| `nginx-certs`     | TLS certificate and key                                          |

Back up `app-config` together with `clickhouse-data`: without its `secret.key`,
stored controller credentials cannot be decrypted.

### Backup and restore

`scripts/backup.sh [dir]` writes one archive (default `./backups/`) with every ClickHouse
table (schema + data in Native format), the `app-config` and `nginx-certs` volumes and
`.env`. It contains secrets: keep it private and copy it off the host.

Restore on a fresh host (same version):

```bash
mkdir r && tar xzf sflow-backup-<stamp>.tar.gz -C r
cp r/env .env
docker volume create sflow-analytics_app-config
docker run --rm -v sflow-analytics_app-config:/v -v "$PWD/r:/b" alpine tar xzf /b/app-config.tar.gz -C /v
docker volume create sflow-analytics_nginx-certs
docker run --rm -v sflow-analytics_nginx-certs:/v -v "$PWD/r:/b" alpine tar xzf /b/nginx-certs.tar.gz -C /v
docker compose up -d                       # the collector recreates the schema
set -a; . ./.env; set +a
for f in r/clickhouse/*.native.gz; do t=$(basename "$f" .native.gz)
  gunzip -c "$f" | docker compose exec -T clickhouse clickhouse-client --user "$CLICKHOUSE_USER"     --password "$CLICKHOUSE_PASSWORD" --query "INSERT INTO sflow.$t FORMAT Native"; done
```

User administration: page **System → Users & sign-ins** (administrators), or the CLI
`docker compose exec api python -m app.users list|create [--role viewer]|set-role|reset-password|delete`.
Roles: `admin` (everything) and `viewer` (read-only: every analytics page, no change, no
Administration). Every sign-in attempt (user name, address, result) is logged and kept 180 days.

Exposure to the Internet: forward only TCP 443 (or another external port) to the appliance,
never 6343, 8000, 8081, 8123, 9000 or 22. Give each person their own account (read-only unless
they administer the appliance), watch the sign-in log, and prefer a VPN or an IP allow-list
when possible. nginx overwrites `X-Forwarded-For` with the real client address, so login
throttling and the sign-in log cannot be fooled by a forged header.

### Retention and growing the disk

`RETENTION_DAYS` (default 90) in `.env` sets how long flows and raw counters are kept; change it
and run `docker compose up -d collector api` (existing data is not rewritten; shortening it removes
the expired days at the next merge). Sizing: docs/architecture.md "Storage sizing". The disk guard
drops the oldest days early when the disk is too small, so a full disk never stops the appliance.

### Data on a second disk (recommended)

Keep the operating system and the data apart: a full data disk cannot break Ubuntu, and the data
disk can be grown, snapshotted or moved on its own. With a second, empty VM disk (e.g. `/dev/sdb`,
formatted whole, no partition table):

```bash
sudo mkfs.ext4 -L sflow-data /dev/sdb
echo "UUID=$(sudo blkid -s UUID -o value /dev/sdb) /data ext4 defaults,noatime,nofail 0 2" | sudo tee -a /etc/fstab
sudo mkdir -p /data && sudo mount /data
sudo rsync -aHAX --numeric-ids /var/lib/docker/ /data/docker/          # live pre-copy, app running
sudo systemctl stop docker.socket docker                              # short downtime starts
sudo rsync -aHAX --numeric-ids --delete /var/lib/docker/ /data/docker/
sudo mv /var/lib/docker /var/lib/docker.old                            # rollback copy; delete later
echo '{ "data-root": "/data/docker" }' | sudo tee /etc/docker/daemon.json
sudo mkdir -p /etc/systemd/system/docker.service.d
printf '[Unit]
RequiresMountsFor=/data
' | sudo tee /etc/systemd/system/docker.service.d/10-data-disk.conf
sudo systemctl daemon-reload && sudo systemctl start docker           # containers restart by themselves
```

Measured on the lab appliance: 17 s of downtime. Docker volumes (ClickHouse, configuration,
certificate) then live on `/data`; images stay where containerd keeps them.

### Growing the disk

Take a snapshot or a backup (`scripts/backup.sh`) first, then increase the disk size in
vSphere / ESXi (possible with the VM running) and on the VM:

```bash
# data disk (/data on /dev/sdb, no partition): online, no reboot
echo 1 | sudo tee /sys/class/block/sdb/device/rescan
sudo resize2fs /dev/sdb

# system disk (/ on /dev/sda1)
echo 1 | sudo tee /sys/class/block/sda/device/rescan
sudo growpart /dev/sda 1 && sudo resize2fs /dev/sda1
```

The disk guard and the "achievable retention" shown in Collector Health adapt at once.

## Ports

| Port        | Exposure            | Purpose                        |
|-------------|---------------------|--------------------------------|
| 6343/udp    | network             | sFlow from the switches        |
| 8000/tcp    | 127.0.0.1 (dev)     | REST API (`/api/v1/docs`)      |
| 8081/tcp    | 127.0.0.1 (dev)     | collector status               |
| 9000, 8123  | 127.0.0.1 (dev)     | ClickHouse native / HTTP       |
| 443/tcp     | network (Phase 4)   | web UI and API through nginx   |

## Appliance (OVA)

`appliance/build-ova.sh dist/sflow-analytics-<v>.tar.gz` (normal user; needs Docker,
libguestfs-tools and qemu-utils) builds `sflow-analytics-<v>.ova` (~1 GB) from the official
Ubuntu 24.04 cloud image: 4 vCPU, 8 GB, PVSCSI, VMXNET3, BIOS, hardware version 14 (ESXi 6.7+),
system disk 30 GB and data disk 100 GB (thin). Packages are downloaded in a throw-away
container and installed offline; the application images are bundled.

At first boot, the console (tty1) runs `sflow-setup`: network (DHCP or static), host name, time
zone, NTP, password of the system account `sflow` (no default password is shipped), then it
grows the system disk, formats the data disk (first empty disk of 10 GB or more → `/data`,
Docker data-root), installs the application and shows the URL and the initial admin password.
`/etc/issue` shows the address at every boot. Console menu: log in as `sflow`, `sudo sflow-console`
(status, network change, initial/reset admin password, restart, reboot). Unattended setup:
`/etc/sflow/setup.conf` (template in that directory).

Test note: in a TCG-only emulator (no hardware virtualization) ClickHouse refuses to start
(needs SSSE3; use `-cpu max`); test the OVA on ESXi / Workstation.

