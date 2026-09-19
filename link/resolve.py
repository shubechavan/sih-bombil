"""resolve.py — score every persona pair on direct evidence and write links.

    python -m link.resolve --dry-run
    python -m link.resolve --preset claude_md
    python -m link.resolve --no-renormalise

WHAT THIS MODULE WILL NOT DO
----------------------------
It computes **direct pairwise evidence only**. It never propagates a score along
a path, and every row it writes carries `method='pairwise'`. A->B and B->C
being PROBABLE says nothing here about A->C; if the two personas share no
identifier, no writeprint similarity and no behavioural overlap of their own,
their pair scores WEAK and is stored as WEAK.

Inferring the missing edge is a separate, explicitly invoked step in
link/graph.py, which decays the derived score and caps its band. That separation
is deliberate: it keeps the `links` table a record of what was *observed*, so a
later pass cannot quietly average an inference back in as though it were
evidence.

It also never writes `actors` or `personas.actor_id`. Clustering personas into
an actor is a decision about identity, not a similarity measurement, and it
belongs to an explicit step an operator asks for.

THE H TERM
----------
Shared identifiers are matched on `(type, value_norm)` and combined with
noisy-OR (see score/attribution.hard_identifier_score), so two independent
identifiers reinforce without the total leaving 0..1. Handle evidence
contributes at most once: an exact match (0.60) supersedes the normalised
collision (0.45) rather than stacking with it.

WHAT GETS STORED
----------------
Every pair is scored, but not every pair is worth a row. A pair is written when
it reaches `--min-score` (default: the POSSIBLE floor) **or** when it had any
hard identifier evidence at all. The second clause is the point: a pair that
shares a wallet and is still refused is exactly the row an analyst needs to see,
and a table that only ever holds matches cannot show its own negatives.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default Windows console codepage is cp1252 and mangles the em dashes below.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

from sqlalchemy import delete, select  # noqa: E402

from db import (  # noqa: E402
    IDENTIFIER_WEIGHTS,
    Identifier,
    Link,
    Persona,
    PersonaIdentifier,
    Post,
    Scan,
    require_schema,
    session_scope,
    utcnow,
)
from extract.normalize import normalize_handle, normalize_identifier  # noqa: E402
from link import behaviour as behaviour_module  # noqa: E402
from link import infra as infra_module  # noqa: E402
from link import stylometry as stylometry_module  # noqa: E402
from score.attribution import (  # noqa: E402
    DEFAULT_PRESET,
    RENORMALISE_UNMEASURED,
    Attribution,
    attribution_score,
    hard_identifier_score,
    weights_for,
)

__all__ = [
    "Corpus", "PairResult", "load_corpus", "load_corpus_from_fixtures",
    "resolve_pairs", "store_links",
]

#: Weight for a handle that matches exactly, and for one that only matches after
#: leet-decoding and separator stripping. At most one of the two ever fires.
HANDLE_EXACT_WEIGHT = 0.60
HANDLE_NORMALISED_WEIGHT = 0.45

#: Below this, a pair is scored but not written — unless it had hard evidence.
DEFAULT_MIN_SCORE = 0.45

#: Readable names for the evidence list.
IDENTIFIER_LABELS = {
    "pgp_fpr": "PGP fingerprint",
    "btc": "Bitcoin address",
    "eth": "Ethereum address",
    "xmr": "Monero address",
    "ltc": "Litecoin address",
    "email": "email address",
    "jabber": "Jabber/XMPP id",
    "session": "Session id",
    "telegram": "Telegram handle",
    "onion_mirror": "mirror onion",
}


# ─────────────────────────────────────────────────────────────────────────────
# Corpus
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Corpus:
    """Everything the resolver reads, loaded once."""

    personas: dict[int, dict]
    posts: dict[int, list[dict]]
    identifiers: dict[int, set[tuple[str, str]]]   # persona -> {(type, value_norm)}
    identifier_display: dict[tuple[str, str], str]  # (type, value_norm) -> raw value

    @property
    def persona_ids(self) -> list[int]:
        return sorted(self.personas)

    def texts(self) -> dict[int, str]:
        """Bio plus every post title and body, per persona."""
        out: dict[int, str] = {}
        for persona_id, persona in self.personas.items():
            parts = [persona.get("bio") or ""]
            for post in self.posts.get(persona_id, []):
                parts.append(post.get("title") or "")
                parts.append(post.get("body") or "")
            out[persona_id] = "\n".join(p for p in parts if p).strip()
        return out

    def identifier_values(self) -> dict[int, list[str]]:
        """Raw identifier values per persona, for masking before stylometry."""
        return {
            persona_id: [self.identifier_display[key] for key in keys]
            for persona_id, keys in self.identifiers.items()
        }


def load_corpus_from_fixtures() -> Corpus:
    """Build the same Corpus straight from fixtures/, with no database at all.

    CLAUDE.md requires fixtures mode to work offline; an evaluation number that
    can only be reproduced with Postgres running is not much of a demo.

    Identifiers are **derived, not declared**. This runs Phase 1's extractor over
    the prose exactly as scripts/ingest.py does, rather than reading the
    `identifiers` list in personas.json. Those declared lists include eight
    values that appear in no bio and no post (the Phase 0 corpus gap recorded in
    docs/BUILD_PLAN.md) plus two deliberately corrupted wallets. Trusting them
    would hand the resolver evidence no extractor could ever produce and quietly
    inflate the H term for personas 3, 9, 10, 17, 18 and 19.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import ingest  # noqa: PLC0415  — lazy so the DB path does not pay for it

    documents = ingest.read_fixture_documents()
    extracted, _dropped = ingest.run_extraction(documents)

    personas: dict[int, dict] = {}
    posts: dict[int, list[dict]] = {}
    identifiers: dict[int, set[tuple[str, str]]] = {}
    display: dict[tuple[str, str], str] = {}

    for index, document in enumerate(documents):
        persona = document.persona
        persona_id = persona["id"]
        personas[persona_id] = {
            "id": persona_id,
            "source_id": document.source.get("id"),
            "handle": persona["handle"],
            "handle_normalized": normalize_handle(persona["handle"]),
            "bio": persona.get("bio"),
            "category": persona.get("category"),
        }
        posts[persona_id] = [
            {
                "title": post.get("title"),
                "body": post.get("body"),
                "category": post.get("category") or persona.get("category"),
                "posted_at": _as_datetime(post.get("posted_at")),
            }
            for post in document.posts
        ]

        found: set[tuple[str, str]] = set()
        for row in extracted[index]:
            key = (row["type"], normalize_identifier(row["type"], row["value"]))
            found.add(key)
            display.setdefault(key, row["value"])
        identifiers[persona_id] = found

    return Corpus(personas=personas, posts=posts, identifiers=identifiers,
                  identifier_display=display)


def _as_datetime(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def load_corpus(session) -> Corpus:
    """Read personas, posts and identifiers out of the database."""
    personas = {
        row.id: {
            "id": row.id,
            "source_id": row.source_id,
            "handle": row.handle,
            "handle_normalized": row.handle_normalized or normalize_handle(row.handle),
            "bio": row.bio,
            "category": row.category,
        }
        for row in session.execute(select(Persona)).scalars()
    }

    posts: dict[int, list[dict]] = {pid: [] for pid in personas}
    # ORDER BY is not cosmetic here. Corpus.texts() concatenates a persona's
    # posts, and the writeprint is char n-grams over that concatenation, so the
    # order the rows come back in changes the vector — and changes
    # `corpus_version`, which is the key the writeprint cache is stored under.
    # Without this, re-running ingest reshuffles Postgres's physical row order
    # and every cached vector is invalidated for no reason, while two runs of
    # `evaluate --source db` can disagree in the low digits.
    for row in session.execute(
        select(Post).order_by(Post.posted_at.nullslast(), Post.id)
    ).scalars():
        if row.persona_id not in posts:
            continue
        posts[row.persona_id].append({
            "title": row.title,
            "body": row.body,
            # a post with no category of its own inherits the persona's, so a
            # market listing that predates categorisation still counts
            "category": row.category or personas[row.persona_id].get("category"),
            "posted_at": row.posted_at,
        })

    identifiers: dict[int, set[tuple[str, str]]] = {pid: set() for pid in personas}
    display: dict[tuple[str, str], str] = {}
    rows = session.execute(
        select(PersonaIdentifier.persona_id, Identifier.type,
               Identifier.value, Identifier.value_norm)
        .join(Identifier, Identifier.id == PersonaIdentifier.identifier_id)
    )
    for persona_id, kind, value, value_norm in rows:
        if persona_id not in identifiers:
            continue
        key = (kind, value_norm or value)
        identifiers[persona_id].add(key)
        display.setdefault(key, value)

    return Corpus(personas=personas, posts=posts, identifiers=identifiers,
                  identifier_display=display)


# ─────────────────────────────────────────────────────────────────────────────
# Hard identifier evidence
# ─────────────────────────────────────────────────────────────────────────────

def hard_evidence(a: int, b: int, corpus: Corpus) -> tuple[float, list[dict]]:
    """The H term for one pair, with a reason per contributing identifier."""
    weights: list[float] = []
    evidence: list[dict] = []

    shared = corpus.identifiers.get(a, set()) & corpus.identifiers.get(b, set())
    for kind, value_norm in sorted(shared):
        weight = IDENTIFIER_WEIGHTS.get(kind)
        if weight is None:
            continue  # an unknown type must not silently contribute
        raw = corpus.identifier_display.get((kind, value_norm), value_norm)
        weights.append(weight)
        evidence.append({
            "type": "shared_identifier",
            "detail": f"same {IDENTIFIER_LABELS.get(kind, kind)} {raw}",
            "identifier_type": kind,
            "value": raw,
            "weight": weight,
        })

    left, right = corpus.personas[a], corpus.personas[b]
    if left["handle"].casefold() == right["handle"].casefold():
        weights.append(HANDLE_EXACT_WEIGHT)
        evidence.append({
            "type": "handle_reuse",
            "detail": f"identical handle {left['handle']!r} on two sources",
            "weight": HANDLE_EXACT_WEIGHT,
        })
    elif left["handle_normalized"] == right["handle_normalized"]:
        # exact and normalised never stack: they are one piece of evidence
        # observed at two levels of strictness
        weights.append(HANDLE_NORMALISED_WEIGHT)
        evidence.append({
            "type": "handle_reuse",
            "detail": (
                f"handles {left['handle']!r} and {right['handle']!r} both "
                f"normalise to {left['handle_normalized']!r}"
            ),
            "weight": HANDLE_NORMALISED_WEIGHT,
        })

    return hard_identifier_score(weights), evidence


# ─────────────────────────────────────────────────────────────────────────────
# Pairwise resolution
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PairResult:
    """One scored pair, ready to be stored or evaluated."""

    persona_a: int
    persona_b: int
    attribution: Attribution
    evidence: list[dict]
    had_hard_evidence: bool

    @property
    def score(self) -> float:
        return self.attribution.score

    @property
    def band(self) -> str:
        return self.attribution.band

    @property
    def pair(self) -> tuple[int, int]:
        return (self.persona_a, self.persona_b)


def resolve_pairs(
    corpus: Corpus,
    *,
    preset: str = DEFAULT_PRESET,
    renormalise: bool = RENORMALISE_UNMEASURED,
    writeprints: Optional[stylometry_module.WriteprintSet] = None,
    behaviours: Optional[behaviour_module.BehaviourSet] = None,
    infra: Optional[infra_module.InfraSet] = None,
    session=None,
) -> tuple[list[PairResult], stylometry_module.WriteprintSet,
           behaviour_module.BehaviourSet, infra_module.InfraSet]:
    """Score every unordered pair. Nothing is written.

    Returned separately from storage so scripts/evaluate.py can measure the
    whole distribution — including the pairs that are correctly refused and
    therefore never reach the table.

    Pass `session` to let the writeprint cache be consulted; without one the
    vectors are always rebuilt, which is what the offline fixtures path does.
    """
    if writeprints is None:
        writeprints, _ = stylometry_module.load_or_build(
            corpus.texts(), corpus.identifier_values(), session=session
        )
    if behaviours is None:
        behaviours = behaviour_module.build(corpus.posts)
    if infra is None:
        infra = infra_module.build(
            corpus.personas,
            infra_module.load_findings(session=session),
            identifiers=corpus.identifiers,
        )

    results: list[PairResult] = []
    for a, b in itertools.combinations(corpus.persona_ids, 2):
        h, hard = hard_evidence(a, b, corpus)
        s = writeprints.similarity(a, b)
        bvalue = behaviours.similarity(a, b)
        ivalue = infra.similarity(a, b)

        reasons: dict[str, str] = {}
        if s is None:
            reasons["S"] = (
                writeprints.refusal_reason(a) or writeprints.refusal_reason(b)
                or "stylometry did not run for this pair"
            )
        if bvalue is None:
            reasons["B"] = (
                behaviours.refusal_reason(a) or behaviours.refusal_reason(b)
                or "behaviour did not run for this pair"
            )
        if ivalue is None:
            # Recon has run. What it produced is site-level — it fingerprints
            # the marketplace, not the vendor — so unless a persona controls a
            # host of their own, there is nothing about *them* to compare.
            # Unmeasured, never zero. See link/infra.py.
            reasons["I"] = (
                "infrastructure not assessed — "
                + (infra.refusal_reason(a) or infra.refusal_reason(b)
                   or "neither persona controls a fingerprinted host")
            )

        attribution = attribution_score(
            h=h, s=s, b=bvalue, i=ivalue,
            preset=preset, renormalise=renormalise, reasons=reasons,
        )

        evidence = list(hard)
        if s is not None:
            evidence.append({
                "type": "stylometry",
                "detail": (
                    f"writeprint cosine {s:.3f} "
                    f"(char {stylometry_module.CHAR_NGRAM_RANGE[0]}-"
                    f"{stylometry_module.CHAR_NGRAM_RANGE[1]} gram TF-IDF, function "
                    f"words, punctuation, orthographic shape; identifiers masked)"
                ),
                "weight": s,
            })
        if bvalue is not None:
            for line in behaviours.explain(a, b):
                evidence.append({"type": "behaviour", "detail": line})
            evidence.append({
                "type": "behaviour_score",
                "detail": f"behavioural similarity {bvalue:.3f}",
                "weight": bvalue,
            })
        if ivalue is not None:
            evidence.extend(infra.explain(a, b))
            evidence.append({
                "type": "infra_score",
                "detail": f"infrastructure overlap {ivalue:.3f}",
                "weight": ivalue,
            })
        evidence.extend(attribution.evidence)

        results.append(PairResult(
            persona_a=a, persona_b=b, attribution=attribution,
            evidence=evidence, had_hard_evidence=bool(hard),
        ))

    return results, writeprints, behaviours, infra


def worth_storing(result: PairResult, min_score: float) -> bool:
    """A pair earns a row by scoring well enough, or by having been a candidate.

    The second clause keeps the refusals visible: a pair that shared an
    identifier and was still not linked is evidence about the system's
    judgement, and dropping it would leave a table that only ever says yes.
    """
    return result.score >= min_score or result.had_hard_evidence


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

def store_links(session, results: Sequence[PairResult], *,
                min_score: float = DEFAULT_MIN_SCORE,
                method: str = "pairwise", replace: bool = True) -> int:
    """Upsert scored pairs into `links`. Returns the number of rows written.

    `replace` clears rows previously written by this method first, so a re-run
    after a weight change cannot leave a stale mixture of two scorings in the
    table.
    """
    if replace:
        session.execute(delete(Link).where(Link.method == method))

    written = 0
    for result in results:
        if not worth_storing(result, min_score):
            continue
        components = result.attribution.components
        session.add(Link(
            persona_a=result.persona_a,
            persona_b=result.persona_b,
            score=round(result.score, 6),
            band=result.band,
            h_score=components["H"],
            s_score=components["S"],
            b_score=components["B"],
            i_score=components["I"],
            evidence=result.evidence,
            method=method,
            computed_at=utcnow(),
        ))
        written += 1
    return written


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _summary(results: Sequence[PairResult]) -> dict[str, int]:
    counts = {"CONFIRMED": 0, "PROBABLE": 0, "POSSIBLE": 0, "WEAK": 0}
    for result in results:
        counts[result.band] += 1
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score persona pairs on direct evidence and write links."
    )
    parser.add_argument("--source", choices=("db", "fixtures"), default="db",
                        help="read the corpus from Postgres, or straight from "
                             "fixtures/ with no database (offline)")
    parser.add_argument("--preset", default=DEFAULT_PRESET,
                        help="weight preset from score/attribution.py")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                        help="store pairs at or above this score (default 0.45)")
    parser.add_argument("--no-renormalise", action="store_true",
                        help="count unmeasured components as 0.0 instead of "
                             "redistributing their weight")
    parser.add_argument("--dry-run", action="store_true",
                        help="score and report, write nothing")
    parser.add_argument("--operator", default=None,
                        help="operator id for the audit row (default: $OPERATOR_ID)")
    args = parser.parse_args(argv)

    renormalise = not args.no_renormalise
    weights = weights_for(args.preset)
    operator = args.operator or os.environ.get("OPERATOR_ID") or "unknown"
    started = utcnow()

    # ── read ────────────────────────────────────────────────────────────────
    # Scoring never needs a connection. Only writing does, so --dry-run against
    # fixtures runs with Postgres down.
    if args.source == "fixtures":
        corpus = load_corpus_from_fixtures()
    else:
        with session_scope() as session:
            require_schema(session)
            corpus = load_corpus(session)

    if len(corpus.personas) < 2:
        print("fewer than two personas — nothing to link")
        return 0

    # The writeprint cache lives in the database, so consulting it needs a
    # session even when the corpus itself came from fixtures.
    if args.dry_run:
        results, writeprints, behaviours, infra = resolve_pairs(
            corpus, preset=args.preset, renormalise=renormalise
        )
    else:
        with session_scope() as session:
            require_schema(session)
            results, writeprints, behaviours, infra = resolve_pairs(
                corpus, preset=args.preset, renormalise=renormalise, session=session
            )

    # ── report ──────────────────────────────────────────────────────────────
    print(f"scored {len(results)} pairs over {len(corpus.personas)} personas "
          f"(--source {args.source})")
    print(f"  preset {args.preset}: " + "  ".join(
        f"{k} {v:.2f}" for k, v in weights.items()))
    print(f"  renormalise unmeasured components: {renormalise}")
    origin = "loaded from cache" if writeprints.from_cache else "built"
    print(f"  writeprints: {len(writeprints.prints)} {origin}, "
          f"{len(writeprints.refused)} refused below the "
          f"{stylometry_module.MIN_STYLOMETRY_CHARS}-character floor "
          f"({writeprints.feature_version})")
    for persona_id in sorted(writeprints.refused):
        print(f"    persona {persona_id} "
              f"({corpus.personas[persona_id]['handle']}): "
              f"{writeprints.refused[persona_id]} chars — S unmeasured")
    print(f"  infrastructure ({infra.mode}): {len(infra.hosts)} persona(s) "
          f"control a fingerprinted host, {len(infra.refusals)} do not")
    for persona_id in sorted(behaviours.refused):
        print(f"    persona {persona_id}: B unmeasured")

    counts = _summary(results)
    print("\n  bands over every pair")
    for band in ("CONFIRMED", "PROBABLE", "POSSIBLE", "WEAK"):
        print(f"    {band:<10} {counts[band]:>4}")

    storable = [r for r in results if worth_storing(r, args.min_score)]
    print(f"\n  {len(storable)} pairs qualify for a row "
          f"(score >= {args.min_score} or hard evidence present)")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    # ── write ───────────────────────────────────────────────────────────────
    action_hash = hashlib.sha256(json.dumps(
        {"preset": args.preset, "renormalise": renormalise,
         # The infra mode changes what I means, so it changes what the scores
         # below mean. Omitting it would let one hash stand for two runs.
         "infra_mode": infra.mode,
         "pairs": [[r.persona_a, r.persona_b, round(r.score, 6)] for r in storable]},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()

    with session_scope() as session:
        require_schema(session)

        scan = Scan(
            operator_id=operator, mode="manual", data_source=args.source,
            query=f"resolve --preset {args.preset}",
            sources_touched=sorted(
                {str(p["source_id"]) for p in corpus.personas.values()}
            ),
            action_hash=action_hash, started_at=started, status="running",
        )
        session.add(scan)
        session.flush()

        try:
            prints_written = stylometry_module.store(
                session, writeprints,
                hour_histograms={pid: profile.hour_histogram
                                 for pid, profile in behaviours.profiles.items()},
            )
            written = store_links(session, results, min_score=args.min_score)
        except Exception as exc:
            scan.status = "failed"
            scan.error = str(exc)[:2000]
            scan.finished_at = utcnow()
            raise

        scan.links_new = written
        scan.status = "ok"
        scan.finished_at = utcnow()

        refused = len(writeprints.refused)
        print(f"\n  wrote {written} links (method='pairwise') and "
              f"{prints_written} writeprint rows "
              f"({len(writeprints.prints)} scored, {refused} refused and stored "
              f"as such)")
        print("  actors table untouched and personas.actor_id left NULL — "
              "clustering is a separate decision")
        print(f"\n  audit row: scan id {scan.id}, operator {operator}, "
              f"sha256 {action_hash[:16]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
