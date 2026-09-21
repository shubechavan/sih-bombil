"""main.py — the FastAPI application.

    uvicorn api.main:app --reload --port 8000

Extends the shell of legacy/alert_api.py — the app object and the env loader —
and drops everything that belonged to v1's page-centric threat model. CORS is
added here because the legacy file never had it, despite the build plan
crediting it.

Every route but /health, /docs and /auth/login requires a signed-in operator,
and every authenticated request writes an audit_log row including reads. See
api/auth.py for why reads are logged and why the token never reaches the browser.
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

from api.auth import (  # noqa: E402
    PUBLIC_PATHS,
    action_hash,
    current_user,
    principal_from_request,
)
from api.deps import SITE_LEVEL_NOTE, get_session  # noqa: E402
from api.routers import (  # noqa: E402
    actors,
    analyze,
    audit,
    auth,
    export,
    graph,
    recon,
    scan,
    timeline,
)
from db import (  # noqa: E402
    Actor,
    AuditEntry,
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


@app.middleware("http")
async def _audit(request: Request, call_next):
    """Record one row per authenticated request, reads included.

    Runs after the response so the status code is known. The principal is read
    from the header directly rather than through the dependency, because by this
    point the request is finished and a rejected request must not be turned into
    a second error on its way out.

    This fails **open**: if the insert fails, the failure is reported on the
    response as a header and the response still goes out. A database blip should
    not take the console down. Failing closed is defensible for a production
    forensics deployment and is a two-line change here — see AUDIT_REQUIRED.
    """
    response = await call_next(request)

    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return response

    principal = principal_from_request(request)
    if principal is None:
        return response

    try:
        session = SessionLocal()
        try:
            query = str(request.url.query or "")
            session.add(AuditEntry(
                operator_id=principal.username,
                role=principal.role,
                method=request.method,
                path=path,
                query=query or None,
                status=response.status_code,
                action_hash=action_hash(
                    principal.username, request.method, path, query
                ),
            ))
            session.commit()
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        # Visible rather than silent: a console whose audit log has quietly
        # stopped recording is worse than one that says so.
        response.headers["X-Audit-Error"] = f"{type(exc).__name__}"

    return response


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


# /auth carries its own rules — login must be reachable without a token.
app.include_router(auth.router)

# Everything below requires a signed-in operator. Declared here rather than on
# each route so a router added later cannot forget it.
_signed_in = [Depends(current_user)]

app.include_router(actors.router, dependencies=_signed_in)
app.include_router(analyze.router, dependencies=_signed_in)
app.include_router(audit.router)
app.include_router(graph.router, dependencies=_signed_in)
app.include_router(timeline.router, dependencies=_signed_in)
app.include_router(recon.router, dependencies=_signed_in)
app.include_router(scan.router, dependencies=_signed_in)
app.include_router(export.router, dependencies=_signed_in)


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
def meta(session=Depends(get_session),
         principal=Depends(current_user)) -> dict:
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
