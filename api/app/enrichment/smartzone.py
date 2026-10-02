"""Read-only SmartZone client (ICX switch management public API).

Authentication: service ticket (read-only administrator account)

    POST https://<host>:8443/wsg/api/public/<ver>/serviceTicket     {"username", "password"}
    DELETE .../serviceTicket?serviceTicket=<ticket>                   (logoff)

Data (switch management API, ticket as query parameter):

    POST /switchm/api/<ver>/switch                 switches
    POST /switchm/api/<ver>/switch/ports/details   ports
    POST /switchm/api/<ver>/switch/clients         connected devices seen by LLDP

The API version is negotiated from GET /wsg/api/public/apiInfo (public).
Reference: SmartZone Public API Reference Guide (ICX Management), 7.1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

# Switch management API versions this client was written against (newest first).
SUPPORTED_VERSIONS = ["v13_0", "v12_0", "v11_1", "v11_0"]
PAGE_LIMIT = 200
MAX_PAGES = 500

_PORT_IN_NAME = re.compile(r"(\d{1,2}/\d{1,2}/\d{1,3})\s*$")
_DEFAULT_NAME = re.compile(r"^\(INTERFACE[^)]*\)\s*", re.IGNORECASE)
_NAMED = re.compile(r"^\((.*?)\)\s*")


class SZError(Exception):
    pass


@dataclass
class SZConfig:
    host: str
    port: int
    username: str
    password: str
    verify_tls: bool = True


def port_from_interface(name: str | None) -> str | None:
    """'10GigabitEthernet1/1/1' -> '1/1/1'."""
    m = _PORT_IN_NAME.search(name or "")
    return m.group(1) if m else None


def port_label(name: str | None) -> str:
    """User-defined port name from SmartZone's '(<name>) <Interface>' format.
    '(INTERFACE1/1/1) GigabitEthernet1/1/1' is the default: no user name."""
    n = (name or "").strip()
    if not n or _DEFAULT_NAME.match(n):
        return ""
    m = _NAMED.match(n)
    return m.group(1).strip() if m else n


class SmartZoneClient:
    def __init__(self, cfg: SZConfig, transport: httpx.BaseTransport | None = None, timeout: float = 30):
        host = cfg.host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        if not host or not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", host):
            raise SZError("invalid SmartZone host")
        if not (cfg.username and cfg.password):
            raise SZError("username and password are required")
        self.base = f"https://{host}:{int(cfg.port)}"
        self.cfg = cfg
        self.http = httpx.Client(timeout=timeout, transport=transport, verify=cfg.verify_tls,
                                 headers={"Accept": "application/json"})
        self.version: str | None = None
        self.ticket: str | None = None

    def _get(self, url: str, **kw) -> httpx.Response:
        try:
            return self.http.get(url, **kw)
        except httpx.ConnectError as exc:
            raise SZError(f"cannot reach SmartZone at {self.base}: {exc}") from exc

    def _post(self, url: str, **kw) -> httpx.Response:
        try:
            return self.http.post(url, **kw)
        except httpx.ConnectError as exc:
            raise SZError(f"cannot reach SmartZone at {self.base}: {exc}") from exc

    def negotiate(self) -> str:
        if self.version:
            return self.version
        r = self._get(f"{self.base}/wsg/api/public/apiInfo")
        if r.status_code != 200:
            raise SZError(f"apiInfo: HTTP {r.status_code} (is this a SmartZone?)")
        available = set(r.json().get("apiSupportVersions") or [])
        for v in SUPPORTED_VERSIONS:
            if v in available:
                self.version = v
                return v
        raise SZError(f"no supported API version (SmartZone offers {sorted(available)})")

    def login(self) -> str:
        if self.ticket:
            return self.ticket
        v = self.negotiate()
        r = self._post(f"{self.base}/wsg/api/public/{v}/serviceTicket",
                       json={"username": self.cfg.username, "password": self.cfg.password})
        if r.status_code in (401, 403):
            raise SZError("SmartZone refused the credentials")
        if r.status_code >= 400:
            raise SZError(f"serviceTicket: HTTP {r.status_code}")
        ticket = r.json().get("serviceTicket")
        if not ticket:
            raise SZError("serviceTicket missing in the SmartZone response")
        self.ticket = ticket
        return ticket

    def close(self) -> None:
        if self.ticket and self.version:
            try:
                self.http.delete(f"{self.base}/wsg/api/public/{self.version}/serviceTicket",
                                 params={"serviceTicket": self.ticket})
            except httpx.HTTPError:
                pass
        self.ticket = None
        self.http.close()

    def _query(self, path: str, sort_column: str) -> Iterator[dict[str, Any]]:
        ticket = self.login()
        for page in range(1, MAX_PAGES + 1):
            r = self._post(f"{self.base}/switchm/api/{self.version}{path}", params={"serviceTicket": ticket},
                           json={"page": page, "limit": PAGE_LIMIT, "sortInfo": {"sortColumn": sort_column, "dir": "ASC"}})
            if r.status_code in (401, 403):
                raise SZError(f"{path}: not authorized (does the account see the switches' domain?)")
            if r.status_code >= 400:
                raise SZError(f"{path}: HTTP {r.status_code}")
            body = r.json()
            items = body.get("list") or []
            yield from items
            if not body.get("hasMore") or not items:
                return
        raise SZError(f"{path}: more than {MAX_PAGES * PAGE_LIMIT} records")

    def switches(self) -> list[dict[str, Any]]:
        return list(self._query("/switch", "serialNumber"))

    def ports(self) -> list[dict[str, Any]]:
        return list(self._query("/switch/ports/details", "name"))

    def lldp(self) -> list[dict[str, Any]]:
        return list(self._query("/switch/clients", "switchName"))
