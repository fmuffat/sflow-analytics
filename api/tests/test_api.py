"""API tests against ClickHouse. Results are compared with a Python oracle
computed from the same known dataset (conftest.ROWS)."""

import ipaddress

import pytest

from .conftest import EXP_A, EXP_B, IN_WINDOW, WINDOW, exp_id

pytestmark = pytest.mark.clickhouse


def get(client, path, **params):
    r = client.get("/api/v1" + path, params={**WINDOW, **params})
    assert r.status_code == 200, r.text
    return r.json()


def in_net(ip, net):
    return ip is not None and ipaddress.ip_address(ip) in ipaddress.ip_network(net, strict=False)


def total_bytes(rows):
    return sum(r.bytes for r in rows)


# --- Filters: every filter, alone and combined, matches the oracle ----------

CASES = {
    "none": ({}, lambda r: True),
    "src subnet": ({"src_ip": "10.1.1.0/24"}, lambda r: in_net(r.src, "10.1.1.0/24")),
    "dst ip": ({"dst_ip": "10.2.2.20"}, lambda r: r.dst == "10.2.2.20"),
    "either ip": ({"ip": "10.2.2.20"}, lambda r: "10.2.2.20" in (r.src, r.dst)),
    "ipv6 subnet": ({"ip": "2001:db8::/32"}, lambda r: in_net(r.src, "2001:db8::/32") or in_net(r.dst, "2001:db8::/32")),
    "several ips": ({"src_ip": "10.1.1.11,10.3.3.30"}, lambda r: r.src in ("10.1.1.11", "10.3.3.30")),
    "protocol name": ({"protocol": "udp"}, lambda r: r.proto == 17),
    "protocol list": ({"protocol": "tcp,icmp"}, lambda r: r.proto in (6, 1)),
    "dst port": ({"dst_port": "443"}, lambda r: r.dport == 443),
    "either port": ({"port": "443"}, lambda r: 443 in (r.sport, r.dport)),
    "vlan list": ({"vlan": "10,20"}, lambda r: r.vlan in (10, 20)),
    "exporter ip": ({"exporter": "10.0.0.2"}, lambda r: r.exp == EXP_B),
    "exporter id": ({"exporter": exp_id(EXP_A)}, lambda r: r.exp == EXP_A),
    "ingress if": ({"input_ifindex": "1"}, lambda r: r.in_if == 1),
    "egress if": ({"output_ifindex": "24"}, lambda r: r.out_if == 24),
    "either if": ({"ifindex": "48"}, lambda r: 48 in (r.in_if, r.out_if)),
    "service": ({"service": "https"}, lambda r: r.service == "HTTPS"),
    "service unmapped": ({"service": "tcp/9999"}, lambda r: r.service == "TCP/9999"),
    "service unknown": ({"service": "Unknown"}, lambda r: r.service == "Unknown"),
    "service non-ip": ({"service": "Non-IP"}, lambda r: r.service == "Non-IP"),
    "combined": ({"protocol": "tcp", "vlan": "10", "dst_port": "443", "exporter": "10.0.0.1"},
                 lambda r: r.proto == 6 and r.vlan == 10 and r.dport == 443 and r.exp == EXP_A),
    "no match": ({"vlan": "999"}, lambda r: False),
}


@pytest.mark.parametrize("name", CASES)
def test_filters_match_oracle(client, name):
    params, pred = CASES[name]
    expected = [r for r in IN_WINDOW if pred(r)]
    body = get(client, "/traffic/top-services", **params)
    assert body["estimated"] is True
    assert body["total"]["bytes"] == total_bytes(expected)
    assert body["total"]["samples"] == len(expected)


def test_time_window_excludes_outside_rows(client):
    body = get(client, "/traffic/top-sources", **{"from": "2026-01-01T09:00:00Z", "to": "2026-01-01T10:00:00Z"})
    assert body["total"]["samples"] == 1


# --- Top-N ----------------------------------------------------------------

def test_services_labels(client):
    body = get(client, "/traffic/top-services", limit=50)
    got = {i["service"]: i["bytes"] for i in body["items"]}
    expected = {}
    for r in IN_WINDOW:
        expected[r.service] = expected.get(r.service, 0) + r.bytes
    assert got == expected
    assert abs(sum(i["percent"] for i in body["items"]) - 100) < 0.1


def test_top_sources_order_and_percent(client):
    body = get(client, "/traffic/top-sources", limit=3)
    items = body["items"]
    assert len(items) == 3
    assert [i["bytes"] for i in items] == sorted((i["bytes"] for i in items), reverse=True)
    top = items[0]
    expected = sum(r.bytes for r in IN_WINDOW if r.src == top["ip"])
    assert top["bytes"] == expected
    assert top["percent"] == round(expected * 100 / total_bytes(IN_WINDOW), 2)
    assert all(":" not in i["ip"] or i["ip"].startswith("2001") for i in items)  # IPv4 shown dotted


def test_top_destinations(client):
    items = get(client, "/traffic/top-destinations", limit=1)["items"]
    assert items[0]["ip"] == "10.2.2.20"


def test_top_conversations_pair_and_5tuple(client):
    items = get(client, "/traffic/top-conversations", limit=20)["items"]
    https = [i for i in items if i["src_ip"] == "10.1.1.10" and i["dst_ip"] == "10.2.2.20" and i["service"] == "HTTPS"]
    assert len(https) == 1 and https[0]["samples"] == 2 and https[0]["protocol"] == "TCP"
    assert https[0]["exporters"] == [{"id": exp_id(EXP_A), "name": "10.0.0.1"}]
    assert https[0]["input_ifindexes"] == [1] and https[0]["output_ifindexes"] == [48] and https[0]["vlans"] == [10]
    assert https[0]["first_seen"] < https[0]["last_seen"]

    five = get(client, "/traffic/top-conversations", limit=20, by="5tuple")["items"]
    assert len([i for i in five if i["src_ip"] == "10.1.1.10" and i["service"] == "HTTPS"]) == 2


def test_top_protocols_and_vlans(client):
    protos = {i["protocol"]: i["samples"] for i in get(client, "/traffic/top-protocols", limit=20)["items"]}
    assert protos == {"TCP": 6, "UDP": 2, "ICMP": 1, "Non-IP": 1}
    vlans = {i["vlan"]: i["samples"] for i in get(client, "/traffic/top-vlans", limit=20)["items"]}
    assert vlans == {10: 5, 20: 1, 30: 3, 1: 1}


def test_top_exporters(client):
    items = get(client, "/traffic/top-exporters")["items"]
    assert {i["exporter_id"] for i in items} == {exp_id(EXP_A), exp_id(EXP_B)}


def test_top_interfaces(client):
    items = get(client, "/traffic/top-interfaces", limit=20)["items"]
    a48 = next(i for i in items if i["exporter_id"] == exp_id(EXP_A) and i["ifindex"] == 48)
    assert a48["out_bytes"] == sum(r.bytes for r in IN_WINDOW if r.exp == EXP_A and r.out_if == 48)
    assert a48["in_bytes"] == sum(r.bytes for r in IN_WINDOW if r.exp == EXP_A and r.in_if == 48)
    assert a48["speed_bps"] == 10_000_000_000 and a48["label"] == "ifIndex 48"


# --- Timeline and flows ------------------------------------------------------

def test_timeseries(client):
    body = get(client, "/traffic/timeseries")
    assert body["step_seconds"] == 30 and len(body["items"]) == 120
    assert body["total"]["bytes"] == total_bytes(IN_WINDOW)
    first = body["items"][10]  # 10:05:00
    assert first["t"].startswith("2026-01-01T10:05:00") and first["bytes"] == 1_500_000
    assert first["bps"] == round(1_500_000 * 8 / 30, 1)

    coarse = get(client, "/traffic/timeseries", step=600)
    assert len(coarse["items"]) == 6 and coarse["total"]["bytes"] == total_bytes(IN_WINDOW)


def test_flows_newest_first_with_paging(client):
    body = get(client, "/traffic/flows", limit=3)
    ts = [f["timestamp"] for f in body["items"]]
    assert len(ts) == 3 and ts == sorted(ts, reverse=True)
    assert body["items"][0]["service"] == "Unknown"  # 10:50
    page2 = get(client, "/traffic/flows", limit=3, offset=3)["items"]
    assert page2[0]["timestamp"] < ts[-1]
    ipv6 = get(client, "/traffic/flows", ip="2001:db8::1")["items"]
    assert len(ipv6) == 1 and ipv6[0]["src_ip"] == "2001:db8::1" and ipv6[0]["service"] == "QUIC"


# --- Empty dataset and invalid parameters --------------------------------------

def test_empty_window(client):
    empty = {"from": "2025-06-01T00:00:00Z", "to": "2025-06-01T01:00:00Z"}
    for path in ("/traffic/top-sources", "/traffic/top-conversations", "/traffic/top-interfaces", "/traffic/flows"):
        body = get(client, path, **empty)
        assert body["items"] == []
    ts = get(client, "/traffic/timeseries", **empty)
    assert ts["total"]["bytes"] == 0 and all(i["bytes"] == 0 for i in ts["items"])


@pytest.mark.parametrize("params", [
    {"src_ip": "not-an-ip"}, {"vlan": "4096"}, {"protocol": "bogus"}, {"service": "' OR 1=1 --"},
    {"from": "2026-01-01T11:00:00Z", "to": "2026-01-01T10:00:00Z"}, {"range": "3y", "from": None},
    {"limit": "0"}, {"limit": "abc"}, {"exporter": "not-an-exporter"},
])
def test_invalid_parameters_return_422(client, params):
    q = {**WINDOW, **params}
    q = {k: v for k, v in q.items() if v is not None}
    r = client.get("/api/v1/traffic/top-sources", params=q)
    assert r.status_code == 422, r.text


# --- Inventory -------------------------------------------------------------------

def test_exporters_and_rename(client):
    items = client.get("/api/v1/exporters").json()["items"]
    assert [e["id"] for e in items] == [exp_id(EXP_A), exp_id(EXP_B)]
    assert items[0]["name"] == "10.0.0.1" and items[0]["status"] == "inactive"  # last seen long ago

    r = client.patch(f"/api/v1/exporters/{exp_id(EXP_A)}", json={"display_name": "ICX8200-Core-01"})
    assert r.status_code == 200 and r.json()["display_name"] == "ICX8200-Core-01"
    assert r.json()["agent_ip"] == "10.0.0.1"  # discovered IP kept
    detail = client.get(f"/api/v1/exporters/{exp_id(EXP_A)}").json()
    assert detail["name"] == "ICX8200-Core-01" and [i["ifindex"] for i in detail["interfaces"]] == [1, 48]

    # Names show up in analytics.
    top = get(client, "/traffic/top-exporters")["items"]
    assert "ICX8200-Core-01" in {i["name"] for i in top}

    # Notes only: the name is kept.
    r = client.patch(f"/api/v1/exporters/{exp_id(EXP_A)}", json={"notes": "core"})
    assert r.json()["display_name"] == "ICX8200-Core-01" and r.json()["notes"] == "core"

    assert client.patch("/api/v1/exporters/1.2.3.4/1.2.3.4/0", json={"display_name": "x"}).status_code == 404
    assert client.patch(f"/api/v1/exporters/{exp_id(EXP_A)}", json={"display_name": "x" * 65}).status_code == 422


def test_interface_mapping(client):
    iid = f"{exp_id(EXP_A)}/48"
    r = client.patch(f"/api/v1/interfaces/{iid}", json={"name": "1/1/48", "description": "uplink core"})
    assert r.status_code == 200 and r.json()["label"] == "1/1/48"
    listed = client.get("/api/v1/interfaces", params={"exporter": exp_id(EXP_A)}).json()["items"]
    assert {i["ifindex"]: i["label"] for i in listed} == {1: "ifIndex 1", 48: "1/1/48"}
    top = get(client, "/traffic/top-interfaces", limit=20)["items"]
    assert any(i["label"] == "1/1/48" for i in top)
    assert client.get(f"/api/v1/interfaces/{exp_id(EXP_A)}/999").status_code == 404
    assert client.get("/api/v1/interfaces/garbage").status_code == 404


def test_health_and_status(client):
    assert client.get("/api/v1/health").json() == {"status": "ok", "clickhouse": True}
    st = client.get("/api/v1/system/status").json()
    assert st["services"]["clickhouse"] == "running" and st["services"]["collector"] == "unreachable"
    assert client.get("/api/v1/collector/status").status_code == 502
    assert client.get("/api/v1/openapi.json").status_code == 200


def test_summary(client):
    s = get(client, "/traffic/summary")["summary"]
    ip_rows = [r for r in IN_WINDOW if r.src is not None]
    assert s["bytes"] == total_bytes(IN_WINDOW) and s["samples"] == len(IN_WINDOW)
    assert s["conversations"] == len({(r.src, r.dst) for r in ip_rows})
    assert s["sources"] == len({r.src for r in ip_rows}) and s["exporters"] == 2
    assert s["bps"] == round(total_bytes(IN_WINDOW) * 8 / 3600, 1)
    empty = get(client, "/traffic/summary", **{"from": "2025-06-01T00:00:00Z", "to": "2025-06-01T01:00:00Z"})["summary"]
    assert empty["bytes"] == 0 and empty["last_sample"] is None


# --- Utilization (interface counters) ------------------------------------------

def test_utilization_top(client):
    r = client.get("/api/v1/utilization/interfaces", params={**WINDOW, "exporter": exp_id(EXP_A)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estimated"] is False
    (i,) = body["items"]
    assert i["ifindex"] == 48 and i["speed_bps"] == 10_000_000_000
    # 30 polls in 10 min, minus the reset poll.
    assert i["polls"] == 29
    assert i["in_avg_pct"] == 10.0 and i["in_max_pct"] == 10.0 and i["in_avg_bps"] == 1e9
    assert i["out_max_pct"] == 50.0 and i["out_max_bps"] == 5e9
    assert i["out_discards"] == 3 and i["in_errors"] == 0
    assert i["label"] == "1/1/48" or i["label"] == "ifIndex 48"


def test_utilization_timeseries(client):
    r = client.get("/api/v1/utilization/timeseries",
                   params={"from": "2026-01-01T10:00:00Z", "to": "2026-01-01T10:12:00Z",
                           "exporter": exp_id(EXP_A), "ifindex": 48, "step": 60})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["step_seconds"] == 60 and body["speed_bps"] == 10_000_000_000
    items = {i["t"][11:16]: i for i in body["items"]}
    assert items["10:02"]["in_avg_pct"] == 10.0 and items["10:02"]["out_max_pct"] == 0.0
    assert items["10:05"]["out_max_pct"] == 50.0 and items["10:05"]["out_discards"] == 3
    assert items["10:11"]["in_avg_pct"] is None  # no poll after 10:10: unknown, not zero
    assert client.get("/api/v1/utilization/timeseries", params={"exporter": "10.0.0.1", "ifindex": 1}).status_code == 422


# --- Stacked series, bidirectional conversations, CSV ---------------------------

def test_timeseries_grouped_by_service(client):
    body = get(client, "/traffic/timeseries", group_by="service", top=2)
    names = [s["name"] for s in body["series"]]
    per_service = {}
    for r in IN_WINDOW:
        per_service[r.service] = per_service.get(r.service, 0) + r.bytes
    leaders = sorted(per_service, key=per_service.get, reverse=True)[:2]
    assert names == leaders + ["Other"]
    assert sum(s["bytes"] for s in body["series"]) == total_bytes(IN_WINDOW)
    first = body["items"][10]  # 10:05
    assert first["bytes"]["HTTPS"] == 1_500_000 and first["bps"]["HTTPS"] == round(1_500_000 * 8 / 30, 1)


def test_bidirectional_conversations(client):
    items = get(client, "/traffic/top-conversations", by="bidir", limit=20)["items"]
    https = next(i for i in items if {i["host_a"], i["host_b"]} == {"10.1.1.10", "10.2.2.20"} and i["service"] == "HTTPS")
    # rows 1 and 2 go 10.1.1.10 -> 10.2.2.20, row 3 comes back
    assert https["host_a"] == "10.1.1.10" and https["a_to_b_bytes"] == 3_000_000 and https["b_to_a_bytes"] == 1_500_000
    assert https["bytes"] == 4_500_000


def test_csv_export(client):
    r = client.get("/api/v1/traffic/top-sources", params={**WINDOW, "format": "csv", "limit": 3})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="top-sources.csv"' in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].split(",")[:2] == ["ip", "bytes"] and len(lines) == 4
    flows = client.get("/api/v1/traffic/flows", params={**WINDOW, "format": "csv"}).text.strip().splitlines()
    assert len(flows) == 1 + len(IN_WINDOW)
    assert client.get("/api/v1/traffic/top-sources", params={**WINDOW, "format": "xml"}).status_code == 422


# --- Flow map (Sankey) ------------------------------------------------------------

def test_sankey_src_dst(client):
    body = get(client, "/traffic/sankey", levels="src_ip,dst_ip", top=50)
    ip_rows = [r for r in IN_WINDOW if r.src is not None]
    expected = {}
    for r in ip_rows:
        expected[(f"src_ip:{r.src}", f"dst_ip:{r.dst}")] = expected.get((f"src_ip:{r.src}", f"dst_ip:{r.dst}"), 0) + r.bytes
    got = {(l["source"], l["target"]): l["value"] for l in body["links"]}
    assert got == expected
    assert body["shown"] == total_bytes(ip_rows) and body["total"] == total_bytes(IN_WINDOW)
    assert body["coverage_percent"] == round(total_bytes(ip_rows) * 100 / total_bytes(IN_WINDOW), 1)


def test_sankey_three_levels_and_limits(client):
    body = get(client, "/traffic/sankey", levels="src_ip,service,dst_ip", top=3, metric="samples")
    first = sum(l["value"] for l in body["links"] if l["source"].startswith("src_ip:"))
    second = sum(l["value"] for l in body["links"] if l["source"].startswith("service:"))
    assert first == second == body["shown"] and body["metric"] == "samples"
    assert {n["depth"] for n in body["nodes"]} == {0, 1, 2}
    ifs = get(client, "/traffic/sankey", levels="input_if,output_if", exporter=exp_id(EXP_A))
    assert any("ifIndex" in n["label"] or "1/1/48" in n["label"] for n in ifs["nodes"])
    for bad in ("src_ip", "src_ip,src_ip", "src_ip,foo", "a,b,c,d"):
        assert client.get("/api/v1/traffic/sankey", params={**WINDOW, "levels": bad}).status_code == 422
