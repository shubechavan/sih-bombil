"""apply_schema.py — run schema_v2.sql against the configured database.

CLAUDE.md documents `psql $PG_URL -f schema_v2.sql`, which is the right command
when psql is on PATH. It often is not — a Windows box with Postgres in Docker
has the server but no client binaries — and the fallback everyone reaches for is
an unreadable one-line `python -c`. This is that one-liner with a name.

The SQL is re-runnable by construction (`CREATE TABLE IF NOT EXISTS`,
`ADD COLUMN IF NOT EXISTS`, constraint guards in `DO $$` blocks), so applying it
to an up-to-date database is a no-op and applying it to a stale one migrates it.
It is executed as a single script through the raw DBAPI connection rather than
through SQLAlchemy's `text()`: the file contains `DO $$ ... $$` blocks whose
`$$`-quoting and semicolons SQLAlchemy would try to interpret as bind parameters
and statement boundaries.

    python scripts/apply_schema.py
    python scripts/apply_schema.py --check     # report drift, change nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default Windows console codepage is cp1252, which cannot encode the box
# rules or the em dashes below and raises rather than degrading. Ask for UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

from db import REQUIRED_TABLES, engine, pg_url  # noqa: E402

#: db.require_schema() looks in 'public' specifically, not current_schema().
#: Match it, so --check cannot disagree with what every entry point enforces.
SEARCH_SCHEMA = "public"

SCHEMA = ROOT / "schema_v2.sql"
RULE = "─" * 78


def existing_tables(connection) -> set[str]:
    cursor = connection.cursor()
    # BASE TABLE only: schema_v2.sql also defines the v_actor_summary view, and
    # information_schema.tables lists views too, which would report a view as a
    # created table and make the count disagree with REQUIRED_TABLES.
    cursor.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
        (SEARCH_SCHEMA,),
    )
    return {row[0] for row in cursor.fetchall()}


def redacted_url() -> str:
    """The connection target, with the password removed."""
    url = pg_url()
    if "@" in url and "//" in url:
        scheme, rest = url.split("//", 1)
        credentials, host = rest.split("@", 1)
        user = credentials.split(":", 1)[0]
        return f"{scheme}//{user}:***@{host}"
    return url


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply schema_v2.sql to the database in PG_* / PG_URL."
    )
    parser.add_argument("--check", action="store_true",
                        help="report which tables are missing and exit without "
                             "writing anything")
    parser.add_argument("--file", type=Path, default=SCHEMA,
                        help=f"SQL file to apply (default: {SCHEMA.name})")
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"no such file: {args.file}", file=sys.stderr)
        return 1

    print(RULE)
    print("Dark Sentinel v2 — schema")
    print(RULE)
    print(f"  target  {redacted_url()}")
    print(f"  script  {args.file.name}")

    try:
        connection = engine.raw_connection()
    except Exception as exc:
        print(f"\n  cannot connect: {type(exc).__name__}", file=sys.stderr)
        print(f"  {str(exc).strip().splitlines()[0]}", file=sys.stderr)
        print("\n  Is Postgres running? Everything except the database path "
              "works offline:", file=sys.stderr)
        print("    python scripts/evaluate.py", file=sys.stderr)
        print("    python -m recon.fingerprint --source fixtures --dry-run",
              file=sys.stderr)
        return 1

    try:
        before = existing_tables(connection)
        missing = sorted(set(REQUIRED_TABLES) - before)

        if args.check:
            if missing:
                print(f"\n  {len(missing)} table(s) missing: "
                      f"{', '.join(missing)}")
                print("  run without --check to apply")
                return 1
            print(f"\n  all {len(REQUIRED_TABLES)} expected tables present")
            return 0

        cursor = connection.cursor()
        cursor.execute(args.file.read_text(encoding="utf-8"))
        connection.commit()

        after = existing_tables(connection)
        created = sorted(after - before)
        still_missing = sorted(set(REQUIRED_TABLES) - after)

        print("\n  applied cleanly")
        if created:
            print(f"  created {len(created)} table(s): {', '.join(created)}")
        else:
            print("  no new tables — the script is re-runnable, so this")
            print("  is what an up-to-date database looks like")
        if still_missing:
            print(f"\n  STILL MISSING: {', '.join(still_missing)}",
                  file=sys.stderr)
            return 1
        print(f"  all {len(REQUIRED_TABLES)} expected tables present")
    except Exception as exc:
        connection.rollback()
        print(f"\n  failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    print(f"\n  next: python scripts/load_fixtures.py")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
