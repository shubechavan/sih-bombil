"""
Integrity tests for the synthetic corpus under fixtures/.

The corpus is what Phase 2's linking engine will be measured against, so its
claimed properties have to be checked before anything depends on them. The
important test here is `test_stylometric_signal_is_real`: it asserts that the
personas belonging to one actor genuinely write more like each other than like
anyone else, and it does so twice — once on the full text and once with each
author's greeting and signoff removed.

The ablation matters. Posts are assembled with a fixed greeting and signoff, and
those two strings alone would push intra-actor cosine up without the prose being
any more similar. If the gap only survives with the frame attached, the signal is
an artefact of the generator rather than a property of the writing, and Phase 2
would be tuned against a corpus that cannot support it.

Both runs are hard failures. If the gap ever drops below the margin the fix is to
tune the style profiles in scripts/style_profiles.py and regenerate — not to lower
the constant.

Run `pytest tests/test_fixtures.py` and the similarity report prints either way.
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from db import IDENTIFIER_TYPES, MIN_STYLOMETRY_CHARS  # noqa: E402
from style_profiles import PROFILES, check_profiles_distinct, strip_frame  # noqa: E402
from wallet_codec import (  # noqa: E402
    self_test as codec_self_test,
    verify_base58check,
    verify_bech32,
    verify_btc_legacy,
    verify_eip55,
    verify_ltc_legacy,
    verify_monero,
    verify_onion_v3,
)

FIXTURES = ROOT / "fixtures"
SOURCE_DIRS = ("market_alpha", "forum_beta", "market_gamma")

# ─────────────────────────────────────────────────────────────────────────────
# Thresholds — tune the corpus to meet these, never the other way round
# ─────────────────────────────────────────────────────────────────────────────

#: Minimum (mean intra-actor - mean inter-actor) cosine on the full text.
GAP_MARGIN_FULL = 0.20

#: Minimum gap once each author's greeting and signoff are stripped. Deliberately
#: the same value: the frame is not doing the work, so it does not need a
#: discount.
GAP_MARGIN_ABLATED = 0.20

#: On the full text, every pair of personas belonging to one actor must sit above
#: this percentile of the inter-actor distribution.
POSITIVE_PAIR_PERCENTILE = 0.90

#: Expected corpus size.
EXPECTED_SOURCES = 3
EXPECTED_PERSONAS = 20
EXPECTED_POSTS = 200


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus() -> dict:
    personas, posts = {}, []
    for name in SOURCE_DIRS:
        for persona in _load(FIXTURES / name / "personas.json"):
            personas[persona["id"]] = persona
        posts.extend(_load(FIXTURES / name / "posts.json"))

    texts: dict[int, str] = {}
    for post in posts:
        texts[post["persona_id"]] = texts.get(post["persona_id"], "") + " " + post["body"]

    return {
        "sources": _load(FIXTURES / "sources.json"),
        "personas": personas,
        "posts": posts,
        "texts": {pid: text.strip() for pid, text in texts.items()},
        "ground_truth": _load(FIXTURES / "ground_truth.json"),
        "infra": _load(FIXTURES / "infra_findings.json"),
        "clearnet": _load(FIXTURES / "clearnet_obs" / "shodan_observations.json"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Shape and referential integrity
# ─────────────────────────────────────────────────────────────────────────────

def test_corpus_size(corpus):
    assert len(corpus["sources"]) == EXPECTED_SOURCES
    assert len(corpus["personas"]) == EXPECTED_PERSONAS
    assert len(corpus["posts"]) == EXPECTED_POSTS


def test_ids_are_unique_and_referential(corpus):
    source_ids = {s["id"] for s in corpus["sources"]}
    assert len(source_ids) == len(corpus["sources"]), "duplicate source id"

    post_ids = [p["id"] for p in corpus["posts"]]
    assert len(set(post_ids)) == len(post_ids), "duplicate post id"

    for persona in corpus["personas"].values():
        assert persona["source_id"] in source_ids
    for post in corpus["posts"]:
        assert post["persona_id"] in corpus["personas"]
        assert post["source_id"] in source_ids
        assert post["source_id"] == corpus["personas"][post["persona_id"]]["source_id"], (
            f"post {post['id']} is filed under a different source than its persona"
        )


def test_declared_post_counts_match(corpus):
    actual: dict[int, int] = {}
    for post in corpus["posts"]:
        actual[post["persona_id"]] = actual.get(post["persona_id"], 0) + 1
    for persona in corpus["personas"].values():
        assert actual.get(persona["id"], 0) == persona["post_count"], (
            f"persona {persona['id']} declares {persona['post_count']} posts "
            f"but the corpus holds {actual.get(persona['id'], 0)}"
        )


def test_no_persona_repeats_a_post_verbatim(corpus):
    """`posts` is uniquely indexed on (persona_id, body_hash).

    A persona repeating itself is a generator artefact, and the loader would
    reject the second copy with a unique violation.
    """
    seen: set[tuple[int, str]] = set()
    for post in corpus["posts"]:
        key = (post["persona_id"], post["body"])
        assert key not in seen, (
            f"persona {post['persona_id']} posts an identical body twice "
            f"(post {post['id']})"
        )
        seen.add(key)


def test_identifier_types_are_known(corpus):
    for persona in corpus["personas"].values():
        for identifier in persona["identifiers"]:
            assert identifier["type"] in IDENTIFIER_TYPES, (
                f"persona {persona['id']} uses unknown identifier type "
                f"{identifier['type']!r}; it would fail the CHECK constraint in "
                f"schema_v2.sql"
            )


def test_timestamps_are_ordered(corpus):
    for persona in corpus["personas"].values():
        assert persona["first_seen"] <= persona["last_seen"]
    for post in corpus["posts"]:
        persona = corpus["personas"][post["persona_id"]]
        assert persona["first_seen"] <= post["posted_at"] <= persona["last_seen"]


def test_style_profiles_do_not_collide():
    check_profiles_distinct()


# ─────────────────────────────────────────────────────────────────────────────
# Ground truth
# ─────────────────────────────────────────────────────────────────────────────

def test_ground_truth_resolves_both_ways(corpus):
    truth = corpus["ground_truth"]
    persona_ids = set(corpus["personas"])

    mapped = {int(pid) for pid in truth["persona_to_actor"]}
    assert mapped == persona_ids, "persona_to_actor does not cover every persona"

    from_actors = {pid for a in truth["actors"].values() for pid in a["personas"]}
    assert from_actors == persona_ids, "the actor roster does not cover every persona"

    for actor, entry in truth["actors"].items():
        for pid in entry["personas"]:
            assert truth["persona_to_actor"][str(pid)] == actor, (
                f"persona {pid} is listed under {actor} but maps elsewhere"
            )

    for pid, entry in truth["personas"].items():
        persona = corpus["personas"][int(pid)]
        assert entry["handle"] == persona["handle"]
        assert entry["key"] == persona["key"]


def test_expected_pairs_match_the_actor_clusters(corpus):
    truth = corpus["ground_truth"]
    derived = set()
    for entry in truth["actors"].values():
        for a, b in itertools.combinations(sorted(entry["personas"]), 2):
            derived.add((a, b))
    declared = {tuple(pair) for pair in truth["expected_positive_pairs"]}
    assert declared == derived
    for a, b in declared:
        assert a < b, "pairs must be stored with the lower persona id first"


def test_four_vendors_migrate_sharing_a_hard_identifier(corpus):
    """The headline claim: four alpha vendors reappear on gamma."""
    truth = corpus["ground_truth"]
    migrations = [
        (a, b) for a, b in (tuple(p) for p in truth["expected_positive_pairs"])
        if corpus["personas"][a]["source"] == "market_alpha"
        and corpus["personas"][b]["source"] == "market_gamma"
    ]
    assert len(migrations) == 4, f"expected 4 alpha->gamma migrations, got {len(migrations)}"

    strong = {"pgp_fpr", "btc", "eth", "xmr", "ltc"}
    for a, b in migrations:
        left = {(i["type"], i["value"]) for i in corpus["personas"][a]["identifiers"] if i["valid"]}
        right = {(i["type"], i["value"]) for i in corpus["personas"][b]["identifiers"] if i["valid"]}
        shared = left & right
        assert shared, f"personas {a} and {b} share no identifier at all"
        assert any(kind in strong for kind, _ in shared), (
            f"personas {a} and {b} share only soft identifiers {shared}; the "
            f"migration is supposed to be provable by a PGP key or a wallet"
        )
        assert corpus["personas"][a]["handle"] != corpus["personas"][b]["handle"], (
            "a rebrand means a new handle"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Negative cases — a corpus where everything links proves nothing
# ─────────────────────────────────────────────────────────────────────────────

def test_wallet_checksums_are_real(corpus):
    codec_self_test()

    checked = 0
    for persona in corpus["personas"].values():
        for identifier in persona["identifiers"]:
            kind, value, declared = identifier["type"], identifier["value"], identifier["valid"]
            if kind == "btc":
                actual = verify_bech32(value) if value.startswith("bc1") else verify_btc_legacy(value)
            elif kind == "eth":
                actual = verify_eip55(value)
            elif kind == "ltc":
                actual = verify_ltc_legacy(value)
            elif kind == "xmr":
                actual = verify_monero(value)
            elif kind == "onion_mirror":
                actual = verify_onion_v3(value)
            else:
                continue
            checked += 1
            assert actual == declared, (
                f"persona {persona['id']} declares {kind} {value!r} as "
                f"valid={declared} but the checksum says {actual}"
            )
    assert checked >= 15, f"only {checked} checksummable identifiers in the corpus"


def test_the_broken_wallets_really_are_broken(corpus):
    """CLAUDE.md: a failed checksum is dropped, not stored.

    The corpus has to contain a genuine failure for that path to be testable,
    and it has to be a plausible-looking address rather than obvious garbage.
    """
    truth = corpus["ground_truth"]
    case = next(c for c in truth["refusal_cases"] if c["persona_id"] == 8)
    dropped = case["dropped_values"]
    assert len(dropped) == 2

    persona = corpus["personas"][8]
    invalid = [i for i in persona["identifiers"] if not i["valid"]]
    assert {i["value"] for i in invalid} == set(dropped)

    for identifier in invalid:
        if identifier["type"] == "btc":
            assert not verify_base58check(identifier["value"]), "the corrupted BTC address still validates"
            assert len(identifier["value"]) >= 26, "it should still look like an address"
        if identifier["type"] == "eth":
            assert not verify_eip55(identifier["value"])
            assert identifier["value"].startswith("0x") and len(identifier["value"]) == 42

    assert any(i["valid"] for i in persona["identifiers"]), (
        "CryoVault should keep one good identifier, so the test distinguishes "
        "'dropped the bad ones' from 'dropped the persona'"
    )


def test_the_thin_persona_is_below_the_stylometry_floor(corpus):
    truth = corpus["ground_truth"]
    case = next(c for c in truth["refusal_cases"] if c["persona_id"] == 7)
    assert case["reason"]

    length = len(corpus["texts"][7])
    assert length < MIN_STYLOMETRY_CHARS, (
        f"paperghost has {length} characters; the refusal case needs fewer than "
        f"{MIN_STYLOMETRY_CHARS}"
    )


def test_everyone_else_is_scoreable(corpus):
    """Including both near-misses — a near-miss you cannot score is not a test."""
    for pid, text in corpus["texts"].items():
        if pid == 7:
            continue
        assert len(text) >= MIN_STYLOMETRY_CHARS, (
            f"persona {pid} has {len(text)} characters, under the "
            f"{MIN_STYLOMETRY_CHARS} floor"
        )


def test_near_misses_share_no_identifier(corpus):
    """The near-misses must be separable by evidence, not only by style."""
    for case in corpus["ground_truth"]["hard_negatives"]:
        a, b = case["pair"]
        left = {(i["type"], i["value"]) for i in corpus["personas"][a]["identifiers"]}
        right = {(i["type"], i["value"]) for i in corpus["personas"][b]["identifiers"]}
        assert not (left & right), (
            f"personas {a} and {b} are meant to be a hard negative but share "
            f"{left & right}"
        )
        assert PROFILES[corpus["personas"][a]["actor"]].hours != \
               PROFILES[corpus["personas"][b]["actor"]].hours, (
            f"personas {a} and {b} should differ behaviourally as well"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Identifiers must be findable in prose, not just declared
# ─────────────────────────────────────────────────────────────────────────────

def test_identifiers_appear_in_the_text(corpus):
    """Phase 1's extractor reads text, not the declared list."""
    for persona in corpus["personas"].values():
        if not persona["identifiers"]:
            continue
        haystack = persona["bio"] + " " + corpus["texts"].get(persona["id"], "")
        found = [i["value"] for i in persona["identifiers"] if i["value"] in haystack]
        assert found, (
            f"persona {persona['id']} declares identifiers but none of them appear "
            f"verbatim in its bio or posts, so the extractor has nothing to find"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Recon fixtures
# ─────────────────────────────────────────────────────────────────────────────

def test_recon_fixtures_have_something_to_correlate(corpus):
    findings = {f["onion_url"]: f for f in corpus["infra"]}
    assert len(findings) == EXPECTED_SOURCES

    favicons = [f["favicon_hash"] for f in corpus["infra"] if f["favicon_hash"]]
    shared = {h for h in favicons if favicons.count(h) > 1}
    assert shared, "no two onions share a favicon hash, so the I term is always zero"

    clearnet_favicons = {str(o["http"]["favicon"]["hash"]) for o in corpus["clearnet"]}
    assert shared & clearnet_favicons, (
        "the shared onion favicon hash matches no clearnet observation"
    )

    serials = {f["tls_serial"] for f in corpus["infra"] if f.get("tls_serial")}
    clearnet_serials = {o["ssl"]["cert"]["serial"] for o in corpus["clearnet"] if "ssl" in o}
    assert serials & clearnet_serials, "no TLS serial pivot in the clearnet fixtures"

    assert len(corpus["clearnet"]) >= 8, "too few clearnet records to require discrimination"


# ─────────────────────────────────────────────────────────────────────────────
# The stylometric signal
# ─────────────────────────────────────────────────────────────────────────────

def _similarity(texts: dict[int, str], ids: list[int]):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    matrix = TfidfVectorizer(
        analyzer="char",          # not char_wb: spacing habits are part of the signal
        ngram_range=(3, 5),
        max_features=5000,
        sublinear_tf=True,
    ).fit_transform([texts[pid] for pid in ids])
    return cosine_similarity(matrix)


def _partition(texts: dict[int, str], corpus: dict):
    """Split every persona pair into intra-actor, inter-actor and near-miss."""
    truth = corpus["ground_truth"]
    positives = {tuple(p) for p in truth["expected_positive_pairs"]}
    near_misses = {tuple(c["pair"]) for c in truth["hard_negatives"]}

    # personas below the floor are never scored by Phase 2, so they are not
    # scored here either
    ids = sorted(pid for pid, text in texts.items() if len(text) >= MIN_STYLOMETRY_CHARS)
    scores = _similarity(texts, ids)
    index = {pid: i for i, pid in enumerate(ids)}

    intra, inter, near = [], [], []
    for a, b in itertools.combinations(ids, 2):
        value = float(scores[index[a]][index[b]])
        if (a, b) in positives:
            intra.append(((a, b), value))
        elif (a, b) in near_misses:
            near.append(((a, b), value))
        else:
            inter.append(((a, b), value))
    return intra, inter, near


def _report(label: str, corpus: dict, intra, inter, near) -> tuple[str, float, float]:
    mean_intra = statistics.mean(v for _, v in intra)
    mean_inter = statistics.mean(v for _, v in inter)
    ordered = sorted(v for _, v in inter)
    percentile = ordered[int(POSITIVE_PAIR_PERCENTILE * len(ordered))]
    handles = {pid: p["handle"] for pid, p in corpus["personas"].items()}

    lines = [
        f"",
        f"  {label}",
        f"    mean intra-actor : {mean_intra:.4f}   ({len(intra)} pairs)",
        f"    mean inter-actor : {mean_inter:.4f}   ({len(inter)} pairs, "
        f"near-misses excluded)",
        f"    gap              : {mean_intra - mean_inter:.4f}",
        f"    inter p{int(POSITIVE_PAIR_PERCENTILE * 100)}         : {percentile:.4f}"
        f"   (max inter {ordered[-1]:.4f})",
    ]
    for (a, b), value in sorted(intra):
        lines.append(
            f"      {a:>2}~{b:<2} {handles[a]:>13} ~ {handles[b]:<13} {value:.4f}"
        )
    for (a, b), value in sorted(near):
        lines.append(
            f"      {a:>2}~{b:<2} {handles[a]:>13} ~ {handles[b]:<13} {value:.4f}"
            f"   (designed near-miss)"
        )
    return "\n".join(lines), mean_intra, mean_inter


def test_stylometric_signal_is_real(corpus, capsys):
    """Personas of one actor must write measurably more like each other.

    Asserted twice. The second run strips each author's greeting and signoff, so
    a gap that exists only because two fixed strings repeat cannot pass.
    """
    full = _partition(corpus["texts"], corpus)
    full_report, full_intra, full_inter = _report(
        "FULL TEXT", corpus, *full
    )

    stripped = {
        pid: strip_frame(text, PROFILES[corpus["personas"][pid]["actor"]])
        for pid, text in corpus["texts"].items()
    }
    ablated = _partition(stripped, corpus)
    ablated_report, ab_intra, ab_inter = _report(
        "ABLATION - greeting and signoff removed", corpus, *ablated
    )

    full_gap = full_intra - full_inter
    ablated_gap = ab_intra - ab_inter

    with capsys.disabled():
        print("\n  stylometric separation, char 3-5 gram TF-IDF cosine")
        print(full_report)
        print(ablated_report)
        print(f"\n    full gap {full_gap:.4f}  |  ablated gap {ablated_gap:.4f}  "
              f"|  margins {GAP_MARGIN_FULL} / {GAP_MARGIN_ABLATED}\n")

    assert full_gap >= GAP_MARGIN_FULL, (
        f"intra-actor similarity exceeds inter-actor by only {full_gap:.4f}, "
        f"under the {GAP_MARGIN_FULL} margin. Tune the style profiles in "
        f"scripts/style_profiles.py and regenerate — do not lower this constant."
    )

    assert ablated_gap >= GAP_MARGIN_ABLATED, (
        f"with greetings and signoffs removed the gap collapses to "
        f"{ablated_gap:.4f}, under the {GAP_MARGIN_ABLATED} margin. That means "
        f"the separation came from two repeated strings rather than from the "
        f"prose, and Phase 2 cannot rely on it. Strengthen the orthographic "
        f"habits — misspellings, idiolect, punctuation — and regenerate."
    )

    # On the full text, every actor's personas must stand out individually and
    # not merely on average.
    inter_values = sorted(v for _, v in full[1])
    threshold = inter_values[int(POSITIVE_PAIR_PERCENTILE * len(inter_values))]
    weak = [(pair, v) for pair, v in full[0] if v <= threshold]
    assert not weak, (
        f"these same-actor pairs do not stand out from the inter-actor "
        f"distribution (p{int(POSITIVE_PAIR_PERCENTILE * 100)} = {threshold:.4f}): "
        f"{[(p, round(v, 4)) for p, v in weak]}"
    )

    # After ablation the two cross-source pairs with no shared identifier are
    # legitimately the hardest in the corpus, so the per-pair bar is the
    # inter-actor mean rather than its 90th percentile.
    below = [(pair, v) for pair, v in ablated[0] if v <= ab_inter]
    assert not below, (
        f"after stripping the frame these same-actor pairs fall to or below the "
        f"inter-actor mean ({ab_inter:.4f}): "
        f"{[(p, round(v, 4)) for p, v in below]}"
    )
