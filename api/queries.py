"""queries.py — shared reads, so two routers cannot disagree about one fact.

Persona summaries, identifier provenance and link conversion are each needed by
more than one endpoint. Writing them twice is how /actors and /graph end up
reporting different bands for the same pair.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from api.deps import components_from_link, evidence_from_rows  # noqa: E402
from api.schemas import (  # noqa: E402
    Identifier,
    LinkSummary,
    PersonaSummary,
    PostSample,
    TrustEdge,
)
from db import (  # noqa: E402
    Identifier as IdentifierRow,
    Link,
    Persona,
    PersonaIdentifier,
    Post,
    Source,
    Writeprint,
)

__all__ = [
    "derived_lookup",
    "identifiers_for",
    "link_summaries",
    "persona_summaries",
    "posts_for",
    "source_names",
    "trust_edges_for",
]


#: Mirrors link.trust.MIN_SHARED_BUYERS. A display threshold, nothing branches
#: on it.
TRUST_MIN_SHARED = 2


def source_names(session) -> dict[int, str]:
    return {row.id: row.name for row in session.execute(select(Source)).scalars()}


def derived_lookup(session) -> dict[tuple[str, str], bool]:
    """(type, value) -> was this found in prose, or only declared?

    `meta.source` is written by whichever step created the row: "ingest" when
    Phase 1's extractor found the value in a bio or a post, "fixtures" when the
    corpus declares it but no prose contains it. docs/BUILD_PLAN.md records the
    six that fall in the second group; a UI citing one of them must not imply
    the pipeline discovered it.
    """
    out: dict[tuple[str, str], bool] = {}
    for row in session.execute(select(IdentifierRow)).scalars():
        origin = (row.meta or {}).get("source")
        out[(row.type, row.value)] = origin == "ingest"
    return out


def _refusals(session) -> dict[int, tuple[Optional[str], Optional[int]]]:
    """persona_id -> (refusal reason, chars available), for refused personas.

    A refusal is stored as a writeprints row with a NULL vector. Reading it here
    means /actors and /graph can both surface the refusal rather than leaving a
    blank where a score would be.
    """
    out: dict[int, tuple[Optional[str], Optional[int]]] = {}
    for row in session.execute(select(Writeprint)).scalars():
        if row.vector is None:
            out[row.persona_id] = (row.refused_reason, row.char_count)
    return out


def persona_summaries(session, persona_ids: Optional[Iterable[int]] = None
                      ) -> dict[int, PersonaSummary]:
    statement = select(Persona)
    if persona_ids is not None:
        ids = list(persona_ids)
        if not ids:
            return {}
        statement = statement.where(Persona.id.in_(ids))

    names = source_names(session)
    refused = _refusals(session)
    counts = dict(
        session.execute(
            select(Post.persona_id, func.count(Post.id)).group_by(Post.persona_id)
        ).all()
    )

    out: dict[int, PersonaSummary] = {}
    for row in session.execute(statement).scalars():
        reason, chars = refused.get(row.id, (None, None))
        out[row.id] = PersonaSummary(
            id=row.id,
            handle=row.handle,
            handle_normalized=row.handle_normalized,
            source_id=row.source_id,
            source_name=names.get(row.source_id),
            category=row.category,
            post_count=counts.get(row.id, row.post_count or 0),
            stylometry_refused=row.id in refused,
            stylometry_refused_reason=reason,
            char_count=chars,
        )
    return out


def identifiers_for(session, persona_ids: Sequence[int]) -> dict[int, list[Identifier]]:
    if not persona_ids:
        return {}
    rows = session.execute(
        select(PersonaIdentifier.persona_id, IdentifierRow)
        .join(IdentifierRow, IdentifierRow.id == PersonaIdentifier.identifier_id)
        .where(PersonaIdentifier.persona_id.in_(list(persona_ids)))
    ).all()

    out: dict[int, list[Identifier]] = {}
    for persona_id, row in rows:
        out.setdefault(persona_id, []).append(Identifier(
            type=row.type,
            value=row.value,
            value_norm=row.value_norm,
            validated=bool(row.validated),
            derived=(row.meta or {}).get("source") == "ingest",
            first_seen=row.first_seen,
            last_seen=row.last_seen,
        ))
    for values in out.values():
        values.sort(key=lambda i: (i.type, i.value))
    return out


def posts_for(session, persona_ids: Sequence[int], *, limit: int = 5
              ) -> dict[int, list[PostSample]]:
    if not persona_ids:
        return {}
    out: dict[int, list[PostSample]] = {}
    for persona_id in persona_ids:
        rows = session.execute(
            select(Post)
            .where(Post.persona_id == persona_id)
            .order_by(Post.posted_at.desc().nullslast())
            .limit(limit)
        ).scalars()
        out[persona_id] = [
            PostSample(title=r.title, body=r.body, category=r.category,
                       posted_at=r.posted_at)
            for r in rows
        ]
    return out


def link_summaries(session, *, persona_ids: Optional[Sequence[int]] = None,
                   min_score: Optional[float] = None,
                   both_ends: bool = False,
                   handles: Optional[Mapping[int, str]] = None
                   ) -> list[LinkSummary]:
    """Links, converted once, with components that never fake a zero."""
    statement = select(Link)
    if min_score is not None:
        statement = statement.where(Link.score >= min_score)
    if persona_ids:
        ids = list(persona_ids)
        if both_ends:
            statement = statement.where(
                Link.persona_a.in_(ids), Link.persona_b.in_(ids)
            )
        else:
            statement = statement.where(
                Link.persona_a.in_(ids) | Link.persona_b.in_(ids)
            )

    rows = list(session.execute(statement.order_by(Link.score.desc())).scalars())
    if handles is None:
        wanted = {r.persona_a for r in rows} | {r.persona_b for r in rows}
        handles = {
            pid: summary.handle
            for pid, summary in persona_summaries(session, wanted).items()
        }
    lookup = derived_lookup(session)

    return [
        LinkSummary(
            persona_a=row.persona_a,
            persona_b=row.persona_b,
            handle_a=handles.get(row.persona_a),
            handle_b=handles.get(row.persona_b),
            score=row.score,
            band=row.band,
            method=row.method,
            components=components_from_link(row),
            evidence=evidence_from_rows(row.evidence, lookup),
            computed_at=row.computed_at,
        )
        for row in rows
    ]


def trust_edges_for(session, persona_ids: Optional[Sequence[int]] = None,
                    *, min_shared: int = TRUST_MIN_SHARED) -> list[TrustEdge]:
    """Buyer-mediated vendor relationships, as context rows.

    `link/trust.py` owns the computation and the argument for why it is not a
    score; this only converts. With `persona_ids`, keeps edges with at least
    one end inside that set — an actor profile wants the vendors its personas
    share buyers with, which by definition are mostly outside the actor.
    """
    from link.trust import NOT_A_SCORE, trust_edges  # noqa: PLC0415

    wanted = set(persona_ids) if persona_ids is not None else None
    rows = []
    for edge in trust_edges(session, min_shared=min_shared):
        if wanted is not None and not ({edge.persona_a, edge.persona_b} & wanted):
            continue
        payload = edge.to_dict()
        payload["note"] = NOT_A_SCORE
        rows.append(TrustEdge(**payload))
    return rows
