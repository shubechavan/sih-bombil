"""audit.py — read the audit log. Admin only.

Reads are in this log, not just writes, because for an attribution tool the
sensitive act is usually looking. "Which analyst opened the profile for actor 3,
and when" is the question an oversight body actually asks, and a log that only
recorded pipeline runs could not answer it.

That cuts both ways, which is why this endpoint is admin-only: a log of what
your colleagues have been reading is itself sensitive. Reading it is logged too.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from api.auth import Principal, require_role  # noqa: E402
from api.deps import get_session  # noqa: E402
from db import AuditEntry, Scan  # noqa: E402

router = APIRouter(tags=["audit"])

__all__ = ["router"]


class AuditRow(BaseModel):
    id: int
    operator_id: str
    role: Optional[str] = None
    method: str
    path: str
    query: Optional[str] = None
    status: Optional[int] = None
    action_hash: Optional[str] = None
    at: Optional[datetime] = None


class ScanRow(BaseModel):
    """A pipeline run, from the other audit table."""

    id: int
    operator_id: Optional[str] = None
    mode: Optional[str] = None
    data_source: Optional[str] = None
    query: Optional[str] = None
    status: Optional[str] = None
    personas_new: Optional[int] = None
    links_new: Optional[int] = None
    action_hash: Optional[str] = None
    job_id: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class AuditPage(BaseModel):
    requests: list[AuditRow] = Field(default_factory=list)
    scans: list[ScanRow] = Field(default_factory=list)
    total_requests: int = 0
    total_scans: int = 0
    operators: list[str] = Field(default_factory=list)
    note: str


NOTE = (
    "Every authenticated request is recorded here, reads included — for an "
    "attribution tool, who viewed which actor is itself the sensitive act. "
    "`requests` is what people looked at; `scans` is what the pipeline did. "
    "action_hash is a SHA-256 over operator, method, path and query, so a row "
    "can be checked against the action it claims to record."
)


@router.get("/audit", response_model=AuditPage)
def read_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    operator: Optional[str] = Query(default=None),
    session=Depends(get_session),
    principal: Principal = Depends(require_role("admin")),
) -> AuditPage:
    requests_q = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(limit)
    if operator:
        requests_q = requests_q.where(AuditEntry.operator_id == operator)

    rows = session.execute(requests_q).scalars().all()
    scans = session.execute(
        select(Scan).order_by(Scan.id.desc()).limit(limit)
    ).scalars().all()

    return AuditPage(
        requests=[AuditRow.model_validate(r, from_attributes=True) for r in rows],
        scans=[ScanRow.model_validate(s, from_attributes=True) for s in scans],
        total_requests=session.execute(
            select(func.count()).select_from(AuditEntry)
        ).scalar() or 0,
        total_scans=session.execute(
            select(func.count()).select_from(Scan)
        ).scalar() or 0,
        operators=[
            row[0] for row in session.execute(
                select(AuditEntry.operator_id).distinct().order_by(AuditEntry.operator_id)
            ).all() if row[0]
        ],
        note=NOTE,
    )
