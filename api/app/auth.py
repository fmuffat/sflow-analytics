"""Authentication: login/logout, current user dependency, password change."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import security
from .config import get_settings

COOKIE = "sflow_session"
router = APIRouter(prefix="/auth", tags=["auth"])

# Login throttling per client address (per API process).
_FAILS: dict[str, deque] = defaultdict(deque)
_FAIL_LOCK = threading.Lock()
MAX_FAILS, FAIL_WINDOW = 5, 300


def _client(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "?")


def _throttled(client: str) -> bool:
    now = time.monotonic()
    with _FAIL_LOCK:
        q = _FAILS[client]
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        return len(q) >= MAX_FAILS


def _record_fail(client: str) -> None:
    with _FAIL_LOCK:
        _FAILS[client].append(time.monotonic())


ANONYMOUS_ADMIN = {"id": 0, "username": "anonymous", "role": "admin", "must_change_password": False}


def current_user(request: Request) -> dict:
    """Dependency protecting the API. With AUTH_ENABLED=false (development
    only) every request is treated as an administrator."""
    if not get_settings().auth_enabled:
        return ANONYMOUS_ADMIN
    user = security.session_user(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "authentication required")
    # Mutating requests must come from the web UI (CSRF defence in depth,
    # in addition to the SameSite=Strict cookie).
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "sflow":
        raise HTTPException(403, "missing X-Requested-With header")
    return user


def require_ready_user(request: Request, user: dict = Depends(current_user)) -> dict:
    """Same as current_user, but refuses users who must first change their password,
    and gives read-only accounts (role viewer) no change and no Administration."""
    if user["must_change_password"]:
        raise HTTPException(403, "password change required")
    if user["role"] != "admin":
        admin_path = request.url.path.startswith("/api/v1/admin")
        if request.method not in ("GET", "HEAD", "OPTIONS") or admin_path:
            raise HTTPException(403, "read-only account")
    return user


def require_admin(user: dict = Depends(require_ready_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "administrator only")
    return user


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


@router.post("/login", summary="Open a session (sets an HttpOnly cookie)")
def login(body: LoginBody, request: Request, response: Response):
    client = _client(request)
    if _throttled(client):
        security.log_login(body.username, client, False, "throttled")
        raise HTTPException(429, "too many failed attempts, try again in a few minutes")
    user = security.verify_login(body.username, body.password)
    if not user:
        _record_fail(client)
        security.log_login(body.username, client, False, "invalid username or password")
        raise HTTPException(401, "invalid username or password")
    security.log_login(user["username"], client, True, user["role"])
    token = security.create_session(user["id"], client)
    s = get_settings()
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        path="/", max_age=s.session_max_hours * 3600)
    return {"username": user["username"], "role": user["role"],
            "must_change_password": bool(user["must_change_password"])}


@router.post("/logout", summary="Close the current session")
def logout(request: Request, response: Response):
    security.delete_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"status": "logged out"}


@router.get("/me", summary="Current user")
def me(user: dict = Depends(current_user)):
    return {**user, "auth_enabled": get_settings().auth_enabled}


class PasswordBody(BaseModel):
    current_password: str = Field(..., max_length=256)
    new_password: str = Field(..., max_length=256)


@router.post("/password", summary="Change own password (closes other sessions)")
def change_password(body: PasswordBody, request: Request, response: Response, user: dict = Depends(current_user)):
    if not get_settings().auth_enabled:
        raise HTTPException(400, "authentication is disabled")
    if not security.verify_login(user["username"], body.current_password):
        raise HTTPException(403, "current password is wrong")
    try:
        security.check_password_policy(body.new_password, user["username"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.new_password == body.current_password:
        raise HTTPException(422, "new password must differ from the current one")
    security.set_password(user["id"], body.new_password)
    # Re-open a session for this browser.
    token = security.create_session(user["id"], _client(request))
    s = get_settings()
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        path="/", max_age=s.session_max_hours * 3600)
    return {"status": "password changed"}
