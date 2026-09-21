"""analyze.py — score arbitrary pasted text against every stored writeprint.

This endpoint exists to answer a question a demo cannot answer on its own: is
the dashboard reading a lookup table? Everything else in this API reports what
the pipeline already decided about a corpus it was seeded with. Here an analyst
pastes text nobody has seen, and the engine either matches it or does not, live.

That claim is only worth making if this runs the *same* engine. It does, by
construction rather than by care: the text is projected into the stored
vocabulary by `stylometry.featurise()` — transform only, no refit — and the
resulting vector is compared with the same dot product `link/resolve.py` uses,
then combined by the same `attribution_score()`. There is no scoring arithmetic
in this file.

H and I are structurally unavailable for a paste. Hard identifier overlap needs
a persona to own the identifiers, and infrastructure needs a host; text has
neither. Both are reported unmeasured with a reason and their weight is
redistributed, which is the same treatment I already gets on every pair in this
corpus.

The 300-character floor applies here exactly as it applies to persona 7: below
it, S is unmeasured and the paste is ranked on B alone if timestamps were
supplied. A short paste is not an error — refusing to score it is the feature.
Only a paste with neither enough prose nor any timestamp has nothing to say, and
that is the one case this returns 422 for.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.deps import get_session  # noqa: E402
from api.schemas import (  # noqa: E402
    AnalyseMatch,
    AnalyseRequest,
    AnalyseResponse,
    Components,
    EvidenceEntry,
    measured,
    unmeasured,
)
from db import Actor, Persona, Post, Source, Writeprint as WriteprintRow  # noqa: E402
from link import behaviour as behaviour_module  # noqa: E402
from link import stylometry as stylometry_module  # noqa: E402
from score.attribution import (  # noqa: E402
    DEFAULT_PRESET,
    attribution_score,
    weights_for,
)

router = APIRouter(tags=["analyze"])

__all__ = ["router"]

#: Below this many timestamps, a posting profile is flagged as too thin to
#: carry a band on its own. Not a floor: behaviour.build() deliberately has
#: none, and refusing here would throw away the only signal a short paste has.
THIN_PROFILE = 5

#: The pasted text's persona id. Negative so it can never collide with a real
#: one when the paste and the corpus share a profile map.
PASTE_ID = -1

_NO_HARD = (
    "hard identifier overlap was not assessed — pasted text has no persona to "
    "own an identifier, so H is structurally unavailable here rather than zero"
)
_NO_INFRA = (
    "infrastructure was not assessed — pasted text controls no hidden service, "
    "so there is nothing to fingerprint and I is structurally unavailable here"
)
_NO_TIMESTAMPS = (
    "behaviour was not assessed — no posting timestamps were supplied with this "
    "text, and a posting-hour profile cannot be inferred from prose alone"
)

NOTE = (
    "Scored on stylometry and behaviour only. H and I need a persona and a host; "
    "pasted text has neither, so their weight is redistributed over what was "
    "measured. These are investigative leads requiring corroboration, never "
    "conclusions."
)


def _live_feature_version(session) -> Optional[str]:
    """The version the stored writeprints were built at.

    Read from the rows rather than recomputed. If the corpus moved and nothing
    re-ran, this returns the stale version, `load_vocabulary` matches it, and
    every comparison stays internally consistent — which is what the caller
    wants from a comparison against what is actually in the table.
    """
    versions = {
        row[0] for row in session.execute(
            select(WriteprintRow.feature_version).distinct()
        ).all() if row[0]
    }
    if len(versions) != 1:
        return None
    return versions.pop()


def _corpus_posts(session) -> dict[int, list[dict]]:
    """Posts per persona, ordered as link/resolve.py orders them."""
    posts: dict[int, list[dict]] = {}
    for row in session.execute(
        select(Post).order_by(Post.posted_at.nullslast(), Post.id)
    ).scalars():
        posts.setdefault(row.persona_id, []).append({
            "title": row.title,
            "body": row.body,
            "category": row.category,
            "posted_at": row.posted_at,
        })
    return posts


def _paste_rows(request: AnalyseRequest) -> list[dict]:
    """The paste as behaviour.build() wants it.

    One row carries the text so the trade vector sees it exactly once; the rest
    carry only their timestamp. Repeating the body per timestamp would multiply
    every trade term by the number of posts and inflate that sub-signal.
    """
    rows = [{
        "posted_at": request.posted_at[0],
        "body": request.text,
        "category": request.categories[0] if request.categories else None,
    }]
    for index, moment in enumerate(request.posted_at[1:], start=1):
        rows.append({
            "posted_at": moment,
            "body": "",
            "category": (request.categories[index]
                         if index < len(request.categories) else None),
        })
    return rows


@router.post("/analyze", response_model=AnalyseResponse)
def analyse(request: AnalyseRequest, session=Depends(get_session)) -> AnalyseResponse:
    version = _live_feature_version(session)
    if version is None:
        raise HTTPException(
            status_code=503,
            detail="no writeprints to compare against, or several extractor "
                   "versions are present — run `python -m link.resolve "
                   "--source db`",
        )

    vocabulary = stylometry_module.load_vocabulary(session, version)
    if vocabulary is None:
        raise HTTPException(
            status_code=503,
            detail=f"the fitted vocabulary for {version} is not stored, so "
                   f"pasted text cannot be projected into the same space — run "
                   f"`python -m link.resolve --source db` to write it",
        )

    weights = weights_for(DEFAULT_PRESET)

    # ── the paste ────────────────────────────────────────────────────────────
    featurised = stylometry_module.featurise(request.text, vocabulary,
                                             persona_id=PASTE_ID)

    behaviours = None
    if request.posted_at:
        behaviours = behaviour_module.build({
            PASTE_ID: _paste_rows(request),
            **_corpus_posts(session),
        })

    if featurised.writeprint is None and behaviours is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{featurised.refused_reason}, and no posting timestamps were "
                f"supplied either — with neither prose nor activity there is "
                f"nothing to compare, and a score with no measured component "
                f"would be a number with nothing behind it"
            ),
        )

    # Categories are all-or-nothing per paste: supplying some and not others
    # would make the sub-signal mean different things for different personas.
    omit = () if request.categories else ("category",)

    # ── the corpus ───────────────────────────────────────────────────────────
    stored = stylometry_module.load(session, version)
    refusals = {
        row.persona_id: row.refused_reason
        for row in session.execute(
            select(WriteprintRow).where(WriteprintRow.vector.is_(None))
        ).scalars()
    }
    personas = {row.id: row for row in session.execute(select(Persona)).scalars()}
    sources = {row.id: row.name for row in session.execute(select(Source)).scalars()}
    actors = {row.id: row.label for row in session.execute(select(Actor)).scalars()}

    matches: list[AnalyseMatch] = []
    not_scored: list[dict] = []

    for persona_id, persona in personas.items():
        reasons = {"H": _NO_HARD, "I": _NO_INFRA}

        s_value = None
        if featurised.writeprint is None:
            reasons["S"] = featurised.refused_reason
        elif persona_id in stored:
            s_value = featurised.writeprint.similarity(stored[persona_id])
        else:
            reasons["S"] = (
                refusals.get(persona_id)
                or f"no writeprint is stored for persona {persona_id}"
            )

        b_value = None
        if behaviours is None:
            reasons["B"] = _NO_TIMESTAMPS
        else:
            b_value = behaviours.similarity(PASTE_ID, persona_id, omit=omit)
            if b_value is None:
                reasons["B"] = (
                    behaviours.refusal_reason(persona_id)
                    or f"behaviour not assessed — persona {persona_id} has no "
                       f"timestamped posts"
                )

        if s_value is None and b_value is None:
            # Nothing was measurable for this persona. Ranking it last would put
            # a number on a comparison that never happened.
            not_scored.append({
                "persona_id": persona_id,
                "handle": persona.handle,
                "reason": reasons.get("S") or reasons.get("B"),
            })
            continue

        attribution = attribution_score(None, s_value, b_value, None, reasons=reasons)

        matches.append(AnalyseMatch(
            persona_id=persona_id,
            handle=persona.handle,
            source_name=sources.get(persona.source_id),
            actor_id=persona.actor_id,
            actor_label=actors.get(persona.actor_id),
            score=round(attribution.score, 6),
            band=attribution.band,
            components=Components(
                H=unmeasured(_NO_HARD, attribution.weights["H"]),
                S=(measured(s_value, attribution.weights["S"]) if s_value is not None
                   else unmeasured(reasons["S"], attribution.weights["S"])),
                B=(measured(b_value, attribution.weights["B"]) if b_value is not None
                   else unmeasured(reasons["B"], attribution.weights["B"])),
                I=unmeasured(_NO_INFRA, attribution.weights["I"]),
            ),
            evidence=[
                EvidenceEntry(**_entry(e))
                for e in _describe(attribution, s_value, b_value, behaviours,
                                   persona_id, omit)
            ],
        ))

    matches.sort(key=lambda m: m.score, reverse=True)

    return AnalyseResponse(
        feature_version=version,
        char_count=featurised.char_count,
        masked_chars=featurised.masked_chars,
        stylometry=(
            unmeasured(featurised.refused_reason, weights["S"])
            if featurised.writeprint is None
            else measured(float(featurised.char_count), weights["S"])
        ),
        behaviour=(
            unmeasured(_NO_TIMESTAMPS, weights["B"]) if behaviours is None
            else measured(float(len(request.posted_at)), weights["B"])
        ),
        matches=matches[: request.limit],
        not_scored=not_scored,
        note=NOTE,
    )


def _describe(attribution, s_value, b_value, behaviours, persona_id,
              omit) -> list[dict]:
    """The evidence list, in the vocabulary link/resolve.py already writes."""
    entries = list(attribution.evidence)

    if s_value is not None:
        entries.insert(0, {
            "type": "stylometry",
            "detail": (
                f"writeprint cosine {s_value:.3f} against the pasted text "
                f"(char {stylometry_module.CHAR_NGRAM_RANGE[0]}-"
                f"{stylometry_module.CHAR_NGRAM_RANGE[1]} gram TF-IDF, function "
                f"words, punctuation, orthographic shape; identifiers masked; "
                f"transformed against the stored vocabulary, not refitted)"
            ),
            "weight": s_value,
        })

    if b_value is not None and behaviours is not None:
        for line in behaviours.explain(PASTE_ID, persona_id):
            entries.append({"type": "behaviour", "detail": line})
        paste = behaviours.get(PASTE_ID)
        if paste is not None and paste.post_count < THIN_PROFILE:
            # behaviour.build() has no floor on purpose — a real persona with two
            # posts still has a usable hour histogram. But a paste carrying two
            # timestamps can align with someone by coincidence and reach a band
            # on B alone, so the thinness is stated next to the number rather
            # than left for the reader to infer from the peaks line above.
            entries.append({
                "type": "behaviour_thin_profile",
                "detail": (
                    f"this profile rests on {paste.post_count} "
                    f"timestamp{'' if paste.post_count == 1 else 's'} — too few "
                    f"to distinguish a habit from a coincidence, so treat the "
                    f"posting-hour overlap above as weak corroboration only"
                ),
            })
        if omit:
            entries.append({
                "type": "behaviour_subsignal_not_assessed",
                "detail": (
                    f"{', '.join(omit)} not supplied with the pasted text, so "
                    f"its weight was redistributed over the remaining "
                    f"sub-signals rather than scored as a mismatch"
                ),
            })
        entries.append({
            "type": "behaviour_score",
            "detail": f"behavioural similarity {b_value:.3f}",
            "weight": b_value,
        })

    return entries


def _entry(entry: dict) -> dict:
    """Keep the keys EvidenceEntry declares; the rest travel in `extra`."""
    known = {"type", "detail", "weight", "identifier_type", "value", "component",
             "label", "derived"}
    out = {k: v for k, v in entry.items() if k in known}
    extra = {k: v for k, v in entry.items() if k not in known}
    if extra:
        out["extra"] = extra
    out.setdefault("type", "note")
    out.setdefault("detail", "")
    return out
