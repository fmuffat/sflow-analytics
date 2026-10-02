"""Roles (admin / read-only viewer), user management and the sign-in log."""

from .test_auth import UI, login, secured  # noqa: F401 - fixture

NEW = "a-long-password"


def _ready_admin(c, password):
    assert login(c, password).status_code == 200
    r = c.post("/api/v1/auth/password", headers=UI, json={"current_password": password, "new_password": NEW})
    assert r.status_code == 200


def _ready(c, username, password, new="viewer-password-1"):
    assert login(c, password, username).status_code == 200
    assert c.post("/api/v1/auth/password", headers=UI,
                  json={"current_password": password, "new_password": new}).status_code == 200


def test_admin_creates_viewer_who_is_read_only(secured):
    c, password, _ = secured
    _ready_admin(c, password)
    r = c.post("/api/v1/admin/users", headers=UI, json={"username": "analytics", "role": "viewer"})
    assert r.status_code == 200, r.text
    viewer_pw = r.json()["password"]
    assert len(viewer_pw) >= 12
    assert c.post("/api/v1/admin/users", headers=UI, json={"username": "analytics"}).status_code == 409
    assert c.post("/api/v1/admin/users", headers=UI, json={"username": "bad name!"}).status_code == 422
    users = {u["username"]: u for u in c.get("/api/v1/admin/users").json()["items"]}
    assert users["analytics"]["role"] == "viewer" and users["analytics"]["must_change_password"]

    c.cookies.clear()
    _ready(c, "analytics", viewer_pw)
    me = c.get("/api/v1/auth/me").json()
    assert me["role"] == "viewer"
    # reading works
    assert c.get("/api/v1/health").status_code == 200
    # no change anywhere, no Administration (even reading it)
    assert c.put("/api/v1/groups", headers=UI, json={"kind": "ip", "name": "x", "members": ["10.0.0.1"]}).status_code == 403
    assert c.put("/api/v1/aliases", headers=UI, json={"kind": "ip", "key": "10.0.0.1", "name": "x"}).status_code == 403
    assert c.patch("/api/v1/exporters/1.1.1.1/1.1.1.1/0", headers=UI, json={"display_name": "x"}).status_code == 403
    for path in ("/api/v1/admin/config", "/api/v1/admin/users", "/api/v1/admin/enrichment", "/api/v1/admin/dns",
                 "/api/v1/admin/logins"):
        assert c.get(path).status_code == 403, path
    assert c.post("/api/v1/admin/users", headers=UI, json={"username": "evil", "role": "admin"}).status_code == 403
    # but a viewer can change their own password and sign out
    assert c.post("/api/v1/auth/password", headers=UI,
                  json={"current_password": "viewer-password-1", "new_password": "viewer-password-2"}).status_code == 200
    assert c.post("/api/v1/auth/logout", headers=UI).status_code == 200


def test_role_change_reset_and_delete(secured):
    c, password, _ = secured
    _ready_admin(c, password)
    pw = c.post("/api/v1/admin/users", headers=UI, json={"username": "colleague", "role": "admin"}).json()["password"]
    # cannot demote / delete the last administrator... there are two now
    assert c.put("/api/v1/admin/users/colleague/role", headers=UI, json={"role": "viewer"}).status_code == 200
    assert c.put("/api/v1/admin/users/admin/role", headers=UI, json={"role": "viewer"}).status_code == 409
    assert c.put("/api/v1/admin/users/nobody/role", headers=UI, json={"role": "viewer"}).status_code == 404
    assert c.delete("/api/v1/admin/users/admin", headers=UI).status_code == 409  # own account
    new_pw = c.post("/api/v1/admin/users/colleague/reset-password", headers=UI).json()["password"]
    assert new_pw != pw
    assert c.post("/api/v1/admin/users/admin/reset-password", headers=UI).status_code == 409
    assert c.delete("/api/v1/admin/users/colleague", headers=UI).status_code == 200
    assert "colleague" not in [u["username"] for u in c.get("/api/v1/admin/users").json()["items"]]


def test_role_change_closes_sessions(secured):
    c, password, _ = secured
    _ready_admin(c, password)
    pw = c.post("/api/v1/admin/users", headers=UI, json={"username": "bob", "role": "admin"}).json()["password"]
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as bob:
        _ready(bob, "bob", pw)
        assert bob.get("/api/v1/admin/users").status_code == 200
        assert c.put("/api/v1/admin/users/bob/role", headers=UI, json={"role": "viewer"}).status_code == 200
        assert bob.get("/api/v1/admin/users").status_code == 401  # signed out; signs in again as viewer


def test_sign_in_log(secured):
    c, password, _ = secured
    assert login(c, "wrong-password").status_code == 401
    assert login(c, password, "nobody").status_code == 401
    _ready_admin(c, password)
    r = c.get("/api/v1/admin/logins")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [(i["username"], i["ok"]) for i in items[:3]] == [("admin", True), ("nobody", False), ("admin", False)]
    assert r.json()["last_24h"] == {"total": 3, "failed": 2, "failed_clients": 1}
    assert all(i["ok"] is False for i in c.get("/api/v1/admin/logins", params={"failed_only": True}).json()["items"])


def test_forwarded_for_is_used_as_client(secured):
    c, _, _ = secured
    login(c, "wrong", "admin")
    c.post("/api/v1/auth/login", json={"username": "x", "password": "y"}, headers={"X-Forwarded-For": "203.0.113.9"})
    from app import store
    assert store.one("SELECT client FROM login_events ORDER BY id DESC LIMIT 1")["client"] == "203.0.113.9"


def test_users_cli_roles(secured, capsys):
    from app import store, users
    assert users.main(["create", "viewer1", "--role", "viewer"]) == 0
    assert store.one("SELECT role FROM users WHERE username = 'viewer1'")["role"] == "viewer"
    assert users.main(["set-role", "viewer1", "admin"]) == 0
    assert users.main(["set-role", "viewer1", "viewer"]) == 0
    assert users.main(["set-role", "admin", "viewer"]) == 1  # last administrator
    assert users.main(["set-role", "ghost", "viewer"]) == 1
