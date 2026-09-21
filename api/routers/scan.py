"""scan.py — trigger a pipeline run, return a job id.

A manual trigger, not a scheduler: APScheduler and continuous mode are Phase 5.

The job runs the same module entry points an operator would run by hand —
ingest, resolve, cluster — as subprocesses rather than in-process calls. Two
reasons. Those modules call `sys.exit`, reconfigure stdout and hold their own
sessions, so importing them into a request worker would fight the server for
both; and each writes its own `scans` audit row, which is the record that
matters. The in-memory job dict below is progress reporting for the UI, and it
is deliberately not the audit trail.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.auth import Principal, require_role  # noqa: E402
from api.deps import get_session  # noqa: E402
from api.schemas import ScanJob  # noqa: E402
from db import Scan, utcnow  # noqa: E402

router = APIRouter(tags=["scan"])

__all__ = ["router"]

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL_SECONDS = 60 * 60

#: The pipeline, in order. Each is a module with its own audit row.
_STEPS: dict[str, list[str]] = {
    "ingest": ["scripts/ingest.py", "--source", "fixtures"],
    "resolve": ["-m", "link.resolve", "--source", "db"],
    "cluster": ["-m", "link.cluster", "--source", "db"],
    "recon": ["-m", "recon.fingerprint", "--source", "fixtures"],
    "correlate": ["-m", "recon.correlate", "--source", "db"],
}


class ScanRequest(BaseModel):
    steps: list[str] = Field(
        default_factory=lambda: ["ingest", "resolve", "cluster"],
        description=f"subset of {sorted(_STEPS)}, run in the order given",
    )
    operator: Optional[str] = Field(
        default=None, description="written into every audit row this run creates"
    )


def _cleanup() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _JOBS_LOCK:
        for job_id in [j for j, v in _JOBS.items() if v["created"] < cutoff]:
            _JOBS.pop(job_id, None)


def _set(job_id: str, **fields) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _run(job_id: str, steps: list[str], operator: Optional[str]) -> None:
    import os  # noqa: PLC0415

    env = dict(os.environ)
    if operator:
        env["OPERATOR_ID"] = operator

    _set(job_id, status="running", started_at=utcnow())
    try:
        for name in steps:
            _set(job_id, stage=name)
            result = subprocess.run(
                [sys.executable, *_STEPS[name]],
                cwd=str(ROOT), env=env, capture_output=True, text=True,
                timeout=900,
            )
            if result.returncode != 0:
                tail = (result.stderr or result.stdout or "").strip().splitlines()
                _set(job_id, status="failed", stage=name, finished_at=utcnow(),
                     error=f"{name} exited {result.returncode}: "
                           + (tail[-1] if tail else "no output"))
                return
        _set(job_id, status="ok", stage=None, finished_at=utcnow())
    except Exception as exc:  # noqa: BLE001
        _set(job_id, status="failed", finished_at=utcnow(),
             error=f"{type(exc).__name__}: {exc}")


def _view(job: dict) -> ScanJob:
    return ScanJob(
        job_id=job["job_id"], status=job["status"], stage=job.get("stage"),
        steps=job["steps"], started_at=job.get("started_at"),
        finished_at=job.get("finished_at"), error=job.get("error"),
        scan_ids=job.get("scan_ids", []),
    )


@router.post("/scan", response_model=ScanJob, status_code=202)
def start_scan(
    request: ScanRequest,
    background: BackgroundTasks,
    # Running the pipeline rewrites the links table. Reading is an analyst's
    # job; changing what everyone else reads is not.
    principal: Principal = Depends(require_role("admin")),
) -> ScanJob:
    unknown = [s for s in request.steps if s not in _STEPS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown step(s) {unknown}; expected any of {sorted(_STEPS)}",
        )
    if not request.steps:
        raise HTTPException(status_code=400, detail="no steps requested")

    _cleanup()
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id, "status": "queued", "stage": None,
        "steps": list(request.steps), "created": time.time(),
        "started_at": None, "finished_at": None, "error": None, "scan_ids": [],
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job

    background.add_task(_run, job_id, list(request.steps), request.operator)
    return _view(job)


@router.get("/scan/{job_id}", response_model=ScanJob)
def scan_status(job_id: str, session=Depends(get_session)) -> ScanJob:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no scan job {job_id!r}")

    # The audit rows are the record; surface the ones this run produced.
    if job.get("started_at"):
        rows = session.execute(
            select(Scan.id).where(Scan.started_at >= job["started_at"])
            .order_by(Scan.id)
        ).all()
        job["scan_ids"] = [r[0] for r in rows]
    return _view(job)
