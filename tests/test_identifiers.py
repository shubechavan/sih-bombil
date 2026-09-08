"""
Phase 1 tests — identifier extraction, normalisation, PGP parsing, GLiNER fallback.

Written before the implementation. The contract these tests encode:

  * The declared `identifiers` list in each fixtures/<source>/personas.json is the
    ORACLE, never an input. extract/identifiers.py sees only prose — the bio, post
    titles and post bodies — and has to re-derive the same values from it.

  * Recall is reported against two denominators, because only one of them is a
    statement about the extractor:

        extractable  = declared identifiers that appear verbatim in that persona's
                       own prose. The extractor is accountable for all of these.
        declared     = every identifier in personas.json. Eight of the 44 appear in
                       no bio and no post (see UNREACHABLE below), so no reader of
                       text can produce them. Counting those as extractor misses
                       would be as dishonest as hiding them.

  * A false positive is anything extracted that is not in the oracle. That number
    must be zero. A wallet regex with no checksum behind it produces dozens.

  * CLAUDE.md: a failed checksum is DROPPED, not stored at low confidence.
    test_persona_8_corrupted_wallets_are_dropped is the one that matters.

Run `pytest tests/test_identifiers.py -q` and the recall table prints either way.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract.gliner_extract import (  # noqa: E402
    extract_entities,
    redact,
    status as gliner_status,
)
from extract.identifiers import (  # noqa: E402
    CONFIDENCE,
    extract_identifiers,
    extract_identifiers_report,
)
from extract.normalize import normalize, normalize_identifier  # noqa: E402
from extract.pgp import (  # noqa: E402
    crc24,
    find_key_blocks,
    normalize_fingerprint,
    parse_key_block,
    parse_key_block_report,
)

FIXTURES = ROOT / "fixtures"
SOURCE_DIRS = ("market_alpha", "forum_beta", "market_gamma")

# ─────────────────────────────────────────────────────────────────────────────
# Expected numbers — assert these, never quietly relax them
# ─────────────────────────────────────────────────────────────────────────────

#: Every identifier declared across the 20 personas.
EXPECTED_DECLARED = 44

#: Those of them that appear verbatim in the persona's own prose.
EXPECTED_EXTRACTABLE = 36

#: A Phase 0 corpus artifact, documented in docs/BUILD_PLAN.md. These are declared
#: with a placeholder context ("declared on the <source> profile of X") and appear
#: in no bio and no post, so text extraction cannot reach them. Two are the
#: deliberately corrupted wallets; the other six are valid and simply absent.
UNREACHABLE = {
    (3, "onion_mirror", "x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspyd.onion"),
    (8, "btc", "1K1gaku9wLHA1C4JYjQL2z1Nz9HovFbHSE"),
    (8, "eth", "0x4daF2Cc326158a523D9B67fA72Cf616343455679"),
    (9, "telegram", "@dread_fam"),
    (10, "telegram", "@nordic_supply"),
    (17, "eth", "0x6e05b5893F34dd1077cae73F8BB39357759FbE4F"),
    (18, "onion_mirror", "x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspyd.onion"),
    (19, "btc", "bc1q3wk97fe0ce3w6p3xxsumxkj57ylhy7rxyrva5y"),
}

#: The two values ground_truth.json marks as refusal cases for persona 8.
CORRUPT_BTC = "1K1gaku9wLHA1C4JYjQL2z1Nz9HovFbHSE"
CORRUPT_ETH = "0x4daF2Cc326158a523D9B67fA72Cf616343455679"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus() -> dict:
    personas: dict[int, dict] = {}
    posts: list[dict] = []
    sources = {s["id"]: s for s in _load(FIXTURES / "sources.json")}

    for name in SOURCE_DIRS:
        for persona in _load(FIXTURES / name / "personas.json"):
            personas[persona["id"]] = persona
        posts.extend(_load(FIXTURES / name / "posts.json"))

    blocks = _load(FIXTURES / "pgp_blocks.json") if (FIXTURES / "pgp_blocks.json").exists() else []
    by_persona: dict[int, list[dict]] = {}
    for block in blocks:
        by_persona.setdefault(block["persona_id"], []).append(block)

    # The haystack is prose only: bio, post titles, post bodies, plus any armoured
    # key block published on the profile. Post `url` fields are deliberately left
    # out — every one of the 200 of them contains its own market's onion, which is
    # structural metadata, not something the vendor wrote.
    haystacks: dict[int, str] = {}
    for pid, persona in personas.items():
        parts = [persona["bio"] or ""]
        parts += [b["armored"] for b in by_persona.get(pid, [])]
        haystacks[pid] = "\n".join(parts)
    for post in posts:
        haystacks[post["persona_id"]] += "\n" + (post.get("title") or "") + "\n" + post["body"]

    return {
        "sources": sources,
        "personas": personas,
        "posts": posts,
        "haystacks": haystacks,
        "pgp_blocks": blocks,
        "pgp_by_persona": by_persona,
        "ground_truth": _load(FIXTURES / "ground_truth.json"),
    }


def _source_onions(corpus: dict, persona: dict) -> tuple[str, ...]:
    """The onions this persona is not allowed to 'mirror' — its own site."""
    url = corpus["sources"][persona["source_id"]]["url"]
    host = url.lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    return (host,)


def _oracle(corpus: dict, pid: int) -> set[tuple[str, str]]:
    """Every (type, value) the extractor is allowed to produce for this persona."""
    persona = corpus["personas"][pid]
    pairs = {(i["type"], i["value"]) for i in persona["identifiers"]}
    for block in corpus["pgp_by_persona"].get(pid, []):
        pairs.add(("pgp_fpr", block["fingerprint"]))
    return pairs


def _extractable(corpus: dict, pid: int) -> set[tuple[str, str]]:
    """The oracle entries that actually appear in this persona's prose."""
    persona = corpus["personas"][pid]
    text = corpus["haystacks"][pid]
    pairs = {
        (i["type"], i["value"])
        for i in persona["identifiers"]
        if i.get("valid", True) and i["value"] in text
    }
    for block in corpus["pgp_by_persona"].get(pid, []):
        pairs.add(("pgp_fpr", block["fingerprint"]))
    return pairs


def _extract_pairs(corpus: dict, pid: int) -> set[tuple[str, str]]:
    persona = corpus["personas"][pid]
    found = extract_identifiers(
        corpus["haystacks"][pid],
        exclude_onions=_source_onions(corpus, persona),
    )
    return {(row["type"], row["value"]) for row in found}


# ─────────────────────────────────────────────────────────────────────────────
# The oracle: recall and false positives
# ─────────────────────────────────────────────────────────────────────────────

def test_corpus_oracle_is_the_shape_we_think(corpus):
    """Guard the denominators. If the corpus changes, this fails before recall does."""
    declared = sum(len(p["identifiers"]) for p in corpus["personas"].values())
    assert declared == EXPECTED_DECLARED

    unreachable = {
        (pid, i["type"], i["value"])
        for pid, p in corpus["personas"].items()
        for i in p["identifiers"]
        if i["value"] not in corpus["haystacks"][pid]
    }
    assert unreachable == UNREACHABLE, (
        "the set of identifiers absent from all prose has changed; update "
        "UNREACHABLE here and the corpus-gap note in docs/BUILD_PLAN.md"
    )


def test_every_in_text_identifier_is_rederived(corpus):
    """The extractor reads prose and must recover every identifier hiding in it."""
    missed: list[str] = []
    total = 0
    for pid in sorted(corpus["personas"]):
        expected = _extractable(corpus, pid)
        total += len(expected)
        for kind, value in sorted(expected - _extract_pairs(corpus, pid)):
            missed.append(
                f"persona {pid} ({corpus['personas'][pid]['handle']}): {kind} {value}"
            )

    assert not missed, "identifiers present in the text but not extracted:\n  " + \
        "\n  ".join(missed)
    assert total == EXPECTED_EXTRACTABLE + len(corpus["pgp_blocks"]), (
        f"expected {EXPECTED_EXTRACTABLE} in-prose declared identifiers plus "
        f"{len(corpus['pgp_blocks'])} key-block fingerprints, counted {total}"
    )


def test_no_false_positives(corpus):
    """Anything outside the oracle poisons the link graph in Phase 2."""
    extra: list[str] = []
    for pid in sorted(corpus["personas"]):
        for kind, value in sorted(_extract_pairs(corpus, pid) - _oracle(corpus, pid)):
            extra.append(
                f"persona {pid} ({corpus['personas'][pid]['handle']}): {kind} {value}"
            )
    assert not extra, "extracted values that nobody declared:\n  " + "\n  ".join(extra)


def test_recall_report(corpus, capsys):
    """Prints the number this phase is judged on. Asserts it too."""
    per_type_hit: dict[str, int] = {}
    per_type_total: dict[str, int] = {}
    hits = extractable = declared_valid = 0
    dropped_rows: list[str] = []

    for pid in sorted(corpus["personas"]):
        persona = corpus["personas"][pid]
        expected = _extractable(corpus, pid)
        got = _extract_pairs(corpus, pid)
        extractable += len(expected)
        hits += len(expected & got)
        declared_valid += sum(1 for i in persona["identifiers"] if i.get("valid", True))
        for kind, _ in expected:
            per_type_total[kind] = per_type_total.get(kind, 0) + 1
        for kind, _ in expected & got:
            per_type_hit[kind] = per_type_hit.get(kind, 0) + 1

        report = extract_identifiers_report(
            corpus["haystacks"][pid], exclude_onions=_source_onions(corpus, persona)
        )
        for row in report.dropped:
            dropped_rows.append(f"persona {pid}: {row.type} {row.value} — {row.reason}")

    declared = sum(len(p["identifiers"]) for p in corpus["personas"].values())
    false_positives = sum(
        len(_extract_pairs(corpus, pid) - _oracle(corpus, pid))
        for pid in corpus["personas"]
    )

    lines = [
        "",
        "  identifier extraction — fixtures corpus",
        "  " + "-" * 62,
        f"    extractable (present in prose) : {hits}/{extractable}"
        f"   = {hits / extractable:6.1%}",
        f"    declared (personas.json)       : {hits - len(corpus['pgp_blocks'])}"
        f"/{declared}   = {(hits - len(corpus['pgp_blocks'])) / declared:6.1%}",
        f"    false positives                : {false_positives}",
        "",
        "    by type                          found / in prose",
    ]
    for kind in sorted(per_type_total):
        lines.append(
            f"      {kind:<14} {per_type_hit.get(kind, 0):>4} / {per_type_total[kind]:<4}"
        )
    lines += [
        "",
        f"    unreachable from any text      : {len(UNREACHABLE)}"
        "   (Phase 0 corpus gap, see docs/BUILD_PLAN.md)",
    ]
    for pid, kind, value in sorted(UNREACHABLE):
        note = "  (corrupted on purpose)" if value in {CORRUPT_BTC, CORRUPT_ETH} else ""
        lines.append(f"      persona {pid:>2}  {kind:<14} {value[:46]}{note}")
    if dropped_rows:
        lines += ["", "    dropped by validation"]
        lines += [f"      {row}" for row in dropped_rows]
    lines.append("")

    with capsys.disabled():
        print("\n".join(lines))

    assert hits == extractable
    assert false_positives == 0


# ─────────────────────────────────────────────────────────────────────────────
# Refusals — a tool that only ever says yes is a tool that is guessing
# ─────────────────────────────────────────────────────────────────────────────

def test_persona_8_corrupted_wallets_are_dropped(corpus):
    """CLAUDE.md: failed checksum = dropped, not stored.

    Neither value appears in CryoVault's prose, so they are fed to the extractor
    in a sentence of the same shape. The good email in that sentence has to
    survive — the test must distinguish "dropped the bad wallets" from "dropped
    the whole line".
    """
    case = next(
        c for c in corpus["ground_truth"]["refusal_cases"] if c["persona_id"] == 8
    )
    assert set(case["dropped_values"]) == {CORRUPT_BTC, CORRUPT_ETH}

    text = (
        "Cryo here heads up, payments go to "
        f"{CORRUPT_BTC} or {CORRUPT_ETH} and reach me at "
        "cryovault@protonmail.com, I check it every evening"
    )
    report = extract_identifiers_report(text)
    kept = {(r.type, r.value) for r in report.kept}
    dropped = {r.value: r.reason for r in report.dropped}

    assert ("btc", CORRUPT_BTC) not in kept
    assert ("eth", CORRUPT_ETH) not in kept
    assert not any(v in {CORRUPT_BTC, CORRUPT_ETH} for _, v in kept), (
        "a corrupted wallet was stored under some other type"
    )

    assert CORRUPT_BTC in dropped and "checksum" in dropped[CORRUPT_BTC].lower()
    assert CORRUPT_ETH in dropped and "checksum" in dropped[CORRUPT_ETH].lower()

    assert ("email", "cryovault@protonmail.com") in kept, (
        "the good identifier in the same sentence was collateral damage"
    )


def test_unchecksummed_eth_is_rejected(corpus):
    """An all-lowercase 0x address carries no EIP-55 checksum, so it is a guess."""
    good = "0x4D8AC0817b59D528e51F3BA5E743751828792c4d"
    text = f"pay {good.lower()} or {good.upper().replace('0X', '0x')} today"
    kept = {(r["type"], r["value"]) for r in extract_identifiers(text)}
    assert not [v for _, v in kept if v.lower() == good.lower()]

    assert ("eth", good) in {
        (r["type"], r["value"]) for r in extract_identifiers(f"pay {good} today")
    }


def test_own_source_onion_is_not_a_mirror(corpus):
    """A vendor quoting the market they are posting on is not a mirror."""
    persona = corpus["personas"][1]
    own = _source_onions(corpus, persona)[0]
    other = "x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspyd.onion"
    text = f"find me at http://{own}/vendor/Dr3adPirat3 or on the mirror {other}"

    kept = {(r["type"], r["value"]) for r in extract_identifiers(text, exclude_onions=(own,))}
    assert ("onion_mirror", other) in kept
    assert ("onion_mirror", own) not in kept

    # Without the exclusion it is a perfectly good onion — the rule is context,
    # not validation.
    assert ("onion_mirror", own) in {
        (r["type"], r["value"]) for r in extract_identifiers(text)
    }


def test_invalid_onion_checksum_is_dropped():
    bad = "x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspya.onion"
    report = extract_identifiers_report(f"our mirror is {bad}")
    assert not [r for r in report.kept if r.type == "onion_mirror"]
    assert any(r.value.startswith("x6bdjzta") for r in report.dropped)


def test_email_does_not_yield_a_telegram_handle():
    kept = extract_identifiers("write to nordicpharm.supply@protonmail.com only")
    types = {r["type"] for r in kept}
    assert "email" in types
    assert "telegram" not in types


def test_jabber_and_email_are_told_apart():
    kept = {
        (r["type"], r["value"])
        for r in extract_identifiers(
            "xmpp dreadpirate@jabber.calyxinstitute.net and graypigeon@xmpp.jp, "
            "mail mtl.broker@mail2tor.com"
        )
    }
    assert ("jabber", "dreadpirate@jabber.calyxinstitute.net") in kept
    assert ("jabber", "graypigeon@xmpp.jp") in kept
    assert ("email", "mtl.broker@mail2tor.com") in kept
    assert ("email", "dreadpirate@jabber.calyxinstitute.net") not in kept


def test_session_id_does_not_yield_a_fingerprint():
    session = "050b75c0a5f0ebcb85b03d03b86eb5c33a8f745893622912cef586489150e4b8b4"
    kept = {(r["type"], r["value"]) for r in extract_identifiers(f"reach me at {session}")}
    assert ("session", session) in kept
    assert not [v for k, v in kept if k == "pgp_fpr"]


def test_eth_address_does_not_yield_a_fingerprint():
    eth = "0x4D8AC0817b59D528e51F3BA5E743751828792c4d"
    kept = {(r["type"], r["value"]) for r in extract_identifiers(f"send to {eth}")}
    assert ("eth", eth) in kept
    assert not [v for k, v in kept if k == "pgp_fpr"]


def test_spaced_fingerprint_is_canonicalised():
    spaced = "CE58 8316 E131 A327 E4F1  AB41 8BEE 1D17 E258 2FE2"
    kept = {(r["type"], r["value"]) for r in extract_identifiers(f"key {spaced} signed")}
    assert ("pgp_fpr", "CE588316E131A327E4F1AB418BEE1D17E2582FE2") in kept


def test_every_kept_row_has_the_contract_shape():
    text = (
        "pgp CE588316E131A327E4F1AB418BEE1D17E2582FE2 btc "
        "bc1q5c8pyjdf4737dwd7tzklxu8tepcc0ytxrj7r3r jabber "
        "dreadpirate@jabber.calyxinstitute.net"
    )
    rows = extract_identifiers(text)
    assert rows
    for row in rows:
        assert set(row) >= {"type", "value", "raw_context", "confidence"}
        assert row["value"] in text or row["type"] == "pgp_fpr"
        assert row["value"] in row["raw_context"] or row["type"] == "pgp_fpr"
        assert 0.0 < row["confidence"] <= 1.0
        assert row["confidence"] == CONFIDENCE[row["type"]]


def test_extraction_is_deduplicated():
    addr = "bc1q5c8pyjdf4737dwd7tzklxu8tepcc0ytxrj7r3r"
    rows = extract_identifiers(f"pay {addr} — again, {addr}. always {addr}")
    assert len([r for r in rows if r["value"] == addr]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation
# ─────────────────────────────────────────────────────────────────────────────

def test_the_headline_case():
    assert normalize("Dr3ad_P1rat3") == normalize("dreadpirate") == "dreadpirate"


@pytest.mark.parametrize("left,right", [
    ("Dr3ad_P1rat3", "Dread.Pirate"),
    ("V3ct0r_Supply", "vector supply"),
    ("silk_hands", "S1LK-HANDS"),
    ("N0rd1cPh4rm", "nordicpharm"),
])
def test_obfuscated_handles_collide(left, right):
    assert normalize(left) == normalize(right)


def test_unicode_is_folded():
    assert normalize("Dréád Pirate") == "dreadpirate"
    assert normalize(unicodedata.normalize("NFD", "Dréád_Pirate")) == "dreadpirate"
    assert normalize("ＤＲＥＡＤpirate") == "dreadpirate"


def test_normalisation_does_not_collapse_distinct_handles(corpus):
    """Folding hard enough to link everyone links no one."""
    normalised: dict[str, list[str]] = {}
    for persona in corpus["personas"].values():
        normalised.setdefault(normalize(persona["handle"]), []).append(persona["handle"])

    for value, handles in normalised.items():
        actors = {
            corpus["personas"][pid]["actor"]
            for pid, p in corpus["personas"].items()
            if normalize(p["handle"]) == value
        }
        assert len(actors) == 1, (
            f"normalised handle {value!r} spans actors {actors} via {handles}"
        )


def test_matches_ground_truth_handles(corpus):
    """Offline check against the answer key's recorded normal forms."""
    for pid, entry in corpus["ground_truth"]["personas"].items():
        assert normalize(entry["handle"]) == entry["handle_normalized"], (
            f"persona {pid}: normalize({entry['handle']!r}) != "
            f"{entry['handle_normalized']!r}"
        )


def test_matches_database_handle_normalized():
    """The values already written by Phase 0's loader must not move.

    personas.handle_normalized is indexed and Phase 2 joins on it, so a change in
    the folding rules is a silent data migration. Skipped, loudly, when Postgres
    is not running.
    """
    try:
        from sqlalchemy import select

        from db import Persona, session_scope

        with session_scope() as session:
            rows = session.execute(
                select(Persona.id, Persona.handle, Persona.handle_normalized)
                .order_by(Persona.id)
            ).all()
    except Exception as exc:  # noqa: BLE001 - any connection failure is a skip
        pytest.skip(f"Postgres unavailable: {type(exc).__name__}: {exc}")

    if not rows:
        pytest.skip("personas table is empty — run scripts/load_fixtures.py first")

    drift = [
        f"persona {pid} {handle!r}: db has {stored!r}, normalize() gives "
        f"{normalize(handle)!r}"
        for pid, handle, stored in rows
        if normalize(handle) != stored
    ]
    assert not drift, "extract/normalize.py disagrees with the database:\n  " + \
        "\n  ".join(drift)


@pytest.mark.parametrize("kind,value,expected", [
    ("pgp_fpr", "ce58 8316 E131A327E4F1AB418BEE1D17E2582FE2", "ce588316e131a327e4f1ab418bee1d17e2582fe2"),
    ("eth", "0x4D8AC0817b59D528e51F3BA5E743751828792c4d", "0x4d8ac0817b59d528e51f3ba5e743751828792c4d"),
    ("email", "Quiet.Courier@Tutanota.com", "quiet.courier@tutanota.com"),
    ("telegram", "@SilkHands", "silkhands"),
    ("onion_mirror", "http://X6BDJZTA.onion/mirror", "x6bdjzta.onion"),
    ("btc", "bc1Q5C8PYJDF", "bc1q5c8pyjdf"),
    ("btc", "1PkWTVLPskQTBRrK7FUnRLVGB5u8g4yNzp", "1PkWTVLPskQTBRrK7FUnRLVGB5u8g4yNzp"),
    ("xmr", "45kz1FpMcpgM4ogM", "45kz1FpMcpgM4ogM"),
])
def test_identifier_comparison_forms(kind, value, expected):
    """Case-sensitivity per type: base58 carries meaning in its casing, ETH does not."""
    assert normalize_identifier(kind, value) == expected


# ─────────────────────────────────────────────────────────────────────────────
# PGP
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def blocks(corpus) -> list[dict]:
    if not corpus["pgp_blocks"]:
        pytest.skip("fixtures/pgp_blocks.json missing — run scripts/gen_pgp_blocks.py")
    return corpus["pgp_blocks"]


def test_crc24_matches_the_rfc_vector():
    # RFC 4880 §6.1 initialises to 0xB704CE; the empty message keeps it.
    assert crc24(b"") == 0xB704CE


def test_key_block_parses_to_fingerprint_keyid_uid(blocks):
    for block in blocks:
        key = parse_key_block(block["armored"])
        assert key is not None, f"failed to parse the block for persona {block['persona_id']}"
        assert key.fingerprint == block["fingerprint"]
        assert key.key_id == block["key_id"]
        assert key.key_id == key.fingerprint[-16:]
        assert block["uid"] in key.uids
        assert key.version == 4


def test_shared_key_blocks_agree(corpus, blocks):
    """The two personas of one actor publish one key, so one fingerprint."""
    by_fpr: dict[str, set[int]] = {}
    for block in blocks:
        by_fpr.setdefault(block["fingerprint"], set()).add(block["persona_id"])
    for fingerprint, personas in by_fpr.items():
        actors = {corpus["personas"][pid]["actor"] for pid in personas}
        assert len(actors) == 1, (
            f"key {fingerprint} is shared across actors {actors} — that would "
            f"manufacture a link the ground truth does not claim"
        )


def test_corrupt_armour_is_rejected(blocks):
    armored = blocks[0]["armored"]
    lines = armored.splitlines()
    body = next(i for i, line in enumerate(lines) if line and not line.startswith("-----")
                and "=" not in line[:1] and len(line) > 20)
    ch = lines[body][10]
    lines[body] = lines[body][:10] + ("A" if ch != "A" else "B") + lines[body][11:]
    corrupted = "\n".join(lines)

    key, reason = parse_key_block_report(corrupted)
    assert key is None, "a block whose CRC-24 does not match was parsed anyway"
    assert reason and ("crc" in reason.lower() or "checksum" in reason.lower())


def test_truncated_block_returns_none(blocks):
    armored = blocks[0]["armored"]
    key, reason = parse_key_block_report(armored[: len(armored) // 2])
    assert key is None
    assert reason


def test_find_key_blocks_locates_them_in_prose(blocks):
    armored = blocks[0]["armored"]
    text = f"here is my key\n\n{armored}\n\nverify before you send funds"
    found = find_key_blocks(text)
    assert len(found) == 1
    assert parse_key_block(found[0]).fingerprint == blocks[0]["fingerprint"]


def test_key_block_reaches_the_identifier_extractor(blocks):
    block = blocks[0]
    rows = extract_identifiers(f"my key:\n{block['armored']}\nverify it")
    pgp = [r for r in rows if r["type"] == "pgp_fpr"]
    assert len(pgp) == 1
    assert pgp[0]["value"] == block["fingerprint"]
    assert pgp[0]["meta"]["key_id"] == block["key_id"]
    assert block["uid"] in pgp[0]["meta"]["uids"]


def test_normalize_fingerprint():
    assert normalize_fingerprint("ce58 8316 e131 a327 e4f1 ab41 8bee 1d17 e258 2fe2") == \
        "CE588316E131A327E4F1AB418BEE1D17E2582FE2"


# ─────────────────────────────────────────────────────────────────────────────
# GLiNER — the regex path must not depend on it
# ─────────────────────────────────────────────────────────────────────────────

def test_gliner_status_is_honest():
    state = gliner_status()
    assert set(state) >= {"installed", "loaded", "device", "reason"}
    assert isinstance(state["installed"], bool)
    if not state["installed"]:
        assert state["loaded"] is False
        assert state["reason"], "an unavailable model has to say why"


def test_extract_entities_degrades_to_empty():
    """No model, no exception, no invented entities."""
    entities = extract_entities("Dread Pirate emails dreadpirate@jabber.calyxinstitute.net")
    assert isinstance(entities, list)
    if not gliner_status()["installed"]:
        assert entities == []


def test_redact_works_without_the_model():
    text = "reach quiet.courier@tutanota.com or pay 1PkWTVLPskQTBRrK7FUnRLVGB5u8g4yNzp"
    clean, removed = redact(text)
    assert "quiet.courier@tutanota.com" not in clean
    assert "1PkWTVLPskQTBRrK7FUnRLVGB5u8g4yNzp" not in clean
    assert removed


def test_regex_extraction_needs_no_model(corpus):
    """The headline guarantee: full recall with GLiNER absent."""
    assert not gliner_status()["loaded"]
    for pid in sorted(corpus["personas"]):
        assert not (_extractable(corpus, pid) - _extract_pairs(corpus, pid))
