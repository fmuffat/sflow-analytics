"""Passwords (Argon2id), sessions and encryption of secrets at rest."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from . import store
from .config import get_settings

log = logging.getLogger("api")
_hasher = PasswordHasher()  # Argon2id with the library's recommended parameters
_DUMMY_HASH = _hasher.hash("timing-equalizer")

MIN_PASSWORD_LENGTH = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Passwords -----------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_password_policy(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if username and password.lower() == username.lower():
        raise ValueError("password must differ from the username")


def verify_login(username: str, password: str) -> dict | None:
    """Returns the user row when the credentials are valid. Always runs one
    Argon2 verification so that unknown users take as long as known ones."""
    user = store.one("SELECT * FROM users WHERE username = ?", (username,))
    try:
        _hasher.verify(user["password_hash"] if user else _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None
    if not user:
        return None
    if _hasher.check_needs_rehash(user["password_hash"]):
        store.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user["id"]))
    return user


def set_password(user_id: int, password: str) -> None:
    store.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                  (hash_password(password), user_id))
    # Changing the password closes every other session.
    store.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def ensure_admin() -> None:
    """Creates the initial administrator on first start.

    Password: ADMIN_PASSWORD when set, otherwise a random one written once to
    <config_dir>/initial-admin-password (mode 0600) and to the log. The admin
    must change it at first login.
    """
    if store.one("SELECT id FROM users LIMIT 1"):
        return
    s = get_settings()
    password = s.admin_password or secrets.token_urlsafe(12)
    with store.transaction() as conn:
        if conn.execute("SELECT id FROM users LIMIT 1").fetchone():
            return
        conn.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password, created_at) VALUES (?, ?, 'admin', 1, ?)",
            (s.admin_username, hash_password(password), now_iso()))
    if not s.admin_password:
        path = Path(s.config_dir) / "initial-admin-password"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password + "\n")
        log.warning("created user %r with a random password: %s (also in %s) - change it at first login",
                    s.admin_username, password, path)


# --- Roles and sign-in log ----------------------------------------------------------

# admin: everything. viewer: read-only (no change, no Administration).
ROLES = ("admin", "viewer")
LOGIN_EVENTS_KEEP_DAYS = 180


def log_login(username: str, client: str, ok: bool, detail: str = "") -> None:
    store.execute("INSERT INTO login_events (at, username, client, ok, detail) VALUES (?, ?, ?, ?, ?)",
                  (now_iso(), username[:64], client[:200], 1 if ok else 0, detail[:200]))


def purge_login_events() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOGIN_EVENTS_KEEP_DAYS)).isoformat(timespec="seconds")
    return store.execute("DELETE FROM login_events WHERE at < ?", (cutoff,))


# --- Sessions --------------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, client: str) -> str:
    token = secrets.token_urlsafe(32)
    t = time.time()
    store.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, client) VALUES (?, ?, ?, ?, ?)",
                  (_token_hash(token), user_id, t, t, client[:200]))
    store.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user_id))
    return token


def session_user(token: str | None) -> dict | None:
    """User of a valid session (idle and absolute timeouts), refreshing its activity."""
    if not token:
        return None
    s = get_settings()
    h = _token_hash(token)
    row = store.one("""SELECT s.created_at, s.last_seen_at, u.id, u.username, u.role, u.must_change_password
                       FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""", (h,))
    if not row:
        return None
    t = time.time()
    if t - row["last_seen_at"] > s.session_idle_minutes * 60 or t - row["created_at"] > s.session_max_hours * 3600:
        store.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
        return None
    if t - row["last_seen_at"] > 30:  # limit writes
        store.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (t, h))
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "must_change_password": bool(row["must_change_password"])}


def delete_session(token: str | None) -> None:
    if token:
        store.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def purge_sessions() -> int:
    s = get_settings()
    t = time.time()
    return store.execute("DELETE FROM sessions WHERE last_seen_at < ? OR created_at < ?",
                         (t - s.session_idle_minutes * 60, t - s.session_max_hours * 3600))


# --- Secrets at rest -----------------------------------------------------------------

def _fernet() -> Fernet:
    """Key from SECRET_KEY, else a per-installation key file on the config volume."""
    s = get_settings()
    if s.secret_key:
        key = s.secret_key.encode()
    else:
        path = Path(s.config_dir) / "secret.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        key = path.read_bytes().strip()
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("secret cannot be decrypted (secret key changed?)") from exc


def set_setting(key: str, value: str, secret: bool = False) -> None:
    stored = encrypt(value) if secret else value
    store.execute("""INSERT INTO settings (key, value, secret, updated_at) VALUES (?, ?, ?, ?)
                     ON CONFLICT(key) DO UPDATE SET value = excluded.value, secret = excluded.secret,
                     updated_at = excluded.updated_at""", (key, stored, int(secret), now_iso()))


def get_setting(key: str, default: str | None = None) -> str | None:
    row = store.one("SELECT value, secret FROM settings WHERE key = ?", (key,))
    if not row:
        return default
    return decrypt(row["value"]) if row["secret"] else row["value"]
