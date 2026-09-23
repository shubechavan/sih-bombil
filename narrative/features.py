"""features.py — the derived facts a behavioural profile is written from.

Everything here is already computed by the engine and stored: posting hours
come from `posts.posted_at`, orthographic habits from `writeprints.features`
(which `link/stylometry.py` fills for exactly this kind of use), categories
from `personas.category`, trade vocabulary from `link/behaviour.py`'s fixed
lexicon. Nothing is recomputed differently and nothing new is measured.

**No raw post text appears in anything this module returns.** That is the
property that makes it safe to hand to an external model, and it is a stronger
guarantee than redacting prose would be: text that was never collected cannot
leak. `tests/test_narrative.py` asserts it by checking the derived payload
against the actual post bodies in the corpus.

The package is called `narrative` rather than `profile` because `profile` is a
standard library module and shadowing it breaks `cProfile` in ways that surface
a long way from the cause.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

__all__ = [
    "STYLE_KEYS",
    "ActorFeatures",
    "PersonaFeatures",
    "derive",
]

#: The readable stylometric keys worth putting in a profile. A deliberate
#: subset of `writeprints.features`: the rest are either implementation detail
#: (`vector_dim`, `family_weights`) or too fine to say anything about a person
#: in a sentence (`double_space_rate`).
STYLE_KEYS: tuple[str, ...] = (
    "avg_sentence_words",
    "type_token_ratio",
    "capitalisation_ratio",
    "shouted_word_rate",
    "ellipsis_per_sentence",
    "exclamation_per_sentence",
    "comma_per_sentence",
    "emdash_per_sentence",
    "emoji_per_sentence",
    "word_count",
    "sentence_count",
)


@dataclass(frozen=True)
class PersonaFeatures:
    """One handle, described by numbers rather than by what it wrote."""

    persona_id: int
    handle: str
    source_name: Optional[str] = None
    source_reliability: Optional[float] = None
    first_post: Optional[str] = None          #: ISO date, month precision
    last_post: Optional[str] = None
    post_count: int = 0
    category: Optional[str] = None
    peak_hours: tuple[int, ...] = ()
    trade_terms: tuple[str, ...] = ()
    style: dict = field(default_factory=dict)
    #: Why stylometry refused, when it did. Persona 7 has 152 characters
    #: against a 300 floor, and a profile that quietly omits that is a profile
    #: that overstates what is known.
    stylometry_refused: Optional[str] = None


@dataclass(frozen=True)
class ActorFeatures:
    """One resolved actor, and the personas that make it up."""

    actor_id: int
    personas: tuple[PersonaFeatures, ...] = ()
    display_name: Optional[str] = None

    @property
    def sources(self) -> tuple[str, ...]:
        seen = [p.source_name for p in self.personas if p.source_name]
        return tuple(dict.fromkeys(seen))

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({p.category for p in self.personas if p.category}))

    @property
    def trade_terms(self) -> tuple[str, ...]:
        terms: set[str] = set()
        for persona in self.personas:
            terms.update(persona.trade_terms)
        return tuple(sorted(terms))

    @property
    def peak_hours(self) -> tuple[int, ...]:
        hours: set[int] = set()
        for persona in self.personas:
            hours.update(persona.peak_hours)
        return tuple(sorted(hours))

    @property
    def span(self) -> tuple[Optional[str], Optional[str]]:
        firsts = [p.first_post for p in self.personas if p.first_post]
        lasts = [p.last_post for p in self.personas if p.last_post]
        return (min(firsts) if firsts else None, max(lasts) if lasts else None)

    @property
    def timeline(self) -> tuple[PersonaFeatures, ...]:
        """Personas in the order they appeared — the migration story."""
        return tuple(sorted(
            self.personas,
            key=lambda p: (p.first_post or "9999-99", p.persona_id),
        ))

    @property
    def refused(self) -> tuple[PersonaFeatures, ...]:
        return tuple(p for p in self.personas if p.stylometry_refused)

    def fingerprint(self) -> str:
        """Stable hash of the inputs, so a cached profile can be invalidated.

        Hashes the *features*, not the actor id: re-running the pipeline over
        an unchanged corpus must not invalidate a cached profile, and a corpus
        that did change must. Same principle as `feature_version` in
        link/stylometry.py — and the same trap, so this hashes every field that
        reaches the prompt rather than a convenient subset.
        """
        payload = json.dumps(
            [asdict(p) for p in self.timeline],
            sort_keys=True, default=str, ensure_ascii=True,
        )
        return "nar-1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _month(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.strftime("%Y-%m")
    except AttributeError:
        return str(value)[:7] or None


def derive(session, actor_id: int) -> Optional[ActorFeatures]:
    """Read one actor's derived features out of the database.

    Returns None when the actor has no personas, which is a data problem rather
    than an empty profile.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from db import Actor, Persona, Post, Source, Writeprint  # noqa: PLC0415
    from link.behaviour import build as build_behaviour  # noqa: PLC0415

    personas = list(session.execute(
        select(Persona).where(Persona.actor_id == actor_id).order_by(Persona.id)
    ).scalars())
    if not personas:
        return None

    ids = [p.id for p in personas]
    source_names = dict(session.execute(select(Source.id, Source.name)).all())
    reliability = dict(session.execute(select(Source.id, Source.reliability)).all())

    posts: dict[int, list[dict]] = {pid: [] for pid in ids}
    for row in session.execute(
        select(Post).where(Post.persona_id.in_(ids)).order_by(Post.id)
    ).scalars():
        posts[row.persona_id].append({
            "posted_at": row.posted_at,
            "body": row.body or "",
            "category": row.category,
        })

    # The engine's own behaviour extractor, so the hours and trade terms in a
    # profile are the same numbers the evidence list quotes.
    behaviours = build_behaviour(posts)

    writeprints = {
        w.persona_id: w for w in session.execute(
            select(Writeprint).where(Writeprint.persona_id.in_(ids))
        ).scalars()
    }

    built: list[PersonaFeatures] = []
    for persona in personas:
        rows = posts.get(persona.id) or []
        dates = [r["posted_at"] for r in rows if r["posted_at"]]
        behaviour = behaviours.get(persona.id)
        writeprint = writeprints.get(persona.id)
        stored = (writeprint.features or {}) if writeprint else {}

        built.append(PersonaFeatures(
            persona_id=persona.id,
            handle=persona.handle,
            source_name=source_names.get(persona.source_id),
            source_reliability=(
                float(reliability[persona.source_id])
                if reliability.get(persona.source_id) is not None else None
            ),
            first_post=_month(min(dates)) if dates else None,
            last_post=_month(max(dates)) if dates else None,
            post_count=len(rows),
            category=persona.category,
            peak_hours=tuple(behaviour.peak_hours) if behaviour else (),
            trade_terms=tuple(behaviour.trade_terms) if behaviour else (),
            style={k: stored[k] for k in STYLE_KEYS if k in stored},
            stylometry_refused=(
                writeprint.refused_reason if writeprint else None
            ),
        ))

    actor = session.get(Actor, actor_id)
    return ActorFeatures(
        actor_id=actor_id,
        personas=tuple(built),
        display_name=getattr(actor, "display_name", None) if actor else None,
    )
