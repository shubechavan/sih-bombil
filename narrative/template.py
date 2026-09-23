"""template.py — the behavioural profile written without a model.

This is what `/actors/<id>` shows when no LLM is configured, when the API call
fails, and in CI. It is not a degraded mode that exists to be replaced: it runs
offline, it is deterministic, and it is the only version of this feature whose
output can be checked line by line against the database.

IT MUST NEVER BE PRESENTED AS AI OUTPUT
---------------------------------------
Every caller receives a `kind` alongside the text, and the UI and PDF label
from that field rather than from a default. A template described as an AI
summary is a lie about provenance in a tool whose entire argument is that it
says where things come from — and it is the easy lie, because nobody reading
the paragraph could tell.

WHAT IT WILL AND WILL NOT SAY
-----------------------------
It states what the features contain and stops. It does not infer motive, does
not guess at location or identity from timezone, and does not describe anyone
as sophisticated, careless or professional. Those readings are exactly what an
analyst is for, and a sentence generated from a histogram that sounds like a
judgement is worse than no sentence.

Where a persona was refused by the stylometry floor, the profile says so rather
than omitting it. A profile quietly built from 2 of 3 handles overstates what
is known.
"""

from __future__ import annotations

from typing import Iterable, Optional

from narrative.features import ActorFeatures, PersonaFeatures

__all__ = ["KIND", "LABEL", "render"]

#: What this is, in the field the UI and PDF read.
KIND = "rule-based"

#: What the reader is shown. Says "no AI" out loud, because the alternative is
#: that a reader assumes the AI label applies to everything on the page.
LABEL = "Rule-based profile — generated from stored features, no AI"


def _oxford(items: Iterable[str], conjunction: str = "and") -> str:
    values = [str(i) for i in items if str(i)]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} {conjunction} {values[1]}"
    return f"{', '.join(values[:-1])}, {conjunction} {values[-1]}"


def _hour_phrase(hours: tuple[int, ...]) -> str:
    """Describe a peak-hour set without pretending it is a clean window."""
    if not hours:
        return ""
    runs: list[list[int]] = []
    for hour in sorted(hours):
        if runs and hour == runs[-1][-1] + 1:
            runs[-1].append(hour)
        else:
            runs.append([hour])
    # 23 and 0 are adjacent on a clock even though they are not as integers.
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == 23:
        runs[0] = runs[-1] + runs[0]
        runs.pop()
    parts = [
        f"{run[0]:02d}:00" if len(run) == 1 else f"{run[0]:02d}:00–{run[-1]:02d}:59"
        for run in runs
    ]
    return _oxford(parts)


def _style_notes(style: dict) -> list[str]:
    """The orthographic habits distinctive enough to be worth a clause.

    Thresholds are descriptive cut-offs for prose, not measurements — they
    decide whether a habit is worth mentioning, never what anything scores.
    """
    notes: list[str] = []
    sentence_words = style.get("avg_sentence_words")
    if isinstance(sentence_words, (int, float)) and sentence_words:
        if sentence_words < 9:
            notes.append(f"short sentences (mean {sentence_words:.1f} words)")
        elif sentence_words > 18:
            notes.append(f"long sentences (mean {sentence_words:.1f} words)")

    for key, threshold, phrase in (
        ("ellipsis_per_sentence", 0.25, "frequent ellipses"),
        ("exclamation_per_sentence", 0.25, "frequent exclamation marks"),
        ("emdash_per_sentence", 0.15, "em-dashes"),
        ("emoji_per_sentence", 0.10, "emoji"),
        ("shouted_word_rate", 0.02, "words in full capitals"),
    ):
        value = style.get(key)
        if isinstance(value, (int, float)) and value >= threshold:
            notes.append(f"{phrase} ({value:.2f} per sentence)"
                         if "per sentence" not in phrase else phrase)

    ttr = style.get("type_token_ratio")
    if isinstance(ttr, (int, float)) and ttr and ttr < 0.30:
        notes.append(f"a repetitive vocabulary (type-token ratio {ttr:.2f})")
    return notes


def _persona_clause(persona: PersonaFeatures) -> str:
    where = persona.source_name or "an unnamed source"
    when = ""
    if persona.first_post and persona.last_post:
        when = (f" from {persona.first_post} to {persona.last_post}"
                if persona.first_post != persona.last_post
                else f" in {persona.first_post}")
    return f"{persona.handle} on {where}{when}"


def render(features: ActorFeatures) -> str:
    """A short behavioural profile. Deterministic for a given feature set."""
    if not features.personas:
        return "No personas are attached to this actor, so there is nothing to describe."

    paragraphs: list[str] = []
    timeline = features.timeline
    first, last = features.span

    # 1. What this actor is, structurally.
    count = len(timeline)
    handles = "handle" if count == 1 else "handles"
    sources = features.sources
    opening = (
        f"This actor is {count} {handles} — "
        f"{_oxford([p.handle for p in timeline])} — "
        f"across {len(sources)} "
        f"{'source' if len(sources) == 1 else 'sources'}"
    )
    if first and last:
        opening += f", active from {first} to {last}"
    paragraphs.append(opening + ".")

    # 2. The migration, if there is one.
    if count > 1:
        paragraphs.append(
            "In order of first appearance: "
            + _oxford([_persona_clause(p) for p in timeline], "then")
            + "."
        )

    # 3. When they work.
    hours = _hour_phrase(features.peak_hours)
    if hours:
        posts = sum(p.post_count for p in timeline)
        paragraphs.append(
            f"Posting concentrates at {hours} UTC across {posts} posts. "
            f"Hours are recorded as published by the sources and are not "
            f"evidence of a timezone."
        )

    # 4. What they trade in.
    trade_bits: list[str] = []
    if features.categories:
        trade_bits.append(
            f"listed under {_oxford(features.categories)}"
        )
    if features.trade_terms:
        trade_bits.append(
            f"using the trade terms {_oxford(sorted(features.trade_terms))}"
        )
    if trade_bits:
        paragraphs.append("Activity is " + _oxford(trade_bits) + ".")

    # 5. How they write — only for personas stylometry actually read.
    scored = [p for p in timeline if p.style and not p.stylometry_refused]
    for persona in scored:
        notes = _style_notes(persona.style)
        if notes:
            paragraphs.append(
                f"{persona.handle} writes with {_oxford(notes)}."
            )

    # 6. What was refused. Last, so it is the note the reader leaves with.
    refused = features.refused
    if refused:
        paragraphs.append(
            "Stylometry was refused for "
            + _oxford([p.handle for p in refused])
            + ": "
            + _oxford(sorted({p.stylometry_refused or "" for p in refused}))
            + ". Nothing above describes how "
            + ("that persona writes" if len(refused) == 1 else "those personas write")
            + "."
        )

    return " ".join(paragraphs)
