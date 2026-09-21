"""fingerprint.py — passive surface fingerprint of one hidden service.

Reads what the server already hands to any visitor and writes it to
`infra_findings`. Six GETs of conventional, publicly-served paths:

    /   /favicon.ico   /robots.txt   /sitemap.xml   /server-status   /server-info

That list is the entire request surface. There is no POST, no auth header, no
credential, no cookie replay, no parameter fuzzing and no path enumeration
beyond those six well-known names — CLAUDE.md's passive-only rule, and
tests/test_recon.py asserts PROBE_PATHS against it rather than trusting this
paragraph.

WHAT THE SIGNALS ARE FOR
------------------------
Two different jobs, and they are worth keeping apart:

  * **Correlation pivots** — favicon hash, TLS serial and SANs, ETag, banner.
    These identify a *host*, and recon/correlate.py matches them against
    clearnet observations. A favicon hash shared between an onion and a clearnet
    IP is the classic deanonymisation lead.
  * **Misconfiguration evidence** — exposed status pages, directory listings,
    default index pages, clearnet asset references. These say how carelessly the
    service is run, which is `misconfig_score`, and they are a triage aid rather
    than a link.

RATE LIMITING
-------------
CLAUDE.md mandates 1 request / 2 s per host and a global concurrency cap.
Nothing in legacy/darksearch.py implements either — it has an adaptive timeout
and a per-*engine* circuit breaker, but no throttle — so `HostRateLimiter` below
is new code. Six probes at 1 req/2 s is roughly twelve seconds per host.

MISCONFIG SCORE
---------------
A normalised weighted sum over MISCONFIG_SIGNALS, not a noisy-OR. Noisy-OR is
right for the H term, where any single shared PGP key is nearly conclusive on its
own; leakiness is not like that. Ten independent weak signals should not
saturate a "how badly is this run" gauge at 0.98, and a weighted mean says
something an analyst can check: *these seven of ten leak signals fired, weighted
0.71*. `score_misconfig` returns the per-signal breakdown alongside the number
for exactly that reason.

It is computed identically in fixtures and live mode, so `--source fixtures` and
`--source live` mean the same pipeline rather than the same word. Where the
rubric disagrees with the hand-authored values in fixtures/infra_findings.json,
the divergence is reported and recorded in docs/BUILD_PLAN.md — the fixture file
is not edited to match, and the weights here were not tuned backwards to hit it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default Windows console codepage is cp1252, which cannot encode the box
# rules or the em dashes below and raises rather than degrading. Ask for UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

from recon import tor  # noqa: E402

# We pass verify=False deliberately (see _probe), so urllib3's warning is noise
# that would print six times per https onion and bury the actual findings.
try:  # pragma: no cover - urllib3 internals move between versions
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # noqa: BLE001
    pass

__all__ = [
    "DEFAULT_PAGE_MARKERS",
    "GLOBAL_CONCURRENCY",
    "MAX_RESPONSE_BYTES",
    "MISCONFIG_SIGNALS",
    "PER_HOST_INTERVAL",
    "PROBE_PATHS",
    "Fingerprint",
    "HostRateLimiter",
    "clearnet_refs",
    "detect_default_page",
    "detect_dir_listing",
    "favicon_hash",
    "fetch_fingerprint",
    "generator_meta",
    "html_comments",
    "read_fixture_findings",
    "score_misconfig",
    "write_findings",
]

# ─────────────────────────────────────────────────────────────────────────────
# Request surface — the passive-only contract
# ─────────────────────────────────────────────────────────────────────────────

#: Every path recon will ever request. Conventional, publicly-served names only.
#: Adding anything here needs to survive the question "does the server offer this
#: to an anonymous visitor without being asked twice?"
PROBE_PATHS: tuple[str, ...] = (
    "/",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/server-status",
    "/server-info",
)

#: The only HTTP method this module uses.
PROBE_METHOD = "GET"

#: CLAUDE.md: 1 request / 2 seconds per host.
PER_HOST_INTERVAL = 2.0

#: CLAUDE.md: a global concurrency cap across all hosts.
GLOBAL_CONCURRENCY = 4

#: Body cap per probe, matching legacy/darksearch.py's guard.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

DEFAULT_TIMEOUT = 30

#: Served HTML that means "nobody configured this server".
DEFAULT_PAGE_MARKERS: tuple[str, ...] = (
    "welcome to nginx!",
    "apache2 ubuntu default page",
    "apache2 debian default page",
    "it works!",
    "iis windows server",
    "welcome to caddy",
    "test page for the http server",
)

_DIR_LISTING = re.compile(r"<(?:title|h1)[^>]*>\s*index of /", re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_GENERATOR = re.compile(
    r"""<meta\s+[^>]*name\s*=\s*["']generator["'][^>]*content\s*=\s*["']([^"']*)["']""",
    re.IGNORECASE,
)
_GENERATOR_REVERSED = re.compile(
    r"""<meta\s+[^>]*content\s*=\s*["']([^"']*)["'][^>]*name\s*=\s*["']generator["']""",
    re.IGNORECASE,
)
_ABSOLUTE_URL = re.compile(r"""https?://[^\s"'<>)\\]+""", re.IGNORECASE)
_VERSIONED_BANNER = re.compile(r"/\s*\d")


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────────────────────

class HostRateLimiter:
    """1 request / interval per host, plus a global concurrency ceiling.

    Two independent constraints. The per-host clock stops us hammering one
    hidden service; the semaphore stops a wide scan opening forty circuits at
    once. Both matter on Tor, where a burst is both rude and slow.

    Threading model follows legacy/darksearch.py's `_engine_health_lock` idiom:
    a module-level lock guarding a plain dict. `acquire` blocks; there is no
    try-and-fail variant on purpose, because a probe that silently skipped its
    wait would put a hole in the guarantee.
    """

    def __init__(self, interval: float = PER_HOST_INTERVAL,
                 concurrency: int = GLOBAL_CONCURRENCY,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        if interval < 0:
            raise ValueError("interval must not be negative")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self.interval = interval
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._slots = threading.BoundedSemaphore(concurrency)
        self._clock = clock
        self._sleep = sleep

    def acquire(self, host: str) -> float:
        """Block until this host may be requested again. Returns seconds waited."""
        self._slots.acquire()
        with self._lock:
            now = self._clock()
            previous = self._last.get(host)
            wait = 0.0 if previous is None else max(
                0.0, self.interval - (now - previous)
            )
            # Reserve the slot before releasing the lock, so two threads racing
            # on the same host queue behind each other instead of both seeing an
            # expired timestamp and firing together.
            self._last[host] = now + wait
        if wait > 0:
            self._sleep(wait)
        return wait

    def release(self) -> None:
        self._slots.release()

    def __enter__(self) -> "HostRateLimiter":
        return self

    def __exit__(self, *exc) -> None:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# The finding
# ─────────────────────────────────────────────────────────────────────────────

#: Signal -> weight. The denominator is the sum, so the score is the weighted
#: fraction of leak signals that fired. Ordered strongest first.
MISCONFIG_SIGNALS: dict[str, float] = {
    # /server-status leaks vhosts, client IPs and in-flight request URIs. On a
    # hidden service that is the single most damaging thing to leave open.
    "status_exposed": 0.45,
    # An absolute clearnet URL in the page pulls an asset from a host that is
    # not the onion — the deanonymisation risk this whole module exists for.
    "clearnet_refs": 0.40,
    # A listable directory exposes the file tree, including what was meant to
    # stay unlinked.
    "dir_listing": 0.35,
    # robots.txt Disallow lines advertise the paths worth looking at.
    "robots_disallow": 0.25,
    # An unmodified index page means nobody finished the install.
    "default_page": 0.20,
    # ETags are commonly inode/size/mtime, so they leak a little and pivot a lot.
    "etag": 0.20,
    # X-Powered-By announces language and exact version.
    "powered_by": 0.15,
    # <meta name="generator"> announces the CMS and exact version.
    "generator_meta": 0.15,
    # A Server banner carrying a version number, e.g. nginx/1.18.0.
    "server_version": 0.15,
    # Comments left in served HTML — build tags, TODOs, staging hostnames.
    "html_comments": 0.10,
}

#: sitemap.xml is deliberately NOT a signal. Publishing one is an intentional,
#: entirely normal act, not a misconfiguration, and scoring it would make
#: "leaky" mean "has a sitemap".


@dataclass
class Fingerprint:
    """One passive observation of one hidden service.

    Field names mirror the `infra_findings` columns exactly (db.py:465) so the
    write path is a straight mapping. `declared_misconfig_score` is the one
    extra: it carries the value a fixture file asserted, purely so the CLI can
    report where the rubric disagrees with it.
    """

    onion_url: str
    source_id: Optional[int] = None
    server_banner: Optional[str] = None
    powered_by: Optional[str] = None
    etag: Optional[str] = None
    favicon_hash: Optional[str] = None
    status_exposed: bool = False
    default_page: bool = False
    dir_listing: bool = False
    tls_subject: Optional[str] = None
    tls_issuer: Optional[str] = None
    tls_serial: Optional[str] = None
    tls_sans: Optional[list] = None
    tls_not_before: Optional[datetime] = None
    robots_txt: Optional[str] = None
    sitemap_xml: Optional[str] = None
    html_comments: list = field(default_factory=list)
    generator_meta: Optional[str] = None
    clearnet_refs: list = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    #: Header names in the order the server sent them. Kept apart from `headers`
    #: because JSONB re-sorts object keys on the way into Postgres, which would
    #: silently destroy the ordering signal recon/correlate.py matches on.
    #:
    #: Deliberately NOT defaulted from `headers`. Empty means "the order is not
    #: known", and the banner rule declines rather than comparing a sequence
    #: Postgres invented. Every construction site sets it explicitly.
    header_order: list = field(default_factory=list)
    misconfig_score: float = 0.0
    scanned_at: Optional[datetime] = None
    declared_misconfig_score: Optional[float] = None

    @property
    def host(self) -> str:
        return (urlparse(self.onion_url).hostname or "").lower()

    def as_row(self) -> dict:
        """The dict written to infra_findings — schema columns only."""
        row = asdict(self)
        row.pop("declared_misconfig_score", None)
        return row


# ─────────────────────────────────────────────────────────────────────────────
# Parsers — pure functions over what the server served
# ─────────────────────────────────────────────────────────────────────────────

def favicon_hash(data: bytes) -> Optional[str]:
    """Shodan-compatible mmh3 hash of favicon bytes.

    Shodan hashes the *base64 re-encoding* of the icon, with the 76-column line
    wrapping `base64.encodebytes` produces — not the raw bytes, and not
    unwrapped b64. Getting that wrong yields a number that is internally
    consistent and matches nothing anyone else has ever published, which is the
    worst possible failure for a correlation pivot.

    Returned as a string because the column is TEXT and the fixtures hold signed
    ints in string form ("-1274392844").
    """
    if not data:
        return None
    try:
        import mmh3  # noqa: PLC0415  — live-path dependency, see requirements.txt
    except ImportError as exc:
        raise RuntimeError(
            "favicon hashing needs mmh3 (pip install mmh3). Fixtures mode does "
            "not require it: the corpus carries its hashes already."
        ) from exc
    return str(mmh3.hash(base64.encodebytes(data)))


def detect_default_page(html: str) -> bool:
    """True if the served index is an unmodified distribution default."""
    lowered = (html or "").lower()
    return any(marker in lowered for marker in DEFAULT_PAGE_MARKERS)


def detect_dir_listing(html: str) -> bool:
    """True if the response is an auto-generated directory index."""
    return bool(_DIR_LISTING.search(html or ""))


def html_comments(html: str) -> list[str]:
    """Every HTML comment, in document order, deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _COMMENT.findall(html or ""):
        text = " ".join(match.split())
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def generator_meta(html: str) -> Optional[str]:
    """The <meta name="generator"> content, attribute order notwithstanding."""
    for pattern in (_GENERATOR, _GENERATOR_REVERSED):
        match = pattern.search(html or "")
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def clearnet_refs(html: str) -> list[str]:
    """Absolute http(s) URLs in the source whose host is not a .onion.

    Scanned over the raw HTML rather than parsed attributes on purpose: the most
    interesting reference in the fixture corpus sits next to a comment reading
    "TODO: remove cdn reference before launch", and an operator who left that in
    may well have left one in a comment too. Sorted for a stable row.
    """
    found: set[str] = set()
    for url in _ABSOLUTE_URL.findall(html or ""):
        url = url.rstrip(".,;:")
        host = (urlparse(url).hostname or "").lower()
        if host and not tor.is_onion_host(host):
            found.add(url)
    return sorted(found)


def _robots_discloses_paths(robots: Optional[str]) -> bool:
    """True if robots.txt names a path, rather than just existing."""
    for line in (robots or "").splitlines():
        if line.strip().lower().startswith("disallow:"):
            if line.split(":", 1)[1].strip().strip("/"):
                return True
    return False


def score_misconfig(finding: Fingerprint) -> tuple[float, list[dict]]:
    """How leaky this service is, 0..1, with the reasons that got it there.

    Weighted fraction of MISCONFIG_SIGNALS that fired. Returns the breakdown as
    well as the number because `misconfig_score` has no CHECK constraint and no
    evidence column in the schema — if this function does not explain itself,
    nothing downstream can.
    """
    present = {
        "status_exposed": bool(finding.status_exposed),
        "clearnet_refs": bool(finding.clearnet_refs),
        "dir_listing": bool(finding.dir_listing),
        "robots_disallow": _robots_discloses_paths(finding.robots_txt),
        "default_page": bool(finding.default_page),
        "etag": bool(finding.etag),
        "powered_by": bool(finding.powered_by),
        "generator_meta": bool(finding.generator_meta),
        "server_version": bool(
            finding.server_banner and _VERSIONED_BANNER.search(finding.server_banner)
        ),
        "html_comments": bool(finding.html_comments),
    }

    breakdown = [
        {"signal": name, "weight": weight, "present": present[name]}
        for name, weight in MISCONFIG_SIGNALS.items()
    ]
    earned = sum(w for name, w in MISCONFIG_SIGNALS.items() if present[name])
    total = sum(MISCONFIG_SIGNALS.values())
    score = earned / total if total else 0.0
    # The column has no CHECK constraint, unlike links.score, so clamp here.
    return min(max(score, 0.0), 1.0), breakdown


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures mode
# ─────────────────────────────────────────────────────────────────────────────

FIXTURES = ROOT / "fixtures"


def _as_datetime(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def read_fixture_findings(path: Optional[Path] = None) -> list[Fingerprint]:
    """Load fixtures/infra_findings.json into the same shape live mode yields.

    Same discipline as scripts/ingest.py's two Document readers: both modes
    reduce to one in-memory type so everything downstream is mode-agnostic.
    `misconfig_score` is recomputed here rather than trusted, and the declared
    value is kept alongside so the CLI can report the disagreement.
    """
    path = path or (FIXTURES / "infra_findings.json")
    rows = json.loads(path.read_text(encoding="utf-8"))

    findings: list[Fingerprint] = []
    for row in rows:
        finding = Fingerprint(
            onion_url=row["onion_url"],
            source_id=row.get("source_id"),
            server_banner=row.get("server_banner"),
            powered_by=row.get("powered_by"),
            etag=row.get("etag"),
            favicon_hash=row.get("favicon_hash"),
            status_exposed=bool(row.get("status_exposed")),
            default_page=bool(row.get("default_page")),
            dir_listing=bool(row.get("dir_listing")),
            tls_subject=row.get("tls_subject"),
            tls_issuer=row.get("tls_issuer"),
            tls_serial=row.get("tls_serial"),
            tls_sans=row.get("tls_sans"),
            tls_not_before=_as_datetime(row.get("tls_not_before")),
            robots_txt=row.get("robots_txt"),
            sitemap_xml=row.get("sitemap_xml"),
            html_comments=list(row.get("html_comments") or []),
            generator_meta=row.get("generator_meta"),
            clearnet_refs=list(row.get("clearnet_refs") or []),
            headers=dict(row.get("headers") or {}),
            # json.loads preserves object key order, so the fixture file still
            # carries the served sequence even though Postgres would not.
            header_order=list(row.get("header_order")
                              or (row.get("headers") or {})),
            scanned_at=_as_datetime(row.get("scanned_at")),
            declared_misconfig_score=row.get("misconfig_score"),
        )
        finding.misconfig_score, _ = score_misconfig(finding)
        findings.append(finding)
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Live mode
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Probe:
    path: str
    status: Optional[int] = None
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == 200


def _probe(session, base: str, path: str, limiter: HostRateLimiter,
           timeout: int) -> _Probe:
    """One rate-limited GET. Never raises; a failed probe is a probe that failed."""
    url = urljoin(base, path)
    host = (urlparse(url).hostname or "").lower()
    limiter.acquire(host)
    try:
        with session.get(
            url,
            headers={"User-Agent": tor.user_agent()},
            timeout=timeout,
            stream=True,
            allow_redirects=True,
            # Same stance as fetch_tls(), and for the same reason. A v3 onion
            # address *is* a public key: Tor authenticates the endpoint
            # end-to-end before any TLS handshake, so certificate validation
            # adds no assurance here. What it does add is a refusal to read —
            # hidden-service certs are routinely self-signed, and with
            # verify=True every https probe dies at the handshake and the
            # certificate we came to look at is never seen. We are reading
            # what the server publishes, not trusting it.
            verify=False,
        ) as response:
            # Same guard as legacy/darksearch.py:722 — a redirect off the onion
            # would have us fingerprinting somebody else's server.
            final_host = (urlparse(response.url).hostname or "").lower()
            if not tor.is_onion_host(final_host):
                return _Probe(path=path, error=f"redirected off-onion to {final_host}")
            body = response.raw.read(MAX_RESPONSE_BYTES, decode_content=True) or b""
            return _Probe(
                path=path,
                status=response.status_code,
                headers=dict(response.headers),
                body=body,
            )
    except Exception as exc:
        return _Probe(path=path, error=f"{type(exc).__name__}: {exc}")
    finally:
        limiter.release()


def fetch_tls(host: str, port: int, *, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Read the served certificate. Best effort; {} on any failure.

    Deliberately does not validate: `check_hostname` off and `verify_mode`
    CERT_NONE. Hidden-service TLS certificates are routinely self-signed and we
    are *reading* what the server presents, not establishing trust in it. That
    is the opposite of an auth bypass — we never authenticate at all, and the
    certificate is served to every visitor before any handshake decision.

    Most v3 onions speak plain HTTP, so this returns {} more often than not.
    """
    try:
        import socks  # noqa: PLC0415
        import ssl  # noqa: PLC0415
        from cryptography import x509  # noqa: PLC0415
    except ImportError:
        return {}

    proxy = (os.environ.get("TOR_SOCKS") or "socks5h://127.0.0.1:9050").strip()
    parsed = urlparse(proxy)
    proxy_host, proxy_port = parsed.hostname or "127.0.0.1", parsed.port or 9050

    sock = None
    try:
        sock = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, proxy_host, proxy_port, rdns=True)
        sock.settimeout(timeout)
        sock.connect((host, port))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with context.wrap_socket(sock, server_hostname=host) as wrapped:
            der = wrapped.getpeercert(binary_form=True)
        if not der:
            return {}
        cert = x509.load_der_x509_certificate(der)
        try:
            # get_values_for_type returns the values themselves — plain `str`
            # for DNSName, not the x509 objects. Reading `.value` off them
            # raises AttributeError, and because this function is best-effort
            # the exception below used to swallow it and return {}, so a live
            # onion's SANs came back empty while the fixtures path (which reads
            # them from JSON) looked fine.
            sans = list(
                cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value.get_values_for_type(x509.DNSName)
            )
        except x509.ExtensionNotFound:
            sans = []
        # Uppercase hex, no 0x, padded to whole bytes — the form Shodan and
        # Censys publish. Without the padding a serial with a leading zero
        # nibble (0F3A9C…) reads back as F3A9C… and never matches the
        # observation, losing the 1.00-weight correlation signal entirely.
        serial = format(cert.serial_number, "X")
        if len(serial) % 2:
            serial = "0" + serial
        return {
            "tls_subject": cert.subject.rfc4514_string(),
            "tls_issuer": cert.issuer.rfc4514_string(),
            "tls_serial": serial,
            "tls_sans": sans,
            "tls_not_before": getattr(cert, "not_valid_before_utc", None)
            or cert.not_valid_before,
        }
    except Exception as exc:  # noqa: BLE001 - best effort, but not silent
        # Most onions speak plain HTTP and failing here is ordinary. Saying so
        # is not: a bug in the parsing above is indistinguishable from "no TLS"
        # unless the reason is printed.
        print(f"    tls: no certificate read from {host}:{port} "
              f"({type(exc).__name__}: {exc})")
        return {}
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def fetch_fingerprint(url: str, *, session=None, limiter: Optional[HostRateLimiter] = None,
                      timeout: int = DEFAULT_TIMEOUT,
                      source_id: Optional[int] = None) -> Fingerprint:
    """Probe one onion and build its Fingerprint. Live path.

    Six requests, rate limited, no failure fatal: a hidden service that 404s
    /server-status has told us something, and one that times out on /sitemap.xml
    still yields a usable fingerprint from the rest.
    """
    normalized = tor.normalize_onion_url(url)
    if not normalized:
        raise ValueError(f"not an http(s) .onion URL: {url!r}")

    session = session or tor.session()
    limiter = limiter or HostRateLimiter()
    parsed = urlparse(normalized)
    base = f"{parsed.scheme}://{parsed.netloc}/"

    probes = {path: _probe(session, base, path, limiter, timeout)
              for path in PROBE_PATHS}

    root = probes["/"]
    headers = root.headers
    html = root.body.decode("utf-8", errors="replace") if root.body else ""

    finding = Fingerprint(
        onion_url=normalized,
        source_id=source_id,
        server_banner=headers.get("Server"),
        powered_by=headers.get("X-Powered-By"),
        etag=headers.get("ETag"),
        favicon_hash=(favicon_hash(probes["/favicon.ico"].body)
                      if probes["/favicon.ico"].ok else None),
        status_exposed=probes["/server-status"].ok or probes["/server-info"].ok,
        default_page=detect_default_page(html),
        dir_listing=detect_dir_listing(html),
        robots_txt=(probes["/robots.txt"].body.decode("utf-8", errors="replace")
                    if probes["/robots.txt"].ok else None),
        sitemap_xml=(probes["/sitemap.xml"].body.decode("utf-8", errors="replace")
                     if probes["/sitemap.xml"].ok else None),
        html_comments=html_comments(html),
        generator_meta=generator_meta(html),
        clearnet_refs=clearnet_refs(html),
        headers=headers,
        header_order=list(headers),
        scanned_at=datetime.utcnow(),
    )

    if parsed.scheme == "https":
        for key, value in fetch_tls(parsed.hostname or "", parsed.port or 443,
                                    timeout=timeout).items():
            setattr(finding, key, value)

    finding.misconfig_score, _ = score_misconfig(finding)
    return finding


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

def write_findings(session, findings: Sequence[Fingerprint]) -> int:
    """Replace the findings for these onions. Returns rows written.

    `infra_findings` has no UNIQUE on onion_url — a real scan appends a new
    observation every time — so `on_conflict_do_update` is unavailable and
    idempotency means clearing this onion's rows first. Same shape as
    scripts/load_fixtures.py:326.
    """
    from sqlalchemy import delete  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    from db import InfraFinding  # noqa: PLC0415

    if not findings:
        return 0

    urls = [f.onion_url for f in findings]
    session.execute(delete(InfraFinding.__table__).where(
        InfraFinding.__table__.c.onion_url.in_(urls)
    ))
    session.execute(pg_insert(InfraFinding.__table__).values(
        [f.as_row() for f in findings]
    ))
    session.execute(text(
        "SELECT setval(pg_get_serial_sequence('infra_findings', 'id'), "
        "COALESCE((SELECT MAX(id) FROM infra_findings), 1))"
    ))
    return len(findings)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def payload_hash(findings: Sequence[Fingerprint]) -> str:
    """SHA-256 over exactly the onions that were probed."""
    canonical = json.dumps(sorted(f.onion_url for f in findings),
                           sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _report(findings: Sequence[Fingerprint]) -> None:
    rule = "─" * 78
    print(rule)
    print("recon/fingerprint.py — passive hidden service fingerprints")
    print(rule)
    print(f"  probed {len(findings)} onion(s) over "
          f"{len(PROBE_PATHS)} passive GET paths each")
    print(f"  rate limit 1 req / {PER_HOST_INTERVAL:.0f}s per host, "
          f"global concurrency {GLOBAL_CONCURRENCY}")

    divergences: list[tuple[str, float, float]] = []
    for finding in findings:
        score, breakdown = score_misconfig(finding)
        fired = [b["signal"] for b in breakdown if b["present"]]
        print(f"\n  {finding.onion_url}")
        print(f"    banner    {finding.server_banner or '—'}"
              f"   powered-by {finding.powered_by or '—'}")
        print(f"    etag      {finding.etag or '—'}"
              f"   favicon {finding.favicon_hash or '—'}")
        if finding.tls_serial:
            print(f"    tls       serial {finding.tls_serial}  "
                  f"sans {', '.join(finding.tls_sans or []) or '—'}")
        print(f"    misconfig {score:.3f}  ({len(fired)}/{len(breakdown)} signals: "
              f"{', '.join(fired) or 'none'})")
        if finding.clearnet_refs:
            for ref in finding.clearnet_refs:
                print(f"    clearnet  {ref}")
        declared = finding.declared_misconfig_score
        if declared is not None and abs(declared - score) >= 0.005:
            divergences.append((finding.onion_url, declared, score))

    if divergences:
        print(f"\n{rule}")
        print("  misconfig_score: rubric vs the value declared in fixtures/")
        for url, declared, computed in divergences:
            print(f"    {url[:34]}…  declared {declared:.2f}  "
                  f"computed {computed:.3f}  Δ {computed - declared:+.3f}")
        print("  Recorded, not patched: the fixture file keeps its hand-authored")
        print("  numbers and the rubric was not tuned backwards to match them.")
        print("  See docs/BUILD_PLAN.md, 'Known corpus gaps'.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Passively fingerprint hidden services into infra_findings."
    )
    parser.add_argument("--source", choices=("fixtures", "live"), default="fixtures",
                        help="the fixture corpus (offline, default), or probe "
                             "onions over Tor")
    parser.add_argument("--onion", action="append", default=[],
                        help="onion URL to probe; repeatable. --source live only")
    parser.add_argument("--dry-run", action="store_true",
                        help="probe and report, write nothing")
    parser.add_argument("--operator", default=None,
                        help="operator id for the audit row (default: $OPERATOR_ID)")
    args = parser.parse_args(argv)

    if args.source == "live":
        if not args.onion:
            parser.error("--source live needs at least one --onion URL")
        if not tor.probe_ready():
            print("Tor is not reachable — no proxy in the pool answered "
                  "check.torproject.org. Nothing was probed.", file=sys.stderr)
            return 1
        limiter = HostRateLimiter()
        session_obj = tor.session()
        findings = [fetch_fingerprint(url, session=session_obj, limiter=limiter)
                    for url in args.onion]
    else:
        if args.onion:
            parser.error("--onion only applies to --source live")
        findings = read_fixture_findings()

    _report(findings)

    if args.dry_run:
        print("\n  dry run: nothing written")
        return 0

    from db import (  # noqa: PLC0415
        finish_scan, record_scan, require_schema, session_scope, utcnow,
    )

    operator = args.operator or os.environ.get("OPERATOR_ID") or "unknown"
    action_hash = payload_hash(findings)
    started = utcnow()

    with session_scope() as session:
        require_schema(session)
        scan = record_scan(
            session,
            operator=operator,
            mode="manual",
            data_source=args.source,
            query="recon.fingerprint",
            sources_touched=sorted(f.onion_url for f in findings),
            action_hash=action_hash,
            started_at=started,
        )
        try:
            written = write_findings(session, findings)
        except Exception as exc:
            finish_scan(session, scan, status="failed", error=exc)
            raise
        finish_scan(session, scan)
        print(f"\n  wrote {written} infra_findings row(s)")
        print(f"  audit row: scan id {scan.id}, operator {operator}, "
              f"sha256 {action_hash[:16]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
