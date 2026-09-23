"""narrative — a short behavioural profile per actor.

The problem statement asks for "AI-based analysis including behavioural
profiling". This package provides it, with three properties that matter more
than the model:

**It cannot move a number.** No import path from here reaches `score/`, and
`tests/test_narrative.py` asserts that by parsing imports. A profile is prose
next to the evidence, never an input to it. If the model hallucinates, the
attribution score is exactly what it was.

**It says which it is.** Every profile carries `kind` — `"ai"` or
`"rule-based"` — and the UI and PDF label from that field. A template presented
as AI output would be undetectable to a reader and is the one lie this package
is built to make impossible.

**It works with the network unplugged.** Profiles are cached in `actor_profiles`
keyed by a fingerprint of the features they were written from, so a generated
profile survives into an offline demo. With no key configured, no cache, and no
network, the rule-based path still returns a profile.

    from narrative import profile_for
    text, kind, meta = profile_for(session, actor_id)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from narrative import features as _features
from narrative import prompt as _prompt
from narrative import template as _template
from narrative.features import ActorFeatures, PersonaFeatures, derive
from narrative.provider import Provider, ProviderError, from_env

__all__ = [
    "AI_LABEL",
    "ActorFeatures",
    "PersonaFeatures",
    "Profile",
    "derive",
    "profile_for",
]

#: Shown wherever an AI-written profile appears. The wording is deliberate:
#: "summary" not "analysis", and the limitation is in the label rather than a
#: footnote, because labels travel with the text and footnotes do not.
AI_LABEL = "AI-generated summary — written from derived features, not evidence"


@dataclass(frozen=True)
class Profile:
    text: str
    kind: str                       #: "ai" | "rule-based"
    label: str
    fingerprint: str
    model: Optional[str] = None
    provider: Optional[str] = None
    #: Why the AI path was not used, when it was attempted and failed. Shown to
    #: an operator running the generator, not to a jury.
    fallback_reason: Optional[str] = None

    @property
    def is_ai(self) -> bool:
        return self.kind == "ai"


def _rule_based(feats: ActorFeatures, reason: Optional[str] = None) -> Profile:
    return Profile(
        text=_template.render(feats),
        kind=_template.KIND,
        label=_template.LABEL,
        fingerprint=feats.fingerprint(),
        fallback_reason=reason,
    )


def generate(
    feats: ActorFeatures,
    provider: Optional[Provider] = None,
    *,
    allow_llm: bool = True,
) -> Profile:
    """Write one profile. Pure apart from the optional network call.

    Falls back to the template on any provider failure, carrying the reason.
    An LLM that is down, rate limited, misconfigured or returning fragments
    must degrade to something true rather than to an error page.
    """
    if not allow_llm:
        return _rule_based(feats, "LLM disabled for this run")

    if provider is None:
        try:
            provider = from_env()
        except ProviderError as exc:
            return _rule_based(feats, str(exc))
    if provider is None:
        return _rule_based(feats, "no LLM configured")

    try:
        completion = provider.complete(_prompt.build(feats))
    except ProviderError as exc:
        return _rule_based(feats, str(exc))

    return Profile(
        text=completion.text,
        kind="ai",
        label=AI_LABEL,
        fingerprint=feats.fingerprint(),
        model=completion.model,
        provider=completion.provider,
    )


def profile_for(
    session,
    actor_id: int,
    *,
    provider: Optional[Provider] = None,
    allow_llm: bool = False,
    use_cache: bool = True,
) -> Optional[Profile]:
    """The profile for one actor: cached, or generated, or rule-based.

    `allow_llm` defaults to **False** so that ordinary read paths — the actor
    page, the PDF — never make an outbound call while somebody is looking at a
    screen. `scripts/generate_profiles.py` passes True. A read gets whatever is
    cached, and the rule-based profile when nothing is.
    """
    feats = derive(session, actor_id)
    if feats is None:
        return None

    fingerprint = feats.fingerprint()
    if use_cache:
        cached = _load(session, actor_id, fingerprint)
        if cached is not None:
            return cached

    return generate(feats, provider, allow_llm=allow_llm)


def _load(session, actor_id: int, fingerprint: str) -> Optional[Profile]:
    """A cached profile, if one was written from these exact features."""
    from sqlalchemy import select  # noqa: PLC0415

    try:
        from db import ActorProfile  # noqa: PLC0415
    except ImportError:  # pragma: no cover - schema not yet applied
        return None

    row = session.execute(
        select(ActorProfile).where(ActorProfile.actor_id == actor_id)
    ).scalars().first()
    if row is None or row.fingerprint != fingerprint:
        # A stale cache is worse than none: it describes a corpus that is no
        # longer there. Ignore it and let the caller regenerate.
        return None

    return Profile(
        text=row.text,
        kind=row.kind,
        label=AI_LABEL if row.kind == "ai" else _template.LABEL,
        fingerprint=row.fingerprint,
        model=row.model,
        provider=row.provider,
    )


def store(session, actor_id: int, profile: Profile) -> None:
    """Cache a profile so the demo works with the network unplugged."""
    from sqlalchemy import select  # noqa: PLC0415

    from db import ActorProfile, utcnow  # noqa: PLC0415

    row = session.execute(
        select(ActorProfile).where(ActorProfile.actor_id == actor_id)
    ).scalars().first()
    if row is None:
        row = ActorProfile(actor_id=actor_id)
        session.add(row)

    row.text = profile.text
    row.kind = profile.kind
    row.model = profile.model
    row.provider = profile.provider
    row.fingerprint = profile.fingerprint
    row.generated_at = utcnow()
