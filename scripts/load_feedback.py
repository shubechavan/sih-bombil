"""load_feedback.py — buyer feedback into the `feedback` table.

    python scripts/load_feedback.py --source fixtures
    python scripts/load_feedback.py --source live --input collected-feedback.json

Separate from `scripts/ingest.py` on purpose. Ingest owns the frozen document
contract — source, persona, posts, key blocks — and everything it writes feeds
attribution. Feedback feeds nothing. Running it through the same entry point
would put a stream that must never reach `score/` inside the function that
exists to fill the scoring tables, and the first person to add "just one more
field" would wire it in by accident.

WHY FEEDBACK IS NOT A LINK
--------------------------
Shared-buyer overlap was measured against ground truth before this writer was
built. Over the 78 pairs among the 13 personas that have any feedback — the
population where the signal exists at all — it separates true pairs from false
ones **worse than chance**:

    ROC-AUC              0.389        (0.500 is a coin flip)
    mean overlap, true   0.0250
    mean overlap, false  0.0852       — higher for the wrong pairs
    measurable positives 4 of 8       — the forum sells nothing

The strongest overlaps in the corpus — (18,20) and (1,4) at 0.444, (7,8) and
(3,4) at 0.429 — are all same-market vendor pairs sharing a buyer pool, and not
one is a true positive. Buyers shop around; that a vendor and their own alt
account were both rated by `runegate50` says only that `runegate50` buys a lot.

`python -m link.trust --measure` re-runs this against the live database.

So feedback lands here, shows up on the graph and the actor profile as
relationship context an analyst can read, and contributes nothing to A.

Both input shapes are accepted: the fixture rows carry `persona_id` directly,
the collector emits `source` + `vendor_handle` because a crawler reads handles
off a page and has no idea what the database numbered them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from db import (  # noqa: E402
    Feedback,
    Persona,
    Source,
    require_schema,
    session_scope,
    utcnow,
)
from extract.normalize import normalize  # noqa: E402
from ingest import _ts  # noqa: E402  (scripts/ is on sys.path above)

__all__ = ["load_rows", "resolve_rows", "write_feedback"]

FIXTURE_FEEDBACK = ROOT / "fixtures" / "feedback.json"


def load_rows(source: str, path: Optional[Path]) -> list[dict]:
    if source == "fixtures":
        path = path or FIXTURE_FEEDBACK
    if path is None:
        raise SystemExit("--source live needs --input")
    if not path.exists():
        raise SystemExit(f"{path}: not found")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: expected a JSON list")
    return rows


def resolve_rows(session, rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Attach persona_id and source_id. Returns (values, unresolved)."""
    by_id = {
        pid: (pid, sid)
        for pid, sid in session.execute(select(Persona.id, Persona.source_id))
    }
    by_handle = {
        (name, handle): (pid, sid)
        for pid, sid, handle, name in session.execute(
            select(Persona.id, Persona.source_id, Persona.handle, Source.name)
            .join(Source, Source.id == Persona.source_id)
        )
    }

    values: list[dict] = []
    unresolved: list[str] = []
    seen: set[tuple] = set()

    for row in rows:
        found = None
        if row.get("persona_id") is not None:
            found = by_id.get(row["persona_id"])
        if found is None and row.get("vendor_handle"):
            found = by_handle.get((row.get("source"), row["vendor_handle"]))
        if found is None:
            unresolved.append(
                f"{row.get('source') or '?'}/"
                f"{row.get('vendor_handle') or row.get('persona_id') or '?'}"
            )
            continue

        persona_id, source_id = found
        body = row.get("body") or ""
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        posted_at = _ts(row.get("posted_at"))
        buyer = row.get("buyer_handle") or ""

        key = (persona_id, buyer, posted_at, body_hash)
        if key in seen:
            continue  # the same page fetched twice in one run
        seen.add(key)

        values.append({
            "persona_id": persona_id,
            "source_id": row.get("source_id") or source_id,
            "buyer_handle": buyer,
            "buyer_normalized": normalize(buyer),
            "rating": row.get("rating"),
            "body": body or None,
            "posted_at": posted_at,
            "url": row.get("url"),
            "body_hash": body_hash,
            "collected_at": utcnow(),
        })

    return values, unresolved


def write_feedback(session, values: list[dict]) -> int:
    if not values:
        return 0
    statement = pg_insert(Feedback.__table__).values(values)
    session.execute(statement.on_conflict_do_update(
        constraint="feedback_persona_id_buyer_handle_posted_at_body_hash_key",
        set_={
            "source_id": statement.excluded.source_id,
            "buyer_normalized": statement.excluded.buyer_normalized,
            "rating": statement.excluded.rating,
            "url": statement.excluded.url,
            "collected_at": statement.excluded.collected_at,
        },
    ))
    return len(values)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load buyer feedback. Does not affect attribution scores."
    )
    parser.add_argument("--source", choices=("fixtures", "live"), default="fixtures")
    parser.add_argument("--input", type=Path,
                        help="feedback JSON; defaults to fixtures/feedback.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    rows = load_rows(args.source, args.input)
    print(f"  read {len(rows)} feedback row(s) from "
          f"{args.input or FIXTURE_FEEDBACK}")

    with session_scope() as session:
        require_schema(session)
        values, unresolved = resolve_rows(session, rows)

        if unresolved:
            print(f"  {len(unresolved)} row(s) name a vendor that is not in "
                  f"`personas`; skipped:")
            for line in sorted(set(unresolved))[:6]:
                print(f"    - {line}")

        if args.dry_run:
            print(f"\n  dry run — would write {len(values)} row(s)")
            return 0

        written = write_feedback(session, values)
        session.flush()
        buyers = len({v["buyer_normalized"] for v in values})
        vendors = len({v["persona_id"] for v in values})
        print(f"  wrote {written} feedback row(s): {buyers} distinct buyer(s) "
              f"across {vendors} vendor(s)")
        print("  attribution scores are unchanged — feedback is context, not a "
              "component of A")

    return 1 if not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
