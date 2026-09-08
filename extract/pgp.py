"""
pgp.py — read an ASCII-armoured OpenPGP public key block.

Neither `pgpy` nor `python-gnupg` is installed and neither is in requirements.txt,
so this is a small RFC 4880 reader written the same way scripts/wallet_codec.py
was: parsing and checksum arithmetic only, no key derivation, no signature
verification, no crypto. It answers one question — *what key is this, exactly* —
and refuses to guess when it cannot.

    fingerprint (v4) = SHA-1( 0x99 ‖ uint16(len(body)) ‖ body )     RFC 4880 §12.2
    key id           = the low 8 bytes of that fingerprint
    uid              = the User ID packets that follow

What it deliberately does not do:

  * It does not mine identifiers out of the User ID. A key block published by two
    personas of one actor carries one uid; treating that uid's address as an
    observation for *both* personas would invent evidence neither profile stated.
    The uid travels in `meta` for an analyst to read, and stops there.
  * It does not parse v3 keys (MD5 over the MPIs, a different rule) or v5/v6.
    Unsupported versions return None with a reason rather than a wrong answer.

`parse_key_block` returns None on anything it cannot vouch for. Use
`parse_key_block_report` when you need to know why.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

__all__ = [
    "PgpKey",
    "PgpParseError",
    "crc24",
    "dearmor",
    "find_key_blocks",
    "iter_packets",
    "normalize_fingerprint",
    "parse_key_block",
    "parse_key_block_report",
]

# ─────────────────────────────────────────────────────────────────────────────
# Armour
# ─────────────────────────────────────────────────────────────────────────────

ARMOR_RE = re.compile(
    r"-----BEGIN PGP PUBLIC KEY BLOCK-----"
    r".*?"
    r"-----END PGP PUBLIC KEY BLOCK-----",
    re.DOTALL,
)

_HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: ")

CRC24_INIT = 0xB704CE
CRC24_POLY = 0x1864CFB


class PgpParseError(ValueError):
    """The block is not something we can read with confidence."""


def crc24(data: bytes) -> int:
    """The RFC 4880 §6.1 armour checksum."""
    crc = CRC24_INIT
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= CRC24_POLY
    return crc & 0xFFFFFF


def find_key_blocks(text: str) -> list[str]:
    """Every armoured public key block in a piece of text, in order."""
    return ARMOR_RE.findall(text)


def dearmor(armored: str) -> bytes:
    """Decode one armoured block and verify its CRC-24.

    Raises PgpParseError if the markers are missing, the base64 is malformed, or
    the checksum does not match the payload. A block whose checksum fails is not
    "probably fine" — it is corrupt, and a fingerprint derived from corrupt bytes
    would be a fabricated identifier.
    """
    match = ARMOR_RE.search(armored)
    if match is None:
        raise PgpParseError("no complete PGP PUBLIC KEY BLOCK found")

    lines = match.group(0).splitlines()[1:-1]  # drop BEGIN and END
    payload: list[str] = []
    checksum: Optional[str] = None
    in_headers = True

    for raw in lines:
        line = raw.strip()
        if in_headers:
            if not line:
                in_headers = False
                continue
            if _HEADER_RE.match(line):
                continue
            in_headers = False  # no armour headers at all, this is already data
        if not line:
            continue
        if line.startswith("="):
            checksum = line[1:]
            break
        payload.append(line)

    if not payload:
        raise PgpParseError("armoured block has no payload")

    try:
        data = base64.b64decode("".join(payload), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PgpParseError(f"malformed base64 payload: {exc}") from exc

    if checksum is None:
        raise PgpParseError("armoured block has no CRC-24 checksum line")
    try:
        expected = int.from_bytes(base64.b64decode(checksum, validate=True), "big")
    except (binascii.Error, ValueError) as exc:
        raise PgpParseError(f"malformed CRC-24 checksum line: {exc}") from exc

    actual = crc24(data)
    if actual != expected:
        raise PgpParseError(
            f"CRC-24 checksum mismatch: armour claims {expected:06X}, "
            f"payload computes {actual:06X}"
        )
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Packets
# ─────────────────────────────────────────────────────────────────────────────

TAG_PUBLIC_KEY = 6
TAG_USER_ID = 13
TAG_PUBLIC_SUBKEY = 14


def iter_packets(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (tag, body) for each packet. Handles both header formats."""
    offset = 0
    end = len(data)

    while offset < end:
        header = data[offset]
        if not header & 0x80:
            raise PgpParseError(f"packet at offset {offset} has no header bit set")
        offset += 1

        if header & 0x40:  # new format, RFC 4880 §4.2.2
            tag = header & 0x3F
            if offset >= end:
                raise PgpParseError("truncated new-format length")
            first = data[offset]
            offset += 1
            if first < 192:
                length = first
            elif first < 224:
                if offset >= end:
                    raise PgpParseError("truncated two-octet length")
                length = ((first - 192) << 8) + data[offset] + 192
                offset += 1
            elif first == 255:
                if offset + 4 > end:
                    raise PgpParseError("truncated five-octet length")
                length = int.from_bytes(data[offset:offset + 4], "big")
                offset += 4
            else:
                # Partial body lengths only appear in streamed literal/compressed
                # data, never in a transferable public key.
                raise PgpParseError("partial body lengths are not supported")
        else:  # old format, RFC 4880 §4.2.1
            tag = (header >> 2) & 0x0F
            length_type = header & 0x03
            if length_type == 0:
                if offset + 1 > end:
                    raise PgpParseError("truncated one-octet length")
                length = data[offset]
                offset += 1
            elif length_type == 1:
                if offset + 2 > end:
                    raise PgpParseError("truncated two-octet length")
                length = int.from_bytes(data[offset:offset + 2], "big")
                offset += 2
            elif length_type == 2:
                if offset + 4 > end:
                    raise PgpParseError("truncated four-octet length")
                length = int.from_bytes(data[offset:offset + 4], "big")
                offset += 4
            else:
                length = end - offset  # indeterminate: runs to the end

        if offset + length > end:
            raise PgpParseError(
                f"packet tag {tag} claims {length} bytes but only "
                f"{end - offset} remain"
            )
        yield tag, data[offset:offset + length]
        offset += length


# ─────────────────────────────────────────────────────────────────────────────
# Keys
# ─────────────────────────────────────────────────────────────────────────────

#: RFC 4880 §9.1, only the ones that turn up on a vendor profile.
PUBLIC_KEY_ALGORITHMS = {
    1: "RSA",
    2: "RSA (encrypt only)",
    3: "RSA (sign only)",
    16: "ElGamal",
    17: "DSA",
    18: "ECDH",
    19: "ECDSA",
    22: "EdDSA",
}


@dataclass(frozen=True)
class PgpKey:
    """What an armoured public key block says about itself."""

    fingerprint: str                      # 40 uppercase hex
    key_id: str                           # 16 uppercase hex, the fingerprint's tail
    uids: tuple[str, ...] = ()
    version: int = 4
    algorithm: int = 0
    created: Optional[datetime] = None
    subkeys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def uid(self) -> Optional[str]:
        return self.uids[0] if self.uids else None

    @property
    def algorithm_name(self) -> str:
        return PUBLIC_KEY_ALGORITHMS.get(self.algorithm, f"algorithm {self.algorithm}")

    def __repr__(self) -> str:
        return f"<PgpKey {self.fingerprint} {self.uid!r}>"


def normalize_fingerprint(value: str) -> str:
    """Uppercase, unspaced — the form the corpus and the identifiers table use."""
    return re.sub(r"[\s:]", "", value).upper()


def _fingerprint_v4(body: bytes) -> str:
    digest = hashlib.sha1(
        b"\x99" + len(body).to_bytes(2, "big") + body  # noqa: S324 - RFC 4880 defines SHA-1 here
    ).digest()
    return digest.hex().upper()


def _read_public_key(body: bytes) -> tuple[str, int, int, Optional[datetime]]:
    """(fingerprint, version, algorithm, created) for a v4 public key packet."""
    if not body:
        raise PgpParseError("empty public key packet")
    version = body[0]
    if version != 4:
        raise PgpParseError(
            f"unsupported key version {version}; only v4 fingerprints are defined "
            f"as SHA-1 over the packet"
        )
    if len(body) < 6:
        raise PgpParseError("truncated v4 public key packet")

    created_at = int.from_bytes(body[1:5], "big")
    algorithm = body[5]
    created = datetime.fromtimestamp(created_at, tz=timezone.utc).replace(tzinfo=None)
    return _fingerprint_v4(body), version, algorithm, created


def parse_key_block_report(armored: str) -> tuple[Optional[PgpKey], Optional[str]]:
    """Parse a block. Returns (key, None) or (None, reason)."""
    try:
        data = dearmor(armored)
    except PgpParseError as exc:
        return None, str(exc)

    fingerprint = key_id = None
    version = algorithm = 0
    created: Optional[datetime] = None
    uids: list[str] = []
    subkeys: list[str] = []

    try:
        for tag, body in iter_packets(data):
            if tag == TAG_PUBLIC_KEY:
                if fingerprint is not None:
                    continue  # a second primary key in one block: the first wins
                fingerprint, version, algorithm, created = _read_public_key(body)
                key_id = fingerprint[-16:]
            elif tag == TAG_PUBLIC_SUBKEY:
                try:
                    subkeys.append(_read_public_key(body)[0])
                except PgpParseError:
                    continue  # an unreadable subkey does not invalidate the key
            elif tag == TAG_USER_ID:
                uids.append(body.decode("utf-8", errors="replace"))
    except PgpParseError as exc:
        return None, str(exc)

    if fingerprint is None or key_id is None:
        return None, "no public key packet in the block"

    return (
        PgpKey(
            fingerprint=fingerprint,
            key_id=key_id,
            uids=tuple(uids),
            version=version,
            algorithm=algorithm,
            created=created,
            subkeys=tuple(subkeys),
        ),
        None,
    )


def parse_key_block(armored: str) -> Optional[PgpKey]:
    """The key, or None if the block cannot be vouched for."""
    return parse_key_block_report(armored)[0]
