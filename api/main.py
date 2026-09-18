"""main.py — the FastAPI application.

    uvicorn api.main:app --reload --port 8000

Extends the shell of legacy/alert_api.py — the app object, the env loader, the
in-memory job dict for POST /scan — and drops everything that belonged to v1's
page-centric threat model. CORS is added here because the legacy file never had
it, despite the build plan crediting it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from api.deps import SITE_LEVEL_NOTE, get_session  # noqa: E402
from api.routers import actors, export, graph, recon, scan, timeline  # noqa: E402
from db import (  # noqa: E402
    Actor,
    InfraFinding,
    Link,
    Persona,
    SchemaNotApplied,
    Scan,
    SessionLocal,
    Source,
    Writeprint,
)
from score.attribution import DEFAULT_PRESET, weights_for  # noqa: E402

__all__ = ["app"]

DESCRIPTION = """
Actor attribution over dark web personas. Read-only except for `POST /scan`.

**Every score served by this API distinguishes "not assessed" from "assessed as
zero".** Components arrive as `{"measured": true, "value": 0.86}` or
`{"measured": false, "reason": "..."}` — never a bare number standing in for
silence. On this corpus the infrastructure term is unmeasured on every pair, and
the reason is a sentence the pipeline wrote, not a blank.

Outputs are investigative leads requiring corroboration, never conclusions.
"""

app = FastAPI(
    title="Dark Sentinel v2 — Attribution API",
    description=DESCRIPTION,
    version="2.0",
)

_origins = [
    o.strip() for o in
    os.environ.get("API_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(SchemaNotApplied)
async def _schema_missing(request: Request, exc: SchemaNotApplied) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "hint": "python scripts/apply_schema.py && python scripts/load_fixtures.py",
        },
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}", "path": str(request.url.path)},
    )


app.include_router(actors.router)
app.include_router(graph.router)
app.include_router(timeline.router)
app.include_router(recon.router)
app.include_router(scan.router)
app.include_router(export.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Liveness plus whether the pipeline has actually been run."""
    counts: dict = {}
    database = "ok"
    try:
        session = SessionLocal()
        try:
            for name, model in (
                ("sources", Source), ("personas", Persona), ("actors", Actor),
                ("links", Link), ("infra_findings", InfraFinding), ("scans", Scan),
            ):
                counts[name] = session.execute(
                    select(func.count()).select_from(model)
                ).scalar()
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        database = f"unavailable: {type(exc).__name__}"

    ready = database == "ok" and counts.get("actors", 0) > 0
    return {
        "status": "ok" if ready else "degraded",
        "database": database,
        "counts": counts,
        "ready": ready,
        "hint": (
            None if ready else
            "actors is empty — run `python -m link.cluster --source db`, or POST "
            "/scan with steps ['ingest','resolve','cluster']"
        ),
    }


@app.get("/meta", tags=["meta"])
def meta(session=Depends(get_session)) -> dict:
    """What the numbers on screen mean, served next to the numbers themselves."""
    preset = DEFAULT_PRESET
    total_links = session.execute(select(func.count()).select_from(Link)).scalar()
    unmeasured_i = session.execute(
        select(func.count()).select_from(Link).where(Link.i_score.is_(None))
    ).scalar()
    refused = session.execute(
        select(func.count()).select_from(Writeprint)
        .where(Writeprint.vector.is_(None))
    ).scalar()

    return {
        "formula": "A = 0.40*H + 0.25*S + 0.20*B + 0.15*I",
        "preset": preset,
        "weights": weights_for(preset),
        "bands": {"CONFIRMED": 0.85, "PROBABLE": 0.65, "POSSIBLE": 0.45, "WEAK": 0.0},
        "links": total_links,
        "links_with_unmeasured_infrastructure": unmeasured_i,
        "infrastructure_note": SITE_LEVEL_NOTE,
        "personas_refused_by_stylometry": refused,
        "disclaimer": (
            "Outputs are investigative leads requiring corroboration, never "
            "conclusions. Figures are measured against a synthetic answer key."
        ),
    }
