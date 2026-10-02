"""Controller sync written to ClickHouse and used by the inventory (needs ClickHouse)."""

import pytest

from app.enrichment import sync

from .conftest import EXP_A, EXP_B, exp_id

pytestmark = pytest.mark.clickhouse


class FakeClient:
    def switches(self):
        return [
            {"serialNumber": "FEK-A", "name": "icx-core", "model": "ICX8200-24", "firmware": "10.0.20",
             "ipAddress": "10.0.0.1", "switchMac": "AA:00:00:00:00:01", "venueName": "HQ", "deviceStatus": "ONLINE"},
            {"serialNumber": "FEK-B", "name": "icx-access", "model": "ICX7150-C12P", "firmware": "10.0.20",
             "ipAddress": "10.0.0.2", "switchMac": "AA:00:00:00:00:02", "venueName": "HQ", "deviceStatus": "ONLINE"},
        ]

    def ports(self):
        return [
            {"portIdentifier": "1/1/48", "switchSerial": "FEK-A", "name": "Uplink", "portSpeed": "10 Gb/sec",
             "status": "Up", "neighborName": "core-router", "neighborMacAddress": "BB:00:00:00:00:01"},
            # sFlow says ifIndex 1 is 1 Gb/s: speed mismatch -> uncertain mapping
            {"portIdentifier": "1/1/1", "switchSerial": "FEK-A", "name": "Printer", "portSpeed": "10 Gb/sec"},
            {"portIdentifier": "1/1/24", "switchSerial": "FEK-B", "name": "Server", "portSpeed": "1 Gb/sec"},
        ]

    def clients(self):
        return [{"clientMac": "00:11:22:33:44:55", "clientIpv4Addr": "10.1.1.10", "dhcpClientHostName": "nas-01",
                 "switchSerialNumber": "FEK-A", "switchPort": "1/1/5", "clientVlan": "10"}]

    def close(self):
        pass


def test_sync_enriches_inventory(client, testdb, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "config_dir", str(tmp_path))
    sync.save_config({"source": "ruckusone"})
    r = sync.run_sync(testdb, client_factory=FakeClient)
    assert r["ok"] and r["switches"] == 2 and r["ports"] == 3 and r["clients"] == 1 and r["lldp_neighbors"] == 1

    exps = {e["id"]: e for e in client.get("/api/v1/exporters").json()["items"]}
    b = exps[exp_id(EXP_B)]
    assert b["name"] == "icx-access" and b["controller"]["model"] == "ICX7150-C12P"
    a = exps[exp_id(EXP_A)]
    assert a["controller"]["serial"] == "FEK-A"
    assert a["name"] in ("ICX8200-Core-01", "icx-core")  # a manual name (earlier test) wins

    ifs = {i["ifindex"]: i for i in client.get("/api/v1/interfaces", params={"exporter": exp_id(EXP_A)}).json()["items"]}
    up = ifs[48]["controller_port"]
    assert up["port_id"] == "1/1/48" and up["lldp_neighbor"] == "core-router" and up["mapping_uncertain"] is False
    assert ifs[1]["controller_port"]["mapping_uncertain"] is True and ifs[1]["label"] == "ifIndex 1"
    b24 = client.get("/api/v1/interfaces", params={"exporter": exp_id(EXP_B)}).json()["items"][0]
    assert b24["label"] == "1/1/24 Server" and b24["description"] == "Server"


def test_host_names_from_controller_clients(client, tmp_path, monkeypatch):
    # FakeClient (previous test) wrote 10.1.1.10 = nas-01; names only come from the selected source.
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "config_dir", str(tmp_path))
    assert client.get("/api/v1/traffic/top-sources", params={"from": "2026-01-01T10:00:00Z", "to": "2026-01-01T11:00:00Z"}).json()["items"][0].get("name") is None
    sync.save_config({"source": "ruckusone"})
    items = client.get("/api/v1/traffic/top-sources", params={"from": "2026-01-01T10:00:00Z", "to": "2026-01-01T11:00:00Z", "limit": 20}).json()["items"]
    named = {i["ip"]: i["name"] for i in items}
    assert named["10.1.1.10"] == "nas-01" and named.get("10.1.1.11") is None
    conv = client.get("/api/v1/traffic/top-conversations", params={"from": "2026-01-01T10:00:00Z", "to": "2026-01-01T11:00:00Z", "limit": 20}).json()["items"]
    assert any(c["src_name"] == "nas-01" for c in conv)
    sk = client.get("/api/v1/traffic/sankey", params={"from": "2026-01-01T10:00:00Z", "to": "2026-01-01T11:00:00Z"}).json()
    assert any(n["label"] == "nas-01 (10.1.1.10)" for n in sk["nodes"])
