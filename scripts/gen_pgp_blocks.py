"""
gen_pgp_blocks.py — build the armoured PGP key blocks the corpus was missing.

    python scripts/gen_pgp_blocks.py            # no-op if the file is already good
    python scripts/gen_pgp_blocks.py --force    # rebuild it
    python scripts/gen_pgp_blocks.py --check    # verify only, exit 1 on drift

fixtures/*/personas.json declares bare 40-hex fingerprints, so extract/pgp.py had
nothing to parse. This writes fixtures/pgp_blocks.json: two real, armoured, v4 RSA
public key blocks with genuine CRC-24 checksums, one published by both personas of
actor_001 and one by both personas of actor_003, mirroring the shared-key evidence
ground_truth.json already claims for those pairs.

Three things to be straight about:

  * The fingerprints are *not* the hex strings already in personas.json. A v4
    fingerprint is SHA-1 over the key packet and SHA-1 does not run backwards, so
    a block cannot be made to land on a chosen value. These are additional
    fingerprints for the same two actor pairs — they reinforce Phase 2's H term
    and create no edge the ground truth does not already claim.

  * The keys are real RSA public keys (n = p·q from a seeded prime search,
    e = 65537) at 1024 bits. Nothing ever verifies a signature with them; they
    are parse targets, and 1024 keeps generation to well under a second.

  * The output is committed. Nothing in the test suite or in scripts/ingest.py
    runs this script — they read the JSON. Re-running it without --force checks
    the committed file and exits.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from extract.pgp import crc24, parse_key_block_report  # noqa: E402

OUTPUT = ROOT / "fixtures" / "pgp_blocks.json"

#: Same seed as scripts/gen_fixtures.py — one deterministic corpus.
SEED = 1337

#: 1024-bit modulus: parse target, not crypto. See the module docstring.
KEY_BITS = 1024

RSA_ENCRYPT_SIGN = 1  # RFC 4880 §9.1

#: (persona ids, actor, uid, creation time as a unix timestamp, packet format)
#: Both header formats are represented on purpose so extract/pgp.iter_packets is
#: exercised on each path by the fixtures rather than only by a synthetic case.
KEYS = (
    {
        "personas": (1, 16),
        "actor": "actor_001",
        "uid": "Dread Pirate <dreadpirate@jabber.calyxinstitute.net>",
        "created": 1757462400,  # 2025-09-10T00:00:00Z, before persona 1's first_seen
        "format": "new",
    },
    {
        "personas": (3, 18),
        "actor": "actor_003",
        "uid": "Vector Supply <vector.supply@protonmail.com>",
        "created": 1757289600,  # 2025-09-08T00:00:00Z
        "format": "old",
    },
)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic RSA public keys
# ─────────────────────────────────────────────────────────────────────────────

_SMALL_PRIMES = (
    3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113,
)


def _is_probable_prime(n: int, rng: random.Random, rounds: int = 40) -> bool:
    """Miller-Rabin. 40 rounds is far beyond what a parse target needs."""
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n == p:
            return True
        if n % p == 0:
            return False

    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1

    for _ in range(rounds):
        a = rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, rng: random.Random) -> int:
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate, rng):
            return candidate


def rsa_public_key(rng: random.Random, bits: int = KEY_BITS) -> tuple[int, int]:
    """A real (n, e) public key. The private half is never derived or needed."""
    half = bits // 2
    p = _prime(half, rng)
    q = _prime(half, rng)
    while q == p:
        q = _prime(half, rng)
    return p * q, 65537


# ─────────────────────────────────────────────────────────────────────────────
# OpenPGP packet assembly — RFC 4880 §4.2, §5.2, §5.5.2, §6.2
# ─────────────────────────────────────────────────────────────────────────────

def mpi(value: int) -> bytes:
    """Multiprecision integer: 2-byte bit length, then big-endian bytes."""
    bits = value.bit_length()
    return bits.to_bytes(2, "big") + value.to_bytes((bits + 7) // 8, "big")


def public_key_body(n: int, e: int, created: int) -> bytes:
    return (
        b"\x04"
        + created.to_bytes(4, "big")
        + bytes([RSA_ENCRYPT_SIGN])
        + mpi(n)
        + mpi(e)
    )


def _length_new(size: int) -> bytes:
    if size < 192:
        return bytes([size])
    if size < 8384:
        size -= 192
        return bytes([(size >> 8) + 192, size & 0xFF])
    return b"\xff" + size.to_bytes(4, "big")


def _length_old(size: int) -> tuple[int, bytes]:
    if size < 0x100:
        return 0, bytes([size])
    if size < 0x10000:
        return 1, size.to_bytes(2, "big")
    return 2, size.to_bytes(4, "big")


def packet(tag: int, body: bytes, form: str = "new") -> bytes:
    if form == "new":
        return bytes([0xC0 | tag]) + _length_new(len(body)) + body
    length_type, encoded = _length_old(len(body))
    return bytes([0x80 | (tag << 2) | length_type]) + encoded + body


def armor(data: bytes, width: int = 64) -> str:
    """Armour a transferable public key. No armour headers, as modern gpg emits."""
    payload = base64.b64encode(data).decode("ascii")
    lines = [payload[i:i + width] for i in range(0, len(payload), width)]
    checksum = base64.b64encode(crc24(data).to_bytes(3, "big")).decode("ascii")
    return "\n".join(
        ["-----BEGIN PGP PUBLIC KEY BLOCK-----", ""]
        + lines
        + ["=" + checksum, "-----END PGP PUBLIC KEY BLOCK-----"]
    )


def build_block(spec: dict, rng: random.Random) -> str:
    n, e = rsa_public_key(rng)
    body = public_key_body(n, e, spec["created"])
    return armor(
        packet(6, body, spec["format"])
        + packet(13, spec["uid"].encode("utf-8"), spec["format"])
    )


# ─────────────────────────────────────────────────────────────────────────────
# Build / verify
# ─────────────────────────────────────────────────────────────────────────────

def generate() -> list[dict]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    for spec in KEYS:
        armored = build_block(spec, rng)
        key, reason = parse_key_block_report(armored)
        if key is None:
            raise SystemExit(f"generated a block extract/pgp.py cannot read: {reason}")
        for persona_id in spec["personas"]:
            rows.append({
                "persona_id": persona_id,
                "actor": spec["actor"],
                "fingerprint": key.fingerprint,
                "key_id": key.key_id,
                "uid": spec["uid"],
                "created": spec["created"],
                "packet_format": spec["format"],
                "armored": armored,
            })
    return rows


def verify(rows: list[dict]) -> list[str]:
    """Every recorded value must come back out of the armoured block itself."""
    problems: list[str] = []
    for row in rows:
        key, reason = parse_key_block_report(row["armored"])
        if key is None:
            problems.append(f"persona {row['persona_id']}: unparseable ({reason})")
            continue
        if key.fingerprint != row["fingerprint"]:
            problems.append(
                f"persona {row['persona_id']}: fingerprint drift, block says "
                f"{key.fingerprint}, file says {row['fingerprint']}"
            )
        if key.key_id != row["key_id"] or key.key_id != key.fingerprint[-16:]:
            problems.append(f"persona {row['persona_id']}: key id drift")
        if row["uid"] not in key.uids:
            problems.append(f"persona {row['persona_id']}: uid drift")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--force", action="store_true",
                        help="regenerate even if the committed file verifies")
    parser.add_argument("--check", action="store_true",
                        help="verify the committed file and exit; never writes")
    args = parser.parse_args()

    existing = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else None

    if existing is not None and not args.force:
        problems = verify(existing)
        if not problems:
            print(f"{OUTPUT.relative_to(ROOT)}: {len(existing)} rows, "
                  f"{len({r['fingerprint'] for r in existing})} keys, all verify")
            return 0
        print("committed file does not verify:")
        for problem in problems:
            print(f"  - {problem}")
        if args.check:
            return 1
        print("regenerating")
    elif args.check:
        if existing is None:
            print(f"missing: {OUTPUT.relative_to(ROOT)}")
            return 1
        return 1 if verify(existing) else 0

    rows = generate()
    problems = verify(rows)
    if problems:
        raise SystemExit("generated rows do not verify:\n  " + "\n  ".join(problems))

    OUTPUT.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    for fingerprint in dict.fromkeys(r["fingerprint"] for r in rows):
        personas = [r["persona_id"] for r in rows if r["fingerprint"] == fingerprint]
        print(f"  {fingerprint}  key id {fingerprint[-16:]}  personas {personas}")
    print(f"\nwrote {OUTPUT.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
