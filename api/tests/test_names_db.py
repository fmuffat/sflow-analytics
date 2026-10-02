"""Name resolution priority, aliases API, CSV import/export, DNS job (needs ClickHouse)."""

import pytest

from app import rdns

from .conftest import WINDOW

pytestmark = pytest.mark.clickhouse


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "config_dir", str(tmp_path))
    return tmp_path


def names_of(client, path="/api/v1/traffic/top-destinations"):
    items = client.get(path, params={**WINDOW, "limit": 50}).json()["items"]
    return {i["ip"]: i.get("name") for i in items}


def test_aliases_api_and_priority(client, testdb, cfg):
    # DNS knows 10.2.2.20 and 10.2.2.53; an IP alias overrides DNS for 10.2.2.20.
    fake = {"10.2.2.20": ("ok", "srv20.example.lan"), "10.2.2.53": ("ok", "dns1.example.lan")}
    r = rdns.run(testdb, resolver=lambda ip: fake.get(ip, ("nxdomain", "")))
    # Test data is dated 2026-01-01: nothing "seen in the last hour", so fill the cache directly.
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    testdb.insert("dns_cache", [[ip, n, s, now, now + timedelta(hours=1)] for ip, (s, n) in fake.items()],
                  ["ip", "name", "status", "resolved_at", "expires_at"])
    assert r["enabled"] is True
    assert names_of(client)["10.2.2.53"] == "dns1.example.lan"

    assert client.put("/api/v1/aliases", json={"kind": "ip", "key": "10.2.2.20", "name": "NAS"}).status_code == 200
    assert client.put("/api/v1/aliases", json={"kind": "cidr", "key": "10.4.4.0/24", "name": "Lab LAN"}).status_code == 200
    got = names_of(client)
    assert got["10.2.2.20"] == "NAS"          # alias beats DNS
    assert got["10.2.2.53"] == "dns1.example.lan"
    assert got["10.4.4.40"] == "Lab LAN"      # subnet fallback
    h = client.get("/api/v1/hosts/10.2.2.20").json()
    assert (h["ip"], h["name"], h["source"]) == ("10.2.2.20", "NAS", "alias")
    assert [h["ip"] for h in client.get("/api/v1/hosts", params={"q": "nas"}).json()["items"]] == ["10.2.2.20"]
    assert any(h["ip"] == "10.2.2.53" for h in client.get("/api/v1/hosts", params={"q": "dns1"}).json()["items"])

    # Delete: DNS name comes back.
    assert client.delete("/api/v1/aliases", params={"kind": "ip", "key": "10.2.2.20"}).status_code == 200
    assert names_of(client)["10.2.2.20"] == "srv20.example.lan"
    assert client.put("/api/v1/aliases", json={"kind": "ip", "key": "nope", "name": "x"}).status_code == 422


def test_mac_alias_follows_the_device(client, testdb, cfg):
    # Flows are older than 1 day, so the MAC comes from controller clients.
    from datetime import datetime, timezone
    testdb.insert("ctrl_clients", [["ruckusone", "00:11:22:33:44:55", "10.1.1.12", "", "", "", "", "", "", "",
                                    datetime.now(timezone.utc)]],
                  ["source", "mac", "ip", "ipv6", "name", "device_type", "vendor", "switch_serial", "port_id", "vlan", "synced_at"])
    client.put("/api/v1/aliases", json={"kind": "mac", "key": "00-11-22-33-44-55", "name": "Printer"})
    assert names_of(client, "/api/v1/traffic/top-sources")["10.1.1.12"] == "Printer"
    client.delete("/api/v1/aliases", params={"kind": "mac", "key": "00:11:22:33:44:55"})


def test_csv_import_export(client, cfg):
    text = "kind,key,name,notes\nip,10.3.3.30,SMB server,rack 2\nmac,zz,Bad\ncidr,10.9.0.0/16,Site B\nweird\n"
    r = client.post("/api/v1/aliases/import", content=text, headers={"Content-Type": "text/csv"})
    body = r.json()
    assert r.status_code == 200 and body["imported"] == 2 and body["error_count"] == 2
    exported = client.get("/api/v1/aliases/export").text
    assert "ip,10.3.3.30,SMB server,rack 2" in exported and "cidr,10.9.0.0/16,Site B" in exported
    assert names_of(client, "/api/v1/traffic/top-sources")["10.3.3.30"] == "SMB server"


def test_dns_settings_api(client, cfg):
    r = client.put("/api/v1/admin/dns", json={"enabled": False, "servers": ["192.168.1.1"]})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["servers"] == ["192.168.1.1"]
    assert client.post("/api/v1/admin/dns/run").json() == {"enabled": False}
    assert client.put("/api/v1/admin/dns", json={"servers": ["not-an-ip"]}).status_code == 422



def test_gateway_mac_does_not_name_internet_hosts(client, testdb, cfg):
    """A router MAC carries many IPs: an alias or vendor on it must not spread to them."""
    from datetime import datetime, timedelta, timezone
    import ipaddress

    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    cols = ["timestamp", "exporter_ip", "agent_ip", "agent_sub_id", "sample_sequence", "source_id_type", "source_id_index",
            "src_mac", "src_ip", "dst_ip", "sampled_packet_size", "sampling_rate", "estimated_bytes", "estimated_packets"]
    gw = ipaddress.IPv6Address("::ffff:192.0.2.1")
    rows = [[now, gw, gw, 0, i, 0, 1, "00:0c:29:00:00:01", ipaddress.IPv6Address(f"::ffff:198.51.100.{i}"),
             ipaddress.IPv6Address("::ffff:10.0.0.9"), 100, 1, 100, 1] for i in range(1, 30)]
    rows.append([now, gw, gw, 0, 99, 0, 1, "00:11:32:00:00:01", ipaddress.IPv6Address("::ffff:10.0.0.30"),
                 ipaddress.IPv6Address("::ffff:10.0.0.9"), 100, 1, 100, 1])
    testdb.insert("flow_records", rows, cols)
    client.put("/api/v1/aliases", json={"kind": "mac", "key": "00:0c:29:00:00:01", "name": "Router"})
    r = client.get("/api/v1/hosts/198.51.100.7").json()
    assert r["name"] is None and r["vendor"] is None      # behind the gateway MAC
    nas = client.get("/api/v1/hosts/10.0.0.30").json()
    assert nas["mac"] == "00:11:32:00:00:01" and nas["vendor"] == "Synology"
    assert client.get("/api/v1/mac/00-11-32-01-02-03").json()["vendor"] == "Synology"
    client.delete("/api/v1/aliases", params={"kind": "mac", "key": "00:0c:29:00:00:01"})
