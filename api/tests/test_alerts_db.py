"""Alerting: rule validation, evaluation against the test dataset, life cycle, notifications."""

from datetime import timedelta

import pytest

from app import alerts, notify, store

from .conftest import EXP_A, T0, exp_id

IF48 = f"{exp_id(EXP_A)}/48"
UI = {"X-Requested-With": "sflow"}


class Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, event, channels, recipients=None):
        self.calls.append((event["state"], event["subject"], tuple(channels), tuple(recipients or ())))
        return {c: "ok" for c in channels}


@pytest.fixture()
def clean():
    alerts.ensure_default_rules()
    store.execute("DELETE FROM alerts")
    store.execute("UPDATE alert_rules SET enabled = 0")
    created = []
    yield created
    for rid in created:
        store.execute("DELETE FROM alert_rules WHERE id = ?", (rid,))
    store.execute("DELETE FROM alerts")


def _rule(created, kind, params, name=None, channels=("email",), **extra):
    r = alerts.save_rule({"name": name or kind, "kind": kind, "severity": "warning", "params": params,
                          "notify": {"channels": list(channels), "recipients": ["noc@example.com"]}, **extra})
    created.append(r["id"])
    return r


def test_default_rules_and_validation():
    alerts.ensure_default_rules()
    kinds = {r["kind"] for r in alerts.list_rules()}
    assert {"utilization", "discards", "broadcast", "exporter_silent"} <= kinds
    with pytest.raises(alerts.AlertError):
        alerts.validate("utilization", {"threshold_pct": 150}, {}, {})
    with pytest.raises(alerts.AlertError):
        alerts.validate("nope", {}, {}, {})
    with pytest.raises(alerts.AlertError):
        alerts.validate("traffic", {"target": "ip", "value": ""}, {}, {})
    p, s, n = alerts.validate("utilization", {"threshold_pct": "70", "junk": 1}, {"exporter": " x ", "bad": 1},
                              {"channels": ["email", "sms"], "recipients": ["a@b.c", "not-an-email"]})
    assert p == {"threshold_pct": 70.0, "window_min": 5, "direction": "either"}
    assert s == {"exporter": "x"} and n["channels"] == ["email"] and n["recipients"] == ["a@b.c"]


def test_utilization_alert_life_cycle(testdb, clean):
    _rule(clean, "utilization", {"threshold_pct": 8, "window_min": 5, "direction": "in"})
    send = Sender()
    now = T0 + timedelta(minutes=6, seconds=30)
    r = alerts.evaluate(testdb, now=now, sender=send)
    assert r["opened"] == 1 and not r["errors"]
    a = alerts.list_alerts()["items"][0]
    assert a["object_key"] == IF48 and a["state"] == "open" and a["value"] == pytest.approx(10, abs=0.2)
    assert a["link"].startswith("/interfaces/")
    assert send.calls == [("open", a["subject"], ("email",), ("noc@example.com",))]
    assert a["notified"]["open"]["result"] == {"email": "ok"}

    # still true: same alert, no new notification
    alerts.evaluate(testdb, now=now + timedelta(seconds=60), sender=send)
    assert len(alerts.list_alerts()["items"]) == 1 and len(send.calls) == 1

    alerts.acknowledge(a["id"], "admin")
    assert alerts.counts()["acknowledged"] == 1 and alerts.counts()["open"] == 0
    with pytest.raises(alerts.AlertError):
        alerts.acknowledge(a["id"], "admin")

    # no more counters after 10:10 -> condition false -> closed and notified
    r = alerts.evaluate(testdb, now=T0 + timedelta(minutes=30), sender=send)
    assert r["closed"] == 1 and r["active"] == 0
    closed = alerts.list_alerts("closed")["items"][0]
    assert closed["state"] == "closed" and closed["acked_by"] == "admin"
    assert send.calls[-1][0] == "closed"


def test_disabled_rule_and_scope(testdb, clean):
    _rule(clean, "utilization", {"threshold_pct": 8, "window_min": 5, "direction": "in"},
          scope={"exporter": exp_id(("192.0.2.2", "10.0.0.2", 0))})
    assert alerts.evaluate(testdb, now=T0 + timedelta(minutes=6, seconds=30), sender=Sender())["opened"] == 0
    r = _rule(clean, "utilization", {"threshold_pct": 8}, name="off", enabled=False)
    assert not r["enabled"]


def test_discards_broadcast_silent_and_traffic(testdb, clean):
    _rule(clean, "discards", {"threshold": 3, "window_min": 5, "metric": "discards"})
    _rule(clean, "broadcast", {"threshold_pps": 1, "window_min": 5})
    send = Sender()
    r = alerts.evaluate(testdb, now=T0 + timedelta(minutes=6, seconds=30), sender=send)
    kinds = {a["kind"] for a in alerts.list_alerts()["items"]}
    assert kinds == {"discards"} and r["opened"] == 1  # no broadcast in the dataset

    _rule(clean, "exporter_silent", {"silent_min": 5}, channels=())
    alerts.evaluate(testdb, now=T0 + timedelta(minutes=60), sender=send)
    silent = [a for a in alerts.list_alerts()["items"] if a["kind"] == "exporter_silent"]
    assert len(silent) == 2 and all(a["value"] >= 10 for a in silent)
    assert not any(c[1] in [a["subject"] for a in silent] for c in send.calls)  # no channel: no notification

    _rule(clean, "traffic", {"target": "ip", "value": "10.1.1.10", "threshold_bps": 1000, "window_min": 5,
                              "direction": "src"})
    alerts.evaluate(testdb, now=T0 + timedelta(minutes=6, seconds=30), sender=send)
    t = [a for a in alerts.list_alerts()["items"] if a["kind"] == "traffic"]
    assert len(t) == 1 and t[0]["subject"] == "Host 10.1.1.10" and "src_ip=10.1.1.10" in t[0]["link"]


def test_unknown_group_is_a_rule_error(testdb, clean):
    _rule(clean, "traffic", {"target": "ip_group", "value": "nope", "threshold_bps": 1})
    r = alerts.evaluate(testdb, now=T0 + timedelta(minutes=6, seconds=30), sender=Sender())
    assert "traffic" in r["errors"]


def test_alerts_api(client, testdb, clean):
    _rule(clean, "utilization", {"threshold_pct": 8, "window_min": 5, "direction": "in"}, channels=())
    alerts.evaluate(testdb, now=T0 + timedelta(minutes=6, seconds=30), sender=Sender())
    d = client.get("/api/v1/alerts").json()
    assert d["counts"]["open"] == 1 and d["items"][0]["rule"] == "utilization"
    aid = d["items"][0]["id"]
    assert client.post(f"/api/v1/alerts/{aid}/ack", headers=UI).status_code == 200
    assert client.post(f"/api/v1/alerts/{aid}/close", headers=UI).status_code == 200
    assert client.post("/api/v1/alerts/9999/ack", headers=UI).status_code == 404
    assert client.get("/api/v1/alerts", params={"state": "closed"}).json()["items"][0]["id"] == aid
    rules = client.get("/api/v1/alerts/rules").json()
    assert "utilization" in rules["kinds"]
    bad = client.post("/api/v1/alerts/rules", headers=UI,
                      json={"name": "x", "kind": "utilization", "params": {"threshold_pct": 0}})
    assert bad.status_code == 422


def test_notification_settings_hide_secrets(client):
    r = client.put("/api/v1/admin/notifications", headers=UI, json={
        "public_url": "https://sflow.example.com",
        "email": {"enabled": True, "host": "smtp.example.com", "port": 587, "sender": "sflow@example.com",
                  "recipients": ["noc@example.com"], "username": "u"},
        "webhook": {"enabled": True, "format": "slack"},
        "smtp_password": "s3cret-pass", "webhook_url": "https://hooks.example.com/abc/def"})
    assert r.status_code == 200, r.text
    assert "s3cret-pass" not in r.text and "abc/def" not in r.text
    d = r.json()
    assert d["email"]["password_set"] and d["webhook"]["url_set"] and d["webhook"]["url_hint"] == "hooks.example.com"
    # secrets are kept when omitted
    d = client.put("/api/v1/admin/notifications", headers=UI, json={"email": {"enabled": False}}).json()
    assert d["email"]["password_set"] and not d["email"]["enabled"]
    assert client.put("/api/v1/admin/notifications", headers=UI, json={"webhook_url": "http://insecure"}).status_code == 422
    assert client.post("/api/v1/admin/notifications/test/syslog", headers=UI).status_code == 502  # no host
    client.put("/api/v1/admin/notifications", headers=UI, json={"smtp_password": "", "webhook_url": ""})


def test_webhook_payloads():
    ev = {"state": "open", "severity": "critical", "rule": "Broadcast storm", "subject": "SW1 · 1/1/1",
          "message": "Broadcast 5,000 packets/s", "link": "/interfaces/x/1"}
    teams = notify._webhook_payload("teams", ev)
    assert teams["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert "Broadcast storm" in notify._webhook_payload("slack", ev)["text"]
    assert notify.title(ev).startswith("[sFlow Analytics] ALERT critical")
