"""New version detection: the worker reads the latest release of the project on GitHub once a
day (can be disabled). Nothing about the appliance is sent besides the usual HTTP request.

State in the configuration store (key `updates`): enabled flag and the result of the last
check (latest version, release page, notes, date, error).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from . import security
from .config import get_settings

REPO = "fmuffat/sflow-analytics"
# UPDATES_API_URL: only for tests (local release server); the default is GitHub.
LATEST_URL = os.environ.get("UPDATES_API_URL") or f"https://api.github.com/repos/{REPO}/releases/latest"
# Shared with the host-side updater (packaging/updater.sh); absent in the development stack.
UPDATES_DIR = Path(os.environ.get("UPDATES_DIR", "/updates"))
FINAL_PHASES = ("done", "failed")
STALE = timedelta(hours=1)
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
KEY = "updates"
CHECK_EVERY = timedelta(hours=24)
TIMEOUT = 15
_VER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_version(v: str | None) -> tuple[int, int, int] | None:
    m = _VER.match((v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def _load() -> dict[str, Any]:
    raw = security.get_setting(KEY)
    return {"enabled": True, **(json.loads(raw) if raw else {})}


def _save(state: dict[str, Any]) -> None:
    security.set_setting(KEY, json.dumps(state))


def status() -> dict[str, Any]:
    st = _load()
    current = get_settings().app_version
    cur, latest = parse_version(current), parse_version(st.get("latest_version"))
    out = {
        "enabled": st["enabled"],
        "current_version": current,
        "development_build": cur is None,
        "latest_version": st.get("latest_version"),
        "release_url": st.get("release_url") or RELEASES_PAGE,
        "release_name": st.get("release_name"),
        "published_at": st.get("published_at"),
        "notes": st.get("notes"),
        "checked_at": st.get("checked_at"),
        "error": st.get("error"),
        "update_available": bool(cur and latest and latest > cur),
        "updater": updater(),
    }
    return out


class UpdateError(ValueError):
    pass


def updater() -> dict[str, Any]:
    """Host-side updater installed by install.sh (absent in the development stack)."""
    try:
        agent = json.loads((UPDATES_DIR / "agent.json").read_text())
    except (OSError, ValueError):
        return {"available": False}
    return {"available": True, "unit": agent.get("updater")}


def progress() -> dict[str, Any] | None:
    try:
        st = json.loads((UPDATES_DIR / "status.json").read_text())
    except (OSError, ValueError):
        st = None
    if (UPDATES_DIR / "request").exists() and (not st or st.get("phase") in FINAL_PHASES):
        return {"phase": "queued", "message": "Update requested, starting…", "target_version": None, "log_tail": []}
    return st


def running() -> bool:
    p = progress()
    if not p or p.get("phase") in FINAL_PHASES:
        return False
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(p["updated_at"]) < STALE
    except (KeyError, ValueError):
        return p.get("phase") == "queued"


def apply(version: str) -> dict[str, Any]:
    """Asks the host updater to install `version` (only the latest published release)."""
    s = status()
    if not s["updater"]["available"]:
        raise UpdateError("updates from the web interface are not available on this installation "
                          "(development stack, or installed before version 0.16.0: run install.sh once)")
    if not s["update_available"] or version != s["latest_version"]:
        raise UpdateError("this version is not a newer published release")
    if running():
        raise UpdateError("an update is already in progress")
    tmp = UPDATES_DIR / ".request.tmp"
    tmp.write_text(version + "\n")
    os.replace(tmp, UPDATES_DIR / "request")
    return {"requested": version, "progress": progress()}


def set_enabled(enabled: bool) -> dict[str, Any]:
    st = _load()
    st["enabled"] = bool(enabled)
    _save(st)
    return status()


def check(force: bool = False, now: datetime | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Reads the latest release on GitHub (at most once a day unless forced)."""
    now = now or datetime.now(timezone.utc)
    st = _load()
    if not force:
        if not st["enabled"]:
            return status()
        last = st.get("checked_at")
        if last and now - datetime.fromisoformat(last) < CHECK_EVERY:
            return status()
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"sflow-analytics/{get_settings().app_version}"}
    try:
        c = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True)
        r = c.get(LATEST_URL, headers=headers)
        if r.status_code == 404:
            raise RuntimeError("no release published yet")
        r.raise_for_status()
        d = r.json()
        st.update(latest_version=str(d.get("tag_name", "")).lstrip("v"), release_url=d.get("html_url"),
                  release_name=d.get("name"), published_at=d.get("published_at"),
                  notes=(d.get("body") or "")[:20000], error=None)
    except Exception as exc:  # noqa: BLE001 - offline sites: keep the previous result, record the error
        st["error"] = f"{type(exc).__name__}: {exc}"[:300]
    st["checked_at"] = now.isoformat(timespec="seconds")
    _save(st)
    return status()
