# sFlow Analytics — quick start

sFlow collector and traffic analytics for RUCKUS ICX (and any sFlow v5 switch):
top talkers, applications, conversations, flow maps, port utilization, trends,
broadcast storms, alerts. Internal beta — not for redistribution.

## 1. Requirements
- A VM or server with **Ubuntu 22.04 / 24.04** (or Debian 12), x86_64, with Internet access
  for the first install only if Docker is missing.
- **2 vCPU, 4 GB RAM** minimum (4 vCPU, 8 GB recommended), **50 GB disk** or more.
- Ports: **443/tcp** (web), **6343/udp** (sFlow from the switches).

## 2. Install (one command)
```bash
tar xzf sflow-analytics-*.tar.gz
cd sflow-analytics-*/
sudo ./install.sh
```
At the end the script shows the **URL**. First sign-in: user **admin**, password **sflow**;
a new password is required immediately. Sign in right after the installation, before
exposing the appliance (e.g. NAT from the Internet). Your browser warns about the self-signed
certificate: accept it.

No switch at hand? `sudo ./install.sh --demo` also starts 5 synthetic switches.

## 3. Send sFlow from a RUCKUS ICX
```
sflow destination <appliance-IP> 6343
sflow sample 512
sflow polling-interval 20
sflow enable
interface ethernet 1/1/1 to 1/1/48
 sflow forwarding
```
`sflow enable` is required. Enable `sflow forwarding` on every port to watch (access ports
and uplinks). Data appears in the web interface within a minute.

## 4. Optional
- **Administration → Enrichment**: RUCKUS One or SmartZone (read-only API account) for switch
  names, port names, LLDP neighbors and client names.
- **Users & sign-ins**: one account per person; "Read-only" for viewers.
- **Alerts**: default rules are active; set e-mail / Teams / syslog in Administration → Notifications.

## 5. Everyday commands (in /opt/sflow-analytics)
| Task | Command |
|---|---|
| Status | `sudo docker compose ps` |
| Logs | `sudo docker compose logs -f collector` |
| Restart | `sudo docker compose restart` |
| Backup | `sudo ./backup.sh` (archive with data, users, settings, certificate) |
| Upgrade | extract the new package, `sudo ./install.sh` (data kept) |
| Change retention, ports... | edit `.env`, then `sudo docker compose up -d` |
| Uninstall | `sudo ./uninstall.sh` (data kept) / `sudo ./uninstall.sh --purge` |
| Forgotten admin password | `sudo docker compose exec api python -m app.users reset-password admin` |

## 6. Disk sizing
Detailed data is kept 90 days (`RETENTION_DAYS`); an hourly history per interface is kept
3 years. Roughly **25 bytes per sFlow sample**: 100 samples/s ≈ 19 GB for 90 days,
1 000 samples/s ≈ 194 GB. When the disk fills up, the oldest days are dropped automatically.
Grow the VM disk at any time (then `sudo growpart /dev/sda 1 && sudo resize2fs /dev/sda1`, or reboot).

---

# Démarrage rapide (FR)

1. **Pré-requis** : VM Ubuntu 22.04/24.04, 2 vCPU, 4 Go RAM, 50 Go de disque ; ports 443/tcp et 6343/udp.
2. **Installation** : `tar xzf sflow-analytics-*.tar.gz && cd sflow-analytics-*/ && sudo ./install.sh`
   — le script affiche l'URL ; première connexion avec `admin` / `sflow`, un nouveau mot de passe est demandé aussitôt (connectez-vous avant d'exposer l'appliance).
   Sans switch : `sudo ./install.sh --demo` (5 switchs simulés).
3. **Switch ICX** : configuration du §3 ci-dessus (`sflow enable` est indispensable).
4. **Mise à jour** : extraire la nouvelle archive puis `sudo ./install.sh` (données conservées).
5. **Sauvegarde** : `sudo /opt/sflow-analytics/backup.sh`.

Beta interne — merci de ne pas diffuser en dehors de l'entreprise. Retours bienvenus !
