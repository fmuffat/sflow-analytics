"""User administration from the command line (e.g. forgotten admin password).

    docker compose exec api python -m app.users list
    docker compose exec api python -m app.users reset-password admin      # prints a new random password
    docker compose exec api python -m app.users create <name> [--role viewer|admin] [--password-file FILE]
    docker compose exec api python -m app.users set-role <name> viewer|admin
    docker compose exec api python -m app.users delete <name>

Reset and created passwords are random unless --password-file is given; the
user must change a reset password at next login.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from . import security, store


def _password(args: argparse.Namespace) -> tuple[str, bool]:
    if getattr(args, "password_file", None):
        pw = open(args.password_file, encoding="utf-8").read().strip()
        security.check_password_policy(pw, args.username)
        return pw, False
    return secrets.token_urlsafe(12), True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.users")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("create", "reset-password"):
        sp = sub.add_parser(name)
        sp.add_argument("username")
        sp.add_argument("--password-file", help="read the password from this file instead of generating one")
        sp.add_argument("--no-change", action="store_true", help="do not force a password change at next login")
        if name == "create":
            sp.add_argument("--role", choices=security.ROLES, default="admin",
                            help="admin (default) or viewer (read-only)")
    sp = sub.add_parser("set-role")
    sp.add_argument("username")
    sp.add_argument("role", choices=security.ROLES)
    sp = sub.add_parser("delete")
    sp.add_argument("username")
    args = p.parse_args(argv)

    if args.cmd == "list":
        for u in store.query("SELECT username, role, must_change_password, created_at, last_login_at FROM users ORDER BY username"):
            print(f"{u['username']:<20} {u['role']:<7} last login {u['last_login_at'] or 'never'}"
                  f"{'  (must change password)' if u['must_change_password'] else ''}")
        return 0

    user = store.one("SELECT id FROM users WHERE username = ?", (args.username,))
    if args.cmd == "set-role":
        if not user:
            print("no such user", file=sys.stderr)
            return 1
        if args.role != "admin" and store.one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND id != ?",
                                               (user["id"],))["n"] == 0:
            print("refusing to remove the last administrator", file=sys.stderr)
            return 1
        store.execute("UPDATE users SET role = ? WHERE id = ?", (args.role, user["id"]))
        store.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        print(f"{args.username}: {args.role}")
        return 0
    if args.cmd == "delete":
        if not user:
            print("no such user", file=sys.stderr)
            return 1
        if store.one("SELECT COUNT(*) AS n FROM users")["n"] <= 1:
            print("refusing to delete the last user", file=sys.stderr)
            return 1
        store.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        print(f"deleted {args.username}")
        return 0

    pw, generated = _password(args)
    must_change = 0 if args.no_change else 1
    if args.cmd == "create":
        if user:
            print("user already exists", file=sys.stderr)
            return 1
        store.execute("INSERT INTO users (username, password_hash, role, must_change_password, created_at) "
                      "VALUES (?, ?, ?, ?, ?)", (args.username, security.hash_password(pw), args.role, must_change,
                                                 security.now_iso()))
    else:
        if not user:
            print("no such user", file=sys.stderr)
            return 1
        security.set_password(user["id"], pw)
        store.execute("UPDATE users SET must_change_password = ? WHERE id = ?", (must_change, user["id"]))
    print(f"{args.cmd}: {args.username}" + (f"\npassword: {pw}" if generated else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
