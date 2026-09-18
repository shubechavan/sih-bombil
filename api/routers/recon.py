"""recon.py — the fingerprint and correlation candidates for one onion.

Serves `infra_findings` and `infra_correlations` as recon wrote them, plus one
thing neither table contains: a sentence saying why these findings do not feed
the I term for any persona pair. Without it a reader sees a page full of real
infrastructure evidence next to a blank I column and concludes recon failed. It
did not — the findings are site-level and the I term is persona-level, and that
distinction is the Phase 3 result.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.deps import SITE_LEVEL_NOTE, evidence_from_rows, get_session  # noqa: E402
from api.queries import source_names  # noqa: E402
from api.schemas import Correlation, ReconReport  # noqa: E402
from db import InfraCorrelation, InfraFinding  # noqa: E402

router = APIRouter(tags=["recon"])

__all__ = ["router"]


def _normalise(onion: str) -> str:
    value = unquote(onion).strip()
    for prefix in ("http://", "https://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    return value.rstrip("/").lower()


@router.get("/recon", response_model=list[ReconReport])
def list_recon(session=Depends(get_session)) -> list[ReconReport]:
    findings = list(session.execute(
        select(InfraFinding).order_by(InfraFinding.id.desc())
    ).scalars())
    seen: set[str] = set()
    out: list[ReconReport] = []
    for row in findings:
        if row.onion_url in seen:
            continue
        seen.add(row.onion_url)
        out.append(_report(session, row))
    return out


@router.get("/recon/{onion}", response_model=ReconReport)
def get_recon(onion: str, session=Depends(get_session)) -> ReconReport:
    wanted = _normalise(onion)
    rows = list(session.execute(
        select(InfraFinding).order_by(InfraFinding.id.desc())
    ).scalars())
    for row in rows:
        if _normalise(row.onion_url) == wanted:
            return _report(session, row)
    raise HTTPException(status_code=404, detail=f"no recon finding for {onion!r}")


def _report(session, row: InfraFinding) -> ReconReport:
    names = source_names(session)
    correlations = list(session.execute(
        select(InfraCorrelation)
        .where(InfraCorrelation.onion_url == row.onion_url)
        .order_by(InfraCorrelation.score.desc())
    ).scalars())

    return ReconReport(
        onion_url=row.onion_url,
        source_id=row.source_id,
        source_name=names.get(row.source_id) if row.source_id else None,
        server_banner=row.server_banner,
        powered_by=row.powered_by,
        etag=row.etag,
        favicon_hash=row.favicon_hash,
        status_exposed=bool(row.status_exposed),
        default_page=bool(row.default_page),
        dir_listing=bool(row.dir_listing),
        tls_subject=row.tls_subject,
        tls_issuer=row.tls_issuer,
        tls_serial=row.tls_serial,
        tls_sans=list(row.tls_sans or []),
        robots_txt=row.robots_txt,
        sitemap_xml=row.sitemap_xml,
        html_comments=list(row.html_comments or []),
        generator_meta=row.generator_meta,
        clearnet_refs=list(row.clearnet_refs or []),
        headers=dict(row.headers or {}),
        header_order=list(row.header_order or []),
        misconfig_score=row.misconfig_score,
        scanned_at=row.scanned_at,
        correlations=[
            Correlation(
                clearnet_host=c.clearnet_host,
                clearnet_ip=c.clearnet_ip,
                clearnet_port=c.clearnet_port,
                match_type=c.match_type,
                score=c.score,
                provider=c.provider,
                evidence=evidence_from_rows(c.evidence),
                observed_at=c.observed_at,
            )
            for c in correlations
        ],
        attribution_note=SITE_LEVEL_NOTE,
    )
