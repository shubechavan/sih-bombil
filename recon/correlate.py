"""correlate.py — score onion↔clearnet candidate matches.

A hidden service hides the *server*, not the server's habits. If the same
operator runs a clearnet host with the same favicon, the same TLS certificate or
the same ETag, those artefacts are served identically on both sides and can be
matched. That is the whole pivot, and it is the reason recon/fingerprint.py
collects what it collects.

THE RUBRIC
----------
    tls_serial   1.00   a certificate serial is unique per issuer
    tls_san      0.95   the onion's cert names the clearnet host
    favicon      0.80   same icon bytes, same mmh3
    etag         0.70   commonly inode/size/mtime — same file, same filesystem
    banner       0.40   same product/version *and* the same header order

The gap between 0.40 and 0.70 is doing real work. Thousands of hosts run
nginx/1.18.0; a banner match is a coincidence until something else agrees with
it, and this module is built to say so rather than to find links. On the fixture
corpus the banner rule fires against unrelated noise hosts on purpose — that is
the rule behaving correctly, not a false positive to tune away.

Each signal is stored as its own `infra_correlations` row, because `match_type`
is singular in the schema and evidence should stay atomic: an analyst reading a
row must see *which* artefact matched. The per-host roll-up is a noisy-OR over
those rows, reusing score.attribution.hard_identifier_score so infrastructure
and identifiers combine the same way rather than two ways.

PROVIDERS
---------
Clearnet observations arrive behind `ClearnetProvider`. `FixturesProvider` reads
fixtures/clearnet_obs/ and is what the demo runs on. `ShodanProvider` is a real
implementation of the same interface against api.shodan.io — and it has never
been executed against a live key in this repo. That caveat is not left in this
docstring where nobody will read it: selecting the provider prints it, and it is
written into the evidence of every row it produces.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Protocol, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default Windows console codepage is cp1252, which cannot encode the box
# rules or the em dashes below and raises rather than degrading. Ask for UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

from recon.fingerprint import Fingerprint, read_fixture_findings  # noqa: E402
from score.attribution import hard_identifier_score  # noqa: E402

__all__ = [
    "MATCH_WEIGHTS",
    "MIN_SHARED_HEADERS",
    "ClearnetProvider",
    "Correlation",
    "FixturesProvider",
    "Pivots",
    "ShodanProvider",
    "correlate",
    "header_order_agrees",
    "normalise_banner",
    "provider_for",
    "roll_up",
    "write_correlations",
]

#: match_type -> score. The vocabulary is fixed by the schema comment on
#: infra_correlations.match_type; do not invent a sixth without a column note.
MATCH_WEIGHTS: dict[str, float] = {
    "tls_serial": 1.00,
    "tls_san": 0.95,
    "favicon": 0.80,
    "etag": 0.70,
    "banner": 0.40,
}

#: A header-order comparison over one shared header is not evidence of anything —
#: every host in the corpus sends `Server`. Two is the minimum that can disagree.
MIN_SHARED_HEADERS = 2

#: Favicon hashes that mean "no icon", not "the same icon".
EMPTY_FAVICONS = {"", "0", "none", "null"}

FIXTURES = ROOT / "fixtures"


# ─────────────────────────────────────────────────────────────────────────────
# Comparison helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalise_banner(banner: Optional[str]) -> str:
    """Reduce a Server banner to product/version.

    Onions and scanners disagree about the parenthesised OS: the fixture market
    serves `nginx/1.18.0 (Ubuntu)` while Shodan records `nginx/1.18.0`. The
    distribution suffix is not part of the identity of the software, so it is
    dropped before comparison. Everything else is kept — `nginx/1.18.0` must not
    match `nginx/1.24.0`.
    """
    if not banner:
        return ""
    return banner.split("(", 1)[0].strip().lower()


def header_order_agrees(onion, clearnet) -> bool:
    """True if the headers both hosts send appear in the same relative order.

    Servers emit headers in an order set by their build and module list, so the
    sequence is a weak fingerprint of the software stack. Compared over the
    *intersection* of the two key sets, because a scanner records fewer headers
    than a full response carries — the question is whether the shared ones
    disagree, not whether one side is missing some.

    Accepts either an ordered sequence of header names or a mapping. Prefer the
    sequence: a mapping that has been through a JSONB column has had its keys
    re-sorted by Postgres and no longer carries the order the server used, which
    is why `infra_findings.header_order` exists as a separate array column.
    """
    left = [str(k).lower() for k in (onion or ())]
    right = [str(k).lower() for k in (clearnet or ())]
    shared = set(left) & set(right)
    if len(shared) < MIN_SHARED_HEADERS:
        return False
    return [k for k in left if k in shared] == [k for k in right if k in shared]


def _san_matches(sans: Optional[Iterable[str]], host: str) -> Optional[str]:
    """The SAN entry covering `host`, or None. Handles wildcards."""
    host = (host or "").lower().strip(".")
    if not host:
        return None
    for raw in sans or []:
        san = str(raw).lower().strip(".")
        if san == host:
            return raw
        if san.startswith("*.") and host.endswith(san[1:]) \
                and host.count(".") == san.count("."):
            return raw
    return None


def _observation_hosts(observation: dict) -> list[str]:
    hosts = [h for h in (observation.get("hostnames") or []) if h]
    return hosts or ([observation.get("ip_str")] if observation.get("ip_str") else [])


def _dig(mapping: dict, *path, default=None):
    cursor = mapping
    for key in path:
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(key)
        if cursor is None:
            return default
    return cursor


# ─────────────────────────────────────────────────────────────────────────────
# Providers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Pivots:
    """What a set of findings can be searched on.

    An offline corpus ignores this and hands back every record it has. A live
    scanner cannot — you do not download Shodan, you query it — so the interface
    carries the pivots rather than assuming enumeration is free.
    """

    favicon_hashes: set[str] = field(default_factory=set)
    cert_serials: set[str] = field(default_factory=set)
    etags: set[str] = field(default_factory=set)
    banners: set[str] = field(default_factory=set)

    @classmethod
    def from_findings(cls, findings: Sequence[Fingerprint]) -> "Pivots":
        pivots = cls()
        for finding in findings:
            if finding.favicon_hash and finding.favicon_hash.lower() not in EMPTY_FAVICONS:
                pivots.favicon_hashes.add(finding.favicon_hash)
            if finding.tls_serial:
                pivots.cert_serials.add(finding.tls_serial)
            if finding.etag:
                pivots.etags.add(finding.etag)
            banner = normalise_banner(finding.server_banner)
            if banner:
                pivots.banners.add(banner)
        return pivots


class ClearnetProvider(Protocol):
    """Where clearnet observations come from. Shodan-shaped records."""

    name: str

    def observations(self, pivots: Pivots) -> list[dict]: ...

    def caveats(self) -> list[str]:
        """Runtime warnings that belong in the output and in stored evidence."""
        ...


class FixturesProvider:
    """Every record in fixtures/clearnet_obs/. Offline, deterministic, complete."""

    name = "fixtures"

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (FIXTURES / "clearnet_obs")

    def observations(self, pivots: Pivots) -> list[dict]:
        records: list[dict] = []
        for file in sorted(self.path.glob("*.json")):
            payload = json.loads(file.read_text(encoding="utf-8"))
            records.extend(payload if isinstance(payload, list) else [payload])
        return records

    def caveats(self) -> list[str]:
        return []


class ShodanProvider:
    """api.shodan.io, queried on the pivots the findings actually carry.

    !! NEVER EXERCISED AGAINST A LIVE KEY IN THIS REPOSITORY. !!

    The endpoint, the filters and the response shape below are written from
    Shodan's documented API and the record shape the fixture corpus imitates,
    but no test covers this path and no run has confirmed it — there is no key
    to confirm it with. `caveats()` surfaces that at runtime and it is written
    into the evidence of every row this provider produces, so a stored
    correlation carries its own provenance warning rather than looking like the
    fixtures path.

    Uses plain `requests` rather than the `shodan` client so Phase 3 adds no
    dependency for a path that cannot be run here.
    """

    name = "shodan"
    BASE = "https://api.shodan.io"
    UNVERIFIED = (
        "the shodan provider has never been exercised against a live key in "
        "this repository — treat its first run as untested code"
    )

    def __init__(self, api_key: Optional[str] = None, *, timeout: int = 30) -> None:
        self.api_key = api_key or os.environ.get("SHODAN_API_KEY") or ""
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError(
                "SHODAN_API_KEY is not set. Correlation falls back to fixtures: "
                "use --provider fixtures."
            )

    def queries(self, pivots: Pivots) -> list[str]:
        """One Shodan filter per pivot. Cheapest and most selective first."""
        out = [f"ssl.cert.serial:{serial}" for serial in sorted(pivots.cert_serials)]
        out += [f"http.favicon.hash:{h}" for h in sorted(pivots.favicon_hashes)]
        # ETag and banner are not first-class Shodan filters; the banner text is
        # searchable free-form, and ETag is matched locally against what the
        # banner queries return rather than asked for directly.
        out += [f'"{banner}"' for banner in sorted(pivots.banners)]
        return out

    def observations(self, pivots: Pivots) -> list[dict]:
        import requests  # noqa: PLC0415

        seen: set[tuple] = set()
        records: list[dict] = []
        for query in self.queries(pivots):
            response = requests.get(
                f"{self.BASE}/shodan/host/search",
                params={"key": self.api_key, "query": query},
                timeout=self.timeout,
            )
            response.raise_for_status()
            for match in response.json().get("matches", []):
                key = (match.get("ip_str"), match.get("port"))
                if key in seen:
                    continue
                seen.add(key)
                records.append(match)
        return records

    def caveats(self) -> list[str]:
        return [self.UNVERIFIED]


def provider_for(name: str, **kwargs) -> ClearnetProvider:
    if name == "fixtures":
        return FixturesProvider(**kwargs)
    if name == "shodan":
        return ShodanProvider(**kwargs)
    raise ValueError(f"unknown clearnet provider {name!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Correlation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Correlation:
    """One onion↔clearnet candidate, on one signal."""

    onion_url: str
    clearnet_host: str
    clearnet_ip: Optional[str]
    clearnet_port: Optional[int]
    match_type: str
    score: float
    evidence: list = field(default_factory=list)
    provider: Optional[str] = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.onion_url, self.clearnet_host, self.match_type)

    def as_row(self, finding_id: Optional[int] = None) -> dict:
        return {
            "finding_id": finding_id,
            "onion_url": self.onion_url,
            "clearnet_host": self.clearnet_host,
            "clearnet_ip": self.clearnet_ip,
            "clearnet_port": self.clearnet_port,
            "match_type": self.match_type,
            # infra_correlations carries a real CHECK (score >= 0 AND score <= 1).
            "score": min(max(self.score, 0.0), 1.0),
            "evidence": self.evidence,
            "provider": self.provider,
        }


def _match_one(finding: Fingerprint, observation: dict, host: str,
               caveats: Sequence[str]) -> list[Correlation]:
    """Every signal on which this onion and this clearnet host agree."""
    ip = observation.get("ip_str")
    port = observation.get("port")
    out: list[Correlation] = []

    def add(match_type: str, detail: str, **extra) -> None:
        evidence = [{"type": match_type, "detail": detail, **extra}]
        for caveat in caveats:
            evidence.append({"type": "provenance", "detail": caveat})
        out.append(Correlation(
            onion_url=finding.onion_url, clearnet_host=host, clearnet_ip=ip,
            clearnet_port=port, match_type=match_type,
            score=MATCH_WEIGHTS[match_type], evidence=evidence,
        ))

    serial = _dig(observation, "ssl", "cert", "serial")
    if finding.tls_serial and serial and str(serial).upper() == finding.tls_serial.upper():
        add("tls_serial", f"certificate serial {finding.tls_serial} served on both",
            value=finding.tls_serial)

    san = _san_matches(finding.tls_sans, host)
    if san:
        add("tls_san", f"the onion's certificate names {host} in its SANs ({san})",
            value=san)

    favicon = str(_dig(observation, "http", "favicon", "hash") or "")
    if (finding.favicon_hash and favicon
            and favicon.lower() not in EMPTY_FAVICONS
            and finding.favicon_hash.lower() not in EMPTY_FAVICONS
            and favicon == finding.favicon_hash):
        add("favicon", f"same favicon mmh3 hash {favicon}", value=favicon)

    headers = observation.get("headers") or {}
    clearnet_etag = next(
        (v for k, v in headers.items() if k.lower() == "etag"), None
    )
    if finding.etag and clearnet_etag and clearnet_etag == finding.etag:
        add("etag", f"same ETag {finding.etag}", value=finding.etag)

    onion_banner = normalise_banner(finding.server_banner)
    clearnet_banner = normalise_banner(
        _dig(observation, "http", "server")
        or next((v for k, v in headers.items() if k.lower() == "server"), None)
    )
    if (onion_banner and onion_banner == clearnet_banner
            and header_order_agrees(finding.header_order, headers)):
        add("banner",
            f"same server banner {onion_banner} and the shared response headers "
            f"appear in the same order",
            value=onion_banner)

    return out


def correlate(findings: Sequence[Fingerprint], provider: ClearnetProvider,
              observations: Optional[Sequence[dict]] = None) -> list[Correlation]:
    """Score every onion against every clearnet observation the provider offers.

    Deduplicated on (onion_url, clearnet_host, match_type) — a host with two
    open ports must not produce the same favicon match twice — keeping the
    highest-scoring instance, which is also the one with a port attached.
    """
    caveats = list(provider.caveats())
    if observations is None:
        observations = provider.observations(Pivots.from_findings(findings))

    best: dict[tuple[str, str, str], Correlation] = {}
    for finding in findings:
        for observation in observations:
            for host in _observation_hosts(observation):
                for correlation in _match_one(finding, observation, host, caveats):
                    correlation.provider = provider.name
                    existing = best.get(correlation.key)
                    if existing is None or correlation.score > existing.score:
                        best[correlation.key] = correlation

    return sorted(best.values(),
                  key=lambda c: (c.onion_url, -c.score, c.clearnet_host, c.match_type))


def roll_up(correlations: Sequence[Correlation]) -> dict[tuple[str, str], float]:
    """Per (onion, clearnet host) combined confidence, noisy-OR over signals.

    Reuses score.attribution.hard_identifier_score rather than reimplementing
    1 - Π(1 - w): independent evidence should combine the same way whether it is
    a shared PGP key or a shared certificate.
    """
    grouped: dict[tuple[str, str], list[float]] = {}
    for correlation in correlations:
        grouped.setdefault(
            (correlation.onion_url, correlation.clearnet_host), []
        ).append(correlation.score)
    return {key: hard_identifier_score(scores) for key, scores in grouped.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

def write_correlations(session, correlations: Sequence[Correlation]) -> int:
    """Replace the correlations for these onions. Returns rows written.

    `finding_id` is resolved from the most recent infra_findings row per onion,
    so correlate.py can be re-run against fingerprints written by an earlier
    scan without being handed their ids.
    """
    from sqlalchemy import delete, func, select  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    from db import InfraCorrelation, InfraFinding  # noqa: PLC0415

    if not correlations:
        return 0

    urls = sorted({c.onion_url for c in correlations})
    latest = {
        url: finding_id
        for url, finding_id in session.execute(
            select(InfraFinding.onion_url, func.max(InfraFinding.id))
            .where(InfraFinding.onion_url.in_(urls))
            .group_by(InfraFinding.onion_url)
        )
    }

    session.execute(delete(InfraCorrelation.__table__).where(
        InfraCorrelation.__table__.c.onion_url.in_(urls)
    ))
    session.execute(pg_insert(InfraCorrelation.__table__).values(
        [c.as_row(latest.get(c.onion_url)) for c in correlations]
    ))
    session.execute(text(
        "SELECT setval(pg_get_serial_sequence('infra_correlations', 'id'), "
        "COALESCE((SELECT MAX(id) FROM infra_correlations), 1))"
    ))
    return len(correlations)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

RULE = "─" * 78


def payload_hash(correlations: Sequence[Correlation]) -> str:
    canonical = json.dumps(
        sorted([c.onion_url, c.clearnet_host, c.match_type] for c in correlations),
        sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _report(findings: Sequence[Fingerprint], correlations: Sequence[Correlation],
            provider: ClearnetProvider, observation_count: int) -> None:
    print(RULE)
    print("recon/correlate.py — onion ↔ clearnet candidates")
    print(RULE)
    print(f"  provider  {provider.name}  ({observation_count} clearnet observations)")
    print(f"  onions    {len(findings)}")
    print("  rubric    " + "  ".join(
        f"{k} {v:.2f}" for k, v in MATCH_WEIGHTS.items()))

    for caveat in provider.caveats():
        print(f"\n  !! {caveat}")
        print("     Every row written by this provider carries the same note in "
              "its evidence.")

    combined = roll_up(correlations)
    by_onion: dict[str, list[Correlation]] = {}
    for correlation in correlations:
        by_onion.setdefault(correlation.onion_url, []).append(correlation)

    for finding in findings:
        rows = by_onion.get(finding.onion_url, [])
        print(f"\n  {finding.onion_url}")
        if not rows:
            print("    no clearnet candidate matched")
            continue
        hosts = sorted(
            {r.clearnet_host for r in rows},
            key=lambda h: -combined[(finding.onion_url, h)],
        )
        for host in hosts:
            score = combined[(finding.onion_url, host)]
            signals = [r for r in rows if r.clearnet_host == host]
            types = ", ".join(f"{r.match_type} {r.score:.2f}"
                              for r in sorted(signals, key=lambda r: -r.score))
            ip = signals[0].clearnet_ip or "?"
            print(f"    {score:.3f}  {host:<34} {ip:<16} [{types}]")

    print(f"\n{RULE}")
    print(f"  {len(correlations)} candidate row(s) across "
          f"{len(combined)} onion/host pair(s)")
    print("  These are leads, not conclusions. A banner-only match at 0.40 means")
    print("  two hosts run the same software — that is a coincidence until a")
    print("  certificate, favicon or ETag agrees with it.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Correlate onion fingerprints against clearnet observations."
    )
    parser.add_argument("--source", choices=("fixtures", "db"), default="fixtures",
                        help="read infra_findings from fixtures/ (offline, "
                             "default) or from Postgres")
    parser.add_argument("--provider", choices=("fixtures", "shodan"),
                        default="fixtures",
                        help="where clearnet observations come from")
    parser.add_argument("--dry-run", action="store_true",
                        help="correlate and report, write nothing")
    parser.add_argument("--operator", default=None,
                        help="operator id for the audit row (default: $OPERATOR_ID)")
    args = parser.parse_args(argv)

    if args.source == "fixtures":
        findings = read_fixture_findings()
    else:
        from db import InfraFinding, require_schema, session_scope  # noqa: PLC0415
        from sqlalchemy import select  # noqa: PLC0415

        with session_scope() as session:
            require_schema(session)
            findings = [
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

    try:
        provider = provider_for(args.provider)
    except RuntimeError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    observations = provider.observations(Pivots.from_findings(findings))
    correlations = correlate(findings, provider, observations)
    _report(findings, correlations, provider, len(observations))

    if args.dry_run:
        print("\n  dry run: nothing written")
        return 0

    from db import Scan, require_schema, session_scope, utcnow  # noqa: PLC0415

    operator = args.operator or os.environ.get("OPERATOR_ID") or "unknown"
    action_hash = payload_hash(correlations)
    started = utcnow()

    with session_scope() as session:
        require_schema(session)
        scan = Scan(
            operator_id=operator,
            mode="manual",
            data_source=args.source,
            query=f"recon.correlate[{provider.name}]",
            sources_touched=sorted({c.onion_url for c in correlations}),
            action_hash=action_hash,
            started_at=started,
            status="running",
        )
        session.add(scan)
        session.flush()
        try:
            written = write_correlations(session, correlations)
        except Exception as exc:
            scan.status = "failed"
            scan.error = str(exc)[:2000]
            scan.finished_at = utcnow()
            raise
        scan.status = "ok"
        scan.finished_at = utcnow()
        session.flush()
        print(f"\n  wrote {written} infra_correlations row(s)")
        print(f"  audit row: scan id {scan.id}, operator {operator}, "
              f"sha256 {action_hash[:16]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
