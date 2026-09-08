"""
wallet_codec.py — address encoding and checksum verification.

Written for the fixture generator: CLAUDE.md requires that a regex-only match on a
hex string never reaches the link graph, so the synthetic corpus must contain
addresses whose checksums genuinely pass, plus a couple that genuinely fail. An
invented-looking address would make the Phase 1 validator untestable.

No third-party crypto library is available in this environment, and `hashlib.sha3_256`
is FIPS-202 SHA-3 rather than the original Keccak that Ethereum and Monero use, so
Keccak-256 is implemented here. It is verified against published vectors by
`self_test()`, which runs on import of this module as a script and from the tests.

Everything here is checksum arithmetic only — no key derivation, no signing.

Phase 1's extract/identifiers.py should reuse the verify_* functions rather than
reimplementing the checksum rules.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Keccak-256 (original padding, not FIPS-202 SHA-3)
# ─────────────────────────────────────────────────────────────────────────────

_MASK64 = (1 << 64) - 1

_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# rho offsets, indexed [x][y]
_ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rol(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f1600(state: list[int]) -> None:
    """In-place Keccak-f[1600] permutation over 25 64-bit lanes, A[x + 5y]."""
    for rc in _ROUND_CONSTANTS:
        # theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
             for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(state[x + 5 * y], _ROTATION[x][y])

        # chi
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ (
                    (b[(x + 1) % 5 + 5 * y] ^ _MASK64) & b[(x + 2) % 5 + 5 * y]
                )

        # iota
        state[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Keccak-256 with the original 0x01 padding byte (what Ethereum calls keccak256)."""
    rate = 136  # 1088 bits
    state = [0] * 25

    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] |= 0x80

    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f1600(state)

    out = bytearray()
    for lane in state:
        out += lane.to_bytes(8, "little")
    return bytes(out[:32])


# ─────────────────────────────────────────────────────────────────────────────
# Base58 / base58check — Bitcoin and Litecoin legacy addresses
# ─────────────────────────────────────────────────────────────────────────────

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {ch: i for i, ch in enumerate(_B58_ALPHABET)}

BTC_P2PKH_VERSION = 0x00  # '1...'
LTC_P2PKH_VERSION = 0x30  # 'L...'


def b58encode(raw: bytes) -> str:
    num = int.from_bytes(raw, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    # leading zero bytes are encoded as '1'
    for byte in raw:
        if byte != 0:
            break
        out = "1" + out
    return out


def b58decode(text: str) -> bytes:
    num = 0
    for ch in text:
        if ch not in _B58_INDEX:
            raise ValueError(f"invalid base58 character {ch!r}")
        num = num * 58 + _B58_INDEX[ch]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = 0
    for ch in text:
        if ch != "1":
            break
        pad += 1
    return b"\x00" * pad + body


def _sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def base58check_encode(version: int, payload: bytes) -> str:
    raw = bytes([version]) + payload
    return b58encode(raw + _sha256d(raw)[:4])


def verify_base58check(address: str, expected_version: Optional[int] = None) -> bool:
    """True if the address decodes to 25 bytes whose sha256d checksum matches."""
    try:
        raw = b58decode(address)
    except ValueError:
        return False
    if len(raw) != 25:
        return False
    body, checksum = raw[:-4], raw[-4:]
    if _sha256d(body)[:4] != checksum:
        return False
    return expected_version is None or body[0] == expected_version


def verify_btc_legacy(address: str) -> bool:
    return verify_base58check(address, BTC_P2PKH_VERSION)


def verify_ltc_legacy(address: str) -> bool:
    return verify_base58check(address, LTC_P2PKH_VERSION)


# ─────────────────────────────────────────────────────────────────────────────
# Bech32 — BIP-173, native segwit ('bc1q...')
# ─────────────────────────────────────────────────────────────────────────────

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values: list[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, g in enumerate(generator):
            if (top >> i) & 1:
                chk ^= g
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data: bytes | list[int], frombits: int, tobits: int, pad: bool = True):
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            raise ValueError("value out of range")
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("invalid padding")
    return out


def bech32_encode(hrp: str, witness_version: int, witness_program: bytes) -> str:
    data = [witness_version] + _convertbits(witness_program, 8, 5)
    checksum_input = _bech32_hrp_expand(hrp) + data + [0, 0, 0, 0, 0, 0]
    polymod = _bech32_polymod(checksum_input) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in data + checksum)


def verify_bech32(address: str, hrp: str = "bc") -> bool:
    """True if the address is a well-formed bech32 string with a valid checksum."""
    addr = address.lower()
    if not addr.startswith(hrp + "1") or len(addr) > 90:
        return False
    data_part = addr[len(hrp) + 1:]
    if any(c not in _BECH32_CHARSET for c in data_part):
        return False
    values = [_BECH32_CHARSET.index(c) for c in data_part]
    return _bech32_polymod(_bech32_hrp_expand(hrp) + values) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Ethereum — EIP-55 mixed-case checksum
# ─────────────────────────────────────────────────────────────────────────────

def to_checksum_address(address: str) -> str:
    """Apply the EIP-55 mixed-case checksum to a 0x-prefixed hex address."""
    body = address.lower().removeprefix("0x")
    if len(body) != 40 or any(c not in "0123456789abcdef" for c in body):
        raise ValueError(f"not a 20-byte hex address: {address!r}")
    digest = keccak256(body.encode("ascii")).hex()
    return "0x" + "".join(
        ch.upper() if ch.isalpha() and int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(body)
    )


def verify_eip55(address: str) -> bool:
    """True if the address carries a correct EIP-55 checksum.

    An all-lowercase or all-uppercase address carries no checksum at all, so it
    is rejected here: for attribution we want the strong form, since an unchecked
    40-hex string is exactly the regex-only match CLAUDE.md warns about.
    """
    if not address.startswith("0x") or len(address) != 42:
        return False
    body = address[2:]
    if any(c not in "0123456789abcdefABCDEF" for c in body):
        return False
    if body == body.lower() or body == body.upper():
        return False
    try:
        return to_checksum_address(address) == address
    except ValueError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Monero — base58 in 8-byte blocks with a Keccak-256 checksum
# ─────────────────────────────────────────────────────────────────────────────

XMR_MAINNET_STANDARD = 0x12  # '4...'

#: encoded length for a block of 1..8 bytes
_XMR_BLOCK_SIZES = (0, 2, 3, 5, 6, 7, 9, 10, 11)


def _xmr_b58_encode_block(block: bytes) -> str:
    num = int.from_bytes(block, "big")
    size = _XMR_BLOCK_SIZES[len(block)]
    out = ["1"] * size
    i = size - 1
    while num > 0:
        num, rem = divmod(num, 58)
        out[i] = _B58_ALPHABET[rem]
        i -= 1
    return "".join(out)


def _xmr_b58_decode_block(block: str) -> bytes:
    size = _XMR_BLOCK_SIZES.index(len(block))
    num = 0
    for ch in block:
        if ch not in _B58_INDEX:
            raise ValueError(f"invalid base58 character {ch!r}")
        num = num * 58 + _B58_INDEX[ch]
    return num.to_bytes(size, "big")


def xmr_b58_encode(raw: bytes) -> str:
    out = []
    for offset in range(0, len(raw), 8):
        out.append(_xmr_b58_encode_block(raw[offset:offset + 8]))
    return "".join(out)


def xmr_b58_decode(text: str) -> bytes:
    out = bytearray()
    for offset in range(0, len(text), 11):
        out += _xmr_b58_decode_block(text[offset:offset + 11])
    return bytes(out)


def monero_address(spend_key: bytes, view_key: bytes,
                   network_byte: int = XMR_MAINNET_STANDARD) -> str:
    """Encode a standard Monero address: netbyte + spend + view + keccak checksum."""
    if len(spend_key) != 32 or len(view_key) != 32:
        raise ValueError("spend and view keys must be 32 bytes each")
    body = bytes([network_byte]) + spend_key + view_key
    return xmr_b58_encode(body + keccak256(body)[:4])


def verify_monero(address: str, network_byte: int = XMR_MAINNET_STANDARD) -> bool:
    """True if the address decodes to 69 bytes whose Keccak-256 checksum matches."""
    if len(address) != 95:
        return False
    try:
        raw = xmr_b58_decode(address)
    except ValueError:
        return False
    if len(raw) != 69:
        return False
    body, checksum = raw[:-4], raw[-4:]
    if keccak256(body)[:4] != checksum:
        return False
    return body[0] == network_byte


# ─────────────────────────────────────────────────────────────────────────────
# Tor v3 onion addresses
#
# Not a wallet, but the same shape of problem: the corpus needs onion URLs that
# pass a real checksum so the recon and onion_mirror paths are testable offline.
# v3 uses FIPS-202 SHA3-256, which hashlib does provide.
# ─────────────────────────────────────────────────────────────────────────────

def onion_v3_address(pubkey: bytes) -> str:
    if len(pubkey) != 32:
        raise ValueError("ed25519 public key must be 32 bytes")
    version = b"\x03"
    checksum = hashlib.sha3_256(b".onion checksum" + pubkey + version).digest()[:2]
    return base64.b32encode(pubkey + checksum + version).decode("ascii").lower() + ".onion"


def verify_onion_v3(address: str) -> bool:
    host = address.strip().lower()
    for prefix in ("http://", "https://"):
        host = host.removeprefix(prefix)
    host = host.split("/")[0]
    if not host.endswith(".onion"):
        return False
    body = host.removesuffix(".onion")
    if len(body) != 56:
        return False
    try:
        raw = base64.b32decode(body.upper())
    except Exception:
        return False
    if len(raw) != 35 or raw[34] != 0x03:
        return False
    pubkey, checksum = raw[:32], raw[32:34]
    return hashlib.sha3_256(b".onion checksum" + pubkey + b"\x03").digest()[:2] == checksum


# ─────────────────────────────────────────────────────────────────────────────
# Self-test against published vectors
# ─────────────────────────────────────────────────────────────────────────────

#: Keccak-256 vectors (the original Keccak submission, as used by Ethereum).
KECCAK_VECTORS = (
    (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
    (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
    (b"testing", "5f16f4c7f149ac4f9510d9cf8cf384038ad348b3bcdc01915f95de12df9d1b02"),
)

#: EIP-55 examples from the specification.
EIP55_VECTORS = (
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
)

#: A well-known mainnet Bitcoin address (the genesis coinbase output).
BTC_VECTOR = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def self_test() -> None:
    """Raise AssertionError if any primitive disagrees with a published vector."""
    for data, expected in KECCAK_VECTORS:
        got = keccak256(data).hex()
        assert got == expected, f"keccak256({data!r}) = {got}, expected {expected}"

    for address in EIP55_VECTORS:
        assert verify_eip55(address), f"EIP-55 vector rejected: {address}"
        assert to_checksum_address(address.lower()) == address, (
            f"EIP-55 recomputation mismatch for {address}"
        )
        # flipping the case of one checksummed character must invalidate it
        idx = next(i for i, c in enumerate(address[2:], start=2) if c.isalpha())
        flipped = address[:idx] + address[idx].swapcase() + address[idx + 1:]
        assert not verify_eip55(flipped), f"EIP-55 accepted a corrupted address: {flipped}"

    assert verify_btc_legacy(BTC_VECTOR), f"BTC vector rejected: {BTC_VECTOR}"
    corrupted = BTC_VECTOR[:-1] + ("X" if BTC_VECTOR[-1] != "X" else "Y")
    assert not verify_btc_legacy(corrupted), "BTC checksum accepted a corrupted address"

    # round-trips
    roundtrip_btc = base58check_encode(BTC_P2PKH_VERSION, bytes(range(20)))
    assert verify_btc_legacy(roundtrip_btc)
    roundtrip_ltc = base58check_encode(LTC_P2PKH_VERSION, bytes(range(20)))
    assert verify_ltc_legacy(roundtrip_ltc) and roundtrip_ltc.startswith("L")

    bech = bech32_encode("bc", 0, bytes(range(20)))
    assert verify_bech32(bech) and bech.startswith("bc1q")
    assert not verify_bech32(bech[:-1] + ("q" if bech[-1] != "q" else "p"))

    xmr = monero_address(bytes(range(32)), bytes(range(32, 64)))
    assert len(xmr) == 95 and xmr.startswith("4"), f"unexpected XMR address: {xmr}"
    assert verify_monero(xmr)
    assert not verify_monero(xmr[:-1] + ("A" if xmr[-1] != "A" else "B"))

    onion = onion_v3_address(bytes(range(32)))
    assert verify_onion_v3(onion) and len(onion) == 62, f"bad onion: {onion}"
    assert not verify_onion_v3(onion[:-7] + "a.onion")


if __name__ == "__main__":
    self_test()
    print("wallet_codec: all vectors pass")
