"""Regression tests for the transform-only stylometry path behind POST /analyze.

The /analyze endpoint exists to answer one question from a sceptical room: is
this a lookup table? Arbitrary text is pasted in and scored against every stored
writeprint. That is only meaningful if the vector it builds for the pasted text
is produced by *exactly* the engine that built the stored ones — a parallel
implementation that happens to return plausible numbers would be worse than no
endpoint at all.

So the load-bearing test here is fidelity, not plausibility: featurising a
persona's own corpus text against the stored vocabulary must reproduce that
persona's stored vector, and the pairwise S it yields must equal the S the
resolver already wrote. If those hold, /analyze is the same engine by
construction and cannot drift from it.

The 300-character floor applies to pasted text exactly as it applies to persona
7. Below it, `featurise` refuses and names the floor, and the caller scores
without an S term rather than with a number derived from two sentences.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import MIN_STYLOMETRY_CHARS  # noqa: E402
from link import behaviour as behaviour_module  # noqa: E402
from link import stylometry as stylometry_module  # noqa: E402
from link.resolve import load_corpus_from_fixtures  # noqa: E402

#: How close a round-tripped vector must be to the one the fit produced. These
#: are float32 unit vectors of 5197 dimensions; anything below this is not
#: rounding, it is a different feature pipeline.
FIDELITY = 0.9999


@pytest.fixture(scope="module")
def built():
    """The fixture corpus, featurised once by the real `build()`."""
    corpus = load_corpus_from_fixtures()
    texts = corpus.texts()
    identifiers = corpus.identifier_values()
    writeprints = stylometry_module.build(texts, identifiers)
    return {
        "corpus": corpus,
        "texts": texts,
        "identifiers": identifiers,
        "writeprints": writeprints,
    }


# ─────────────────────────────────────────────────────────────────────────────
# The fitted vocabulary is now an artifact, not a local variable
# ─────────────────────────────────────────────────────────────────────────────

def test_build_keeps_the_vocabulary_it_fitted(built):
    """`build()` used to discard the vectoriser. Nothing could transform after."""
    vocabulary = built["writeprints"].vocabulary
    assert vocabulary is not None, "build() did not return its fitted vocabulary"
    assert vocabulary.feature_version == built["writeprints"].feature_version, (
        "the vocabulary must carry the same version as the vectors it produced, "
        "or the two can be stored together and compared apart"
    )
    assert len(vocabulary.terms) == len(vocabulary.idf), (
        "terms and idf are index-aligned; a length mismatch means the column "
        "mapping is wrong and every transformed vector is silently garbage"
    )
    assert len(vocabulary.terms) == stylometry_module.CHAR_MAX_FEATURES
    assert len(set(vocabulary.terms)) == len(vocabulary.terms), "duplicate terms"


def test_a_cache_hit_carries_no_vocabulary(built):
    """Only a real fit produces one. A cache hit must not invent one."""
    cached = stylometry_module.WriteprintSet(
        prints=dict(built["writeprints"].prints),
        feature_version=built["writeprints"].feature_version,
        from_cache=True,
    )
    assert cached.vocabulary is None


# ─────────────────────────────────────────────────────────────────────────────
# Fidelity — the claim that makes the endpoint worth having
# ─────────────────────────────────────────────────────────────────────────────

def test_featurising_a_personas_own_text_reproduces_its_stored_vector(built):
    """The transform-only path is the same pipeline as the fit path.

    This is the anti-hardcoding proof. Every persona's own corpus text, pushed
    back through `featurise()` against the stored vocabulary, must land on the
    vector `build()` produced for it.
    """
    vocabulary = built["writeprints"].vocabulary
    checked = 0

    for persona_id, writeprint in built["writeprints"].prints.items():
        result = stylometry_module.featurise(
            built["texts"][persona_id],
            vocabulary,
            identifiers=built["identifiers"].get(persona_id, ()),
        )
        assert result.writeprint is not None, (
            f"persona {persona_id} cleared the floor during build but was "
            f"refused on the transform path"
        )
        cosine = float(np.dot(result.writeprint.vector, writeprint.vector))
        assert cosine >= FIDELITY, (
            f"persona {persona_id}: transform-only vector differs from the "
            f"fitted one (cosine {cosine:.6f}). /analyze is not running the "
            f"same feature pipeline as link/resolve.py."
        )
        assert result.char_count == writeprint.char_count, (
            f"persona {persona_id}: masking or normalisation differs between "
            f"the two paths ({result.char_count} vs {writeprint.char_count})"
        )
        checked += 1

    assert checked == 19, f"expected 19 scoreable personas, checked {checked}"


def test_pairwise_similarity_matches_the_resolver(built):
    """S computed through /analyze equals S the resolver already wrote."""
    vocabulary = built["writeprints"].vocabulary
    prints = built["writeprints"].prints

    for a, b in ((1, 16), (3, 18), (4, 19), (2, 10), (4, 15), (2, 20)):
        result = stylometry_module.featurise(
            built["texts"][a], vocabulary, identifiers=built["identifiers"].get(a, ())
        )
        assert result.writeprint is not None
        through_analyze = result.writeprint.similarity(prints[b])
        through_resolver = built["writeprints"].similarity(a, b)
        assert through_analyze == pytest.approx(through_resolver, abs=1e-6), (
            f"pair {a}~{b}: /analyze would report S={through_analyze:.6f} where "
            f"the resolver stored S={through_resolver:.6f}"
        )


def test_the_version_stamp_lets_it_compare_against_stored_prints(built):
    """A mismatched stamp makes `similarity()` raise rather than lie."""
    vocabulary = built["writeprints"].vocabulary
    result = stylometry_module.featurise(built["texts"][1], vocabulary)
    assert result.writeprint.feature_version == vocabulary.feature_version

    stale = stylometry_module.Vocabulary(
        feature_version="sty-1:000000000000",
        terms=vocabulary.terms,
        idf=vocabulary.idf,
    )
    mismatched = stylometry_module.featurise(built["texts"][1], stale)
    with pytest.raises(ValueError, match="different extractors"):
        mismatched.writeprint.similarity(built["writeprints"].prints[1])


def test_featurise_does_not_mutate_the_vocabulary(built):
    """Transform only. Pasted text must never widen the fitted vocabulary."""
    vocabulary = built["writeprints"].vocabulary
    terms_before = list(vocabulary.terms)
    idf_before = vocabulary.idf.copy()

    unseen = (
        "Zzyzx qwertyuiop the quick brown fox jumps over the lazy dog while "
        "counting backwards from one hundred in a language nobody here writes. "
    ) * 6
    result = stylometry_module.featurise(unseen, vocabulary)

    assert result.writeprint is not None
    assert vocabulary.terms == terms_before, "featurise widened the vocabulary"
    assert np.array_equal(vocabulary.idf, idf_before), "featurise refitted the idf"
    assert result.writeprint.vector.size == built["writeprints"].prints[1].vector.size


def test_text_nobody_wrote_does_not_match_anybody(built):
    """A sanity floor on the other side: unrelated prose must not confirm."""
    vocabulary = built["writeprints"].vocabulary
    unrelated = (
        "The municipal drainage subcommittee reconvened at half past two to "
        "review the culvert inspection schedule for the lower catchment. "
        "Members noted that the gabion baskets installed last spring have "
        "settled within tolerance and require no further remediation. "
    ) * 3
    result = stylometry_module.featurise(unrelated, vocabulary)
    assert result.writeprint is not None

    scores = [
        result.writeprint.similarity(p) for p in built["writeprints"].prints.values()
    ]
    assert max(scores) < 0.85, (
        f"unrelated prose reached S={max(scores):.3f} against a fixture persona; "
        f"the vocabulary is not discriminating"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The 300-character floor, applied to pasted text
# ─────────────────────────────────────────────────────────────────────────────

def test_short_text_is_refused_with_the_reason(built):
    """Same rule as persona 7, worded for text that has no persona."""
    vocabulary = built["writeprints"].vocabulary
    result = stylometry_module.featurise("still setting up. first listings soon.",
                                         vocabulary)

    assert result.writeprint is None, "stylometry ran below the floor"
    assert result.char_count < MIN_STYLOMETRY_CHARS
    assert result.refused_reason
    assert "300-character floor" in result.refused_reason
    assert "supplied text" in result.refused_reason, (
        "the reason must name what was actually short — it has no persona id"
    )


def test_the_floor_is_applied_after_masking(built):
    """Text that is mostly a wallet address is refused on the prose it has."""
    vocabulary = built["writeprints"].vocabulary
    wallet = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    text = "contact me " + (wallet + " ") * 12

    result = stylometry_module.featurise(text, vocabulary, identifiers=[wallet])
    assert result.writeprint is None, (
        "identifier text padded the length past the floor; masking must run first"
    )
    assert result.masked_chars > 0
    assert "300-character floor" in result.refused_reason


def test_persona_7s_text_is_refused_on_this_path_too(built):
    """The corpus's own refusal case, through the endpoint's code path."""
    vocabulary = built["writeprints"].vocabulary
    result = stylometry_module.featurise(
        built["texts"][7], vocabulary, identifiers=built["identifiers"].get(7, ())
    )
    assert result.writeprint is None
    assert result.char_count == 152, (
        f"persona 7 has 152 characters after masking, not {result.char_count}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Persistence round-trip
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db():
    """A session factory, or skip if Postgres is not up."""
    try:
        from db import require_schema, session_scope  # noqa: PLC0415

        with session_scope() as session:
            require_schema(session)
    except Exception as exc:  # noqa: BLE001 - any connection failure is a skip
        pytest.skip(f"Postgres unavailable: {type(exc).__name__}: {exc}")

    from db import session_scope  # noqa: PLC0415

    return session_scope


def test_the_vocabulary_survives_the_database(built, db):
    """Terms and idf must come back bit-exact, or the vectors mean nothing."""
    vocabulary = built["writeprints"].vocabulary

    with db() as session:
        stylometry_module.store_vocabulary(session, vocabulary)
        session.flush()
        loaded = stylometry_module.load_vocabulary(
            session, vocabulary.feature_version
        )

    assert loaded is not None, "the vocabulary did not come back"
    assert loaded.feature_version == vocabulary.feature_version
    assert loaded.terms == vocabulary.terms, "term order changed in storage"
    assert np.array_equal(loaded.idf, vocabulary.idf), "idf weights changed"


def test_a_vector_built_from_the_stored_vocabulary_is_the_same_vector(built, db):
    """The full path /analyze actually takes: load from Postgres, then transform."""
    vocabulary = built["writeprints"].vocabulary

    with db() as session:
        stylometry_module.store_vocabulary(session, vocabulary)
        session.flush()
        loaded = stylometry_module.load_vocabulary(
            session, vocabulary.feature_version
        )

    from_memory = stylometry_module.featurise(
        built["texts"][1], vocabulary, identifiers=built["identifiers"].get(1, ())
    )
    from_storage = stylometry_module.featurise(
        built["texts"][1], loaded, identifiers=built["identifiers"].get(1, ())
    )

    cosine = float(
        np.dot(from_storage.writeprint.vector, from_memory.writeprint.vector)
    )
    assert cosine >= FIDELITY, (
        f"a vocabulary round-tripped through Postgres produces a different "
        f"vector (cosine {cosine:.6f})"
    )


def test_load_vocabulary_ignores_other_versions(db):
    """Same discipline as `load()`: a different version is absent, not close."""
    with db() as session:
        assert stylometry_module.load_vocabulary(session, "sty-0:deadbeefcafe") is None


# ─────────────────────────────────────────────────────────────────────────────
# B over the sub-signals that were actually measured
# ─────────────────────────────────────────────────────────────────────────────

def test_blend_default_is_the_arithmetic_the_engine_already_used():
    """`omit=()` must be byte-identical to the old expression.

    This is the guard on the refactor. Every existing call site takes the
    default, so if this drifts, every stored B drifts with it.
    """
    parts = {
        "posting_hours": 0.8581920990542636,
        "category": 1.0,
        "trade_vocabulary": 0.4142135623730951,
        "day_of_week": 0.7071067811865476,
    }
    expected = sum(
        behaviour_module.SUBSIGNAL_WEIGHTS[name] * value
        for name, value in parts.items()
    )
    assert behaviour_module.blend(parts) == pytest.approx(expected, abs=1e-12)


def test_existing_pairs_keep_their_b_scores(built):
    """The corpus's own B values, through the refactored path."""
    behaviours = behaviour_module.build(built["corpus"].posts)

    # Measured on this corpus before `blend` existed. If one of these moves,
    # the refactor leaked into the pipeline.
    for (a, b), expected in {
        (1, 16): 0.8581920990542636,
        (1, 9): 0.901969204391682,
        (2, 10): 0.8553799469405856,
        (3, 18): 0.7953147256761104,
        (4, 19): 0.8192586111086938,
    }.items():
        assert behaviours.similarity(a, b) == pytest.approx(expected, abs=1e-9), (
            f"pair {a}~{b}: B moved"
        )


def test_omitting_a_subsignal_redistributes_rather_than_scores_zero():
    """An unsupplied category must not be counted as a category mismatch.

    `_jaccard` of two empty sets is 0.0 — a *measured* zero worth 0.20 of B.
    Pasted text with no category supplied has not been looked at, so its weight
    is redistributed over the sub-signals that were.
    """
    parts = {
        "posting_hours": 0.9,
        "category": 0.0,       # not supplied, not a mismatch
        "trade_vocabulary": 0.5,
        "day_of_week": 0.6,
    }

    naive = behaviour_module.blend(parts)
    honest = behaviour_module.blend(parts, omit=("category",))

    assert honest > naive, (
        "omitting an unmeasured sub-signal must not be worse than scoring it 0"
    )

    remaining = {"posting_hours": 0.70, "trade_vocabulary": 0.05, "day_of_week": 0.05}
    divisor = sum(remaining.values())
    expected = sum(w * parts[k] for k, w in remaining.items()) / divisor
    assert honest == pytest.approx(expected, abs=1e-12)


def test_omitting_everything_is_refused_not_zero():
    """Nothing measured is None, the same answer the rest of the engine gives."""
    parts = {k: 0.5 for k in behaviour_module.SUBSIGNAL_WEIGHTS}
    assert behaviour_module.blend(parts, omit=tuple(parts)) is None


def test_omit_does_not_touch_the_weights_table():
    """The preset is shared module state; renormalising must not mutate it."""
    before = dict(behaviour_module.SUBSIGNAL_WEIGHTS)
    behaviour_module.blend(
        {k: 0.5 for k in before}, omit=("category", "day_of_week")
    )
    assert behaviour_module.SUBSIGNAL_WEIGHTS == before


def test_behaviour_can_be_built_from_timestamps_alone(built):
    """What /analyze does: a pseudo-persona with no database row."""
    from datetime import datetime  # noqa: PLC0415

    pasted = {
        -1: [
            {"posted_at": datetime(2024, 3, 2, 22, 14), "body": "escrow only, "
             "multisig preferred, stealth shipping as always", "category": None},
            {"posted_at": datetime(2024, 3, 3, 23, 41), "body": "", "category": None},
            {"posted_at": datetime(2024, 3, 4, 21, 8), "body": "", "category": None},
        ]
    }
    profiles = behaviour_module.build(pasted)
    assert profiles.get(-1) is not None
    assert profiles.get(-1).post_count == 3
    assert profiles.get(-1).trade_terms, "the pasted body's trade terms were lost"

    merged = behaviour_module.build({**built["corpus"].posts, **pasted})
    parts = merged.components(-1, 1)
    assert parts is not None
    assert merged.similarity(-1, 1, omit=("category",)) is not None
