"""onion_location.py — the link a site declares about itself.

`Onion-Location` is a header (and an equivalent `<meta http-equiv>`) by which a
clearnet site advertises its own onion address, so Tor Browser can offer the
user the onion version. It is a published, intentional, machine-readable
statement of the form "this clearnet host and this onion service are the same
operator".

That makes it the strongest clearnet↔onion signal in this codebase, and the
cheapest: no correlation, no scoring, no inference. The operator said so. Every
other thing `recon/` does is circumstantial by comparison — a matching TLS
serial is strong evidence, but it is still evidence, whereas this is a claim.

WHY IT IS STILL ONLY A LEAD
---------------------------
Three ways a declared link misleads:

  * **Anyone can declare anything.** The header is unauthenticated. A host can
    advertise an onion it does not run — to poison exactly this kind of
    analysis, or to phish. Nothing about receiving the header proves the
    declaring party controls the onion.
  * **It points the direction we do not need.** It tells us clearnet → onion.
    An investigation usually starts at the onion and wants the clearnet host.
    Finding the header requires already suspecting the clearnet host.
  * **Tor Browser ignores it over plain HTTP**, so a header served on `http://`
    is not even doing the job it claims to. We record that rather than treating
    all declarations alike.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not write an `infra_correlations` row. That table feeds the I term of
the attribution score, and nothing new gets to touch a score in this repo until
it has been measured against ground truth — see `scripts/measure_reliability.py`
for the same restraint applied to source reliability. The fixture corpus has no
Onion-Location ground truth to measure against, so this stays a reported
finding until there is.

Passive: it parses a response that was already fetched. It issues no request of
its own and has no network code in it at all.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional
from urllib.parse import urlparse

__all__ = [
    "ONION_V3_LENGTH",
    "OnionLocation",
    "detect",
    "is_valid_v3_address",
    "parse_meta",
]

#: A v3 address is base32(pubkey[32] || checksum[2] || version[1]) = 56 chars.
ONION_V3_LENGTH = 56

#: The constant the checksum is salted with, from rend-spec-v3 section 6.
_CHECKSUM_SALT = b".onion checksum"
_VERSION = b"\x03"

_BASE32 = re.compile(r"^[a-z2-7]{56}$")

#: `<meta http-equiv="onion-location" content="http://....onion/">`. Attribute
#: order is not fixed by the spec, so match the tag then pull the attributes,
#: rather than assuming a layout.
_META_TAG = re.compile(
    r"<meta\b[^>]*http-equiv\s*=\s*[\"']?onion-location[\"']?[^>]*>",
    re.IGNORECASE,
)
_CONTENT_ATTR = re.compile(
    r"content\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
    re.IGNORECASE,
)


def is_valid_v3_address(address: str) -> bool:
    """Format *and* checksum, the way wallets are validated in this repo.

    A regex-only check accepts 56 random base32 characters, and this codebase's
    position on regex-only identifier matching is in CLAUDE.md: a bad value
    dropped is better than a bad value stored.
    """
    host = address.strip().lower()
    if host.endswith(".onion"):
        host = host[: -len(".onion")]
    if not _BASE32.match(host):
        return False

    try:
        decoded = base64.b32decode(host.upper())
    except Exception:  # noqa: BLE001 - malformed padding is simply invalid
        return False
    if len(decoded) != 35:
        return False

    pubkey, checksum, version = decoded[:32], decoded[32:34], decoded[34:35]
    if version != _VERSION:
        return False

    expected = hashlib.sha3_256(_CHECKSUM_SALT + pubkey + _VERSION).digest()[:2]
    return checksum == expected


def _extract_onion(value: str) -> tuple[Optional[str], Optional[str]]:
    """(onion host, reason it was rejected). Exactly one is not None."""
    candidate = (value or "").strip()
    if not candidate:
        return None, "empty value"

    parsed = urlparse(candidate if "//" in candidate else f"//{candidate}")
    host = (parsed.hostname or "").lower()
    if not host:
        return None, "no host in the declared URL"
    if not host.endswith(".onion"):
        return None, f"declared host {host!r} is not a .onion"
    if not is_valid_v3_address(host):
        return None, f"declared address {host!r} failed the v3 checksum"
    return host, None


def parse_meta(body: str | bytes) -> Optional[str]:
    """The `content` of the first `onion-location` meta tag, if any."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    tag = _META_TAG.search(body or "")
    if not tag:
        return None
    attr = _CONTENT_ATTR.search(tag.group(0))
    if not attr:
        return None
    return next((g for g in attr.groups() if g is not None), None)


@dataclass(frozen=True)
class OnionLocation:
    """A site's own declaration that it runs a given onion service."""

    clearnet_host: str
    onion_address: str
    #: "header" or "meta". The header is the spec's primary mechanism; the meta
    #: tag exists for operators who cannot set headers, and is equally valid.
    via: str
    #: Tor Browser only acts on the declaration over HTTPS (or from an onion).
    #: A declaration on plain HTTP is recorded and marked as inert rather than
    #: dropped — it still tells an analyst what the operator believes.
    honoured_by_browser: bool
    declared_value: str
    detail: dict = field(default_factory=dict)

    @property
    def why(self) -> str:
        where = "an Onion-Location header" if self.via == "header" else \
                "an onion-location meta tag"
        return (f"{self.clearnet_host} served {where} naming "
                f"{self.onion_address}.")

    @property
    def caveat(self) -> str:
        if not self.honoured_by_browser:
            return ("The declaration arrived over plain HTTP, where Tor Browser "
                    "ignores it, so it is inert as published. It is also "
                    "unauthenticated: a host can advertise an onion it does not "
                    "run. Corroborate before relying on it.")
        return ("The declaration is unauthenticated — a host can advertise an "
                "onion it does not run, including to poison this kind of "
                "analysis. It shows what the operator claims, not what is true.")


def detect(
    url: str,
    headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    body: str | bytes | None = None,
) -> Optional[OnionLocation]:
    """Read an already-fetched response for a self-declared onion address.

    Pure: `url`, `headers` and `body` come from a request someone else made.
    Returns None when nothing is declared, or when what is declared does not
    survive validation — and records why in `detail` when it was declared but
    rejected, so a malformed advertisement is visible rather than silent.
    """
    parsed = urlparse(url or "")
    clearnet_host = (parsed.hostname or "").lower()
    over_https = parsed.scheme == "https"

    if isinstance(headers, Mapping):
        items = list(headers.items())
    else:
        items = list(headers or ())
    lowered = {str(k).lower(): str(v) for k, v in items}

    declared = lowered.get("onion-location")
    via = "header"
    if declared is None:
        declared = parse_meta(body or "")
        via = "meta"
    if declared is None:
        return None

    onion, reason = _extract_onion(declared)
    if onion is None:
        # Declared but unusable. Returning None keeps a bad value out of the
        # findings, and the caller can still see it happened via the log.
        return None

    return OnionLocation(
        clearnet_host=clearnet_host,
        onion_address=onion,
        via=via,
        honoured_by_browser=over_https,
        declared_value=declared.strip(),
        detail={"rejected_reason": reason} if reason else {},
    )
