"""actors.py — the resolved entities, and everything hanging off one of them.

Reads `actors` and `personas.actor_id`, both written by `python -m link.cluster`.
If that step has not run the table is empty and this returns an empty list with
a note saying so, rather than inventing clusters on the request path — merging
two identities is a decision with an audit row behind it, not something an API
does per GET.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from api.deps import get_session  # noqa: E402
from api.queries import (  # noqa: E402
    identifiers_for,
    link_summaries,
    persona_summaries,
    posts_for,
    trust_edges_for,
)
from api.schemas import (  # noqa: E402
    ActorDetail,
    ActorSummary,
    PersonaDetail,
    TimelineBucket,
)
from db import Actor, Persona, PersonaIdentifier, Post, band_for  # noqa: E402

router = APIRouter(tags=["actors"])

#: Shown beside the trust edges on the profile. The text is the point: an
#: overlap number on an actor page without it reads as corroboration.
TRUST_NOTE = (
    "Shared buyers between this actor's vendors and others. Relationship "
    "context only — measured against ground truth, buyer overlap separates "
    "true pairs from false ones worse than chance (ROC-AUC 0.389), so it is "
    "not part of this actor's confidence and never merged anyone."
)

__all__ = ["router"]


def _summary(row: Actor, personas, identifier_counts) -> ActorSummary:
    members = personas.get(row.id, [])
    return ActorSummary(
        id=row.id,
        label=row.label,
        category=row.category,
        persona_count=len(members),
        source_count=len({p.source_id for p in members if p.source_id is not None}),
        # max_confidence is written by link/cluster.py as the WEAKEST merging
        # edge. A single-persona actor was never merged, so it has no band —
        # not a band of WEAK, which would imply we looked and were unconvinced.
        band=band_for(row.max_confidence) if row.max_confidence is not None else None,
        confidence=row.max_confidence,
        identifier_count=sum(identifier_counts.get(p.id, 0) for p in members),
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        handles=[p.handle for p in members],
    )


def collect_actors(
    session,
    *,
    category: Optional[str] = None,
    band: Optional[str] = None,
    source_id: Optional[int] = None,
    min_personas: int = 1,
    first_seen_after: Optional[datetime] = None,
    last_seen_before: Optional[datetime] = None,
    limit: int = 200,
) -> list[ActorSummary]:
    """The listing logic, callable as an ordinary function.

    Kept separate from the route so other modules (api/routers/export.py) can
    reuse it. Calling a FastAPI route function directly hands it `Query(...)`
    objects as defaults, which look like values until something calls `.upper()`
    on one.
    """
    rows = list(session.execute(select(Actor)).scalars())
    if not rows:
        return []

    persona_rows = list(session.execute(
        select(Persona).where(Persona.actor_id.isnot(None))
    ).scalars())
    by_actor: dict[int, list] = {}
    for persona in persona_rows:
        by_actor.setdefault(persona.actor_id, []).append(persona)

    identifier_counts = dict(session.execute(
        select(PersonaIdentifier.persona_id, func.count())
        .group_by(PersonaIdentifier.persona_id)
    ).all())

    out = [_summary(row, by_actor, identifier_counts) for row in rows]

    if category:
        out = [a for a in out if a.category == category]
    if band:
        wanted = band.upper()
        out = [a for a in out if a.band == wanted]
    if source_id is not None:
        keep = {
            actor_id for actor_id, members in by_actor.items()
            if any(p.source_id == source_id for p in members)
        }
        out = [a for a in out if a.id in keep]
    if min_personas > 1:
        out = [a for a in out if a.persona_count >= min_personas]
    if first_seen_after:
        out = [a for a in out if a.first_seen and a.first_seen >= first_seen_after]
    if last_seen_before:
        out = [a for a in out if a.last_seen and a.last_seen <= last_seen_before]

    out.sort(key=lambda a: (-a.persona_count, -(a.confidence or 0.0), a.id))
    return out[:limit]


@router.get("/actors", response_model=list[ActorSummary])
def list_actors(
    session=Depends(get_session),
    category: Optional[str] = Query(None, description="exact category match"),
    band: Optional[str] = Query(None, description="CONFIRMED | PROBABLE | POSSIBLE | WEAK"),
    source_id: Optional[int] = Query(None, description="actors with a persona on this source"),
    min_personas: int = Query(1, ge=1, description="only actors of at least this size"),
    first_seen_after: Optional[datetime] = None,
    last_seen_before: Optional[datetime] = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[ActorSummary]:
    return collect_actors(
        session, category=category, band=band, source_id=source_id,
        min_personas=min_personas, first_seen_after=first_seen_after,
        last_seen_before=last_seen_before, limit=limit,
    )


def collect_actor_detail(session, actor_id: int, *,
                         post_samples: int = 5) -> ActorDetail:
    """The profile, callable as an ordinary function.

    Separate from the route for the same reason collect_actors is: calling a
    FastAPI route directly hands it Query(...) objects where its defaults
    should be. export/report.py reads through this.
    """
    row = session.get(Actor, actor_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no actor {actor_id}")

    members = list(session.execute(
        select(Persona).where(Persona.actor_id == actor_id).order_by(Persona.id)
    ).scalars())
    persona_ids = [p.id for p in members]

    summaries = persona_summaries(session, persona_ids)
    identifiers = identifiers_for(session, persona_ids)
    samples = posts_for(session, persona_ids, limit=post_samples)

    details: list[PersonaDetail] = []
    for persona in members:
        summary = summaries[persona.id]
        details.append(PersonaDetail(
            **summary.model_dump(),
            bio=persona.bio,
            identifiers=identifiers.get(persona.id, []),
            posts=samples.get(persona.id, []),
            first_seen=persona.first_seen,
            last_seen=persona.last_seen,
        ))

    # Only links wholly inside this actor: an edge to somebody else's persona is
    # not evidence about this actor's composition.
    links = link_summaries(
        session, persona_ids=persona_ids, both_ends=True,
        handles={p.id: p.handle for p in members},
    )

    buckets = Counter()
    persona_buckets: dict[str, set[int]] = {}
    for persona_id in persona_ids:
        for post in session.execute(
            select(Post.posted_at, Post.persona_id).where(Post.persona_id == persona_id)
        ).all():
            if not post[0]:
                continue
            key = post[0].strftime("%Y-%m")
            buckets[key] += 1
            persona_buckets.setdefault(key, set()).add(post[1])

    identifier_counts = {pid: len(identifiers.get(pid, [])) for pid in persona_ids}
    base = _summary(row, {actor_id: members}, identifier_counts)

    trust = trust_edges_for(session, persona_ids)

    return ActorDetail(
        **base.model_dump(),
        notes=row.notes,
        personas=details,
        links=links,
        trust_edges=trust,
        trust_note=TRUST_NOTE if trust else "",
        timeline=[
            TimelineBucket(bucket=key, posts=buckets[key],
                           personas=len(persona_buckets.get(key, ())))
            for key in sorted(buckets)
        ],
    )


@router.get("/actors/{actor_id}", response_model=ActorDetail)
def get_actor(actor_id: int, session=Depends(get_session),
              post_samples: int = Query(5, ge=0, le=50)) -> ActorDetail:
    return collect_actor_detail(session, actor_id, post_samples=post_samples)
