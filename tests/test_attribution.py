"""Unit tests for score/attribution.py — written before the implementation.

The formula is the one thing in this project that every other number depends on,
so it is tested in isolation from the database, the corpus and the extractors.

Three properties matter more than the arithmetic:

  * an *unmeasured* component is not a component measured as zero. Phase 2 has no
    recon, so I is unmeasured for every pair; counting it as 0.0 silently caps
    every score at 0.85 and makes CONFIRMED unreachable.
  * a score must be able to explain which components produced it, including the
    ones that did not.
  * transitive inference must never hand a derived pair the confidence of the
    direct edges it was derived from.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from score.attribution import (  # noqa: E402
    BAND_ORDER,
    DEFAULT_PRESET,
    TRANSITIVE_DECAY,
    WEIGHT_PRESETS,
    Attribution,
    attribution_score,
    band_for,
    demote,
    hard_identifier_score,
    transitive_score,
    weights_for,
)

MEASURED = weights_for("measured")
CLAUDE_MD = weights_for("claude_md")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

def test_every_preset_is_a_convex_combination():
    """Weights that do not sum to 1 make the bands mean something different."""
    for name, weights in WEIGHT_PRESETS.items():
        assert set(weights) == {"H", "S", "B", "I"}, f"{name} has the wrong components"
        assert sum(weights.values()) == pytest.approx(1.0), (
            f"preset {name} sums to {sum(weights.values())}, not 1.0"
        )
        assert all(w > 0 for w in weights.values()), f"{name} has a non-positive weight"


def test_claude_md_preset_matches_the_documented_formula():
    """A = 0.40H + 0.25S + 0.20B + 0.15I, exactly as written in CLAUDE.md."""
    assert CLAUDE_MD == {"H": 0.40, "S": 0.25, "B": 0.20, "I": 0.15}


def test_measured_preset_only_swaps_s_and_b():
    """The measured preset is a tuning of CLAUDE.md, not a different formula."""
    assert MEASURED == {"H": 0.40, "S": 0.20, "B": 0.25, "I": 0.15}
    assert MEASURED["H"] == CLAUDE_MD["H"]
    assert MEASURED["I"] == CLAUDE_MD["I"]
    assert MEASURED["S"] + MEASURED["B"] == pytest.approx(CLAUDE_MD["S"] + CLAUDE_MD["B"])


def test_default_preset_is_a_real_preset():
    assert DEFAULT_PRESET in WEIGHT_PRESETS


def test_unknown_preset_is_rejected_by_name():
    with pytest.raises(KeyError, match="tuned_by_vibes"):
        weights_for("tuned_by_vibes")


# ─────────────────────────────────────────────────────────────────────────────
# Bands
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("score, band", [
    (1.00, "CONFIRMED"),
    (0.85, "CONFIRMED"),   # inclusive lower bound
    (0.8499, "PROBABLE"),
    (0.65, "PROBABLE"),
    (0.6499, "POSSIBLE"),
    (0.45, "POSSIBLE"),
    (0.4499, "WEAK"),
    (0.00, "WEAK"),
])
def test_band_boundaries_are_inclusive_lower_bounds(score, band):
    assert band_for(score) == band


def test_band_order_runs_strongest_to_weakest():
    assert BAND_ORDER == ("CONFIRMED", "PROBABLE", "POSSIBLE", "WEAK")


@pytest.mark.parametrize("band, weaker", [
    ("CONFIRMED", "PROBABLE"),
    ("PROBABLE", "POSSIBLE"),
    ("POSSIBLE", "WEAK"),
    ("WEAK", "WEAK"),      # nothing below the floor
])
def test_demote_steps_down_exactly_one_band(band, weaker):
    assert demote(band) == weaker


# ─────────────────────────────────────────────────────────────────────────────
# The formula
# ─────────────────────────────────────────────────────────────────────────────

def test_all_components_measured_is_the_plain_weighted_sum():
    result = attribution_score(h=1.0, s=0.8, b=0.9, i=0.5, preset="measured")
    # 0.40*1.0 + 0.20*0.8 + 0.25*0.9 + 0.15*0.5
    assert result.score == pytest.approx(0.40 + 0.16 + 0.225 + 0.075)
    assert result.score == pytest.approx(0.86)
    assert result.band == "CONFIRMED"
    assert result.renormalised is False
    assert result.unmeasured == ()


def test_a_perfect_pair_scores_one_and_an_empty_pair_scores_zero():
    assert attribution_score(h=1, s=1, b=1, i=1).score == pytest.approx(1.0)
    assert attribution_score(h=0, s=0, b=0, i=0).score == pytest.approx(0.0)


def test_the_two_presets_disagree_only_when_s_and_b_differ():
    same = dict(h=0.9, s=0.7, b=0.7, i=0.2)
    assert (attribution_score(**same, preset="measured").score
            == pytest.approx(attribution_score(**same, preset="claude_md").score))

    differ = dict(h=0.9, s=0.4, b=0.9, i=0.2)
    measured = attribution_score(**differ, preset="measured").score
    claude = attribution_score(**differ, preset="claude_md").score
    # B is worth more under the measured preset, and B is the stronger here
    assert measured > claude


def test_component_values_outside_the_unit_interval_are_rejected():
    for bad in (-0.01, 1.01, 2.0):
        with pytest.raises(ValueError, match="0..1"):
            attribution_score(h=bad, s=0.5, b=0.5, i=0.5)


def test_a_score_never_leaves_the_unit_interval():
    for h in (0.0, 0.5, 1.0):
        for s in (0.0, 1.0):
            result = attribution_score(h=h, s=s, b=1.0, i=None)
            assert 0.0 <= result.score <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Unmeasured components — the distinction the whole phase turns on
# ─────────────────────────────────────────────────────────────────────────────

def test_unmeasured_infrastructure_renormalises_over_what_was_measured():
    result = attribution_score(h=1.0, s=0.8, b=0.9, i=None, preset="measured")
    # (0.40*1.0 + 0.20*0.8 + 0.25*0.9) / (0.40 + 0.20 + 0.25)
    assert result.score == pytest.approx(0.785 / 0.85)
    assert result.renormalised is True
    assert result.unmeasured == ("I",)


def test_counting_unmeasured_infrastructure_as_zero_caps_the_score_at_085():
    """Why renormalisation exists: without it CONFIRMED is unreachable.

    A pair sharing a PGP fingerprint with near-perfect style and behaviour is
    the strongest evidence this phase can produce. Treating the missing I as a
    measured 0.0 still denies it the top band.
    """
    raw = attribution_score(h=1.0, s=1.0, b=1.0, i=None, renormalise=False)
    assert raw.score == pytest.approx(0.85)
    assert raw.band == "CONFIRMED"

    realistic = attribution_score(h=1.0, s=0.85, b=0.87, i=None, renormalise=False)
    assert realistic.band == "PROBABLE", "a shared PGP key should not top out here"

    renormalised = attribution_score(h=1.0, s=0.85, b=0.87, i=None, renormalise=True)
    assert renormalised.band == "CONFIRMED"


def test_measured_zero_is_not_the_same_as_unmeasured():
    measured_zero = attribution_score(h=0.9, s=0.5, b=0.5, i=0.0)
    unmeasured = attribution_score(h=0.9, s=0.5, b=0.5, i=None)
    assert unmeasured.score > measured_zero.score
    assert measured_zero.renormalised is False
    assert measured_zero.unmeasured == ()


def test_unmeasured_stylometry_renormalises_the_same_way():
    result = attribution_score(h=0.85, s=None, b=0.8, i=None, preset="measured")
    assert result.unmeasured == ("S", "I")
    assert result.score == pytest.approx((0.40 * 0.85 + 0.25 * 0.8) / 0.65)


def test_a_pair_with_nothing_measured_is_refused():
    with pytest.raises(ValueError, match="no component"):
        attribution_score(h=None, s=None, b=None, i=None)


# ─────────────────────────────────────────────────────────────────────────────
# Explainability — a score with no reasons is not shippable (CLAUDE.md)
# ─────────────────────────────────────────────────────────────────────────────

def test_every_result_carries_its_components_and_weights():
    result = attribution_score(h=1.0, s=0.8, b=0.9, i=None)
    assert isinstance(result, Attribution)
    assert result.components == {"H": 1.0, "S": 0.8, "B": 0.9, "I": None}
    assert result.weights == MEASURED


def test_unmeasured_infrastructure_says_so_in_the_evidence_list():
    """An analyst reading a CONFIRMED row must see which components contributed."""
    result = attribution_score(h=1.0, s=0.9, b=0.9, i=None)
    assert result.band == "CONFIRMED"

    notes = [e for e in result.evidence if e["type"] == "component_not_assessed"]
    assert len(notes) == 1
    note = notes[0]
    assert note["component"] == "I"
    assert "infrastructure not assessed" in note["detail"].lower()
    # the reader has to be told the weight moved, not just that I was missing
    assert "0.15" in note["detail"]


def test_unmeasured_stylometry_says_so_and_can_carry_a_caller_reason():
    result = attribution_score(
        h=0.85, s=None, b=0.8, i=None,
        reasons={"S": "paperghost has 99 characters of text, below the 300-character floor"},
    )
    notes = {e["component"]: e for e in result.evidence
             if e["type"] == "component_not_assessed"}
    assert set(notes) == {"S", "I"}
    assert "99 characters" in notes["S"]["detail"]
    assert "300-character floor" in notes["S"]["detail"]


def test_no_not_assessed_note_when_everything_was_measured():
    result = attribution_score(h=1.0, s=0.8, b=0.9, i=0.4)
    assert [e for e in result.evidence if e["type"] == "component_not_assessed"] == []


def test_evidence_entries_have_the_shape_the_links_table_stores():
    result = attribution_score(h=1.0, s=0.8, b=0.9, i=None)
    assert result.evidence, "evidence is never empty"
    for entry in result.evidence:
        assert {"type", "detail"} <= set(entry)
        assert isinstance(entry["type"], str) and entry["type"]
        assert isinstance(entry["detail"], str) and entry["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# H — hard identifier overlap
# ─────────────────────────────────────────────────────────────────────────────

def test_no_shared_identifiers_is_zero_not_unmeasured():
    """We looked and found none. That is a measurement."""
    assert hard_identifier_score([]) == 0.0


def test_a_single_shared_identifier_scores_its_own_weight():
    assert hard_identifier_score([0.90]) == pytest.approx(0.90)


def test_two_shared_identifiers_combine_by_noisy_or_and_never_exceed_one():
    # a shared jabber id (0.85) plus a normalised handle collision (0.45)
    assert hard_identifier_score([0.85, 0.45]) == pytest.approx(1 - 0.15 * 0.55)
    assert hard_identifier_score([0.85, 0.45]) == pytest.approx(0.9175)
    # summing would give 1.30; the result stays inside the unit interval
    assert hard_identifier_score([0.9, 0.9, 0.9, 0.9]) < 1.0


def test_a_certain_identifier_saturates_the_term():
    assert hard_identifier_score([1.00]) == pytest.approx(1.0)
    assert hard_identifier_score([1.00, 0.45]) == pytest.approx(1.0)


def test_noisy_or_is_order_independent():
    assert (hard_identifier_score([0.85, 0.45, 0.60])
            == pytest.approx(hard_identifier_score([0.60, 0.85, 0.45])))


def test_more_evidence_never_lowers_the_hard_score():
    base = hard_identifier_score([0.60])
    assert hard_identifier_score([0.60, 0.45]) >= base


# ─────────────────────────────────────────────────────────────────────────────
# Transitive inference
# ─────────────────────────────────────────────────────────────────────────────

def test_two_probable_edges_do_not_make_a_probable_link():
    """The rule this phase was asked to state explicitly, as arithmetic."""
    derived = transitive_score([0.846, 0.911])
    assert derived.score == pytest.approx(0.846 * TRANSITIVE_DECAY)
    assert derived.band == "POSSIBLE"
    assert band_for(0.846) == "PROBABLE"


def test_two_confirmed_edges_do_not_make_a_confirmed_link():
    derived = transitive_score([0.95, 0.97])
    assert derived.band != "CONFIRMED"
    assert derived.band == "PROBABLE"


def test_a_derived_score_is_governed_by_the_weakest_edge():
    assert (transitive_score([0.70, 0.99]).score
            == pytest.approx(transitive_score([0.70, 0.72]).score))


def test_each_extra_hop_decays_again():
    two = transitive_score([0.9, 0.9]).score
    three = transitive_score([0.9, 0.9, 0.9]).score
    assert three == pytest.approx(0.9 * TRANSITIVE_DECAY ** 2)
    assert three < two


def test_a_derived_band_never_matches_the_weakest_edge_on_the_path():
    for weakest in (0.46, 0.55, 0.66, 0.75, 0.86, 0.95, 1.0):
        derived = transitive_score([weakest, 1.0])
        direct = band_for(weakest)
        assert BAND_ORDER.index(derived.band) > BAND_ORDER.index(direct), (
            f"a path through a {direct} edge produced a {derived.band} link"
        )


def test_the_band_cap_binds_even_if_decay_alone_would_not():
    """Decay and the one-band cap are both applied; the stricter one wins."""
    derived = transitive_score([1.0, 1.0], decay=0.99)
    assert derived.score == pytest.approx(0.99)
    assert derived.band == "PROBABLE", "the cap must hold even at a negligible decay"


def test_a_single_edge_is_not_a_transitive_inference():
    with pytest.raises(ValueError, match="at least two"):
        transitive_score([0.9])


def test_a_derived_result_explains_that_it_was_derived():
    derived = transitive_score([0.846, 0.911])
    kinds = {e["type"] for e in derived.evidence}
    assert "transitive_inference" in kinds
    note = next(e for e in derived.evidence if e["type"] == "transitive_inference")
    assert "0.75" in note["detail"] or "decay" in note["detail"].lower()
