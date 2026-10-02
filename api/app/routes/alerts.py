from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import alerts, notify
from ..auth import require_admin, require_ready_user
from ..db import Database, get_db

router = APIRouter(tags=["alerts"])


def _err(exc: Exception) -> HTTPException:
    return HTTPException(422 if not str(exc).startswith("no ") else 404, str(exc))


@router.get("/alerts", summary="Alerts (active: open and acknowledged; closed: history)")
def list_alerts(state: Literal["active", "closed", "all"] = "active", limit: int = Query(200, ge=1, le=2000)):
    return alerts.list_alerts(state, limit)


@router.get("/alerts/counts", summary="Number of active alerts (navigation badge)")
def counts():
    return alerts.counts()


@router.post("/alerts/{alert_id}/ack", summary="Acknowledge an open alert")
def ack(alert_id: int, user: dict = Depends(require_ready_user)):
    try:
        return alerts.acknowledge(alert_id, user["username"])
    except alerts.AlertError as exc:
        raise _err(exc) from exc


@router.post("/alerts/{alert_id}/close", summary="Close an alert by hand (reopens if the condition is still true)")
def close(alert_id: int, user: dict = Depends(require_ready_user)):
    try:
        return alerts.close(alert_id, user["username"])
    except alerts.AlertError as exc:
        raise _err(exc) from exc


@router.get("/alerts/rules", summary="Alert rules")
def rules(user: dict = Depends(require_ready_user)):
    items = alerts.list_rules()
    if user["role"] != "admin":  # recipients are personal data
        for r in items:
            r["notify"] = {"channels": r["notify"].get("channels", [])}
    return {"items": items, "kinds": alerts.PARAM_DEFAULTS, "severities": alerts.SEVERITIES, "channels": alerts.CHANNELS}


class RuleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: str
    enabled: bool = True
    severity: str = "warning"
    params: dict[str, Any] = {}
    scope: dict[str, Any] = {}
    notify: dict[str, Any] = {}


@router.post("/alerts/rules", summary="Create a rule")
def create_rule(body: RuleBody, _: dict = Depends(require_admin)):
    try:
        return alerts.save_rule(body.model_dump())
    except alerts.AlertError as exc:
        raise _err(exc) from exc


@router.put("/alerts/rules/{rule_id}", summary="Update a rule")
def update_rule(rule_id: int, body: RuleBody, _: dict = Depends(require_admin)):
    try:
        return alerts.save_rule(body.model_dump(), rule_id)
    except alerts.AlertError as exc:
        raise _err(exc) from exc


@router.delete("/alerts/rules/{rule_id}", summary="Delete a rule and its alerts")
def delete_rule(rule_id: int, _: dict = Depends(require_admin)):
    try:
        alerts.delete_rule(rule_id)
    except alerts.AlertError as exc:
        raise _err(exc) from exc
    return {"deleted": rule_id}


@router.post("/alerts/evaluate", summary="Evaluate the rules now (also done every minute by the worker)")
def evaluate(_: dict = Depends(require_admin), db: Database = Depends(get_db)):
    return alerts.evaluate(db)


# --- notification channels (administration) ---

class NotifyBody(BaseModel):
    public_url: str = Field("", max_length=300)
    email: dict[str, Any] = {}
    webhook: dict[str, Any] = {}
    syslog: dict[str, Any] = {}
    smtp_password: str | None = Field(None, max_length=500, description="omit to keep the stored one")
    webhook_url: str | None = Field(None, max_length=2000, description="omit to keep the stored one")


@router.get("/admin/notifications", summary="Notification channels (secrets never returned)")
def get_notifications():
    return notify.public_settings()


@router.put("/admin/notifications", summary="Update notification channels")
def put_notifications(body: NotifyBody):
    if body.public_url and not body.public_url.startswith(("https://", "http://")):
        raise HTTPException(422, "public URL must start with https://")
    try:
        return notify.save(body.model_dump(exclude={"smtp_password", "webhook_url"}), body.smtp_password, body.webhook_url)
    except notify.NotifyError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/admin/notifications/test/{channel}", summary="Send a test message on one channel")
def test_notification(channel: Literal["email", "webhook", "syslog"]):
    try:
        notify.test(channel)
    except notify.NotifyError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"channel": channel, "status": "sent"}
