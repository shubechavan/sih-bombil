"""timeline.py — posting activity over a date range.

Buckets `posts.posted_at`, which is the only timestamp in the corpus that
reflects when an actor was actually active. `scraped_at` records when we looked,
which is a fact about us rather than about them.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, Query  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.deps import get_session  # noqa: E402
from api.schemas import TimelineBucket  # noqa: E402
from db import Persona, Post  # noqa: E402

router = APIRouter(tags=["timeline"])

__all__ = ["router"]

_FORMATS = {"day": "%Y-%m-%d", "week": "%G-W%V", "month": "%Y-%m"}


@router.get("/timeline", response_model=list[TimelineBucket])
def get_timeline(
    session=Depends(get_session),
    bucket: str = Query("month", pattern="^(day|week|month)$"),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    actor_id: Optional[int] = None,
    source_id: Optional[int] = None,
    persona_id: Optional[int] = None,
) -> list[TimelineBucket]:
    statement = select(Post.posted_at, Post.persona_id).where(Post.posted_at.isnot(None))
    if start:
        statement = statement.where(Post.posted_at >= start)
    if end:
        statement = statement.where(Post.posted_at <= end)
    if persona_id is not None:
        statement = statement.where(Post.persona_id == persona_id)

    if actor_id is not None or source_id is not None:
        scoped = select(Persona.id)
        if actor_id is not None:
            scoped = scoped.where(Persona.actor_id == actor_id)
        if source_id is not None:
            scoped = scoped.where(Persona.source_id == source_id)
        ids = [row[0] for row in session.execute(scoped).all()]
        if not ids:
            return []
        statement = statement.where(Post.persona_id.in_(ids))

    fmt = _FORMATS[bucket]
    counts: dict[str, int] = defaultdict(int)
    personas: dict[str, set[int]] = defaultdict(set)
    for posted_at, pid in session.execute(statement).all():
        key = posted_at.strftime(fmt)
        counts[key] += 1
        personas[key].add(pid)

    return [
        TimelineBucket(bucket=key, posts=counts[key], personas=len(personas[key]))
        for key in sorted(counts)
    ]
