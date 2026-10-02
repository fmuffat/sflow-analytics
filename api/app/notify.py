"""Notification channels for alerts: e-mail (SMTP), webhook (Teams, Slack, generic JSON), syslog.

Settings are stored in the configuration store (key `notify`, JSON) and the
secrets separately and encrypted (`notify.smtp_password`, `notify.webhook_url`).
Only administrators can read or change them; secrets are never returned.
"""

from __future__ import annotations

import json
import smtplib
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

import httpx

from . import security

KEY = "notify"
SECRET_KEYS = {"smtp_password": "notify.smtp_password", "webhook_url": "notify.webhook_url"}
DEFAULTS: dict[str, Any] = {
    "public_url": "",
    "email": {"enabled": False, "host": "", "port": 587, "security": "starttls", "username": "",
              "sender": "", "recipients": []},
    "webhook": {"enabled": False, "format": "teams"},
    "syslog": {"enabled": False, "host": "", "port": 514, "facility": 16},
}
TIMEOUT = 10


class NotifyError(RuntimeError):
    pass


def settings() -> dict[str, Any]:
    raw = security.get_setting(KEY)
    cfg = json.loads(raw) if raw else {}
    out = {k: (dict(v, **cfg.get(k, {})) if isinstance(v, dict) else cfg.get(k, v)) for k, v in DEFAULTS.items()}
    return out


def public_settings() -> dict[str, Any]:
    """Settings for the UI: secrets replaced by whether they are set."""
    s = settings()
    s["email"]["password_set"] = bool(security.get_setting(SECRET_KEYS["smtp_password"]))
    url = security.get_setting(SECRET_KEYS["webhook_url"]) or ""
    s["webhook"]["url_set"] = bool(url)
    s["webhook"]["url_hint"] = (url.split("/")[2] if url.count("/") >= 2 else "") if url else ""
    return s


def save(new: dict[str, Any], smtp_password: str | None = None, webhook_url: str | None = None) -> dict[str, Any]:
    cfg = {k: (dict(DEFAULTS[k], **new.get(k, {})) if isinstance(DEFAULTS[k], dict) else new.get(k, DEFAULTS[k]))
           for k in DEFAULTS}
    for sect in ("email", "webhook", "syslog"):
        cfg[sect] = {k: v for k, v in cfg[sect].items() if k in DEFAULTS[sect] or k == "enabled"}
    security.set_setting(KEY, json.dumps(cfg))
    if smtp_password is not None:
        security.set_setting(SECRET_KEYS["smtp_password"], smtp_password, secret=True)
    if webhook_url is not None:
        if webhook_url and not webhook_url.startswith("https://"):
            raise NotifyError("the webhook URL must start with https://")
        security.set_setting(SECRET_KEYS["webhook_url"], webhook_url, secret=True)
    return public_settings()


# --- message ---------------------------------------------------------------------------

def _link(event: dict[str, Any]) -> str:
    base = (settings().get("public_url") or "").rstrip("/")
    return f"{base}{event['link']}" if base and event.get("link") else ""


def title(event: dict[str, Any]) -> str:
    state = {"open": "ALERT", "closed": "RESOLVED", "test": "TEST"}.get(event["state"], event["state"].upper())
    return f"[sFlow Analytics] {state} {event['severity']}: {event['rule']} · {event['subject']}"


def text(event: dict[str, Any]) -> str:
    lines = [title(event), "", event["message"]]
    if event.get("opened_at"):
        lines.append(f"Since: {event['opened_at']}")
    if event.get("closed_at"):
        lines.append(f"Resolved: {event['closed_at']}")
    link = _link(event)
    if link:
        lines += ["", link]
    return "\n".join(lines)


# --- channels --------------------------------------------------------------------------

def send_email(event: dict[str, Any], recipients: list[str] | None = None) -> None:
    c = settings()["email"]
    to = [r for r in (recipients or c["recipients"]) if r]
    if not c["host"] or not c["sender"] or not to:
        raise NotifyError("e-mail: SMTP host, sender and at least one recipient are required")
    msg = EmailMessage()
    msg["Subject"] = title(event)
    msg["From"] = c["sender"]
    msg["To"] = ", ".join(to)
    msg.set_content(text(event))
    ctx = ssl.create_default_context()
    port = int(c["port"])
    try:
        if c["security"] == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(c["host"], port, timeout=TIMEOUT, context=ctx)
        else:
            server = smtplib.SMTP(c["host"], port, timeout=TIMEOUT)
        with server:
            if c["security"] == "starttls":
                server.starttls(context=ctx)
            password = security.get_setting(SECRET_KEYS["smtp_password"])
            if c["username"] and password:
                server.login(c["username"], password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise NotifyError(f"e-mail: {type(exc).__name__}: {exc}") from exc


def _webhook_payload(fmt: str, event: dict[str, Any]) -> dict[str, Any]:
    if fmt == "slack":
        return {"text": text(event)}
    if fmt == "teams":  # Teams "Workflows" incoming webhook: adaptive card
        body = [{"type": "TextBlock", "text": title(event), "weight": "Bolder", "wrap": True,
                 "color": "Attention" if event["state"] == "open" else "Good"},
                {"type": "TextBlock", "text": event["message"], "wrap": True}]
        link = _link(event)
        card: dict[str, Any] = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                                "type": "AdaptiveCard", "version": "1.4", "body": body}
        if link:
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Open", "url": link}]
        return {"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}
    return {"source": "sflow-analytics", "title": title(event), "text": text(event), "url": _link(event), **event}


def send_webhook(event: dict[str, Any]) -> None:
    url = security.get_setting(SECRET_KEYS["webhook_url"])
    if not url:
        raise NotifyError("webhook: URL not set")
    try:
        r = httpx.post(url, json=_webhook_payload(settings()["webhook"]["format"], event), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise NotifyError(f"webhook: {type(exc).__name__}: {exc}") from exc
    if r.status_code >= 300:
        raise NotifyError(f"webhook: HTTP {r.status_code} {r.text[:200]}")


def send_syslog(event: dict[str, Any]) -> None:
    c = settings()["syslog"]
    if not c["host"]:
        raise NotifyError("syslog: host is required")
    severity = {"critical": 2, "warning": 4, "info": 6}.get(event["severity"], 4)
    if event["state"] == "closed":
        severity = 5
    pri = int(c["facility"]) * 8 + severity
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    msg = f"<{pri}>1 {ts} {socket.gethostname()} sflow-analytics - alert - {title(event)} - {event['message']}"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode()[:2048], (c["host"], int(c["port"])))
    except OSError as exc:
        raise NotifyError(f"syslog: {exc}") from exc


SENDERS = {"email": send_email, "webhook": send_webhook, "syslog": send_syslog}


def dispatch(event: dict[str, Any], channels: list[str], recipients: list[str] | None = None) -> dict[str, str]:
    """Sends to each enabled channel; returns {channel: "ok" | error}. Never raises."""
    cfg = settings()
    out: dict[str, str] = {}
    for ch in channels:
        if ch not in SENDERS:
            continue
        if not cfg[ch]["enabled"]:
            out[ch] = "disabled"
            continue
        try:
            if ch == "email":
                send_email(event, recipients)
            else:
                SENDERS[ch](event)
            out[ch] = "ok"
        except NotifyError as exc:
            out[ch] = str(exc)
    return out


def test(channel: str) -> None:
    if channel not in SENDERS:
        raise NotifyError("unknown channel")
    event = {"state": "test", "severity": "info", "rule": "Test notification", "subject": "sFlow Analytics",
             "message": "This is a test message: the channel works.", "link": "/alerts"}
    SENDERS[channel](event)
