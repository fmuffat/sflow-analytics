"""User management (administrators only) and the sign-in log."""

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import security, store
from ..auth import require_admin

router = APIRouter(prefix="/admin", tags=["administration"], dependencies=[Depends(require_admin)])

Role = Literal["admin", "viewer"]


def _users() -> list[dict]:
    rows = store.query("""SELECT u.id, u.username, u.role, u.must_change_password, u.created_at, u.last_login_at,
                                 (SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id) AS sessions
                          FROM users u ORDER BY u.username COLLATE NOCASE""")
    return [{**dict(r), "must_change_password": bool(r["must_change_password"])} for r in rows]


def _get(username: str) -> dict:
    u = store.one("SELECT id, username, role FROM users WHERE username = ?", (username,))
    if not u:
        raise HTTPException(404, "no such user")
    return dict(u)


def _admins() -> int:
    return store.one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'")["n"]


@router.get("/users", summary="Accounts")
def list_users():
    return {"items": _users(), "roles": list(security.ROLES)}


class NewUser(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    role: Role = "viewer"


@router.post("/users", summary="Create an account (random password, to change at first sign-in)")
def create_user(body: NewUser):
    if store.one("SELECT id FROM users WHERE username = ?", (body.username,)):
        raise HTTPException(409, "user already exists")
    password = secrets.token_urlsafe(12)
    store.execute("INSERT INTO users (username, password_hash, role, must_change_password, created_at) "
                  "VALUES (?, ?, ?, 1, ?)", (body.username, security.hash_password(password), body.role, security.now_iso()))
    return {"username": body.username, "role": body.role, "password": password}


class RoleBody(BaseModel):
    role: Role


@router.put("/users/{username}/role", summary="Change the role of an account")
def set_role(username: str, body: RoleBody, me: dict = Depends(require_admin)):
    u = _get(username)
    if u["role"] == "admin" and body.role != "admin" and _admins() <= 1:
        raise HTTPException(409, "at least one administrator is required")
    store.execute("UPDATE users SET role = ? WHERE id = ?", (body.role, u["id"]))
    store.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))  # new rights at next sign-in
    return {"username": u["username"], "role": body.role}


@router.post("/users/{username}/reset-password", summary="New random password (to change at next sign-in)")
def reset_password(username: str, me: dict = Depends(require_admin)):
    u = _get(username)
    if u["id"] == me["id"]:
        raise HTTPException(409, "use 'change password' for your own account")
    password = secrets.token_urlsafe(12)
    security.set_password(u["id"], password)  # also closes the user's sessions
    store.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (u["id"],))
    return {"username": u["username"], "password": password}


@router.delete("/users/{username}", summary="Delete an account (closes its sessions)")
def delete_user(username: str, me: dict = Depends(require_admin)):
    u = _get(username)
    if u["id"] == me["id"]:
        raise HTTPException(409, "you cannot delete your own account")
    if u["role"] == "admin" and _admins() <= 1:
        raise HTTPException(409, "at least one administrator is required")
    store.execute("DELETE FROM users WHERE id = ?", (u["id"],))
    return {"deleted": u["username"]}


@router.get("/logins", summary="Sign-in log (successes and failures, kept 180 days)")
def logins(limit: int = Query(200, ge=1, le=2000), failed_only: bool = False):
    where = "WHERE ok = 0" if failed_only else ""
    rows = store.query(f"SELECT at, username, client, ok, detail FROM login_events {where} ORDER BY id DESC LIMIT ?", (limit,))
    stats = store.one("""SELECT COUNT(*) AS total, SUM(ok = 0) AS failed,
                                COUNT(DISTINCT CASE WHEN ok = 0 THEN client END) AS failed_clients
                         FROM login_events WHERE at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 day')""")
    return {"items": [{**dict(r), "ok": bool(r["ok"])} for r in rows],
            "last_24h": {k: int(stats[k] or 0) for k in ("total", "failed", "failed_clients")}}
