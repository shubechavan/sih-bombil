"""seed_users.py — create the two demo accounts.

    python scripts/seed_users.py
    python scripts/seed_users.py --username jo --role admin --password '...'

Re-runnable. An existing username is left alone unless --force is given, so this
can sit in the compose seed chain next to load_fixtures without clobbering a
password somebody changed.

The demo passwords are weak and printed to the terminal on purpose: this is a
hackathon demo whose whole point is that a judge can sit down and use it. They
come from the environment so a real deployment can set something else, and the
script says plainly when it is using the default.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default Windows console codepage is cp1252, which cannot encode the box
# rules below and raises rather than degrading. Ask for UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

from sqlalchemy import select  # noqa: E402

from api.auth import hash_password  # noqa: E402
from db import User, require_schema, session_scope, utcnow  # noqa: E402

#: Weak, documented, and overridable. Named in README and DEMO.md.
DEMO_ACCOUNTS = (
    ("analyst", "analyst", "DEMO_ANALYST_PASSWORD", "analyst-demo"),
    ("admin", "admin", "DEMO_ADMIN_PASSWORD", "admin-demo"),
)


def upsert(session, username: str, password: str, role: str,
           *, force: bool) -> str:
    """Create or update one account. Returns what happened, for the report."""
    existing = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()

    if existing is not None and not force:
        return "kept"

    if existing is None:
        session.add(User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            disabled=False,
            created_at=utcnow(),
        ))
        return "created"

    existing.password_hash = hash_password(password)
    existing.role = role
    existing.disabled = False
    return "reset"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", help="create just this one account")
    parser.add_argument("--password", help="required with --username")
    parser.add_argument("--role", default="analyst", choices=["analyst", "admin"])
    parser.add_argument("--force", action="store_true",
                        help="reset the password of an account that already exists")
    args = parser.parse_args(argv)

    if args.username and not args.password:
        parser.error("--username requires --password")

    print("─" * 78)
    print("Dark Sentinel v2 — operator accounts")
    print("─" * 78)

    with session_scope() as session:
        require_schema(session)

        if args.username:
            outcome = upsert(session, args.username, args.password, args.role,
                             force=args.force)
            print(f"  {outcome:<8} {args.username}  ({args.role})")
        else:
            for username, role, env_var, default in DEMO_ACCOUNTS:
                password = os.environ.get(env_var, "").strip()
                using_default = not password
                password = password or default
                outcome = upsert(session, username, password, role,
                                 force=args.force)
                note = (f"password: {password}  ← default, set {env_var} to change"
                        if using_default else f"password: from ${env_var}")
                if outcome == "kept":
                    note = "already exists; --force to reset the password"
                print(f"  {outcome:<8} {username:<9} ({role:<7}) {note}")

        total = session.execute(select(User)).scalars().all()
        print(f"\n  {len(total)} account(s) in the database")

    print("\n  These are investigative-tool credentials. The demo defaults are")
    print("  weak by design so a judge can sign in; change them anywhere real.")
    print("─" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
