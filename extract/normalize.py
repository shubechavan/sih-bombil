"""
normalize.py — folding handles and identifiers into their comparison forms.

Two different jobs live here, and they must not be confused:

  normalize(handle)
      Aggressive. Collapses the ways one person writes one alias — leet
      substitution, separators, unicode confusables — so that
      normalize("Dr3ad_P1rat3") == normalize("dreadpirate"). This feeds
      personas.handle_normalized, which the attribution formula scores at 0.45.
      Deliberately weak evidence: the fold is lossy on purpose, and two
      unrelated people can land on the same string.

  normalize_identifier(kind, value)
      Conservative. Only removes differences that carry no information for that
      identifier type. Base58 is case-sensitive, so a Bitcoin legacy or Monero
      address is left exactly as found; an Ethereum address carries its EIP-55
      checksum *in* the casing, so the comparison form is lowercase while the
      stored value keeps the checksum. This feeds identifiers.value_norm, which
      scores 0.85-1.00. Fold too hard here and the link graph fills with pairs
      that share nothing.

The leet map comes from legacy/obfuslex_engine.py, unchanged. v1 built it to
un-obfuscate drug slang ("m3th" -> "meth"); the same table un-obfuscates aliases.
It already lowercases and strips _ - . *, which is most of the work.

scripts/load_fixtures.py imports from here, so the values Phase 0 wrote into the
database and the values Phase 1 derives cannot drift apart.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

_LEGACY = Path(__file__).resolve().parent.parent / "legacy"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

from obfuslex_engine import LEET_MAP, leet_decode  # noqa: E402

__all__ = [
    "normalize",
    "normalize_handle",
    "normalize_identifier",
    "LEET_MAP",
    "leet_decode",
]

#: Stripped after leet_decode. All whitespace goes, plus the separators unicode
#: keeps distinct from ASCII hyphen (NFKD does not decompose en/em dashes) and a
#: few bullet characters that turn up in scraped display names. ASCII _ - . * are
#: already handled by LEET_MAP.
_SEPARATORS = frozenset("~·•‐‑‒–—―−‧⋅ \t\r\n\v\f   　")


def normalize(value: str) -> str:
    """Fold an alias to its comparison form.

        normalize("Dr3ad_P1rat3") == normalize("dreadpirate") == "dreadpirate"

    Order matters. NFKD first, so that fullwidth and decorated characters become
    the ASCII the leet map is written against; then combining marks are dropped,
    so "Dréád" and "Dread" agree; then leet_decode lowercases and substitutes;
    then whatever separators survive are removed.
    """
    if not value:
        return ""

    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    decoded = leet_decode(stripped)
    return "".join(ch for ch in decoded if ch not in _SEPARATORS)


#: The loader's name for the same operation. Kept so scripts/load_fixtures.py
#: reads the way it did before this module existed.
normalize_handle = normalize


def normalize_identifier(kind: str, value: str) -> str:
    """Fold an identifier into its comparison form.

    Case matters differently per type. Base58 is case-sensitive, so a Bitcoin
    legacy address or a Monero address must not be lowercased; an Ethereum
    address carries its checksum *in* the casing, so the comparison form is the
    lowercase one; bech32 is canonically lowercase.
    """
    value = value.strip()
    if kind == "pgp_fpr":
        return value.replace(" ", "").lower()
    if kind in {"eth", "email", "jabber", "session"}:
        return value.lower()
    if kind == "telegram":
        return value.lstrip("@").lower()
    if kind == "onion_mirror":
        host = value.lower()
        for prefix in ("http://", "https://"):
            host = host.removeprefix(prefix)
        return host.split("/")[0]
    if kind == "btc" and value.lower().startswith("bc1"):
        return value.lower()
    if kind == "ltc" and value.lower().startswith("ltc1"):
        return value.lower()
    return value  # btc/ltc legacy and xmr are case-sensitive base58
