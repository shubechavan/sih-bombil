"""scan.py — trigger a pipeline run, return a job id.

A manual trigger, not a scheduler: APScheduler and continuous mode are Phase 5.

The job runs the same module entry points an operator would run by hand —
ingest, resolve, cluster — as subprocesses rather than in-process calls. Two
reasons. Those modules call `sys.exit`, reconfigure stdout and hold their own
sessions, so importing them into a request worker would fight the server for
both; and each writes its own `scans` audit row, which is the record that
matters.

JOB STATE LIVES IN THE DATABASE
-------------------------------
It used to be a module-level dict behind a lock. That had three problems, and
the third was a live bug:

  * a restart — `uvicorn --reload`, a container bounce — turned every in-flight
    job into a 404, and a job that died mid-run stayed "running" forever;
  * two API replicas could not see each other's jobs, so POST on one and GET on
    the other was a 404;
  * `scan_ids` was computed as "every scan row created since this job started",
    with no ownership predicate and no upper bound, so it happily reported rows
    from a concurrent CLI run or a scheduler tick as belonging to this job.

A job is now a `scans` row like any other, with `mode='api'`, carrying `job_id`,
`stage` and `steps`. The steps it launches inherit that `job_id` through
$SCAN_JOB_ID, which `db.record_scan()` reads — so `scan_ids` becomes a join
rather than a guess, and the third problem disappears with the first two.

The parent row is job state; the child rows are the audit trail. That is the
distinction the old docstring drew between the dict and the table, and it still
holds — both simply live in the same place now.

What this does *not* fix: the work still runs on the replica that accepted the
POST, because it is a BackgroundTask and not a queue. Any replica can report
status; only one is doing the job. Distributing execution needs a real queue and
is not in this phase.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.auth import Principal, current_user, require_role  # noqa: E402
from api.deps import get_session  # noqa: E402
from api.schemas import ScanJob  # noqa: E402
from db import Scan, SessionLocal, finish_scan, record_scan, utcnow  # noqa: E402

router = APIRouter(tags=["scan"])

__all__ = ["router", "reap_interrupted_jobs"]

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


def _job_row(session, job_id: str):
    """The parent row for a job, or None."""
    return session.execute(
        select(Scan).where(Scan.job_id == job_id, Scan.mode == "api")
        .order_by(Scan.id).limit(1)
    ).scalar_one_or_none()


def _set(job_id: str, **fields) -> None:
    """Update the job row from the background task.

    Opens its own session: the request that created the job is long finished and
    its session closed by the time the first step runs. A failure to report
    progress must not abort the run, so this swallows rather than raises.
    """
    session = SessionLocal()
    try:
        row = _job_row(session, job_id)
        if row is not None:
            for key, value in fields.items():
                setattr(row, key, value)
            session.commit()
    except Exception:  # noqa: BLE001 - progress reporting is not the work
        session.rollback()
    finally:
        session.close()


def _run(job_id: str, steps: list[str], operator: str) -> None:
    import os  # noqa: PLC0415

    env = dict(os.environ)
    env["OPERATOR_ID"] = operator
    # Every audit row the steps below write inherits this, so the job and its
    # children can be joined afterwards rather than guessed at by timestamp.
    env["SCAN_JOB_ID"] = job_id

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


def _view(session, row: Scan) -> ScanJob:
    """The job row, plus the audit rows its steps wrote.

    The join is on `job_id` rather than a timestamp window, so a concurrent CLI
    run or scheduler tick cannot be reported as part of this job.
    """
    scan_ids = [
        r[0] for r in session.execute(
            select(Scan.id)
            .where(Scan.job_id == row.job_id, Scan.id != row.id)
            .order_by(Scan.id)
        ).all()
    ]
    return ScanJob(
        job_id=row.job_id,
        status=row.status or "queued",
        stage=row.stage,
        steps=list(row.steps or []),
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        scan_ids=scan_ids,
    )


def reap_interrupted_jobs() -> int:
    """Mark jobs a restart killed as failed. Returns how many.

    A BackgroundTask cannot outlive the process that owns it, so a row still
    reading `running` after a restart is not running — it is lost. Recording
    that beats a status that will never change again.
    """
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Scan).where(
                Scan.mode == "api",
                Scan.status.in_(("queued", "running")),
            )
        ).scalars().all()
        for row in rows:
            finish_scan(
                session, row, status="failed",
                error="interrupted by an API restart; the job did not finish "
                      "and was not resumed",
            )
        session.commit()
        return len(rows)
    except Exception:  # noqa: BLE001 - a cold start must not fail on this
        session.rollback()
        return 0
    finally:
        session.close()


@router.post("/scan", response_model=ScanJob, status_code=202)
def start_scan(
    request: ScanRequest,
    background: BackgroundTasks,
    session=Depends(get_session),
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

    job_id = uuid.uuid4().hex
    row = record_scan(
        session,
        operator=principal.username,
        mode="api",
        query="scan job",
        status="queued",
        job_id=job_id,
        steps=list(request.steps),
    )
    # Committed before the background task starts: _run opens its own session
    # and would not see an uncommitted row.
    session.commit()

    background.add_task(_run, job_id, list(request.steps), principal.username)
    return _view(session, row)


@router.get("/scan/{job_id}", response_model=ScanJob)
def scan_status(
    job_id: str,
    session=Depends(get_session),
    principal: Principal = Depends(current_user),
) -> ScanJob:
    row = _job_row(session, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no scan job {job_id!r}")
    return _view(session, row)
