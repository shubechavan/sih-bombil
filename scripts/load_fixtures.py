"""
load_fixtures.py — seed the demo corpus into Postgres.

    python scripts/load_fixtures.py            # idempotent upsert
    python scripts/load_fixtures.py --reset    # wipe the data tables first
    python scripts/load_fixtures.py --dry-run  # validate and report, write nothing

Loads sources, personas, identifiers, posts and the onion-side recon findings.

Two things it deliberately does NOT do:

  * It never populates `actors` or `personas.actor_id`. fixtures/ground_truth.json
    is an answer key, not input. Seeding the clusters would hand Phase 2's
    resolver the very thing it is supposed to derive, and scripts/evaluate.py
    would be measuring nothing.
  * It never stores an identifier whose checksum fails. CLAUDE.md is explicit —
    a regex-only match on a hex string poisons the link graph — so the corrupted
    wallets in the corpus are counted, reported and dropped.

Every run writes a row to `scans`: operator identity, mode, and a SHA-256 over
the payload it loaded. The loader is a scan like any other.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "legacy"))

from sqlalchemy import delete, func, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from db import (  # noqa: E402
    IDENTIFIER_TYPES,
    Identifier,
    InfraFinding,
    Persona,
    PersonaIdentifier,
    Post,
    Scan,
    finish_scan,
    record_scan,
    Source,
    require_schema,
    session_scope,
    utcnow,
)
from extract.normalize import normalize_handle, normalize_identifier  # noqa: E402

FIXTURES = ROOT / "fixtures"
SOURCE_DIRS = ("market_alpha", "forum_beta", "market_gamma")

#: Wiped by --reset. `scans` is excluded on purpose: an audit log you can clear
#: as a side effect of reloading demo data is not an audit log.
DATA_TABLES = (
    "infra_correlations", "infra_findings", "links", "writeprints", "posts",
    "persona_identifiers", "identifiers", "personas", "actors", "sources",
)


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation
#
# Phase 1 took ownership of these: they now live in extract/normalize.py, built
# on the same v1 leet_decode this loader used, so the handle_normalized values
# already in the database are unchanged.
# ─────────────────────────────────────────────────────────────────────────────

def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# ─────────────────────────────────────────────────────────────────────────────
# Reading the corpus
# ─────────────────────────────────────────────────────────────────────────────

def _read(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(
            f"missing fixture file: {path}\nrun: python scripts/gen_fixtures.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def read_corpus() -> dict:
    personas: list[dict] = []
    posts: list[dict] = []
    for name in SOURCE_DIRS:
        personas.extend(_read(FIXTURES / name / "personas.json"))
        posts.extend(_read(FIXTURES / name / "posts.json"))
    return {
        "sources": _read(FIXTURES / "sources.json"),
        "personas": personas,
        "posts": posts,
        "infra": _read(FIXTURES / "infra_findings.json"),
        "key_blocks": _read(FIXTURES / "pgp_blocks.json"),
    }


def persona_texts(corpus: dict) -> dict[int, str]:
    """Everything a reader of this persona could actually see.

    Bio, every post title and body, and any armoured key block they published.
    This is the same surface scripts/ingest.py runs the extractor over, so an
    identifier absent from it is one no extractor in the pipeline could ever
    produce.
    """
    texts: dict[int, list[str]] = {
        persona["id"]: [persona.get("bio") or ""] for persona in corpus["personas"]
    }
    for post in corpus["posts"]:
        bucket = texts.setdefault(post["persona_id"], [])
        bucket.append(post.get("title") or "")
        bucket.append(post.get("body") or "")
    for block in corpus.get("key_blocks") or []:
        texts.setdefault(block["persona_id"], []).append(block.get("armored") or "")
    return {pid: "\n".join(parts) for pid, parts in texts.items()}


def appears_in_text(value: str, text: str) -> bool:
    """Is this identifier actually written down where the pipeline can read it?

    Case-insensitive, and compared with whitespace removed as well, because a
    PGP fingerprint is often printed in spaced groups of four.
    """
    lowered, needle = text.lower(), value.lower()
    if needle in lowered:
        return True
    return _SPACE.sub("", needle) in _SPACE.sub("", lowered)


_SPACE = re.compile(r"\s+")


def payload_hash(corpus: dict) -> str:
    """SHA-256 over exactly what is about to be written."""
    canonical = json.dumps(corpus, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────

def _resync_sequence(session, table: str) -> None:
    """Point the serial sequence past the explicit ids we just inserted."""
    session.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
    ))


def load_sources(session, rows: list[dict]) -> int:
    statement = pg_insert(Source.__table__).values([
        {
            "id": row["id"],
            "name": row["name"],
            "url": row["url"],
            "kind": row["kind"],
            "is_onion": row["is_onion"],
            "reliability": row["reliability"],
            "first_seen": _ts(row["first_seen"]),
            "last_scan_at": _ts(row["last_scan_at"]),
            "active": row["active"],
        }
        for row in rows
    ])
    session.execute(statement.on_conflict_do_update(
        index_elements=[Source.__table__.c.id],
        set_={
            "name": statement.excluded.name,
            "url": statement.excluded.url,
            "kind": statement.excluded.kind,
            "is_onion": statement.excluded.is_onion,
            "reliability": statement.excluded.reliability,
            "last_scan_at": statement.excluded.last_scan_at,
            "active": statement.excluded.active,
        },
    ))
    _resync_sequence(session, "sources")
    return len(rows)


def load_personas(session, rows: list[dict], post_counts: dict[int, int]) -> int:
    statement = pg_insert(Persona.__table__).values([
        {
            "id": row["id"],
            "source_id": row["source_id"],
            # actor_id stays NULL: the resolver has to earn it
            "actor_id": None,
            "handle": row["handle"],
            "handle_normalized": normalize_handle(row["handle"]),
            "profile_url": row["profile_url"],
            "bio": row["bio"],
            "category": row["category"],
            "trust_score": row["trust_score"],
            "post_count": post_counts.get(row["id"], 0),
            "first_seen": _ts(row["first_seen"]),
            "last_seen": _ts(row["last_seen"]),
            "last_scan_at": _ts(row["last_scan_at"]),
        }
        for row in rows
    ])
    session.execute(statement.on_conflict_do_update(
        index_elements=[Persona.__table__.c.id],
        set_={
            "source_id": statement.excluded.source_id,
            "handle": statement.excluded.handle,
            "handle_normalized": statement.excluded.handle_normalized,
            "profile_url": statement.excluded.profile_url,
            "bio": statement.excluded.bio,
            "category": statement.excluded.category,
            "trust_score": statement.excluded.trust_score,
            "post_count": statement.excluded.post_count,
            "first_seen": statement.excluded.first_seen,
            "last_seen": statement.excluded.last_seen,
            "last_scan_at": statement.excluded.last_scan_at,
        },
    ))
    _resync_sequence(session, "personas")
    return len(rows)


def load_identifiers(session, personas: list[dict],
                     texts: dict[int, str]) -> tuple[int, int, list[str]]:
    """Insert identifiers and their persona links.

    Returns (stored, dropped, notes). Two things are dropped rather than stored:
    anything the corpus flags as failing its checksum, and anything that appears
    in no bio, no post and no key block.

    The second rule matters more than it looks. `personas.json` declares what is
    *true* of a persona; the pipeline can only ever know what is *written down*.
    Seeding a declared-but-unwritten value meant `--source db` could show
    evidence — "3~18 share a mirror onion" — that no extractor in this system
    could produce, and that `--source fixtures` therefore never showed. Those
    are the six recorded in docs/BUILD_PLAN.md as unreachable. Dropping them
    changes no score (measured: all 190 pairs identical, because 3~18 already
    saturates H on two PGP fingerprints and the other five sit on a single
    persona each) and makes the two source modes agree on the evidence as well
    as the numbers.
    """
    catalogue: dict[tuple[str, str], dict] = {}
    links: list[tuple[str, str, int, str]] = []
    dropped: list[str] = []

    for persona in personas:
        first_seen = _ts(persona["first_seen"])
        last_seen = _ts(persona["last_seen"])
        text_for_persona = texts.get(persona["id"], "")
        for identifier in persona["identifiers"]:
            kind, value = identifier["type"], identifier["value"]

            if not identifier.get("valid", True):
                dropped.append(
                    f"persona {persona['id']} ({persona['handle']}): "
                    f"{kind} {value} - failed checksum"
                )
                continue
            if kind not in IDENTIFIER_TYPES:
                dropped.append(
                    f"persona {persona['id']} ({persona['handle']}): "
                    f"unknown identifier type {kind!r}"
                )
                continue
            if not appears_in_text(value, text_for_persona):
                dropped.append(
                    f"persona {persona['id']} ({persona['handle']}): "
                    f"{kind} {value} - declared but in no bio, post or key block, "
                    f"so no extractor could derive it"
                )
                continue

            key = (kind, value)
            entry = catalogue.get(key)
            if entry is None:
                catalogue[key] = {
                    "type": kind,
                    "value": value,
                    "value_norm": normalize_identifier(kind, value),
                    "validated": True,
                    "meta": {"source": "fixtures"},
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                }
            else:
                # a shared identifier's window spans every persona that used it
                if first_seen and (entry["first_seen"] is None or first_seen < entry["first_seen"]):
                    entry["first_seen"] = first_seen
                if last_seen and (entry["last_seen"] is None or last_seen > entry["last_seen"]):
                    entry["last_seen"] = last_seen

            links.append((kind, value, persona["id"],
                          identifier.get("context") or ""))

    if not catalogue:
        return 0, len(dropped), dropped

    statement = pg_insert(Identifier.__table__).values(list(catalogue.values()))
    session.execute(statement.on_conflict_do_update(
        index_elements=[Identifier.__table__.c.type, Identifier.__table__.c.value],
        set_={
            "value_norm": statement.excluded.value_norm,
            "validated": statement.excluded.validated,
            "meta": statement.excluded.meta,
            "first_seen": func.least(Identifier.__table__.c.first_seen,
                                     statement.excluded.first_seen),
            "last_seen": func.greatest(Identifier.__table__.c.last_seen,
                                       statement.excluded.last_seen),
        },
    ))
    _resync_sequence(session, "identifiers")

    ids = {
        (row.type, row.value): row.id
        for row in session.execute(select(Identifier.__table__.c.id,
                                          Identifier.__table__.c.type,
                                          Identifier.__table__.c.value))
    }

    join_rows = [
        {
            "persona_id": persona_id,
            "identifier_id": ids[(kind, value)],
            "context": context[:500],
            "confidence": 1.0,
            "observed_at": utcnow(),
        }
        for kind, value, persona_id, context in links
    ]
    join_statement = pg_insert(PersonaIdentifier.__table__).values(join_rows)
    session.execute(join_statement.on_conflict_do_update(
        index_elements=[PersonaIdentifier.__table__.c.persona_id,
                        PersonaIdentifier.__table__.c.identifier_id],
        set_={"context": join_statement.excluded.context},
    ))

    return len(catalogue), len(dropped), dropped


def load_posts(session, rows: list[dict]) -> int:
    values = []
    for row in rows:
        body = row["body"]
        values.append({
            "id": row["id"],
            "persona_id": row["persona_id"],
            "source_id": row["source_id"],
            "url": row["url"],
            "title": row["title"],
            "body": body,
            "body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "category": row["category"],
            "posted_at": _ts(row["posted_at"]),
            "scraped_at": utcnow(),
        })

    statement = pg_insert(Post.__table__).values(values)
    session.execute(statement.on_conflict_do_update(
        index_elements=[Post.__table__.c.id],
        set_={
            "persona_id": statement.excluded.persona_id,
            "source_id": statement.excluded.source_id,
            "url": statement.excluded.url,
            "title": statement.excluded.title,
            "body": statement.excluded.body,
            "body_hash": statement.excluded.body_hash,
            "category": statement.excluded.category,
            "posted_at": statement.excluded.posted_at,
        },
    ))
    _resync_sequence(session, "posts")
    return len(values)


def load_infra(session, rows: list[dict]) -> int:
    """Replace the fixture findings for these onions.

    infra_findings has no natural key — a real scan appends a new observation
    every time — so idempotency here means clearing this onion's fixture rows
    before writing them again.

    `header_order` is derived from the key order of the fixture `headers` object,
    which json.loads preserves. It has to be stored separately because JSONB
    re-sorts object keys, and recon/correlate.py matches on that order.
    """
    urls = [row["onion_url"] for row in rows]
    session.execute(delete(InfraFinding.__table__).where(
        InfraFinding.__table__.c.onion_url.in_(urls)
    ))
    session.execute(pg_insert(InfraFinding.__table__).values([
        {
            **{k: v for k, v in row.items()
               if k not in {"scanned_at", "tls_not_before"}},
            "header_order": list(row.get("header_order")
                                 or (row.get("headers") or {})),
            "tls_not_before": _ts(row.get("tls_not_before")),
            "scanned_at": _ts(row["scanned_at"]),
        }
        for row in rows
    ]))
    _resync_sequence(session, "infra_findings")
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

COUNT_TABLES = (
    "sources", "actors", "personas", "identifiers", "persona_identifiers",
    "posts", "writeprints", "links", "infra_findings", "infra_correlations",
    "scans",
)


def row_counts(session) -> dict[str, int]:
    return {
        table: session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        for table in COUNT_TABLES
    }


def print_counts(counts: dict[str, int]) -> None:
    width = max(len(name) for name in counts)
    print("\n  table" + " " * (width - 4) + "rows")
    print("  " + "-" * (width + 8))
    for table, count in counts.items():
        print(f"  {table.ljust(width)}  {count:>6}")


def print_lines(title: str, lines: Iterable[str]) -> None:
    lines = list(lines)
    if not lines:
        return
    print(f"\n  {title}")
    for line in lines:
        print(f"    - {line}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Load the demo corpus into Postgres.")
    parser.add_argument("--reset", action="store_true",
                        help="truncate the data tables first (scans is preserved)")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the corpus and report, without writing")
    parser.add_argument("--operator", default=None,
                        help="operator id for the audit row (default: $OPERATOR_ID)")
    args = parser.parse_args()

    import os
    operator = args.operator or os.environ.get("OPERATOR_ID") or "unknown"

    corpus = read_corpus()
    action_hash = payload_hash(corpus)

    post_counts: dict[int, int] = {}
    for post in corpus["posts"]:
        post_counts[post["persona_id"]] = post_counts.get(post["persona_id"], 0) + 1

    invalid = [
        (persona, identifier)
        for persona in corpus["personas"]
        for identifier in persona["identifiers"]
        if not identifier.get("valid", True)
    ]

    print(f"corpus: {len(corpus['sources'])} sources, "
          f"{len(corpus['personas'])} personas, {len(corpus['posts'])} posts, "
          f"{len(corpus['infra'])} infra findings")
    print(f"action sha256: {action_hash}")
    print(f"operator: {operator}")

    if args.dry_run:
        print_lines(
            "would drop (failed checksum)",
            [f"persona {p['id']} ({p['handle']}): {i['type']} {i['value']}"
             for p, i in invalid],
        )
        print("\ndry run - nothing written")
        return 0

    started = utcnow()
    with session_scope() as session:
        require_schema(session)

        if args.reset:
            session.execute(text(
                "TRUNCATE " + ", ".join(DATA_TABLES) + " RESTART IDENTITY CASCADE"
            ))
            print("\nreset: data tables truncated (scans preserved)")

        scan = record_scan(
            session,
            operator=operator,
            mode="manual",
            data_source="fixtures",
            query="load_fixtures",
            sources_touched=[s["name"] for s in corpus["sources"]],
            action_hash=action_hash,
            started_at=started,
        )

        try:
            load_sources(session, corpus["sources"])
            personas_loaded = load_personas(session, corpus["personas"], post_counts)
            stored, dropped, notes = load_identifiers(session, corpus["personas"], persona_texts(corpus))
            posts_loaded = load_posts(session, corpus["posts"])
            infra_loaded = load_infra(session, corpus["infra"])
        except Exception as exc:
            finish_scan(session, scan, status="failed", error=exc)
            raise

        finish_scan(session, scan, personas_new=personas_loaded)

        print(f"\n  loaded {personas_loaded} personas, {stored} identifiers, "
              f"{posts_loaded} posts, {infra_loaded} infra findings")
        print_lines(f"dropped {dropped} identifier(s)", notes)
        print("\n  actors table left empty and personas.actor_id left NULL -")
        print("  ground_truth.json is the answer key, not an input")

        print_counts(row_counts(session))
        print(f"\n  audit row: scan id {scan.id}, operator {operator}, "
              f"sha256 {action_hash[:16]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
