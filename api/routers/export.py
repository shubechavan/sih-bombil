"""export.py — csv | json of the current result set.

PDF is Phase 5 and is not stubbed here; asking for it returns 400 naming the
phase rather than an empty file.

The CSV flattening is where an unmeasured component is most likely to be
silently turned into a zero, because a spreadsheet cell wants a number. It is
written as an empty cell plus a companion `*_reason` column carrying the
sentence, so the distinction survives into a file somebody opens in Excel three
weeks later.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: E402
from fastapi.responses import Response  # noqa: E402

from api.deps import get_session  # noqa: E402
from api.queries import link_summaries, persona_summaries  # noqa: E402
from db import utcnow  # noqa: E402
from api.routers.actors import collect_actors  # noqa: E402

router = APIRouter(tags=["export"])

__all__ = ["router"]

DATASETS = ("actors", "links", "personas")

_COLUMNS: dict[str, list[str]] = {
    "actors": ["id", "label", "category", "persona_count", "source_count", "band",
               "confidence", "identifier_count", "first_seen", "last_seen",
               "handles"],
    "links": ["persona_a", "handle_a", "persona_b", "handle_b", "score", "band",
              "method", "H", "H_reason", "S", "S_reason", "B", "B_reason",
              "I", "I_reason", "evidence"],
    "personas": ["id", "handle", "handle_normalized", "source_id", "source_name",
                 "category", "post_count", "stylometry_refused",
                 "stylometry_refused_reason", "char_count"],
}


def _component_cells(row: dict, key: str) -> tuple[str, str]:
    """(value, reason) for one component. Never a zero standing in for silence."""
    component = row.get(key) or {}
    if component.get("measured"):
        value = component.get("value")
        return ("" if value is None else f"{value:.6f}"), ""
    return "", str(component.get("reason") or "")


def _rows_for(session, dataset: str) -> list[dict]:
    if dataset == "actors":
        return [a.model_dump() for a in collect_actors(session, limit=1000)]
    if dataset == "links":
        return [l.model_dump() for l in link_summaries(session)]
    return [p.model_dump() for p in persona_summaries(session).values()]


def _flatten(dataset: str, row: dict) -> dict:
    if dataset != "links":
        out = dict(row)
        if "handles" in out and isinstance(out["handles"], list):
            out["handles"] = "; ".join(out["handles"])
        return out

    flat = {k: row.get(k) for k in
            ("persona_a", "handle_a", "persona_b", "handle_b", "score", "band", "method")}
    components = row.get("components") or {}
    for key in ("H", "S", "B", "I"):
        value, reason = _component_cells(components, key)
        flat[key] = value
        flat[f"{key}_reason"] = reason
    flat["evidence"] = " | ".join(
        str(e.get("detail") or "") for e in (row.get("evidence") or [])
    )
    return flat


def _serialise(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    if value is None:
        return ""
    return value


@router.get("/export/{fmt}")
def export(
    fmt: str,
    session=Depends(get_session),
    dataset: str = Query("links", description=f"one of {DATASETS}"),
    columns: Optional[str] = Query(
        None, description="comma-separated subset of the dataset's columns"),
) -> Response:
    fmt = fmt.lower()
    if fmt == "pdf":
        raise HTTPException(
            status_code=400,
            detail="PDF export is Phase 5 (export/report.py) and is not built. "
                   "Use csv or json.",
        )
    if fmt not in {"csv", "json"}:
        raise HTTPException(status_code=400, detail=f"unknown format {fmt!r}")
    if dataset not in DATASETS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown dataset {dataset!r}; expected one of {list(DATASETS)}",
        )

    rows = [_flatten(dataset, r) for r in _rows_for(session, dataset)]
    available = _COLUMNS[dataset]
    chosen = available
    if columns:
        wanted = [c.strip() for c in columns.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in available]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown column(s) {unknown}; available: {available}",
            )
        chosen = wanted

    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"dark-sentinel-{dataset}-{stamp}.{fmt}"

    if fmt == "json":
        payload = [{k: r.get(k) for k in chosen} for r in rows]
        return Response(
            content=json.dumps(payload, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=chosen, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _serialise(row.get(k)) for k in chosen})
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
