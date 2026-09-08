"""tor.py — a thin, import-safe door onto legacy/darksearch.py.

CLAUDE.md says reuse the v1 Tor plumbing rather than rewrite it, and that is the
right call: `legacy/darksearch.py` carries a working SOCKS5 session builder, a
proxy pool, adaptive timeouts and a byte-capped scraper. What it is not is
import-safe. Importing it:

  - runs `from obfuslex_engine import ...` **bare**, so `legacy/` has to be on
    sys.path first or the import dies;
  - calls `sys.exit(1)` — killing the whole process — if that import or PySocks
    is missing;
  - executes `build_db()` at module scope;
  - calls `logging.basicConfig(...)`, hijacking root logging for the caller;
  - installs a global `warnings.filterwarnings("ignore")`;
  - writes `legacy/scrape_events.jsonl`.

None of that is acceptable to pay for in `--source fixtures`, which CLAUDE.md
requires to work with no network at all. So the import is deferred into
`session()` / `probe_ready()` and never happens on the fixtures path.

WHAT THIS IS NOT
----------------
It is not circuit rotation. `stem` is pinned in requirements.txt and imported
nowhere in the repo; there is no ControlPort client and no NEWNYM. What
darksearch actually does is round-robin over a *pool of SOCKS ports*
(`_next_proxy`), which yields distinct circuits only if you happen to be running
more than one tor daemon. Recon inherits that behaviour and no more, and the
distinction matters: two probes a second apart may well share a circuit.

ENV
---
`.env.example` and CLAUDE.md both declare `TOR_SOCKS`, and nothing in the repo
reads it — darksearch reads `DS_TOR_PROXY_POOL` and `DS_TOR_PROXY_PORTS`.
`bridge_env()` closes that gap by copying `TOR_SOCKS` into `DS_TOR_PROXY_POOL`
before darksearch is imported, so the documented variable is the one that works.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "legacy"

__all__ = [
    "LEGACY",
    "TorUnavailable",
    "bridge_env",
    "is_onion_host",
    "normalize_onion_url",
    "probe_ready",
    "session",
    "user_agent",
]


class TorUnavailable(RuntimeError):
    """Raised when the live path is asked for and the Tor plumbing will not load."""


_darksearch = None


def bridge_env() -> Optional[str]:
    """Copy TOR_SOCKS into the variable darksearch actually reads.

    Returns the proxy URI that was bridged, or None if TOR_SOCKS was unset or
    DS_TOR_PROXY_POOL was already populated by the operator (whose explicit
    choice wins).
    """
    uri = (os.environ.get("TOR_SOCKS") or "").strip()
    if not uri or os.environ.get("DS_TOR_PROXY_POOL", "").strip():
        return None
    if not uri.startswith(("socks5://", "socks5h://")):
        return None
    os.environ["DS_TOR_PROXY_POOL"] = uri
    return uri


def _load():
    """Import legacy/darksearch.py once, with its sys.path prerequisite met."""
    global _darksearch
    if _darksearch is not None:
        return _darksearch

    bridge_env()
    if str(LEGACY) not in sys.path:
        sys.path.insert(0, str(LEGACY))
    try:
        import darksearch  # noqa: PLC0415  — deferred on purpose, see module docstring
    except SystemExit as exc:  # darksearch calls sys.exit(1) on a missing dep
        raise TorUnavailable(
            "legacy/darksearch.py aborted at import (it calls sys.exit on a "
            "missing dependency — check PySocks and legacy/obfuslex_engine.py). "
            "The fixtures path does not need it: use --source fixtures."
        ) from exc
    except Exception as exc:
        raise TorUnavailable(
            f"could not import legacy/darksearch.py: {exc}. The fixtures path "
            f"does not need it: use --source fixtures."
        ) from exc

    _darksearch = darksearch
    return darksearch


def session():
    """A requests.Session bound to a Tor SOCKS5 proxy from the pool.

    Straight through to `darksearch.get_tor_session()` — session per call, with
    `trust_env=False` and the proxy chosen by round-robin at construction.
    """
    return _load().get_tor_session()


def probe_ready() -> bool:
    """Confirm Tor is reachable and populate the live proxy pool.

    `darksearch.detect_tor()` must run once before `session()` is useful,
    because it is what replaces the single hardcoded 9050 entry with the pool of
    proxies that actually answered. Note it reaches check.torproject.org through
    the proxy, so it needs network — call it only on the live path.
    """
    return bool(_load().detect_tor())


def normalize_onion_url(raw_url: str) -> str:
    """Canonical http(s) onion URL, or "" if the input is not one.

    Reuses `darksearch._normalize_onion_url` when the legacy module is loadable
    and falls back to an equivalent local parse otherwise, so fixtures mode can
    validate URLs without importing the crawler.
    """
    try:
        return _load()._normalize_onion_url(raw_url)
    except TorUnavailable:
        return _normalize_local(raw_url)


def _normalize_local(raw_url: str) -> str:
    from urllib.parse import urlparse, urlunparse  # noqa: PLC0415

    try:
        parsed = urlparse(raw_url.strip())
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme not in {"http", "https"} or not is_onion_host(host):
            return ""
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        return urlunparse((scheme, netloc, parsed.path or "/", "",
                           parsed.query or "", ""))
    except Exception:
        return ""


def is_onion_host(host: str) -> bool:
    return bool(host and host.lower().endswith(".onion"))


def user_agent() -> str:
    """One of the v1 crawler's user agents, or a plain default offline."""
    try:
        import random  # noqa: PLC0415

        return random.choice(_load().USER_AGENTS)
    except (TorUnavailable, AttributeError, IndexError):
        return "Mozilla/5.0 (Windows NT 10.0; rv:115.0) Gecko/20100101 Firefox/115.0"
