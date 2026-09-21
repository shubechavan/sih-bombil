"""
ingest.py — text in, personas / identifiers / posts out.

    python scripts/ingest.py --source fixtures
    python scripts/ingest.py --source fixtures --dry-run
    python scripts/ingest.py --source live --input scraped.json

This is the Phase 1 pipeline. Where scripts/load_fixtures.py seeds the demo
corpus from its *declared* identifier lists, this derives them: it reads the
prose — bio, post titles, post bodies, any armoured PGP block — and runs
extract/identifiers.py over it. The declared lists become the test oracle in
tests/test_identifiers.py and stop being an input.

    fixtures  the corpus under fixtures/. Offline, deterministic, and it keeps
              the persona ids from the fixture files so ground_truth.json still
              points at the same rows.

    live      --input names a JSON file of documents in the shape below. No
              network here either: Phase 2's collectors will write this shape,
              and this is the contract they write into.

              [{"source": "market_delta",
                "source_url": "http://xyz….onion",
                "kind": "market",                       optional
                "handle": "Gh0stP0st",
                "profile_url": "http://xyz….onion/vendor/Gh0stP0st",
                "bio": "…",
                "category": "docs",                     optional
                "trust_score": 4.2,                     optional
                "first_seen": "2026-05-01T12:00:00",    optional
                "last_seen":  "2026-05-30T09:00:00",    optional
                "key_blocks": ["-----BEGIN PGP…"],      optional
                "posts": [{"title": "…", "body": "…",
                           "url": "…", "category": "…",
                           "posted_at": "2026-05-01T12:00:00"}]}]

Idempotent on the schema's natural keys — personas (source_id, handle),
identifiers (type, value), posts (persona_id, body_hash) — so a second run
changes no row counts. It never writes `actors` or `personas.actor_id`:
ground_truth.json is an answer key, and Phase 2's resolver has to earn those.

Every run writes a `scans` row: operator, mode, data source, and a SHA-256 over
exactly the payload that was read.

On `identifiers.validated`: this script writes the honest value — True only for
types with a checksum that actually passed (wallets, onion mirrors, and PGP
blocks whose CRC-24 verified). load_fixtures.py predates the validator and marks
every stored identifier True. Where they disagree, this one is right, and
`meta.checksum` records which test was applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from db import (  # noqa: E402
    IDENTIFIER_TYPES,
    Identifier,
    Persona,
    PersonaIdentifier,
    Post,
    Scan,
    Source,
    require_schema,
    session_scope,
    utcnow,
)
from extract.gliner_extract import describe as gliner_describe  # noqa: E402
from extract.identifiers import extract_identifiers_report  # noqa: E402
from extract.normalize import normalize_handle, normalize_identifier  # noqa: E402

FIXTURES = ROOT / "fixtures"
SOURCE_DIRS = ("market_alpha", "forum_beta", "market_gamma")

#: Types whose value carries a checksum we actually verified.
CHECKSUMMED = {
    "btc": "base58check/bech32",
    "eth": "eip55",
    "xmr": "keccak256",
    "ltc": "base58check/bech32",
    "onion_mirror": "onion v3 sha3-256",
}


# ─────────────────────────────────────────────────────────────────────────────
# Documents — the one shape both modes reduce to
# ─────────────────────────────────────────────────────────────────────────────

class Document:
    """One persona's complete observed footprint on one source."""

    __slots__ = ("source", "persona", "posts", "key_blocks")

    def __init__(self, source: dict, persona: dict, posts: list[dict],
                 key_blocks: list[str]) -> None:
        self.source = source
        self.persona = persona
        self.posts = posts
        self.key_blocks = key_blocks

    @property
    def haystack(self) -> str:
        """The prose the extractor reads.

        Post `url` fields are deliberately excluded. Every one of them contains
        its own market's onion — that is structural metadata, not something the
        vendor wrote, and treating it as a disclosed mirror would give every
        persona on a market an identifier it never published.
        """
        parts = [self.persona.get("bio") or ""]
        parts += self.key_blocks
        for post in self.posts:
            parts.append(post.get("title") or "")
            parts.append(post.get("body") or "")
        return "\n".join(p for p in parts if p)

    @property
    def exclude_onions(self) -> tuple[str, ...]:
        hosts = []
        for url in (self.source.get("url"), self.persona.get("profile_url")):
            if not url:
                continue
            host = url.lower().removeprefix("http://").removeprefix("https://")
            hosts.append(host.split("/")[0])
        return tuple(dict.fromkeys(hosts))


def _ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _read(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_fixture_documents() -> list[Document]:
    sources = {s["id"]: s for s in _read(FIXTURES / "sources.json")}

    blocks: dict[int, list[str]] = {}
    block_file = FIXTURES / "pgp_blocks.json"
    if block_file.exists():
        for row in _read(block_file):
            blocks.setdefault(row["persona_id"], []).append(row["armored"])

    posts: dict[int, list[dict]] = {}
    personas: list[dict] = []
    for name in SOURCE_DIRS:
        personas.extend(_read(FIXTURES / name / "personas.json"))
        for post in _read(FIXTURES / name / "posts.json"):
            posts.setdefault(post["persona_id"], []).append(post)

    documents = []
    for persona in personas:
        documents.append(Document(
            source=sources[persona["source_id"]],
            persona={
                "id": persona["id"],
                "handle": persona["handle"],
                "profile_url": persona.get("profile_url"),
                "bio": persona.get("bio"),
                "category": persona.get("category"),
                "trust_score": persona.get("trust_score"),
                "first_seen": persona.get("first_seen"),
                "last_seen": persona.get("last_seen"),
                "last_scan_at": persona.get("last_scan_at"),
            },
            posts=sorted(posts.get(persona["id"], []), key=lambda p: p["id"]),
            key_blocks=blocks.get(persona["id"], []),
        ))
    return documents


def documents_from_rows(rows: list[dict], origin: str = "input") -> list[Document]:
    """The frozen contract, in memory.

    Split out of `read_live_documents` so a collector can hand its documents
    straight over without a temporary file in between. Both callers validate
    identically, which is the point — a crawl and a file must be able to
    produce the same database or `--source live` proves nothing.
    """
    documents = []
    for index, row in enumerate(rows):
        missing = [k for k in ("source", "source_url", "handle") if not row.get(k)]
        if missing:
            raise SystemExit(
                f"{origin}: document {index} is missing {', '.join(missing)}"
            )
        url = row["source_url"]
        documents.append(Document(
            source={
                "name": row["source"],
                "url": url,
                "kind": row.get("kind"),
                "is_onion": ".onion" in url.lower(),
                "reliability": row.get("reliability", 0.5),
                "active": True,
            },
            persona={
                "handle": row["handle"],
                "profile_url": row.get("profile_url"),
                "bio": row.get("bio"),
                "category": row.get("category"),
                "trust_score": row.get("trust_score"),
                "first_seen": row.get("first_seen"),
                "last_seen": row.get("last_seen"),
                "last_scan_at": None,
            },
            posts=list(row.get("posts") or []),
            key_blocks=list(row.get("key_blocks") or []),
        ))
    return documents


def read_live_documents(path: Path) -> list[Document]:
    rows = _read(path)
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: expected a JSON list of documents")
    return documents_from_rows(rows, origin=str(path))


def payload_hash(documents: list[Document]) -> str:
    """SHA-256 over exactly the text that was read."""
    canonical = json.dumps(
        [
            {
                "source": d.source.get("url"),
                "handle": d.persona["handle"],
                "haystack": d.haystack,
                "posts": [p.get("body", "") for p in d.posts],
            }
            for d in documents
        ],
        sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Writing
# ─────────────────────────────────────────────────────────────────────────────

def _resync_sequence(session, table: str) -> None:
    session.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
    ))


def upsert_sources(session, documents: list[Document]) -> dict[str, int]:
    """Insert every distinct source, keyed by url. Returns url -> id."""
    rows: dict[str, dict] = {}
    for document in documents:
        source = document.source
        rows.setdefault(source["url"], {
            **({"id": source["id"]} if source.get("id") else {}),
            "name": source["name"],
            "url": source["url"],
            "kind": source.get("kind"),
            "is_onion": source.get("is_onion", ".onion" in source["url"].lower()),
            "reliability": source.get("reliability", 0.5),
            "first_seen": _ts(source.get("first_seen")) or utcnow(),
            "last_scan_at": utcnow(),
            "active": source.get("active", True),
        })

    statement = pg_insert(Source.__table__).values(list(rows.values()))
    session.execute(statement.on_conflict_do_update(
        index_elements=[Source.__table__.c.url],
        set_={
            "name": statement.excluded.name,
            "kind": statement.excluded.kind,
            "is_onion": statement.excluded.is_onion,
            "last_scan_at": statement.excluded.last_scan_at,
            "active": statement.excluded.active,
        },
    ))
    _resync_sequence(session, "sources")

    return {
        row.url: row.id
        for row in session.execute(
            select(Source.__table__.c.id, Source.__table__.c.url)
            .where(Source.__table__.c.url.in_(list(rows)))
        )
    }


def upsert_personas(session, documents: list[Document],
                    source_ids: dict[str, int]) -> tuple[dict[int, int], int]:
    """Insert personas keyed by (source_id, handle). Returns (index -> id, new count)."""
    existing = {
        (row.source_id, row.handle)
        for row in session.execute(
            select(Persona.__table__.c.source_id, Persona.__table__.c.handle)
        )
    }

    values = []
    keys: list[tuple[int, str]] = []
    for document in documents:
        source_id = source_ids[document.source["url"]]
        persona = document.persona
        keys.append((source_id, persona["handle"]))
        values.append({
            **({"id": persona["id"]} if persona.get("id") else {}),
            "source_id": source_id,
            "actor_id": None,  # the resolver has to earn it
            "handle": persona["handle"],
            "handle_normalized": normalize_handle(persona["handle"]),
            "profile_url": persona.get("profile_url"),
            "bio": persona.get("bio"),
            "category": persona.get("category"),
            "trust_score": persona.get("trust_score"),
            "post_count": len(document.posts),
            "first_seen": _ts(persona.get("first_seen")),
            "last_seen": _ts(persona.get("last_seen")),
            "last_scan_at": _ts(persona.get("last_scan_at")) or utcnow(),
        })

    statement = pg_insert(Persona.__table__).values(values)
    session.execute(statement.on_conflict_do_update(
        index_elements=[Persona.__table__.c.source_id, Persona.__table__.c.handle],
        set_={
            "handle_normalized": statement.excluded.handle_normalized,
            "profile_url": statement.excluded.profile_url,
            "bio": statement.excluded.bio,
            "category": statement.excluded.category,
            "trust_score": statement.excluded.trust_score,
            "post_count": statement.excluded.post_count,
            "first_seen": func.least(Persona.__table__.c.first_seen,
                                     statement.excluded.first_seen),
            "last_seen": func.greatest(Persona.__table__.c.last_seen,
                                       statement.excluded.last_seen),
            "last_scan_at": statement.excluded.last_scan_at,
        },
    ))
    _resync_sequence(session, "personas")

    lookup = {
        (row.source_id, row.handle): row.id
        for row in session.execute(
            select(Persona.__table__.c.id, Persona.__table__.c.source_id,
                   Persona.__table__.c.handle)
        )
    }
    return ({i: lookup[key] for i, key in enumerate(keys)},
            sum(1 for key in keys if key not in existing))


def upsert_posts(session, documents: list[Document], persona_ids: dict[int, int],
                 source_ids: dict[str, int]) -> int:
    """Insert posts keyed by (persona_id, body_hash). Post ids are not preserved:
    nothing references them, and letting the sequence assign avoids fighting the
    ids scripts/load_fixtures.py may already have used."""
    values = []
    seen: set[tuple[int, str]] = set()
    for index, document in enumerate(documents):
        persona_id = persona_ids[index]
        source_id = source_ids[document.source["url"]]
        for post in document.posts:
            body = post.get("body") or ""
            if not body:
                continue
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if (persona_id, body_hash) in seen:
                continue  # a persona repeating itself verbatim is one post
            seen.add((persona_id, body_hash))
            values.append({
                "persona_id": persona_id,
                "source_id": source_id,
                "url": post.get("url"),
                "title": post.get("title"),
                "body": body,
                "body_hash": body_hash,
                "category": post.get("category"),
                "posted_at": _ts(post.get("posted_at")),
                "scraped_at": utcnow(),
            })

    if not values:
        return 0

    statement = pg_insert(Post.__table__).values(values)
    session.execute(statement.on_conflict_do_update(
        index_elements=[Post.__table__.c.persona_id, Post.__table__.c.body_hash],
        set_={
            "source_id": statement.excluded.source_id,
            "url": statement.excluded.url,
            "title": statement.excluded.title,
            "category": statement.excluded.category,
            "posted_at": statement.excluded.posted_at,
        },
    ))
    _resync_sequence(session, "posts")
    return len(values)


def write_identifiers(session, extracted: dict[int, list], documents: list[Document],
                      persona_ids: dict[int, int]) -> tuple[int, int]:
    """Insert identifiers and their persona_identifiers rows.

    Returns (identifiers, attributions). Nothing here touches the `links` table —
    that is a persona↔persona edge and Phase 2's resolver owns it. The local name
    below is `attributions` precisely so the two are never confused in a count.
    """
    catalogue: dict[tuple[str, str], dict] = {}
    attributions: list[dict] = []

    for index, rows in extracted.items():
        persona = documents[index].persona
        first_seen = _ts(persona.get("first_seen"))
        last_seen = _ts(persona.get("last_seen"))
        persona_id = persona_ids[index]

        for row in rows:
            kind, value = row["type"], row["value"]
            if kind not in IDENTIFIER_TYPES:
                continue

            checksum = CHECKSUMMED.get(kind)
            if kind == "pgp_fpr" and row["meta"].get("method") == "key_block":
                checksum = "openpgp crc-24"

            entry = catalogue.get((kind, value))
            if entry is None:
                catalogue[(kind, value)] = {
                    "type": kind,
                    "value": value,
                    "value_norm": normalize_identifier(kind, value),
                    "validated": checksum is not None,
                    "meta": {"source": "ingest", "checksum": checksum, **row["meta"]},
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                }
            else:
                if first_seen and (entry["first_seen"] is None
                                   or first_seen < entry["first_seen"]):
                    entry["first_seen"] = first_seen
                if last_seen and (entry["last_seen"] is None
                                  or last_seen > entry["last_seen"]):
                    entry["last_seen"] = last_seen

            attributions.append({
                "persona_id": persona_id,
                "type": kind,
                "value": value,
                "context": (row["raw_context"] or "")[:500],
                "confidence": row["confidence"],
            })

    if not catalogue:
        return 0, 0

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
        for row in session.execute(
            select(Identifier.__table__.c.id, Identifier.__table__.c.type,
                   Identifier.__table__.c.value)
        )
    }

    join_rows = {}
    for link in attributions:
        key = (link["persona_id"], ids[(link["type"], link["value"])])
        join_rows[key] = {
            "persona_id": key[0],
            "identifier_id": key[1],
            "context": link["context"],
            "confidence": link["confidence"],
            "observed_at": utcnow(),
        }

    join_statement = pg_insert(PersonaIdentifier.__table__).values(list(join_rows.values()))
    session.execute(join_statement.on_conflict_do_update(
        index_elements=[PersonaIdentifier.__table__.c.persona_id,
                        PersonaIdentifier.__table__.c.identifier_id],
        set_={
            "context": join_statement.excluded.context,
            "confidence": join_statement.excluded.confidence,
            "observed_at": join_statement.excluded.observed_at,
        },
    ))

    return len(catalogue), len(join_rows)


# ─────────────────────────────────────────────────────────────────────────────
# Extraction pass and reporting
# ─────────────────────────────────────────────────────────────────────────────

def run_extraction(documents: list[Document]) -> tuple[dict[int, list], list[str]]:
    """Extract from every document. Returns (index -> rows, dropped notes)."""
    extracted: dict[int, list] = {}
    notes: list[str] = []
    for index, document in enumerate(documents):
        report = extract_identifiers_report(
            document.haystack, exclude_onions=document.exclude_onions
        )
        extracted[index] = [row.as_dict() for row in report.kept]
        for row in report.dropped:
            notes.append(
                f"{document.persona['handle']}: {row.type} {row.value} — {row.reason}"
            )
    return extracted, notes


def declared_recall(documents: list[Document], extracted: dict[int, list]) -> Optional[str]:
    """Recall against the declared lists, for fixtures mode only.

    Reported against two denominators because only one is a statement about the
    extractor: identifiers that appear in the prose, and every identifier the
    corpus declares. Eight of the latter appear in no text at all — a Phase 0
    corpus gap documented in docs/BUILD_PLAN.md.
    """
    declared = extractable = found = 0
    unreachable: list[str] = []

    for index, document in enumerate(documents):
        raw = document.persona.get("declared")
        if raw is None:
            return None
        haystack = document.haystack
        got = {(r["type"], r["value"]) for r in extracted[index]}
        for identifier in raw:
            declared += 1
            if identifier["value"] not in haystack:
                unreachable.append(
                    f"persona {document.persona['id']} "
                    f"({document.persona['handle']}): "
                    f"{identifier['type']} {identifier['value']}"
                )
                continue
            if not identifier.get("valid", True):
                continue
            extractable += 1
            if (identifier["type"], identifier["value"]) in got:
                found += 1

    lines = [
        f"  recall  {found}/{extractable} identifiers present in the prose "
        f"({found / extractable:.1%})" if extractable else "  recall  n/a",
        f"          {found}/{declared} of every identifier the corpus declares "
        f"({found / declared:.1%})" if declared else "",
        f"          {len(unreachable)} declared in no bio and no post — "
        f"not extractable, see docs/BUILD_PLAN.md",
    ]
    lines += [f"            {row}" for row in unreachable]
    return "\n".join(line for line in lines if line)


def print_counts(session) -> None:
    tables = ("sources", "personas", "identifiers", "persona_identifiers", "posts",
              "actors", "links", "scans")
    width = max(len(name) for name in tables)
    print("\n  table" + " " * (width - 4) + "rows")
    print("  " + "-" * (width + 8))
    for table in tables:
        count = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
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
    parser = argparse.ArgumentParser(
        description="Extract identifiers from text and write them to Postgres."
    )
    parser.add_argument("--source", choices=("fixtures", "live"), default="fixtures",
                        help="fixtures corpus, or documents from --input")
    parser.add_argument("--input", type=Path,
                        help="JSON document file, required for --source live")
    parser.add_argument("--dry-run", action="store_true",
                        help="extract and report, write nothing")
    parser.add_argument("--operator", default=None,
                        help="operator id for the audit row (default: $OPERATOR_ID)")
    args = parser.parse_args()

    if args.source == "live":
        if not args.input:
            parser.error("--source live needs --input <documents.json>")
        documents = read_live_documents(args.input)
    else:
        if args.input:
            parser.error("--input only applies to --source live")
        documents = read_fixture_documents()
        # Kept aside for the dry-run recall report; never used as an extractor input.
        declared = {}
        for name in SOURCE_DIRS:
            for persona in _read(FIXTURES / name / "personas.json"):
                declared[persona["id"]] = persona["identifiers"]
        for document in documents:
            document.persona["declared"] = declared.get(document.persona["id"], [])

    if not documents:
        print("nothing to ingest")
        return 0

    operator = args.operator or os.environ.get("OPERATOR_ID") or "unknown"
    action_hash = payload_hash(documents)
    sources_touched = sorted({d.source["name"] for d in documents})

    print(f"corpus: {len(documents)} personas across {len(sources_touched)} sources, "
          f"{sum(len(d.posts) for d in documents)} posts, "
          f"{sum(len(d.key_blocks) for d in documents)} key blocks")
    print(f"{gliner_describe()}")
    print(f"action sha256: {action_hash}")
    print(f"operator: {operator}")

    extracted, dropped = run_extraction(documents)
    by_type = Counter(row["type"] for rows in extracted.values() for row in rows)

    print("\n  extracted")
    for kind, count in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {kind:<14} {count:>4}")
    print(f"    {'total':<14} {sum(by_type.values()):>4}")

    print_lines(f"dropped {len(dropped)} candidate(s)", dropped)

    recall = declared_recall(documents, extracted)
    if recall:
        print()
        print(recall)

    if args.dry_run:
        print("\ndry run - nothing written")
        return 0

    started = utcnow()
    with session_scope() as session:
        require_schema(session)

        scan = Scan(
            operator_id=operator,
            mode="manual",
            data_source=args.source,
            query="ingest",
            sources_touched=sources_touched,
            action_hash=action_hash,
            started_at=started,
            status="running",
        )
        session.add(scan)
        session.flush()

        try:
            source_ids = upsert_sources(session, documents)
            persona_ids, new_personas = upsert_personas(session, documents, source_ids)
            posts_written = upsert_posts(session, documents, persona_ids, source_ids)
            identifiers, joins = write_identifiers(
                session, extracted, documents, persona_ids
            )
        except Exception as exc:
            scan.status = "failed"
            scan.error = str(exc)[:2000]
            scan.finished_at = utcnow()
            raise

        scan.personas_new = new_personas
        scan.status = "ok"
        scan.finished_at = utcnow()
        session.flush()

        print(f"\n  wrote {len(persona_ids)} personas ({new_personas} new), "
              f"{posts_written} posts, {identifiers} identifiers, "
              f"{joins} persona-identifier links")
        print("\n  actors table untouched and personas.actor_id left NULL -")
        print("  Phase 2's resolver has to derive those")

        print_counts(session)
        print(f"\n  audit row: scan id {scan.id}, operator {operator}, "
              f"sha256 {action_hash[:16]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
