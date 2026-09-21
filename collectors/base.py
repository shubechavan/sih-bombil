"""base.py — the shared crawl loop, and the rules every collector obeys.

THREE RULES, ENFORCED HERE RATHER THAN PROMISED
-----------------------------------------------
**No default target list.** There is no built-in set of markets to crawl. A
collector goes where `--onion` sends it and nowhere else. A tool that ships
with a list of real marketplaces is a tool that crawls them the first time
somebody runs it by accident.

**Off-target hosts are refused.** `allow()` rejects any host but the one named
on the command line, and any redirect that leaves it. `--allow-external` exists
for an operator who genuinely means it, requires an operator id, and the
decision is written to the `scans` audit row.

**Passive, at the same rate as recon.** GETs of pages the server links to,
through `recon.tor`'s session, behind the same `HostRateLimiter` — 1 request
per 2 seconds per host. No POST, no auth, no parameter guessing.

THE OUTPUT IS THE FROZEN CONTRACT
---------------------------------
Collectors emit exactly the JSON `scripts/ingest.py::read_live_documents`
already reads: `source`, `source_url`, `handle`, `posts`, `key_blocks` and the
rest. There is no new ingest path and no new table — a live crawl and the
fixture corpus arrive at the database through the same code, which is the only
way `--source live` and `--source fixtures` can be compared at all.

FIDELITY
--------
The extractor reads `bio + key_blocks + post titles + bodies`. One changed
character shifts the writeprint vocabulary and moves every score, so parsing
reads `data-f` attributes and takes `get_text()` verbatim — no stripping, no
whitespace collapse, no normalisation. `--verify` diffs what came back against
`fixtures/` and names the first field that differs.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402

from recon import tor  # noqa: E402
from recon.fingerprint import MAX_RESPONSE_BYTES, HostRateLimiter  # noqa: E402

__all__ = [
    "CollectResult",
    "Crawler",
    "documents_to_json",
    "field_text",
    "fields_text",
]


@dataclass
class CollectResult:
    """What one crawl produced, plus what it refused and why."""

    documents: list[dict] = field(default_factory=list)
    feedback: list[dict] = field(default_factory=list)
    fetched: int = 0
    refused: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "CollectResult") -> "CollectResult":
        self.documents += other.documents
        self.feedback += other.feedback
        self.fetched += other.fetched
        self.refused += other.refused
        self.errors += other.errors
        return self


def field_text(node, name: str) -> Optional[str]:
    """Verbatim text of one `data-f` field. No strip, no normalisation."""
    if node is None:
        return None
    found = node.select_one(f'[data-f="{name}"]')
    return None if found is None else found.get_text()


def fields_text(node, name: str) -> list[str]:
    return [el.get_text() for el in node.select(f'[data-f="{name}"]')]


class Crawler:
    """Rate-limited, host-locked GETs over Tor."""

    def __init__(self, base_url: str, *, allow_external: bool = False,
                 timeout: int = 60, limiter: Optional[HostRateLimiter] = None,
                 session=None) -> None:
        normalized = tor.normalize_onion_url(base_url) or base_url
        parsed = urlparse(normalized)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"not an http(s) URL: {base_url!r}")
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.host = (parsed.hostname or "").lower()
        self.is_onion = self.host.endswith(".onion")
        self.allow_external = allow_external
        self.timeout = timeout
        self.limiter = limiter or HostRateLimiter()
        self._session = session
        self.refused: list[str] = []

    @property
    def session(self):
        """Tor for onions, a direct session otherwise.

        Routing a `.onion` anywhere but through Tor is impossible; routing a
        plain host through Tor when the operator asked for that host is a
        surprise. The rule is therefore the address, not a flag — and
        `is_onion` is reported in the run summary so nobody has to guess which
        happened.
        """
        if self._session is None:
            if self.is_onion:
                self._session = tor.session()
            else:
                import requests  # noqa: PLC0415

                self._session = requests.Session()
                self._session.trust_env = False
        return self._session

    def source_url(self, index_path: str = "/") -> str:
        """The address of *one source*, which is its index page.

        Not the bare host. `sources.url` is UNIQUE and
        `ingest.upsert_sources` keys on it, so two sources that report the
        same URL collapse into one row and every persona from both lands
        under whichever name arrived first. Three co-hosted sources on one
        lab onion is exactly that case: `/market_alpha`, `/forum_beta` and
        `/market_gamma` are three sites that happen to share a web server.

        A target with a single source is crawled with `--index /` and still
        gets the bare host, which is what the fixture corpus records.
        """
        path = "/" + index_path.strip("/")
        return self.base if path == "/" else self.base + path

    def allow(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if host == self.host:
            return True
        if self.allow_external:
            return True
        self.refused.append(url)
        return False

    def get(self, path: str) -> Optional[str]:
        """One page, or None. A failed fetch is reported, never raised."""
        url = urljoin(self.base + "/", path.lstrip("/"))
        if not self.allow(url):
            return None
        self.limiter.acquire(self.host)
        try:
            with self.session.get(
                url, headers={"User-Agent": tor.user_agent()},
                timeout=self.timeout, stream=True, allow_redirects=True,
            ) as response:
                landed = (urlparse(response.url).hostname or "").lower()
                if landed != self.host and not self.allow_external:
                    # Same guard legacy/darksearch.py applies: a redirect off
                    # the target is somebody else's server.
                    self.refused.append(f"{url} -> {landed}")
                    return None
                if response.status_code != 200:
                    return None
                raw = response.raw.read(MAX_RESPONSE_BYTES, decode_content=True) or b""
                return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - one bad page is not fatal
            self.refused.append(f"{url}: {type(exc).__name__}")
            return None
        finally:
            self.limiter.release()

    def soup(self, path: str) -> Optional[BeautifulSoup]:
        body = self.get(path)
        return None if body is None else BeautifulSoup(body, "html.parser")

    def index_handles(self, path: str = "/") -> list[str]:
        """Handles linked from a source index, in document order."""
        page = self.soup(path)
        if page is None:
            return []
        handles: list[str] = []
        for anchor in page.select("a[href]"):
            href = anchor.get("href") or ""
            parts = [p for p in href.split("/") if p]
            if len(parts) == 2 and parts[0] in ("vendor", "user"):
                handles.append(parts[1])
        return list(dict.fromkeys(handles))


def documents_to_json(documents: Iterable[dict], path: Path) -> int:
    """Write the frozen ingest contract. LF endings, UTF-8, stable order."""
    rows = list(documents)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return len(rows)
