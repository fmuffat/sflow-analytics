"""Groups, layer 2 and broadcast storms (needs ClickHouse)."""

import ipaddress
from datetime import timedelta

import pytest

from app import groups

from .conftest import EXP_A, EXP_B, IN_WINDOW, T0, WINDOW, _ip6, exp_id

pytestmark = pytest.mark.clickhouse


def get(client, path, **params):
    r = client.get("/api/v1" + path, params={**WINDOW, **params})
    assert r.status_code == 200, r.text
    return r.json()


def in_net(ip, net):
    return ip is not None and ipaddress.ip_address(ip) in ipaddress.ip_network(net)


def bytes_of(rows):
    return sum(r.bytes for r in rows)


@pytest.fixture(scope="module")
def defined(client):
    for body in ({"kind": "ip", "name": "Clients", "members": ["10.1.1.0/24"]},
                 {"kind": "ip", "name": "Servers", "members": ["10.2.2.0/24", "10.3.3.30"]},
                 {"kind": "if", "name": "Uplinks", "members": [f"{exp_id(EXP_A)}/48"]}):
        assert client.put("/api/v1/groups", json=body).status_code == 200
    yield
    for kind, name in (("ip", "Clients"), ("ip", "Servers"), ("if", "Uplinks")):
        client.delete("/api/v1/groups", params={"kind": kind, "name": name})


def test_group_validation(client):
    for body in ({"kind": "ip", "name": "x", "members": ["nope"]},
                 {"kind": "if", "name": "x", "members": ["1.2.3.4/48"]},
                 {"kind": "ip", "name": "Ungrouped", "members": ["10.0.0.0/8"]}):
        assert client.put("/api/v1/groups", json=body).status_code == 422
    assert groups.normalize_member("ip", "10.1.1.7/24") == "10.1.1.0/24"
    assert groups.normalize_member("ip", "10.1.1.7") == "10.1.1.7"


def test_group_filters(client, defined):
    clients = [r for r in IN_WINDOW if in_net(r.src, "10.1.1.0/24")]
    assert get(client, "/traffic/top-services", src_group="Clients")["total"]["bytes"] == bytes_of(clients)
    servers = [r for r in IN_WINDOW if in_net(r.dst, "10.2.2.0/24") or r.dst == "10.3.3.30"]
    assert get(client, "/traffic/top-services", dst_group="Servers")["total"]["bytes"] == bytes_of(servers)
    uplink = [r for r in IN_WINDOW if r.exp == EXP_A and 48 in (r.in_if, r.out_if)]
    assert get(client, "/traffic/top-services", if_group="Uplinks")["total"]["bytes"] == bytes_of(uplink)
    r = client.get("/api/v1/traffic/top-sources", params={**WINDOW, "group": "Nope"})
    assert r.status_code == 422 and "unknown IP group" in r.text


def test_top_groups_matrix_and_sankey(client, defined):
    dst = {i["grp"]: i["bytes"] for i in get(client, "/traffic/top-groups", direction="dst", limit=10)["items"]}
    assert dst["Servers"] == bytes_of([r for r in IN_WINDOW if in_net(r.dst, "10.2.2.0/24") or r.dst == "10.3.3.30"])
    assert "Non-IP" in dst and "Ungrouped" in dst
    matrix = {(i["src_group"], i["dst_group"]): i["bytes"] for i in get(client, "/traffic/group-matrix")["items"]}
    assert matrix[("Clients", "Servers")] == bytes_of([r for r in IN_WINDOW if in_net(r.src, "10.1.1.0/24") and in_net(r.dst, "10.2.2.0/24")])
    sk = get(client, "/traffic/sankey", levels="src_group,dst_group")
    assert any(l["source"] == "src_group:Clients" and l["target"] == "dst_group:Servers" for l in sk["links"])


def test_top_macs_and_ethertypes(client):
    macs = get(client, "/traffic/top-macs", direction="src")["items"]
    m = next(i for i in macs if i["mac"] == "00:11:22:33:44:55")
    distinct_src = {r.src for r in IN_WINDOW if r.src}
    assert m["ips"] == len(distinct_src) and m["gateway"] is False and m["ip"] is None and "vendor" in m
    et = {i["name"]: i["samples"] for i in get(client, "/traffic/top-ethertypes")["items"]}
    assert et["IPv4"] == sum(1 for r in IN_WINDOW if r.src and ":" not in r.src)
    assert et["IPv6"] == 1 and et["802.3 / LLC (STP, CDP...)"] == 1


def test_broadcast_storm(client, testdb):
    # EXP_B ifIndex 24: 10 broadcast pps normally, 2000 pps between 10:04 and 10:06.
    rows, bc, uc, t = [], 0, 0, T0
    while t <= T0 + timedelta(minutes=10):
        rows.append([t, _ip6(EXP_B[0]), _ip6(EXP_B[1]), EXP_B[2], 24, 6, 1_000_000_000, 1, True, True,
                     0, uc, 0, bc, 0, 0, 0, 0, 0, 0, 0, 0])
        t += timedelta(seconds=20)
        bc += 40_000 if T0 + timedelta(minutes=4) < t <= T0 + timedelta(minutes=6) else 200
        uc += 100_000
    testdb.insert("interface_counters", rows, [
        "timestamp", "exporter_ip", "agent_ip", "agent_sub_id", "ifindex", "if_type", "speed_bps", "direction",
        "admin_up", "oper_up", "in_octets", "in_ucast", "in_multicast", "in_broadcast", "in_discards", "in_errors",
        "out_octets", "out_ucast", "out_multicast", "out_broadcast", "out_discards", "out_errors"])
    body = get(client, "/utilization/broadcast", threshold_pps=1000, limit=10)
    b24 = next(i for i in body["items"] if i["exporter_id"] == exp_id(EXP_B) and i["ifindex"] == 24)
    assert b24["storm"] is True and b24["in_bcast_max_pps"] == 2000.0 and b24["in_bcast_avg_pps"] > 10
    assert b24["storm_polls"] == 6 and str(b24["bcast_peak_at"]).startswith("2026-01-01")
    assert body["storms"] >= 1
    a48 = next(i for i in body["items"] if i["ifindex"] == 48)
    assert a48["storm"] is False and a48["in_bcast_max_pps"] == 0.0
    ts = client.get("/api/v1/utilization/timeseries", params={"from": "2026-01-01T10:00:00Z", "to": "2026-01-01T10:10:00Z",
                                                               "exporter": exp_id(EXP_B), "ifindex": 24, "step": 60}).json()
    by = {i["t"][11:16]: i for i in ts["items"]}
    assert by["10:05"]["in_bcast_max_pps"] == 2000.0 and by["10:01"]["in_bcast_max_pps"] == 10.0
