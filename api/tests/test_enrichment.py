"""Controller enrichment: ICX numbering, RUCKUS One client (mocked HTTP), settings."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app import security, store
from app.config import get_settings
from app.enrichment import icx, sync
from app.enrichment.ruckusone import R1Config, R1Error, RuckusOneClient
from app.main import app


@pytest.mark.parametrize("port,ifindex", [("1/1/1", 1), ("1/1/8", 8), ("1/2/1", 65), ("1/3/2", 130),
                                          ("2/1/1", 257), ("12/1/48", 2864), ("1/1/0", None), ("1/5/1", None),
                                          ("ethernet 1/1/1", None), ("LAG1", None)])
def test_icx_port_ifindex(port, ifindex):
    assert icx.port_ifindex(port) == ifindex


@pytest.mark.parametrize("name,expected", [
    ("GigabitEthernet1/1/1", ""), ("2.5GigabitEthernet1/1/2", ""), ("10GigabitEthernet1/3/1", ""),
    ("ethernet 1/1/5", ""), ("Uplink", "Uplink"), ("AP Bureau ", "AP Bureau"), ("PC-Fred", "PC-Fred"),
    ("", ""), (None, "")])
def test_user_port_name(name, expected):
    assert icx.user_port_name(name) == expected


def test_icx_lag_and_speed():
    assert icx.lag_ifindex("1") == 3073 and icx.lag_ifindex(None) is None and icx.lag_ifindex("x") is None
    assert icx.speed_bps("10 Gb/sec") == 10_000_000_000 and icx.speed_bps("1G") == 1_000_000_000
    assert icx.speed_bps("100 Mb/sec") == 100_000_000 and icx.speed_bps("2.5 Gbps") == 2_500_000_000
    assert icx.speed_bps("Auto") is None and icx.speed_bps("") is None


class FakeR1:
    """Mock RUCKUS One: records requests, serves paginated switches."""

    def __init__(self, token_status=200, query_status=200, switches=3, page_size=2):
        self.calls = []
        self.token_status, self.query_status = token_status, query_status
        self.switches = [{"serialNumber": f"SN{i}", "name": f"sw{i}", "ipAddress": f"10.0.0.{i}"} for i in range(switches)]
        self.page_size = page_size

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path.startswith("/oauth2/token/"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "jwt-abc", "expires_in": 3600})
        if self.query_status != 200:
            return httpx.Response(self.query_status, json={})
        body = json.loads(request.content)
        page, size = body["page"], self.page_size
        data = self.switches[(page - 1) * size: page * size]
        return httpx.Response(200, json={"data": data, "totalCount": len(self.switches), "page": page})


def client_for(fake, region="eu", monkeypatch=None):
    c = RuckusOneClient(R1Config(region, "tenant123", "cid", "csecret"), transport=httpx.MockTransport(fake))
    return c


def test_r1_regions_auth_and_pagination(monkeypatch):
    monkeypatch.setattr("app.enrichment.ruckusone.PAGE_SIZE", 2)
    for region, auth, api in [("eu", "eu.ruckus.cloud", "api.eu.ruckus.cloud"),
                              ("na", "ruckus.cloud", "api.ruckus.cloud"),
                              ("asia", "asia.ruckus.cloud", "api.asia.ruckus.cloud")]:
        fake = FakeR1(switches=3, page_size=2)
        c = client_for(fake, region)
        assert [s["name"] for s in c.switches()] == ["sw0", "sw1", "sw2"]
        tok, q1, q2 = fake.calls
        assert tok.url.host == auth and tok.url.path == "/oauth2/token/tenant123"
        form = parse_qs(tok.content.decode())
        assert form == {"grant_type": ["client_credentials"], "client_id": ["cid"], "client_secret": ["csecret"]}
        assert q1.url.host == api and q1.url.path == "/venues/switches/query"
        assert q1.headers["authorization"] == "Bearer jwt-abc"
        assert json.loads(q1.content)["pageSize"] == 2 and json.loads(q2.content)["page"] == 2
        c.switches()  # token reused
        assert sum(1 for r in fake.calls if r.url.path.startswith("/oauth2")) == 1


def test_r1_errors():
    with pytest.raises(R1Error, match="authentication refused"):
        client_for(FakeR1(token_status=401)).switches()
    with pytest.raises(R1Error, match="forbidden"):
        client_for(FakeR1(query_status=403)).switches()
    with pytest.raises(R1Error, match="unknown region"):
        RuckusOneClient(R1Config("mars", "t", "c", "s"))
    with pytest.raises(R1Error, match="required"):
        RuckusOneClient(R1Config("eu", "", "c", "s"))


def test_normalize_ports_adds_lags():
    ports = sync.normalize_ports([
        {"portIdentifier": "1/1/1", "switchSerial": "SN1", "name": "AP-hall", "portSpeed": "1 Gb/sec",
         "neighborName": "ap-hall", "neighborMacAddress": "AA:BB:CC:00:00:01", "lagId": None},
        {"portIdentifier": "1/2/1", "switchSerial": "SN1", "lagId": "1", "lagName": "uplink-core"},
        {"portIdentifier": "", "switchSerial": "SN1"},
    ])
    by = {p["port_id"]: p for p in ports}
    assert by["1/1/1"]["ifindex"] == 1 and by["1/1/1"]["lldp_mac"] == "aa:bb:cc:00:00:01"
    assert by["1/2/1"]["ifindex"] == 65
    assert by["LAG1"]["ifindex"] == 3073 and by["LAG1"]["name"] == "uplink-core"
    assert len(ports) == 3


@pytest.fixture()
def cfg_store(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "config_dir", str(tmp_path))
    yield tmp_path


def test_settings_secret_is_write_only(cfg_store):
    with TestClient(app) as c:
        r = c.put("/api/v1/admin/enrichment", json={"source": "ruckusone", "ruckusone": {
            "region": "eu", "tenant_id": "44ab", "client_id": "cid", "client_secret": "top-secret"}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "ruckusone" and body["ruckusone"]["client_secret_set"] is True
        assert "top-secret" not in r.text and "client_secret" not in body["ruckusone"]
        # Empty secret keeps the stored one; bad values rejected.
        c.put("/api/v1/admin/enrichment", json={"ruckusone": {"client_secret": ""}})
        assert security.get_setting("enrichment.r1.client_secret") == "top-secret"
        assert c.put("/api/v1/admin/enrichment", json={"source": "cisco"}).status_code == 422
        assert c.put("/api/v1/admin/enrichment", json={"ruckusone": {"region": "mars"}}).status_code == 422
        assert c.put("/api/v1/admin/enrichment", json={"ruckusone": {"tenant_id": "x;drop"}}).status_code == 422
        raw = store.one("SELECT value FROM settings WHERE key = 'enrichment.r1.client_secret'")["value"]
        assert "top-secret" not in raw


def test_sync_due_and_smartzone_not_configured(cfg_store):
    assert sync.due() is False  # source none
    sync.save_config({"source": "smartzone"})
    r = sync.run_sync(db=None)
    assert r["ok"] is False and "host" in r["error"]
    assert sync.due(now=10**12) is True


class FakeSZ:
    """Mock SmartZone: apiInfo, service ticket, paginated switch management API."""

    def __init__(self, login_status=200, versions=("v11_1", "v13_0", "v14_0")):
        self.calls = []
        self.login_status, self.versions = login_status, list(versions)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        p = request.url.path
        if p == "/wsg/api/public/apiInfo":
            return httpx.Response(200, json={"apiSupportVersions": self.versions})
        if p.endswith("/serviceTicket"):
            if request.method == "DELETE":
                return httpx.Response(200)
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"message": "invalid"})
            return httpx.Response(200, json={"serviceTicket": "ST-123"})
        assert request.url.params["serviceTicket"] == "ST-123"
        page = json.loads(request.content)["page"]
        if p.endswith("/switch"):
            data = [{"id": "60:9C:9F:DA:63:80", "switchName": "icx-a", "macAddress": "60:9C:9F:DA:63:80",
                     "model": "ICX7750-48XGC", "ipAddress": "10.0.0.1", "firmwareVersion": "SWS08080b",
                     "serialNumber": "CRJ3333N00K", "status": "ONLINE", "groupName": "HQ"}]
            return httpx.Response(200, json={"list": data if page == 1 else [], "hasMore": False})
        if p.endswith("/switch/ports/details"):
            pages = {1: [{"switchId": "60:9C:9F:DA:63:80", "portIdentifier": "1/1/1", "portSpeed": "10 Gb/sec",
                          "name": "(Uplink core) 10GigabitEthernet1/1/1", "status": "Up", "adminStatus": "Up"}],
                     2: [{"switchId": "60:9C:9F:DA:63:80", "portIdentifier": "1/2/1", "portSpeed": "40 Gb/sec",
                          "name": "(INTERFACE1/2/1) 40GigabitEthernet1/2/1", "status": "Down"}]}
            return httpx.Response(200, json={"list": pages.get(page, []), "hasMore": page < 2})
        if p.endswith("/switch/clients"):
            return httpx.Response(200, json={"list": [{"switchId": "60:9C:9F:DA:63:80", "localPort": "10GigabitEthernet1/1/1",
                                                       "remoteDeviceName": "core-rtr", "remoteDeviceMac": "55:B9:3C:52:21:21",
                                                       "remotePortMac": "40:B9:3C:52:21:21"}], "hasMore": False})
        return httpx.Response(404)


def test_smartzone_client_and_normalization():
    from app.enrichment.smartzone import SmartZoneClient, SZConfig, SZError, port_from_interface, port_label

    assert port_from_interface("10GigabitEthernet1/1/1") == "1/1/1" and port_from_interface("lag 1") is None
    assert port_label("(INTERFACE1/1/1) GigabitEthernet1/1/1") == "" and port_label("(Uplink) Gig1/1/8") == "Uplink"

    fake = FakeSZ()
    c = SmartZoneClient(SZConfig("sz.example.com", 8443, "ro", "pw"), transport=httpx.MockTransport(fake))
    switches, ports, lldp = c.switches(), c.ports(), c.lldp()
    assert c.version == "v13_0"  # newest supported version offered by the controller
    login = next(r for r in fake.calls if r.url.path.endswith("/serviceTicket"))
    assert str(login.url) == "https://sz.example.com:8443/wsg/api/public/v13_0/serviceTicket"
    assert json.loads(login.content) == {"username": "ro", "password": "pw"}
    assert len(ports) == 2  # two pages
    c.close()
    assert fake.calls[-1].method == "DELETE"  # ticket logged off

    sw, pr = sync.normalize_sz(switches, ports, lldp)
    assert sw[0]["serial"] == "CRJ3333N00K" and sw[0]["ip"] == "10.0.0.1" and sw[0]["venue"] == "HQ"
    by = {p["port_id"]: p for p in pr}
    assert by["1/1/1"]["name"] == "Uplink core" and by["1/1/1"]["lldp_name"] == "core-rtr" and by["1/1/1"]["ifindex"] == 1
    assert by["1/2/1"]["name"] == "" and by["1/2/1"]["ifindex"] == 65 and by["1/2/1"]["lldp_name"] == ""

    with pytest.raises(SZError, match="refused"):
        SmartZoneClient(SZConfig("sz", 8443, "ro", "bad"), transport=httpx.MockTransport(FakeSZ(login_status=401))).switches()
    with pytest.raises(SZError, match="no supported API version"):
        SmartZoneClient(SZConfig("sz", 8443, "ro", "pw"), transport=httpx.MockTransport(FakeSZ(versions=["v9_0"]))).switches()
    with pytest.raises(SZError, match="invalid SmartZone host"):
        SmartZoneClient(SZConfig("bad host/", 8443, "ro", "pw"))


def test_smartzone_settings_password_write_only(cfg_store):
    with TestClient(app) as c:
        r = c.put("/api/v1/admin/enrichment", json={"source": "smartzone", "smartzone": {
            "host": "vsz.example.net", "port": 8443, "username": "ro", "password": "sz-secret", "verify_tls": False}})
        assert r.status_code == 200, r.text
        body = r.json()["smartzone"]
        assert body == {"host": "vsz.example.net", "port": 8443, "username": "ro", "verify_tls": False, "password_set": True}
        assert "sz-secret" not in r.text
        assert c.put("/api/v1/admin/enrichment", json={"smartzone": {"host": "bad host/x"}}).status_code == 422
