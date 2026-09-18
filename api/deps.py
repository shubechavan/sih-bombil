"""deps.py — database session dependency and the row→schema conversions.

Every conversion that touches a component score lives here rather than in a
router, so there is exactly one place where a NULL could accidentally become a
zero, and it is a place with a test pointed at it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from api.schemas import (  # noqa: E402
    Component,
    Components,
    EvidenceEntry,
    measured,
    unmeasured,
)
from db import SessionLocal, require_schema  # noqa: E402
from score.attribution import COMPONENT_LABELS, DEFAULT_PRESET, weights_for  # noqa: E402

__all__ = [
    "SITE_LEVEL_NOTE",
    "components_from_link",
    "evidence_from_rows",
    "get_session",
    "reason_for",
]

#: Shown on the recon page. Recon findings are real and site-level; the I term
#: is persona-level. Saying so where the findings are displayed stops a reader
#: assuming the blank I column means recon failed.
SITE_LEVEL_NOTE = (
    "These findings are site-level: they fingerprint the hidden service, not the "
    "vendors trading on it. Infrastructure overlap (the I term) is only scored "
    "between personas that control a host of their own, and no persona in this "
    "corpus does — so I is unmeasured on every pair and its weight is "
    "redistributed. That is a property of the corpus, not a gap in the recon "
    "module."
)

_FALLBACK_REASONS = {
    "H": "hard identifier overlap was not assessed for this pair",
    "S": "stylometry did not run for this pair",
    "B": "behavioural comparison did not run for this pair",
    "I": "infrastructure was not assessed for this pair",
}


def get_session() -> Iterator:
    """FastAPI dependency. Read-only by convention; routers do not commit."""
    session = SessionLocal()
    try:
        require_schema(session)
        yield session
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"database unavailable: {type(exc).__name__}: {exc}",
        ) from exc
    finally:
        session.close()


def reason_for(key: str, evidence: Optional[list]) -> str:
    """The pipeline's own words for why a component was not assessed.

    `component_not_assessed` entries are written by score/attribution.py at
    scoring time and carry the persona and the number. Falling back to a generic
    sentence would throw away the specific one, so the fallback exists only for
    rows written before that entry type did.
    """
    for entry in evidence or []:
        if entry.get("type") == "component_not_assessed" and entry.get("component") == key:
            detail = (entry.get("detail") or "").strip()
            if detail:
                return detail
    return _FALLBACK_REASONS.get(key, f"{key} was not assessed")


def components_from_link(row: Any, *, preset: str = DEFAULT_PRESET) -> Components:
    """Build the four components from a links row.

    NULL means not assessed. This is the only function allowed to decide that,
    and it never substitutes 0.0 — see api/schemas.py for why that matters.
    """
    weights = weights_for(preset)
    evidence = list(row.evidence or [])
    values = {"H": row.h_score, "S": row.s_score, "B": row.b_score, "I": row.i_score}
    built: dict[str, Component] = {}
    for key, value in values.items():
        if value is None:
            built[key] = unmeasured(reason_for(key, evidence), weights.get(key))
        else:
            built[key] = measured(value, weights.get(key))
    return Components(**built)


def evidence_from_rows(
    evidence: Optional[list],
    derived_lookup: Optional[Mapping[tuple[str, str], bool]] = None,
) -> list[EvidenceEntry]:
    """Pass evidence through verbatim, tagging identifier provenance.

    `detail` is never rewritten. The only thing added is `derived`, so a link
    citing an identifier the corpus declares but no extractor ever found in
    prose says so on screen instead of implying the pipeline discovered it.
    """
    out: list[EvidenceEntry] = []
    known = {
        "type", "detail", "weight", "identifier_type", "value", "component", "label",
    }
    for entry in evidence or []:
        if not isinstance(entry, dict):
            continue
        derived: Optional[bool] = None
        if entry.get("type") == "shared_identifier" and derived_lookup is not None:
            key = (entry.get("identifier_type") or "", entry.get("value") or "")
            derived = derived_lookup.get(key)
        out.append(EvidenceEntry(
            type=str(entry.get("type") or "note"),
            detail=str(entry.get("detail") or ""),
            weight=entry.get("weight"),
            identifier_type=entry.get("identifier_type"),
            value=entry.get("value"),
            component=entry.get("component"),
            label=entry.get("label") or COMPONENT_LABELS.get(entry.get("component") or ""),
            derived=derived,
            extra={k: v for k, v in entry.items() if k not in known},
        ))
    return out
