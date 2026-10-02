"""Authentication, sessions, configuration store and worker (no ClickHouse needed)."""

import os
import stat
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth, security, store, worker
from app.config import get_settings
from app.db import get_db
from app.main import app

UI = {"X-Requested-With": "sflow"}


class FakeDB:
    name = "none"

    def ping(self):
        return True


@pytest.fixture()
def secured(tmp_path, monkeypatch):
    """Authentication enabled, fresh configuration store in a temp dir."""
    s = get_settings()
    monkeypatch.setattr(s, "auth_enabled", True)
    monkeypatch.setattr(s, "config_dir", str(tmp_path))
    monkeypatch.setattr(s, "admin_password", "")
    auth._FAILS.clear()
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: FakeDB()
    security.ensure_admin()
    password = (tmp_path / "initial-admin-password").read_text().strip()
    with TestClient(app) as c:
        yield c, password, tmp_path
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def login(c, password, username="admin"):
    return c.post("/api/v1/auth/login", json={"username": username, "password": password})


def test_initial_admin_password_file_is_private(secured):
    _, password, tmp = secured
    f = tmp / "initial-admin-password"
    assert len(password) >= 12
    if os.name == "posix":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    row = store.one("SELECT password_hash, must_change_password FROM users WHERE username = 'admin'")
    assert row["password_hash"].startswith("$argon2id$") and row["must_change_password"] == 1


def test_api_requires_login(secured):
    c, _, _ = secured
    assert c.get("/api/v1/health").status_code == 200  # public
    assert c.get("/api/v1/traffic/top-sources").status_code == 401
    assert c.get("/api/v1/auth/me").status_code == 401


def test_first_login_forces_password_change(secured):
    c, password, _ = secured
    r = login(c, password)
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/v1/system/status").status_code == 403  # password change required
    assert c.get("/api/v1/auth/me").json()["username"] == "admin"

    # CSRF header required on mutations; policy enforced.
    assert c.post("/api/v1/auth/password", json={"current_password": password, "new_password": "x" * 12}).status_code == 403
    assert c.post("/api/v1/auth/password", headers=UI, json={"current_password": password, "new_password": "short"}).status_code == 422
    assert c.post("/api/v1/auth/password", headers=UI, json={"current_password": "wrong", "new_password": "a-long-password"}).status_code == 403
    r = c.post("/api/v1/auth/password", headers=UI, json={"current_password": password, "new_password": "a-long-password"})
    assert r.status_code == 200
    assert c.get("/api/v1/auth/me").json()["must_change_password"] is False
    assert c.get("/api/v1/admin/config").status_code == 200

    # Old password no longer works; logout closes the session.
    assert login(c, password).status_code == 401
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401


def test_login_throttling(secured):
    c, _, _ = secured
    for _ in range(auth.MAX_FAILS):
        assert login(c, "wrong-password").status_code == 401
    assert login(c, "wrong-password").status_code == 429
    assert login(c, "nobody-knows").status_code == 429


def test_unknown_user_and_wrong_password_look_the_same(secured):
    c, _, _ = secured
    a = login(c, "wrong-password")
    b = login(c, "whatever", username="ghost")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_idle_session_expires(secured, monkeypatch):
    c, password, _ = secured
    assert login(c, password).status_code == 200
    real = time.time
    monkeypatch.setattr(security.time, "time", lambda: real() + get_settings().session_idle_minutes * 60 + 5)
    assert c.get("/api/v1/auth/me").status_code == 401


def test_secrets_are_encrypted_at_rest(secured):
    _, _, tmp = secured
    security.set_setting("smartzone.password", "s3cr3t-value", secret=True)
    security.set_setting("dns.servers", "10.0.0.53")
    raw = store.one("SELECT value FROM settings WHERE key = 'smartzone.password'")["value"]
    assert "s3cr3t" not in raw and security.get_setting("smartzone.password") == "s3cr3t-value"
    assert security.get_setting("dns.servers") == "10.0.0.53"
    assert (tmp / "secret.key").exists()
    assert "s3cr3t" not in Path(store.db_path()).read_bytes().decode("latin-1")


def test_worker_records_job_runs(secured, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "wait_for_schema", lambda stop, timeout=300: True)

    @worker.job("test-ok", every=3600)
    def ok_job():
        calls.append(1)
        return "done"

    @worker.job("test-fail", every=3600)
    def failing_job():
        raise RuntimeError("boom")

    try:
        assert worker.run_job(worker.JOBS["test-ok"]) is True
        assert worker.run_job(worker.JOBS["test-fail"]) is False
        rows = {r["name"]: r for r in store.query("SELECT * FROM jobs")}
        assert rows["test-ok"]["last_status"] == "ok" and rows["test-ok"]["runs"] == 1
        assert rows["test-fail"]["failures"] == 1 and "boom" in rows["test-fail"]["last_error"]

        stop = threading.Event()
        t = threading.Thread(target=worker.run_forever, args=(stop,))
        t.start()
        time.sleep(0.5)
        stop.set()
        t.join(5)
        assert not t.is_alive() and len(calls) >= 2
    finally:
        worker.JOBS.pop("test-ok", None)
        worker.JOBS.pop("test-fail", None)


def test_worker_waits_for_schema(monkeypatch):
    class DB:
        name = "sflow"
        answers = [Exception("starting"), 2, 4]

        def scalar(self, *_):
            v = self.answers.pop(0)
            if isinstance(v, Exception):
                raise v
            return v

    db = DB()
    monkeypatch.setattr("app.db.get_db", lambda: db)
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda s: None)
    assert worker.wait_for_schema(stop, timeout=5) is True and db.answers == []


def test_users_cli(secured, tmp_path, capsys):
    from app import users

    pw = tmp_path / "pw"
    pw.write_text("a-long-password\n")
    assert users.main(["create", "ops", "--password-file", str(pw), "--no-change"]) == 0
    assert users.main(["create", "ops", "--password-file", str(pw)]) == 1  # exists
    c, _, _ = secured
    assert login(c, "a-long-password", username="ops").status_code == 200
    assert c.get("/api/v1/admin/config").status_code == 200

    assert users.main(["reset-password", "ops"]) == 0
    new = capsys.readouterr().out.strip().splitlines()[-1].removeprefix("password: ")
    assert c.get("/api/v1/auth/me").status_code == 401  # sessions closed by the reset
    assert login(c, new, username="ops").json()["must_change_password"] is True

    pw.write_text("short")
    with pytest.raises(ValueError):
        users.main(["create", "weak", "--password-file", str(pw)])
    assert users.main(["delete", "ops"]) == 0 and users.main(["delete", "ops"]) == 1
