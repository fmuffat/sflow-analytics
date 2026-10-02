"""Read-only RUCKUS One (R1) API client.

Authentication: OAuth2 client credentials (application token created in
RUCKUS One), JWT valid 60 minutes, per tenant:

    POST https://<auth host>/oauth2/token/<tenantId>   (form: grant_type, client_id, client_secret)

Data: viewmodel query API on the regional API host:

    POST /venues/switches/query               switches
    POST /venues/switches/switchPorts/query   ports (incl. LLDP neighbor)
    POST /venues/switches/clients/query       clients seen on switch ports

Reference: https://docs.ruckus.cloud/api (viewmodel 1.0.x).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

REGIONS: dict[str, tuple[str, str, str]] = {
    # key: (label, auth host, API host)
    "na": ("North America (ruckus.cloud)", "ruckus.cloud", "api.ruckus.cloud"),
    "eu": ("Europe (eu.ruckus.cloud)", "eu.ruckus.cloud", "api.eu.ruckus.cloud"),
    "asia": ("Asia (asia.ruckus.cloud)", "asia.ruckus.cloud", "api.asia.ruckus.cloud"),
}

PAGE_SIZE = 500
MAX_PAGES = 200

SWITCH_FIELDS = ["id", "name", "switchName", "serialNumber", "model", "firmware", "firmwareVersion",
                 "ipAddress", "switchMac", "venueName", "deviceStatus"]
PORT_FIELDS = ["portIdentifier", "switchSerial", "switchMac", "switchName", "name", "status", "adminStatus",
               "portSpeed", "unTaggedVlan", "vlanIds", "lagId", "lagName", "neighborName",
               "neighborMacAddress", "neighborPortMacAddress", "switchUnitId"]
CLIENT_FIELDS = ["clientMac", "clientIpv4Addr", "clientIpv6Addr", "clientName", "alias", "dhcpClientHostName",
                 "dhcpClientDeviceTypeName", "dhcpClientOsVendorName", "clientType", "switchSerialNumber",
                 "switchPort", "clientVlan"]


class R1Error(Exception):
    pass


@dataclass
class R1Config:
    region: str
    tenant_id: str
    client_id: str
    client_secret: str


class RuckusOneClient:
    def __init__(self, cfg: R1Config, transport: httpx.BaseTransport | None = None, timeout: float = 30):
        if cfg.region not in REGIONS:
            raise R1Error(f"unknown region {cfg.region!r} (use {', '.join(REGIONS)})")
        if not (cfg.tenant_id and cfg.client_id and cfg.client_secret):
            raise R1Error("tenant ID, client ID and client secret are required")
        _, self.auth_host, self.api_host = REGIONS[cfg.region]
        self.cfg = cfg
        self.http = httpx.Client(timeout=timeout, transport=transport, headers={"Accept": "application/json"})
        self._token: str | None = None
        self._token_exp = 0.0

    def close(self) -> None:
        self.http.close()

    def token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        r = self.http.post(f"https://{self.auth_host}/oauth2/token/{self.cfg.tenant_id}",
                           data={"grant_type": "client_credentials", "client_id": self.cfg.client_id,
                                 "client_secret": self.cfg.client_secret})
        if r.status_code in (400, 401, 403):
            raise R1Error(f"authentication refused by RUCKUS One ({r.status_code}): check region, tenant ID and credentials")
        if r.status_code >= 400:
            raise R1Error(f"token request failed: HTTP {r.status_code}")
        body = r.json()
        tok = body.get("access_token") or body.get("token")
        if not tok:
            raise R1Error("token response without access_token")
        self._token = tok
        # Tokens last 60 min; refresh a little earlier.
        self._token_exp = time.time() + min(int(body.get("expires_in", 3600)), 3600) - 300
        return tok

    def _query(self, path: str, fields: list[str]) -> Iterator[dict[str, Any]]:
        for page in range(1, MAX_PAGES + 1):
            r = self.http.post(f"https://{self.api_host}{path}",
                               headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"},
                               json={"fields": fields, "page": page, "pageSize": PAGE_SIZE})
            if r.status_code == 401:
                self._token = None
                raise R1Error(f"{path}: unauthorized (token rejected)")
            if r.status_code == 403:
                raise R1Error(f"{path}: forbidden (the application token lacks read access to switches)")
            if r.status_code >= 400:
                raise R1Error(f"{path}: HTTP {r.status_code}")
            body = r.json()
            data = body.get("data") or []
            yield from data
            total = body.get("totalCount")
            if not data or len(data) < PAGE_SIZE or (total is not None and page * PAGE_SIZE >= int(total)):
                return
        raise R1Error(f"{path}: more than {MAX_PAGES * PAGE_SIZE} records")

    def switches(self) -> list[dict[str, Any]]:
        return list(self._query("/venues/switches/query", SWITCH_FIELDS))

    def ports(self) -> list[dict[str, Any]]:
        return list(self._query("/venues/switches/switchPorts/query", PORT_FIELDS))

    def clients(self) -> list[dict[str, Any]]:
        return list(self._query("/venues/switches/clients/query", CLIENT_FIELDS))
