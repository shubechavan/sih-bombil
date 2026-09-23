"""prompt.py — what actually goes on the wire, and what never does.

`narrative/features.py` already guarantees no raw post text is collected. This
module adds the second layer anyway: every free-text string in the payload goes
through `extract.gliner_extract.redact()` before it is serialised.

Two layers rather than one because they fail differently. The first is a
property of what was selected — strong, but it silently weakens the moment
somebody adds a field. The second is a property of what is sent, and it keeps
holding when that happens.

`redact()` fails closed: without the GLiNER model installed it runs the regex
stage rather than passing text through, so the guarantee does not depend on an
optional dependency being present. It is not installed on the demo stack.

WHAT THE MODEL IS ASKED TO DO
-----------------------------
Describe the features. Not interpret them. The instructions forbid inferring
identity, location, timezone, nationality or motive, and forbid characterising
the actor as sophisticated, careless, professional or dangerous — the readings
that sound like expertise and are actually the model filling a template. An
analyst makes those calls with the evidence in front of them.
"""

from __future__ import annotations

import json
from typing import Any

from narrative.features import ActorFeatures

__all__ = ["INSTRUCTIONS", "build", "payload_for"]

INSTRUCTIONS = """\
You are summarising a threat actor's observable behaviour for an investigative
analyst. You are given derived features only — counts, rates and timestamps
computed from a corpus. You have not been given, and will not be given, the
text the actor wrote.

Write 3-5 sentences of plain prose describing the patterns these features show:
when the actor is active, how their handles appear over time, what they trade
in, and any distinctive writing habits recorded in the style figures.

Rules:
- Describe only what the features contain. Do not add facts.
- Do not infer identity, real name, location, timezone or nationality. Posting
  hours are UTC as published by the source and are not evidence of where anyone
  is.
- Do not characterise the actor as sophisticated, careless, professional,
  dangerous or similar. Describe behaviour, not character.
- Where a persona's stylometry was refused, say so rather than omitting it.
- No preamble, no headings, no bullet points. Prose only.
"""


def _clean(value: Any) -> Any:
    """Redact free text. Numbers and booleans pass through untouched."""
    from extract.gliner_extract import redact  # noqa: PLC0415

    if isinstance(value, str):
        cleaned, _removed = redact(value)
        return cleaned
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def payload_for(features: ActorFeatures) -> dict:
    """The feature dictionary that will be serialised into the prompt.

    Deliberately explicit rather than `asdict()`: a field reaches the wire
    because it is named here, so adding one to the dataclass does not silently
    start transmitting it.
    """
    first, last = features.span
    body = {
        "personas": [
            {
                "handle": persona.handle,
                "source": persona.source_name,
                "first_post": persona.first_post,
                "last_post": persona.last_post,
                "post_count": persona.post_count,
                "category": persona.category,
                "peak_hours_utc": list(persona.peak_hours),
                "trade_terms": list(persona.trade_terms),
                "style": persona.style,
                "stylometry_refused": persona.stylometry_refused,
            }
            for persona in features.timeline
        ],
        "active_from": first,
        "active_to": last,
        "sources": list(features.sources),
        "categories": list(features.categories),
    }
    return _clean(body)


def build(features: ActorFeatures) -> str:
    """Instructions plus the redacted feature payload."""
    body = json.dumps(payload_for(features), indent=2, sort_keys=True,
                      ensure_ascii=False)
    return f"{INSTRUCTIONS}\nFeatures:\n{body}\n"
