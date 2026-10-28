"""SNMP polling: parsing of standard MIB data, poll into the controller tables, labels, settings."""

import pytest

from app import inventory, security
from app.enrichment import snmp

from .conftest import EXP_A, EXP_B, exp_id

UI = {"X-Requested-With": "sflow"}
DESCR = b"Ruckus Networks ICX7150-C12P Switch, Software Version 10.0.20aT213"


def raw(name=b"core-sw", aliases=None, lldp=True):
    r = {
        snmp.SYS_NAME: name, snmp.SYS_DESCR: DESCR,
        snmp.IF_NAME: {"1": b"1/1/1", "48": b"GigabitEthernet1/1/48", "65": b"1/2/1", "3073": b"lg1", "999": b"ve 10"},
        snmp.IF_ALIAS: aliases if aliases is not None else {"1": b"Uplink core", "48": b"", "65": b"Server"},
        snmp.IF_OPER: {"1": 1, "48": 2},
        snmp.ENT_SERIAL: {"2": b"", "1": b"FEK1234X0AB"},
        snmp.LLDP_REM: {},
    }
    if lldp:
        r[snmp.LLDP_REM] = {"9.0.1.1": b"distrib-sw", "4.0.1.1": 4, "5.0.1.1": bytes.fromhex("aabbccddeeff"),
                            "4.0.65.2": 7, "5.0.65.2": b"server-01"}
    return r


def test_parse():
    sw, ports = snmp.parse("10.0.0.9", raw())
    assert sw == {"serial": "FEK1234X0AB", "name": "core-sw", "model": "ICX7150-C12P", "firmware": "10.0.20aT213",
                  "ip": "10.0.0.9", "mac": "", "venue": "", "status": "reachable"}
    by = {p["ifindex"]: p for p in ports}
    assert by[1]["port_id"] == "1/1/1" and by[1]["name"] == "Uplink core" and by[1]["status"] == "Up"
    assert by[1]["lldp_name"] == "distrib-sw" and by[1]["lldp_mac"] == "aa:bb:cc:dd:ee:ff"
    assert by[48]["port_id"] == "1/1/48" and by[48]["name"] == "" and by[48]["status"] == "Down"
    assert by[65]["lldp_name"] == "server-01" and by[65]["lldp_mac"] == ""     # chassis id not a MAC
    assert by[3073]["port_id"] == "LAG1" and by[999]["port_id"] == "ve 10"
    sw, _ = snmp.parse("10.0.0.9", {**raw(), snmp.ENT_SERIAL: {}})
    assert sw["serial"] == "ip:10.0.0.9"


@pytest.fixture()
def snmp_on(testdb):
    keys = ("snmp.enabled", "snmp.version", "snmp.community", "snmp.last_poll", "enrichment.source")
    before = {k: security.get_setting(k) for k in keys}
    security.set_setting("enrichment.source", "none")
    snmp.save_config({"enabled": True, "version": "v2c", "community": "public-test"})
    yield
    for k, v in before.items():
        security.set_setting(k, v or "", secret=k == "snmp.community")
    testdb.command(f"DELETE FROM {testdb.name}.ctrl_ports WHERE source = 'snmp'")
    testdb.command(f"DELETE FROM {testdb.name}.ctrl_switches WHERE source = 'snmp'")


def test_settings_validation(snmp_on):
    with pytest.raises(snmp.SnmpError):
        snmp.save_config({"version": "v3"})       # enabled, no SNMPv3 user
    with pytest.raises(snmp.SnmpError):
        snmp.save_config({"v3": {"auth_key": "short"}})
    snmp.save_config({"version": "v3", "v3": {"username": "sflow", "auth_key": "auth-key-1", "priv_key": "priv-key-1",
                                              "auth_protocol": "SHA-256", "priv_protocol": "AES"}})
    cfg = snmp.get_config()
    assert cfg["version"] == "v3" and cfg["v3"]["auth_key_set"] and "auth_key" not in cfg["v3"]
    assert snmp.get_config(include_secret=True)["v3"]["priv_key"] == "priv-key-1"
    snmp.save_config({"version": "v2c"})


def test_poll_names_ports(testdb, snmp_on):
    a_ip = next(e["agent_ip"] for e in inventory.exporters(testdb) if e["id"] == exp_id(EXP_A))
    b_ip = next(e["agent_ip"] for e in inventory.exporters(testdb) if e["id"] == exp_id(EXP_B))
    assert set(snmp.targets(testdb)) == {a_ip, b_ip}
    calls = []

    def fetcher(cfg, ips, tables=snmp.TABLES):
        calls.append((cfg["community"], tuple(ips)))
        return {a_ip: raw(), b_ip: snmp.SnmpError("timeout")}

    r = snmp.run_poll(testdb, fetcher)
    assert r["ok"] and r["switches"] == 1 and r["targets"] == 2 and b_ip in r["errors"], r
    assert calls[0][0] == "public-test"
    e = inventory.exporter(testdb, exp_id(EXP_A))
    assert e["controller"]["name"] == "core-sw" and e["controller"]["source"] == "snmp" and e["controller"]["model"] == "ICX7150-C12P"
    ifs = {i["ifindex"]: i for i in inventory.interfaces(testdb, exp_id(EXP_A))}
    assert ifs[1]["label"] == "1/1/1 Uplink core" and ifs[1]["controller_port"]["lldp_neighbor"] == "distrib-sw"
    assert snmp.last_poll()["switches"] == 1
    # the switch is now known through SNMP only: it stays a target
    assert a_ip in snmp.targets(testdb)


def test_test_endpoint_and_disabled(testdb, snmp_on, client):
    a_ip = next(e["agent_ip"] for e in inventory.exporters(testdb) if e["id"] == exp_id(EXP_A))
    res = snmp.test(testdb, a_ip, lambda cfg, ips, tables: {a_ip: raw()})
    assert res["name"] == "core-sw" and res["interfaces"] == 5
    with pytest.raises(snmp.SnmpError):
        snmp.test(testdb, a_ip, lambda cfg, ips, tables: {a_ip: snmp.SnmpError("No SNMP response received")})
    g = client.get("/api/v1/admin/enrichment/snmp").json()
    assert g["enabled"] and g["community_set"] and "community" not in g
    assert client.put("/api/v1/admin/enrichment/snmp", headers=UI, json={"version": "v1"}).status_code == 422
    assert client.put("/api/v1/admin/enrichment/snmp", headers=UI, json={"enabled": False}).json()["enabled"] is False
    assert snmp.run_poll(testdb, lambda *a: pytest.fail("no poll when disabled")).get("skipped")
