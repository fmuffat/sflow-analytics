"""New version detection (GitHub releases), no network: httpx MockTransport."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import updates
from app.config import get_settings


def client(status=200, tag="v0.16.0", calls=None):
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        assert request.url.path == "/repos/fmuffat/sflow-analytics/releases/latest"
        return httpx.Response(status, json={"tag_name": tag, "name": f"sFlow Analytics {tag}",
                                            "html_url": f"https://github.com/fmuffat/sflow-analytics/releases/tag/{tag}",
                                            "published_at": "2026-10-10T08:00:00Z", "body": "### Added\n- Reports"})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "config_dir", str(tmp_path))
    monkeypatch.setattr(s, "app_version", "0.15.0")
    yield s


def test_parse_version():
    assert updates.parse_version("v0.15.0") == (0, 15, 0)
    assert updates.parse_version("1.2.3-beta") == (1, 2, 3)
    assert updates.parse_version("dev") is None
    assert updates.parse_version("0.10.0") > updates.parse_version("0.9.9")


def test_new_version_detected_once_a_day(fresh):
    calls = []
    now = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)
    s = updates.check(now=now, client=client(calls=calls))
    assert s["update_available"] and s["latest_version"] == "0.16.0" and s["notes"].startswith("### Added")
    assert s["release_url"].endswith("/v0.16.0") and s["error"] is None
    updates.check(now=now + timedelta(hours=5), client=client(calls=calls))
    assert len(calls) == 1  # not again within 24 h
    updates.check(now=now + timedelta(hours=25), client=client(calls=calls))
    assert len(calls) == 2
    updates.check(force=True, now=now + timedelta(hours=25, minutes=1), client=client(calls=calls))
    assert len(calls) == 3


def test_up_to_date_dev_build_and_disabled(fresh):
    assert not updates.check(client=client(tag="v0.15.0"))["update_available"]
    assert not updates.check(force=True, client=client(tag="v0.14.1"))["update_available"]
    fresh.app_version = "dev"
    s = updates.check(force=True, client=client())
    assert s["development_build"] and not s["update_available"]
    updates.set_enabled(False)
    calls = []
    assert updates.check(now=datetime(2030, 1, 1, tzinfo=timezone.utc), client=client(calls=calls))["enabled"] is False
    assert calls == []
    updates.check(force=True, client=client(calls=calls))  # "Check now" still works
    assert len(calls) == 1


def test_offline_keeps_previous_result(fresh):
    updates.check(force=True, client=client())

    def boom(request):
        raise httpx.ConnectError("no route to host")
    s = updates.check(force=True, client=httpx.Client(transport=httpx.MockTransport(boom)))
    assert "ConnectError" in s["error"] and s["latest_version"] == "0.16.0" and s["update_available"]
    s = updates.check(force=True, client=client(status=404))
    assert "no release" in s["error"]


def test_api(fresh):
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        d = c.get("/api/v1/admin/updates").json()
        assert d["current_version"] == "0.15.0" and d["enabled"] is True
        assert c.put("/api/v1/admin/updates", headers={"X-Requested-With": "sflow"}, json={"enabled": False}).json()["enabled"] is False


def test_apply_and_progress(fresh, tmp_path, monkeypatch):
    import json as _json
    d = tmp_path / "updates"
    d.mkdir()
    monkeypatch.setattr(updates, "UPDATES_DIR", d)
    updates.check(force=True, client=client(tag="v0.16.0"))
    # no host updater: refused
    with pytest.raises(updates.UpdateError, match="not available"):
        updates.apply("0.16.0")
    (d / "agent.json").write_text(_json.dumps({"updater": "sflow-update-x", "version": "0.15.0"}))
    assert updates.status()["updater"]["available"]
    with pytest.raises(updates.UpdateError, match="not a newer published"):
        updates.apply("0.17.0")
    r = updates.apply("0.16.0")
    assert (d / "request").read_text().strip() == "0.16.0" and r["progress"]["phase"] == "queued"
    assert updates.running()
    with pytest.raises(updates.UpdateError, match="already in progress"):
        updates.apply("0.16.0")
    # the host updater takes the request and reports progress
    (d / "request").unlink()
    (d / "status.json").write_text(_json.dumps({"phase": "installing", "message": "Installing 0.16.0",
                                                "target_version": "0.16.0", "log_tail": ["..."],
                                                "updated_at": datetime.now(timezone.utc).isoformat()}))
    assert updates.running() and updates.progress()["phase"] == "installing"
    (d / "status.json").write_text(_json.dumps({"phase": "done", "message": "Updated to 0.16.0",
                                                "updated_at": datetime.now(timezone.utc).isoformat()}))
    assert not updates.running()
    # a status stuck for more than an hour does not block a new attempt
    (d / "status.json").write_text(_json.dumps({"phase": "installing", "message": "x",
                                                "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}))
    assert not updates.running()
