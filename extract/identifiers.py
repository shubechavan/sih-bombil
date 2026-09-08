"""
identifiers.py — pull hard identifiers out of raw scraped text.

    from extract.identifiers import extract_identifiers
    rows = extract_identifiers(bio_and_posts, exclude_onions=("thismarket.onion",))
    # [{"type": "btc", "value": "bc1q…", "raw_context": "…payments go to bc1q…",
    #   "confidence": 0.99, "meta": {...}}, …]

Types match db.IDENTIFIER_TYPES: pgp_fpr, btc, eth, xmr, ltc, email, jabber,
session, telegram, onion_mirror.

The rule that shapes this whole module (CLAUDE.md): **a regex-only match on a hex
string poisons the entire link graph, so a failed checksum is dropped, not
stored.** Every wallet here is verified through scripts/wallet_codec.py — the
checksum arithmetic is not reimplemented, and there is no "store it anyway at low
confidence" path. `extract_identifiers_report` returns what was thrown away and
why, so a scan can account for its refusals instead of silently swallowing them.

Two precision rules earn their keep on real text:

  * Armoured PGP blocks are located and parsed first, then blanked out before the
    regexes run. A block is structured data, not prose; sweeping base64 for
    wallet-shaped substrings is how a corpus grows phantom identifiers.
  * `exclude_onions` drops the site the text was scraped from. A vendor writing
    their own market's address is not disclosing a mirror, and without this every
    persona on a market "shares" that market's onion with every other.

`confidence` answers "how sure are we that this string is an identifier of this
type", not "how strong is the resulting link" — that is db.IDENTIFIER_WEIGHTS,
and Phase 2 owns it.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from wallet_codec import (  # noqa: E402
    _bech32_hrp_expand,
    _bech32_polymod,
    _BECH32_CHARSET,
    b58decode,
    verify_base58check,
    verify_eip55,
    verify_monero,
    verify_onion_v3,
)

from extract.pgp import find_key_blocks, normalize_fingerprint, parse_key_block_report

__all__ = [
    "CONFIDENCE",
    "Dropped",
    "Extraction",
    "ExtractionReport",
    "extract_identifiers",
    "extract_identifiers_report",
]

#: How sure we are of the *type*, not of the link. Checksummed wallets are as
#: close to certain as text gets; a bare @handle is the weakest thing on the list
#: and is scored accordingly.
CONFIDENCE: dict[str, float] = {
    "pgp_fpr":      0.95,
    "btc":          0.99,
    "eth":          0.99,
    "xmr":          0.99,
    "ltc":          0.99,
    "session":      0.95,
    "onion_mirror": 0.90,
    "email":        0.90,
    "jabber":       0.85,
    "telegram":     0.70,
}

#: Characters of text kept either side of a match in `raw_context`.
CONTEXT_WINDOW = 60


@dataclass(frozen=True)
class Extraction:
    type: str
    value: str
    raw_context: str
    confidence: float
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "value": self.value,
            "raw_context": self.raw_context,
            "confidence": self.confidence,
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class Dropped:
    """A candidate that looked right and failed. Kept so a scan can say so."""

    type: str
    value: str
    raw_context: str
    reason: str


@dataclass(frozen=True)
class ExtractionReport:
    kept: list[Extraction]
    dropped: list[Dropped]


# ─────────────────────────────────────────────────────────────────────────────
# Patterns
# ─────────────────────────────────────────────────────────────────────────────

#: The bech32 data alphabet: no 1, b, i or o. Spelling it out rather than using
#: [0-9a-z] stops a match swallowing the first letters of the following word.
_B32 = "023456789acdefghjklmnpqrstuvwxyz"

PATTERNS = {
    # Ten space-separated groups of four — how gpg prints a fingerprint.
    "pgp_spaced": re.compile(r"\b(?:[0-9A-Fa-f]{4}[ \t]+){9}[0-9A-Fa-f]{4}\b"),
    # \b at both ends is what stops a 66-char Session id or a 0x-prefixed
    # Ethereum address donating 40 hex characters to a phantom fingerprint.
    "pgp_bare": re.compile(r"\b[0-9A-Fa-f]{40}\b"),
    "session": re.compile(r"\b05[0-9a-fA-F]{64}\b"),
    "eth": re.compile(r"\b0x[0-9a-fA-F]{40}\b"),
    "bech32": re.compile(rf"\b(bc|ltc)1[{_B32}]{{6,71}}\b", re.IGNORECASE),
    "base58": re.compile(r"\b[13LM][1-9A-HJ-NP-Za-km-z]{25,34}\b"),
    "xmr": re.compile(r"\b[48][1-9A-HJ-NP-Za-km-z]{94}\b"),
    "onion": re.compile(r"\b[a-z2-7]{56}\.onion\b", re.IGNORECASE),
    "address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                          r"\.[A-Za-z]{2,}\b"),
    # The lookbehind is the whole trick: it keeps every email in the corpus from
    # also producing a telegram handle out of its domain.
    "telegram": re.compile(r"(?<![A-Za-z0-9_.%+\-/])@([A-Za-z][A-Za-z0-9_]{4,31})\b"),
    "telegram_url": re.compile(r"\b(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})\b",
                               re.IGNORECASE),
}

#: An address on one of these is an XMPP account, not a mailbox. Substring match
#: on `jabber`/`xmpp` covers most of the real world; the rest are named.
XMPP_HOST_MARKERS = ("jabber", "xmpp", "jabb")
XMPP_HOSTS = frozenset({
    "conversations.im", "404.city", "chatterboxtown.us", "creep.im",
    "jabbim.com", "yax.im", "trashserver.net", "movim.eu",
})

#: base58check version byte → identifier type. 0x05 is shared between Bitcoin
#: P2SH and Litecoin's legacy P2SH; Bitcoin wins, which is the right bet on a
#: '3…' address and is recorded in meta so an analyst can see the ambiguity.
BASE58_VERSIONS = {
    0x00: ("btc", "P2PKH"),
    0x05: ("btc", "P2SH"),
    0x30: ("ltc", "P2PKH"),
    0x32: ("ltc", "P2SH"),
}

#: BIP-350. Witness version 0 keeps the original bech32 constant; v1+ (taproot)
#: uses this one. wallet_codec.verify_bech32 only knows the v0 case.
BECH32M_CONST = 0x2BC830A3

#: Monero network bytes: standard address, then subaddress.
XMR_NETWORK_BYTES = (0x12, 0x2A)


def _verify_segwit(address: str, hrp: str) -> bool:
    """Checksum a segwit address, picking bech32 or bech32m by witness version.

    Built on wallet_codec's polymod primitives rather than a second copy of the
    checksum: Phase 0 owns that arithmetic, this only chooses the constant.
    """
    addr = address.lower()
    prefix = hrp + "1"
    if not addr.startswith(prefix) or len(addr) > 90:
        return False
    data_part = addr[len(prefix):]
    if len(data_part) < 6 or any(c not in _BECH32_CHARSET for c in data_part):
        return False
    values = [_BECH32_CHARSET.index(c) for c in data_part]
    expected = 1 if values[0] == 0 else BECH32M_CONST
    return _bech32_polymod(_bech32_hrp_expand(hrp) + values) == expected


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────

def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - CONTEXT_WINDOW)
    right = min(len(text), end + CONTEXT_WINDOW)
    snippet = " ".join(text[left:right].split())
    return ("..." if left > 0 else "") + snippet + ("..." if right < len(text) else "")


def _onion_host(value: str) -> str:
    host = value.lower()
    for prefix in ("http://", "https://"):
        host = host.removeprefix(prefix)
    return host.split("/")[0]


class _Collector:
    """Accumulates kept and dropped rows, deduplicated by (type, value)."""

    def __init__(self) -> None:
        self.kept: list[Extraction] = []
        self.dropped: list[Dropped] = []
        self._seen_kept: set[tuple[str, str]] = set()
        self._seen_dropped: set[tuple[str, str]] = set()

    def keep(self, kind: str, value: str, context: str, meta: Optional[dict] = None) -> None:
        key = (kind, value)
        if key in self._seen_kept:
            return
        self._seen_kept.add(key)
        self.kept.append(
            Extraction(kind, value, context, CONFIDENCE[kind], meta or {})
        )

    def drop(self, kind: str, value: str, context: str, reason: str) -> None:
        key = (kind, value)
        if key in self._seen_dropped:
            return
        self._seen_dropped.add(key)
        self.dropped.append(Dropped(kind, value, context, reason))


def extract_identifiers_report(
    text: str,
    *,
    exclude_onions: Iterable[str] = (),
) -> ExtractionReport:
    """Extract identifiers, and report what was rejected and why."""
    out = _Collector()
    if not text:
        return ExtractionReport(out.kept, out.dropped)

    excluded = {_onion_host(o) for o in exclude_onions}

    # ── PGP key blocks first, then blank them out ────────────────────────────
    masked = text
    for block in find_key_blocks(text):
        start = masked.find(block)
        context = _context(masked, start, start + len(block)) if start >= 0 else ""
        key, reason = parse_key_block_report(block)
        if key is None:
            out.drop("pgp_fpr", "<armoured key block>", context,
                     reason or "unparseable key block")
        else:
            out.keep("pgp_fpr", key.fingerprint, context, {
                "method": "key_block",
                "key_id": key.key_id,
                "uids": list(key.uids),
                "algorithm": key.algorithm_name,
                "created": key.created.isoformat() if key.created else None,
                "subkeys": list(key.subkeys),
            })
        if start >= 0:
            # Same length, so every offset after this point still lines up.
            masked = masked[:start] + (" " * len(block)) + masked[start + len(block):]

    # ── Bare fingerprints ────────────────────────────────────────────────────
    for match in PATTERNS["pgp_spaced"].finditer(masked):
        out.keep("pgp_fpr", normalize_fingerprint(match.group()),
                 _context(masked, *match.span()), {"method": "spaced_fingerprint"})
    for match in PATTERNS["pgp_bare"].finditer(masked):
        out.keep("pgp_fpr", match.group().upper(),
                 _context(masked, *match.span()), {"method": "bare_fingerprint"})

    # ── Session ──────────────────────────────────────────────────────────────
    for match in PATTERNS["session"].finditer(masked):
        out.keep("session", match.group().lower(), _context(masked, *match.span()))

    # ── Ethereum: EIP-55 or nothing ──────────────────────────────────────────
    for match in PATTERNS["eth"].finditer(masked):
        value = match.group()
        context = _context(masked, *match.span())
        if verify_eip55(value):
            out.keep("eth", value, context)
        else:
            body = value[2:]
            reason = (
                "EIP-55 checksum failed"
                if body != body.lower() and body != body.upper()
                else "no EIP-55 checksum (address is all one case, so nothing to verify)"
            )
            out.drop("eth", value, context, reason)

    # ── Segwit ───────────────────────────────────────────────────────────────
    for match in PATTERNS["bech32"].finditer(masked):
        value = match.group().lower()
        hrp = match.group(1).lower()
        kind = "btc" if hrp == "bc" else "ltc"
        context = _context(masked, *match.span())
        if _verify_segwit(value, hrp):
            out.keep(kind, value, context, {"encoding": "bech32"})
        else:
            out.drop(kind, value, context, "bech32 checksum failed")

    # ── Legacy base58check ───────────────────────────────────────────────────
    for match in PATTERNS["base58"].finditer(masked):
        value = match.group()
        context = _context(masked, *match.span())
        if not verify_base58check(value):
            out.drop("btc" if value[0] in "13" else "ltc", value, context,
                     "base58check checksum failed")
            continue
        version = b58decode(value)[0]
        entry = BASE58_VERSIONS.get(version)
        if entry is None:
            out.drop("btc", value, context,
                     f"base58check passed but version byte 0x{version:02x} is not a "
                     f"currency we track")
            continue
        kind, script = entry
        meta = {"encoding": "base58check", "script": script, "version_byte": version}
        if version == 0x05:
            meta["note"] = "version 0x05 is BTC P2SH and legacy LTC P2SH; read as BTC"
        out.keep(kind, value, context, meta)

    # ── Monero ───────────────────────────────────────────────────────────────
    for match in PATTERNS["xmr"].finditer(masked):
        value = match.group()
        context = _context(masked, *match.span())
        network = next(
            (b for b in XMR_NETWORK_BYTES if verify_monero(value, b)), None
        )
        if network is None:
            out.drop("xmr", value, context, "Monero keccak checksum failed")
        else:
            out.keep("xmr", value, context, {
                "network_byte": network,
                "form": "standard" if network == 0x12 else "subaddress",
            })

    # ── Onion mirrors ────────────────────────────────────────────────────────
    for match in PATTERNS["onion"].finditer(masked):
        host = match.group().lower()
        context = _context(masked, *match.span())
        if not verify_onion_v3(host):
            out.drop("onion_mirror", host, context, "onion v3 checksum failed")
        elif host in excluded:
            out.drop("onion_mirror", host, context,
                     "the site this text was scraped from, not a mirror")
        else:
            out.keep("onion_mirror", host, context)

    # ── Email and XMPP ───────────────────────────────────────────────────────
    for match in PATTERNS["address"].finditer(masked):
        value = match.group().lower()
        domain = value.rsplit("@", 1)[1]
        kind = (
            "jabber"
            if domain in XMPP_HOSTS or any(m in domain for m in XMPP_HOST_MARKERS)
            else "email"
        )
        out.keep(kind, value, _context(masked, *match.span()), {"domain": domain})

    # ── Telegram ─────────────────────────────────────────────────────────────
    for key in ("telegram", "telegram_url"):
        for match in PATTERNS[key].finditer(masked):
            out.keep("telegram", "@" + match.group(1),
                     _context(masked, *match.span()),
                     {"form": "handle" if key == "telegram" else "t.me link"})

    return ExtractionReport(out.kept, out.dropped)


def extract_identifiers(
    text: str,
    *,
    exclude_onions: Iterable[str] = (),
) -> list[dict]:
    """Every identifier in `text` that passes its checksum.

    Returns [{type, value, raw_context, confidence, meta}]. Anything that failed
    validation is simply absent; use `extract_identifiers_report` to see it.
    """
    report = extract_identifiers_report(text, exclude_onions=exclude_onions)
    return [row.as_dict() for row in report.kept]
