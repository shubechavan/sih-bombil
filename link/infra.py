"""infra.py — the I term, and an honest account of why it is empty here.

    A = 0.40·H + 0.25·S + 0.20·B + 0.15·I

WHAT I MEANS
------------
Every other term in that sum measures something the two personas *themselves
produced*: H is the identifiers they published, S is the words they wrote, B is
when and about what they posted. For I to belong in the same sum it has to
measure the same kind of thing — **infrastructure the personas control**, not
infrastructure they happen to be standing on.

A vendor on market_alpha does not run market_alpha's nginx. The favicon, ETag,
banner and certificate in `infra_findings` belong to the *marketplace operator*.
market_alpha and market_gamma sharing a favicon is real evidence, and strong —
evidence that the two markets have one operator. It is not evidence about which
vendor is which, and the two claims must not be conflated because they happen to
be derived from the same bytes.

So this module measures I between two personas only when both control a host we
have fingerprinted: a vendor-run mirror, a shop of their own, a declared
`onion_mirror` with a finding attached.

WHY IT IS UNMEASURED ON THIS CORPUS
-----------------------------------
No persona in fixtures/ controls a fingerprinted host.

  * All 20 profile URLs resolve to the 3 source onions; the only per-persona
    variation is a path segment.
  * fixtures/infra_findings.json holds 3 rows, keyed by source_id.
  * Neither infra_findings nor infra_correlations has a persona_id column.
  * The one near-miss — `onion_mirror` on personas 3 and 18 — is already scored
    in **H** at 0.80 (link/resolve.py IDENTIFIER_LABELS), is the same literal
    string on both sides rather than two hosts that fingerprint alike, and is one
    of the six identifiers docs/BUILD_PLAN.md records as appearing in no bio and
    no post. There is nothing there to fingerprint and nothing new to learn.

The refusal is therefore *by rule*: this module is not waiting for recon, which
ran and produced three findings. Point it at a corpus where vendors run their own
mirrors and it measures. See `_vendor_corpus` in tests/test_infra.py for exactly
that, passing.

THE MODE THAT IS NOT SOUND
--------------------------
`site-broadcast` hands every persona its market's fingerprint. It is implemented
here, and it is wrong, and it is implemented *because* it is wrong: an argument
that a signal would hurt is worth much less than a measurement of how much.
scripts/evaluate.py --infra site-broadcast prints that measurement. Three facts
kill it, all of them asserted in tests/test_infra.py:

  1. All 40 alpha×gamma pairs receive an identical value. Only 4 are true
     positives. A feature that is constant across a set carries zero information
     about labels inside that set — it cannot re-rank, only shift, and the shift
     lands on 36 non-positives including the designed hard negative (2,20).
  2. 59 same-source pairs score I = 1.0 for sharing a website. Not one of them
     is a true positive.
  3. It is backwards at both ends: 1~9 and 2~10 are alpha×beta, share no
     infrastructure, and so lose the renormalisation that was carrying them,
     while (2,20) — the pair the corpus was built to make the system refuse —
     gains.

Never make it the default. It exists to be measured, not run.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recon.correlate import (  # noqa: E402
    MATCH_WEIGHTS,
    header_order_agrees,
    normalise_banner,
)
from recon.fingerprint import Fingerprint, read_fixture_findings  # noqa: E402
from score.attribution import hard_identifier_score  # noqa: E402

__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "InfraSet",
    "build",
    "fingerprint_overlap",
    "load_findings",
]

DEFAULT_MODE = "persona-controlled"
MODES: tuple[str, ...] = ("persona-controlled", "site-broadcast")

#: Identifier types that name a host a persona is claiming as their own.
CONTROLLED_HOST_TYPES: tuple[str, ...] = ("onion_mirror",)

SITE_LEVEL_REFUSAL = (
    "recon findings for this corpus are site-level — they fingerprint the "
    "marketplace, not the vendor. {handle} controls no host that was "
    "fingerprinted, and a market's favicon is evidence about its operator, "
    "not about the traders standing in it"
)

BROADCAST_WARNING = (
    "site-broadcast mode: this value is the market's fingerprint, not the "
    "persona's, and is identical for every pair of vendors on these two sites"
)


# ─────────────────────────────────────────────────────────────────────────────
# Comparing two fingerprints
# ─────────────────────────────────────────────────────────────────────────────

def fingerprint_overlap(a: Fingerprint, b: Fingerprint) -> tuple[float, list[dict]]:
    """How much two hosts look like the same deployment, 0..1, with reasons.

    Same rubric and same weights as recon/correlate.py's onion↔clearnet match —
    a shared certificate serial is worth 1.00 whether the other side is a Shodan
    record or a second hidden service — and combined by the same noisy-OR that
    score/attribution.py uses for identifiers, so independent evidence stacks one
    way in this codebase rather than three.

    An empty reason list returns 0.0. That is a measurement: we compared two
    fingerprints and they had nothing in common.
    """
    weights: list[float] = []
    evidence: list[dict] = []

    def add(match_type: str, detail: str, value=None) -> None:
        weights.append(MATCH_WEIGHTS[match_type])
        evidence.append({
            "type": "infrastructure",
            "signal": match_type,
            "detail": detail,
            "value": value,
            "weight": MATCH_WEIGHTS[match_type],
        })

    if a.tls_serial and b.tls_serial and a.tls_serial.upper() == b.tls_serial.upper():
        add("tls_serial", f"both hosts serve certificate serial {a.tls_serial}",
            a.tls_serial)

    shared_sans = sorted({str(s).lower() for s in (a.tls_sans or [])} &
                         {str(s).lower() for s in (b.tls_sans or [])})
    if shared_sans:
        add("tls_san",
            f"both certificates name {', '.join(shared_sans)} in their SANs",
            shared_sans)

    if (a.favicon_hash and b.favicon_hash and a.favicon_hash == b.favicon_hash
            and a.favicon_hash.lower() not in {"", "0"}):
        add("favicon", f"same favicon mmh3 hash {a.favicon_hash}", a.favicon_hash)

    if a.etag and b.etag and a.etag == b.etag:
        add("etag", f"same ETag {a.etag}", a.etag)

    banner = normalise_banner(a.server_banner)
    if (banner and banner == normalise_banner(b.server_banner)
            and header_order_agrees(a.header_order, b.header_order)):
        add("banner",
            f"same server banner {banner} with the shared response headers in "
            f"the same order", banner)

    return hard_identifier_score(weights), evidence


# ─────────────────────────────────────────────────────────────────────────────
# The set
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InfraSet:
    """Per-persona infrastructure, and the pairwise comparison over it.

    Contract deliberately identical to link.stylometry.WriteprintSet and
    link.behaviour.BehaviourSet — `similarity` returns None for unmeasured with
    a paired `refusal_reason` — so link/resolve.py treats all three components
    the same way and none of them can quietly become a zero.
    """

    mode: str
    hosts: dict[int, list[Fingerprint]] = field(default_factory=dict)
    refusals: dict[int, str] = field(default_factory=dict)

    def __contains__(self, persona_id: int) -> bool:
        return persona_id in self.hosts

    def refusal_reason(self, persona_id: int) -> Optional[str]:
        return self.refusals.get(persona_id)

    def _pair(self, a: int, b: int) -> Optional[tuple[float, list[dict]]]:
        left, right = self.hosts.get(a), self.hosts.get(b)
        if not left or not right:
            return None
        best_score, best_evidence = 0.0, []
        for one in left:
            for other in right:
                score, evidence = fingerprint_overlap(one, other)
                if score >= best_score:
                    best_score, best_evidence = score, evidence
        if self.mode == "site-broadcast" and best_evidence:
            best_evidence = best_evidence + [
                {"type": "infrastructure_caveat", "detail": BROADCAST_WARNING}
            ]
        return best_score, best_evidence

    def similarity(self, a: int, b: int) -> Optional[float]:
        """0..1, or None where neither persona controls a fingerprinted host."""
        result = self._pair(a, b)
        return None if result is None else result[0]

    def explain(self, a: int, b: int) -> list[dict]:
        """Evidence entries for a measured pair; empty when unmeasured."""
        result = self._pair(a, b)
        return [] if result is None else result[1]


# ─────────────────────────────────────────────────────────────────────────────
# Building
# ─────────────────────────────────────────────────────────────────────────────

def _host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host:
        return host
    # a bare host, as normalize_identifier("onion_mirror", ...) produces
    return url.strip().lower().split("/")[0]


def load_findings(session=None) -> list[Fingerprint]:
    """Recon findings from the database, or from fixtures/ when offline.

    Mirrors link.stylometry.load_or_build's session handling: pass a session and
    the stored rows are used, omit it and the fixture corpus is read directly, so
    the offline evaluation path never needs Postgres.
    """
    if session is None:
        return read_fixture_findings()

    from sqlalchemy import select  # noqa: PLC0415

    from db import InfraFinding  # noqa: PLC0415

    return [
        Fingerprint(
            onion_url=row.onion_url, source_id=row.source_id,
            server_banner=row.server_banner, powered_by=row.powered_by,
            etag=row.etag, favicon_hash=row.favicon_hash,
            status_exposed=bool(row.status_exposed),
            default_page=bool(row.default_page),
            dir_listing=bool(row.dir_listing),
            tls_subject=row.tls_subject, tls_issuer=row.tls_issuer,
            tls_serial=row.tls_serial, tls_sans=row.tls_sans,
            tls_not_before=row.tls_not_before, robots_txt=row.robots_txt,
            sitemap_xml=row.sitemap_xml,
            html_comments=list(row.html_comments or []),
            generator_meta=row.generator_meta,
            clearnet_refs=list(row.clearnet_refs or []),
            headers=dict(row.headers or {}),
            header_order=list(row.header_order or []),
            misconfig_score=row.misconfig_score or 0.0,
            scanned_at=row.scanned_at,
        )
        for row in session.execute(select(InfraFinding)).scalars()
    ]


def build(personas: Mapping[int, dict], findings: Sequence[Fingerprint], *,
          mode: str = DEFAULT_MODE,
          identifiers: Optional[Mapping[int, set]] = None) -> InfraSet:
    """Attach fingerprinted hosts to the personas that control them.

    Args:
        personas: persona_id -> dict carrying at least `source_id` and `handle`.
            link.resolve.Corpus.personas is exactly this shape.
        findings: recon output, from `load_findings`.
        mode: "persona-controlled" (the only sound one) or "site-broadcast".
        identifiers: persona_id -> {(type, value_norm)}, i.e.
            link.resolve.Corpus.identifiers. Used to find the hosts a persona
            claims. Without it, no persona can be shown to control anything.

    Raises:
        ValueError: on an unknown mode.
    """
    if mode not in MODES:
        raise ValueError(
            f"unknown infra mode {mode!r}; expected one of {', '.join(MODES)}"
        )

    by_host = {f.host: f for f in findings if f.host}
    by_source: dict[int, Fingerprint] = {
        f.source_id: f for f in findings if f.source_id is not None
    }

    hosts: dict[int, list[Fingerprint]] = {}
    refusals: dict[int, str] = {}

    for persona_id, persona in personas.items():
        owned: list[Fingerprint] = []

        if mode == "site-broadcast":
            # Deliberately unsound: the market's fingerprint, worn by every
            # vendor standing in it. See the module docstring.
            finding = by_source.get(persona.get("source_id"))
            if finding is not None:
                owned = [finding]
        else:
            claimed = {
                value for kind, value in (identifiers or {}).get(persona_id, set())
                if kind in CONTROLLED_HOST_TYPES
            }
            owned = [by_host[h] for h in
                     sorted({_host_of(v) for v in claimed} & set(by_host))]

        if owned:
            hosts[persona_id] = owned
        else:
            refusals[persona_id] = SITE_LEVEL_REFUSAL.format(
                handle=persona.get("handle", f"persona {persona_id}")
            )

    return InfraSet(mode=mode, hosts=hosts, refusals=refusals)
