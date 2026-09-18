"""cluster.py — the step that turns scored links into actor rows.

Phase 2 stopped one move short of this on purpose. link/graph.py computes
connected components over the link graph and calls them *candidate* clusters;
link/resolve.py asserts in its tests that it writes no actor assignments. The
reasoning is in graph.py's docstring: deciding that two personas are one actor is
a judgement, not a similarity measurement, and it belongs in a step an operator
runs deliberately rather than as a side effect of scoring.

Phase 4 needs that judgement made, because `/actors/{id}` needs a stable id to
hang a URL off and `actors.label`, `first_seen` and `max_confidence` exist to be
populated. So it happens here — once, explicitly, with an audit row and a
threshold it names.

WHAT THIS IS
------------
Connected components over links at or above a threshold. Nothing cleverer. Two
personas end up in one actor when a chain of links at or above that threshold
connects them, which is how 1-9-16 resolves: 1~9 and 1~16 are both direct, and
9~16 — which shares no identifier at all and pairwise scoring correctly refuses
to reach — comes along because both are fastened to persona 1.

That is also the honest limit of it. A chain is only as strong as its weakest
edge, so `min_edge_score` reports exactly that rather than the strongest edge in
the cluster. Reporting the maximum would let one CONFIRMED pair vouch for a
persona it was never compared against.

THE THRESHOLD IS THE DECISION
-----------------------------
Default is the PROBABLE floor, 0.65. Below PROBABLE a link is a lead worth an
analyst's time, not grounds for merging two identities into one record. Raise it
and clusters split; every result carries the threshold that produced it so a
stored partition can be read back with the assumption that made it.

On the fixture corpus the default reproduces the answer key exactly: 14 actors
over 20 personas, with the same membership `ground_truth.json` declares. That is
a property of this corpus, not a promise about any other.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
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

from db import BAND_THRESHOLDS  # noqa: E402
from link.resolve import Corpus, PairResult  # noqa: E402

__all__ = [
    "DEFAULT_THRESHOLD",
    "ActorCluster",
    "ClusterSet",
    "MergeEdge",
    "build",
    "store_actors",
]

#: A link below PROBABLE is a lead, not grounds for merging two identities.
DEFAULT_THRESHOLD: float = dict(BAND_THRESHOLDS)["PROBABLE"]


@dataclass(frozen=True)
class MergeEdge:
    """One link that helped fasten a cluster together."""

    persona_a: int
    persona_b: int
    score: float
    band: str
    evidence: tuple = ()

    @property
    def pair(self) -> tuple[int, int]:
        return (self.persona_a, self.persona_b)


@dataclass(frozen=True)
class ActorCluster:
    """One resolved actor: the personas, and the links that merged them."""

    personas: tuple[int, ...]
    label: str
    edges: tuple[MergeEdge, ...] = ()
    category: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    sources: tuple[int, ...] = ()

    @property
    def min_edge_score(self) -> Optional[float]:
        """The weakest link holding this cluster together, or None if alone.

        Deliberately the minimum. A cluster is a chain and a chain is only as
        good as its weakest edge; the maximum would let one strong pair vouch
        for a persona it was never compared against.
        """
        return min((e.score for e in self.edges), default=None)

    @property
    def is_singleton(self) -> bool:
        return len(self.personas) == 1


@dataclass(frozen=True)
class ClusterSet:
    """The whole partition, and the threshold that produced it."""

    actors: tuple[ActorCluster, ...]
    threshold: float

    def __len__(self) -> int:
        return len(self.actors)

    def for_persona(self, persona_id: int) -> Optional[ActorCluster]:
        for actor in self.actors:
            if persona_id in actor.personas:
                return actor
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Building
# ─────────────────────────────────────────────────────────────────────────────

def _union_find(persona_ids: Sequence[int],
                edges: Sequence[MergeEdge]) -> dict[int, int]:
    """Smallest persona id in each component, per persona."""
    parent = {pid: pid for pid in persona_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in edges:
        if edge.persona_a not in parent or edge.persona_b not in parent:
            continue
        a, b = find(edge.persona_a), find(edge.persona_b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    return {pid: find(pid) for pid in persona_ids}


def _label_for(personas: Sequence[int], corpus: Corpus,
               edges: Sequence[MergeEdge]) -> str:
    """Name the actor after one of its own personas.

    The persona carrying the strongest evidence, falling back to the lowest id
    so a singleton and a tie are both deterministic. Never a synthetic name: an
    analyst reading a table of actors should see a handle they can search for.
    """
    if edges:
        strongest = max(edges, key=lambda e: (e.score, -e.persona_a))
        candidates = [strongest.persona_a, strongest.persona_b]
        best = min(candidates)
    else:
        best = personas[0]
    return corpus.personas[best]["handle"]


def _span(personas: Sequence[int], corpus: Corpus) -> tuple[Optional[datetime],
                                                            Optional[datetime]]:
    stamps = [
        post["posted_at"]
        for persona in personas
        for post in corpus.posts.get(persona, [])
        if post.get("posted_at")
    ]
    return (min(stamps), max(stamps)) if stamps else (None, None)


def _category(personas: Sequence[int], corpus: Corpus) -> Optional[str]:
    """The category the cluster's personas most often trade in.

    Ties break on the lowest persona id rather than dict order, so the answer
    does not depend on how the corpus happened to be loaded.
    """
    counts = Counter(
        corpus.personas[p].get("category")
        for p in personas
        if corpus.personas[p].get("category")
    )
    if not counts:
        return None
    best = max(counts.values())
    return sorted(name for name, n in counts.items() if n == best)[0]


def build(corpus: Corpus, results: Sequence[PairResult], *,
          threshold: float = DEFAULT_THRESHOLD) -> ClusterSet:
    """Partition personas into actors over links at or above `threshold`.

    Reads the same scored pairs the links table holds, so the partition can
    never rest on an edge the database does not contain.
    """
    edges = tuple(
        MergeEdge(
            persona_a=r.persona_a,
            persona_b=r.persona_b,
            score=r.score,
            band=r.band,
            evidence=tuple(r.evidence),
        )
        for r in results
        if r.score >= threshold
    )

    roots = _union_find(corpus.persona_ids, edges)
    grouped: dict[int, list[int]] = {}
    for persona_id, root in roots.items():
        grouped.setdefault(root, []).append(persona_id)

    actors: list[ActorCluster] = []
    for root, personas in grouped.items():
        members = tuple(sorted(personas))
        inner = tuple(
            e for e in edges
            if e.persona_a in members and e.persona_b in members
        )
        first_seen, last_seen = _span(members, corpus)
        actors.append(ActorCluster(
            personas=members,
            label=_label_for(members, corpus, inner),
            edges=inner,
            category=_category(members, corpus),
            first_seen=first_seen,
            last_seen=last_seen,
            sources=tuple(sorted({
                corpus.personas[p].get("source_id")
                for p in members
                if corpus.personas[p].get("source_id") is not None
            })),
        ))

    actors.sort(key=lambda a: (-len(a.personas), a.personas[0]))
    return ClusterSet(actors=tuple(actors), threshold=threshold)


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

def store_actors(session, clusters: ClusterSet) -> int:
    """Replace the actors table and repoint personas.actor_id. Returns rows written.

    A re-run at a different threshold must not leave two partitions mixed in one
    table, so every actor row is cleared first and every persona repointed. The
    personas themselves are never deleted — only their assignment changes.
    """
    from sqlalchemy import delete, update  # noqa: PLC0415

    from db import Actor, Persona, utcnow  # noqa: PLC0415

    session.execute(update(Persona).values(actor_id=None))
    session.execute(delete(Actor))
    session.flush()

    written = 0
    for cluster in clusters.actors:
        actor = Actor(
            label=cluster.label,
            category=cluster.category,
            first_seen=cluster.first_seen,
            last_seen=cluster.last_seen,
            max_confidence=cluster.min_edge_score,
            notes=(
                f"{len(cluster.personas)} persona(s) merged by connected "
                f"components over links at or above {clusters.threshold:.2f}; "
                f"confidence is the weakest edge in the cluster"
                if not cluster.is_singleton else
                "single persona; no link reached the clustering threshold"
            ),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(actor)
        session.flush()
        session.execute(
            update(Persona)
            .where(Persona.id.in_(cluster.personas))
            .values(actor_id=actor.id)
        )
        written += 1

    return written


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

RULE = "─" * 78


def payload_hash(clusters: ClusterSet) -> str:
    canonical = json.dumps(
        {
            "threshold": clusters.threshold,
            "partition": [list(a.personas) for a in clusters.actors],
        },
        sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _report(clusters: ClusterSet, corpus: Corpus) -> None:
    handles = {p: corpus.personas[p]["handle"] for p in corpus.persona_ids}
    multi = [a for a in clusters.actors if not a.is_singleton]

    print(RULE)
    print("link/cluster.py — personas resolved into actors")
    print(RULE)
    print(f"  threshold  {clusters.threshold:.2f} "
          f"(links at or above this may merge two identities)")
    print(f"  actors     {len(clusters.actors)} over "
          f"{len(corpus.persona_ids)} personas — "
          f"{len(multi)} multi-persona, "
          f"{len(clusters.actors) - len(multi)} singleton")

    if multi:
        print(f"\n  multi-persona actors")
        for actor in multi:
            names = ", ".join(f"{p} ({handles[p]})" for p in actor.personas)
            print(f"    {actor.label:<14} {names}")
            print(f"      confidence {actor.min_edge_score:.3f} "
                  f"(weakest of {len(actor.edges)} merging link(s))")
            for edge in sorted(actor.edges, key=lambda e: -e.score):
                reasons = [
                    e["detail"] for e in edge.evidence
                    if e.get("type") in {"shared_identifier", "handle_reuse"}
                ]
                print(f"        {edge.persona_a}~{edge.persona_b} "
                      f"{edge.band} {edge.score:.3f}"
                      + (f" — {reasons[0]}" if reasons else ""))

    print(f"\n{RULE}")
    print("  A cluster is a chain: confidence above is the WEAKEST link holding")
    print("  it together, not the strongest. Merging is a judgement made at one")
    print("  threshold; re-run with --threshold to see it differently.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve personas into actors over the scored link graph."
    )
    parser.add_argument("--source", choices=("fixtures", "db"), default="db",
                        help="score the corpus from fixtures/ (offline) or read "
                             "personas and links from Postgres (default)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"minimum link score that may merge two personas "
                             f"(default: {DEFAULT_THRESHOLD}, the PROBABLE floor)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the partition, write nothing")
    parser.add_argument("--operator", default=None,
                        help="operator id for the audit row (default: $OPERATOR_ID)")
    args = parser.parse_args(argv)

    if not 0.0 <= args.threshold <= 1.01:
        parser.error("--threshold must be between 0.0 and 1.0")

    from link.resolve import (  # noqa: PLC0415
        load_corpus,
        load_corpus_from_fixtures,
        resolve_pairs,
    )

    if args.source == "fixtures":
        corpus = load_corpus_from_fixtures()
        results, _, _, _ = resolve_pairs(corpus)
    else:
        from db import require_schema, session_scope  # noqa: PLC0415

        with session_scope() as session:
            require_schema(session)
            corpus = load_corpus(session)
            results, _, _, _ = resolve_pairs(corpus, session=session)

    if len(corpus.personas) < 2:
        print("fewer than two personas — nothing to cluster")
        return 0

    clusters = build(corpus, results, threshold=args.threshold)
    _report(clusters, corpus)

    if args.dry_run:
        print("\n  dry run: nothing written")
        return 0

    from db import Scan, require_schema, session_scope, utcnow  # noqa: PLC0415

    operator = args.operator or os.environ.get("OPERATOR_ID") or "unknown"
    action_hash = payload_hash(clusters)
    started = utcnow()

    with session_scope() as session:
        require_schema(session)
        scan = Scan(
            operator_id=operator,
            mode="manual",
            data_source=args.source,
            query=f"cluster --threshold {args.threshold:.2f}",
            sources_touched=sorted({
                str(s) for a in clusters.actors for s in a.sources
            }),
            action_hash=action_hash,
            started_at=started,
            status="running",
        )
        session.add(scan)
        session.flush()
        try:
            written = store_actors(session, clusters)
        except Exception as exc:
            scan.status = "failed"
            scan.error = str(exc)[:2000]
            scan.finished_at = utcnow()
            raise
        scan.status = "ok"
        scan.finished_at = utcnow()
        session.flush()
        print(f"\n  wrote {written} actor(s) and repointed "
              f"{len(corpus.persona_ids)} personas.actor_id")
        print(f"  audit row: scan id {scan.id}, operator {operator}, "
              f"sha256 {action_hash[:16]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
