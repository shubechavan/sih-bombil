"""Regression tests for link/ — stylometry, behaviour, resolution and the graph.

These run against the real fixture corpus with no database, so they hold the
whole Phase 2 pipeline to the claims in fixtures/ground_truth.json:

  * the two designed near-misses stay below their declared ceiling
  * persona 7 is refused rather than scored from 152 characters
  * the six pairs with hard evidence are found
  * transitive inference never hands a derived pair the confidence of the
    direct edges it came from

If one of these fails the engine regressed, not the test. Retune the weights in
score/attribution.py or the sub-signals in link/behaviour.py — do not relax the
assertion.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import MIN_STYLOMETRY_CHARS  # noqa: E402
from link import behaviour as behaviour_module  # noqa: E402
from link import graph as graph_module  # noqa: E402
from link import stylometry as stylometry_module  # noqa: E402
from link.resolve import load_corpus_from_fixtures, resolve_pairs  # noqa: E402
from score.attribution import BAND_ORDER  # noqa: E402

TRUTH = json.loads(
    (ROOT / "fixtures" / "ground_truth.json").read_text(encoding="utf-8")
)
POSITIVES = {tuple(p) for p in TRUTH["expected_positive_pairs"]}
HARD_NEGATIVES = {tuple(c["pair"]): c for c in TRUTH["hard_negatives"]}


@pytest.fixture(scope="module")
def engine():
    """The whole pipeline, run once."""
    corpus = load_corpus_from_fixtures()
    results, writeprints, behaviours, infra = resolve_pairs(corpus)
    return {
        "corpus": corpus,
        "results": results,
        "by_pair": {r.pair: r for r in results},
        "writeprints": writeprints,
        "behaviours": behaviours,
        "infra": infra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stylometry
# ─────────────────────────────────────────────────────────────────────────────

def test_the_thin_persona_is_refused_not_scored(engine):
    """ground_truth.json: paperghost must return None, not a number."""
    writeprints = engine["writeprints"]
    assert 7 not in writeprints, "persona 7 should have no writeprint"
    assert 7 in writeprints.refused
    assert writeprints.refused[7] < MIN_STYLOMETRY_CHARS

    reason = writeprints.refusal_reason(7)
    assert reason and "300-character floor" in reason


def test_every_other_persona_is_scoreable(engine):
    writeprints = engine["writeprints"]
    expected = set(engine["corpus"].personas) - {7}
    assert set(writeprints.prints) == expected


def test_pairs_involving_the_thin_persona_have_s_unmeasured(engine):
    for pair, result in engine["by_pair"].items():
        if 7 in pair:
            assert result.attribution.components["S"] is None
            assert "S" in result.attribution.unmeasured


def test_identifiers_are_masked_out_of_the_writeprint():
    """S must not re-encode H: a shared wallet is not a shared writing habit."""
    wallet = "bc1q5c8pyjdf4737dwd7tzklxu8tepcc0ytxrj7r3r"
    text = f"send payment to {wallet} and wait for confirmation"
    masked = stylometry_module.mask_identifiers(text, [wallet])
    assert wallet not in masked

    # and without being told the value, the structural pattern still catches it
    assert wallet not in stylometry_module.mask_identifiers(text)


def test_pgp_fingerprints_and_onions_are_masked_structurally():
    fingerprint = "A4F57ACBFD98FEB663038D0CA51FDFBD1EDC302F"
    onion = "x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspyd.onion"
    masked = stylometry_module.mask_identifiers(f"key {fingerprint} mirror {onion}")
    assert fingerprint not in masked
    assert onion not in masked


def test_similarity_is_symmetric_and_bounded(engine):
    writeprints = engine["writeprints"]
    ids = sorted(writeprints.prints)[:6]
    for a, b in itertools.combinations(ids, 2):
        forward = writeprints.similarity(a, b)
        assert forward == pytest.approx(writeprints.similarity(b, a))
        assert 0.0 <= forward <= 1.0


def test_a_persona_is_maximally_similar_to_itself(engine):
    writeprints = engine["writeprints"]
    persona_id = sorted(writeprints.prints)[0]
    assert writeprints.similarity(persona_id, persona_id) == pytest.approx(1.0, abs=1e-6)


def test_writeprints_from_different_extractors_refuse_to_compare(engine):
    """The feature_version guard: a stale cached vector must not be trusted."""
    import dataclasses

    writeprints = engine["writeprints"]
    a, b = sorted(writeprints.prints)[:2]
    stale = dataclasses.replace(writeprints.get(a), feature_version="sty-0:deadbeef")
    with pytest.raises(ValueError, match="different extractors"):
        stale.similarity(writeprints.get(b))


def test_feature_version_tracks_the_corpus(engine):
    """Same extractor + same inputs = same version; change either and it moves."""
    version = engine["writeprints"].feature_version
    assert version.startswith(stylometry_module.FEATURE_VERSION + ":")

    baseline = stylometry_module.corpus_version({1: "aaa", 2: "bbb"})
    assert stylometry_module.corpus_version({1: "aaa", 2: "bbb"}) == baseline
    assert stylometry_module.corpus_version({1: "aaa", 2: "bbX"}) != baseline
    assert stylometry_module.corpus_version({1: "aaa"}) != baseline
    # a persona id moving must invalidate too, not just the text
    assert stylometry_module.corpus_version({1: "bbb", 2: "aaa"}) != baseline


# ─────────────────────────────────────────────────────────────────────────────
# Behaviour
# ─────────────────────────────────────────────────────────────────────────────

def test_hour_smoothing_wraps_around_midnight():
    """actor_001 posts 22:00-03:00; an unwrapped kernel splits that in two."""
    histogram = [0.0] * 24
    histogram[0] = 1.0
    smoothed = behaviour_module.smooth_circular(histogram)
    assert smoothed[23] > 0, "hour 23 must receive weight from hour 0"
    assert smoothed[1] > 0
    assert smoothed[0] == pytest.approx(0.5)


def test_smoothing_conserves_mass():
    histogram = [1.0, 2.0, 0.0, 3.0] + [0.0] * 20
    assert behaviour_module.smooth_circular(histogram).sum() == pytest.approx(
        sum(histogram)
    )


def test_behaviour_runs_for_the_persona_stylometry_refuses(engine):
    """No character floor here: two posts still give a usable hour histogram."""
    assert engine["behaviours"].get(7) is not None
    assert engine["by_pair"][(5, 7)].attribution.components["B"] is not None


def test_subsignal_weights_are_a_convex_combination():
    weights = behaviour_module.SUBSIGNAL_WEIGHTS
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["posting_hours"] > sum(
        v for k, v in weights.items() if k != "posting_hours"
    ), "the hour histogram must stay dominant; see the module docstring"


def test_the_two_demoted_subsignals_cannot_move_a_band():
    """Trade vocabulary ranks backwards and day-of-week is noise (AUC 0.505).

    Both are kept for live data but held at a token weight. Their combined
    contribution has to be smaller than the narrowest band, or a signal that
    measured at chance could still change a verdict.
    """
    weights = behaviour_module.SUBSIGNAL_WEIGHTS
    token = weights["trade_vocabulary"] + weights["day_of_week"]
    narrowest_band = 0.20  # POSSIBLE 0.45-0.65 and PROBABLE 0.65-0.85
    assert token * 0.25 < narrowest_band, "B is worth 0.25 of the final score"


def test_hard_negatives_are_behaviourally_separated(engine):
    """The near-misses were built to share style but not hours."""
    behaviours = engine["behaviours"]
    for (a, b) in HARD_NEGATIVES:
        parts = behaviours.components(a, b)
        assert parts["posting_hours"] < 0.5, (
            f"{a}~{b} was designed to post at different times"
        )


def test_category_agreement_alone_would_confirm_both_near_misses(engine):
    """Guard on the trap documented in link/behaviour.py.

    If this ever fails it means the corpus changed and the warning in the module
    docstring needs revisiting — not that the test should go.
    """
    behaviours = engine["behaviours"]
    for (a, b) in HARD_NEGATIVES:
        assert behaviours.components(a, b)["category"] == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────────────────

def test_every_pair_is_scored_once(engine):
    corpus = engine["corpus"]
    expected = len(corpus.personas) * (len(corpus.personas) - 1) // 2
    assert len(engine["results"]) == expected
    for a, b in engine["by_pair"]:
        assert a < b, "pairs are stored with the lower persona id first"


def test_evidence_is_never_empty(engine):
    """CLAUDE.md: a score with no reasons is not shippable."""
    for result in engine["results"]:
        assert result.evidence
        for entry in result.evidence:
            assert entry.get("type") and entry.get("detail")


def test_infrastructure_is_unmeasured_on_every_pair(engine):
    """Phase 2 has no recon. I must be None, never a measured zero."""
    for result in engine["results"]:
        assert result.attribution.components["I"] is None
        assert "I" in result.attribution.unmeasured


def test_a_confirmed_row_says_infrastructure_was_not_assessed(engine):
    """The analyst reading a CONFIRMED row must see which components counted."""
    confirmed = [r for r in engine["results"] if r.band == "CONFIRMED"]
    assert confirmed
    for result in confirmed:
        notes = [e for e in result.evidence
                 if e["type"] == "component_not_assessed" and e["component"] == "I"]
        assert len(notes) == 1
        assert "infrastructure not assessed" in notes[0]["detail"].lower()
        assert "redistributed" in notes[0]["detail"].lower()


def test_the_six_pairs_with_hard_evidence_are_found(engine):
    """1~9, 1~16, 2~10, 2~17, 3~18, 4~19 all share an identifier."""
    reachable = {p for p in POSITIVES
                 if engine["by_pair"][p].attribution.components["H"] > 0}
    assert len(reachable) == 6
    for pair in reachable:
        result = engine["by_pair"][pair]
        assert result.band == "CONFIRMED", (
            f"{pair} has hard evidence and scored {result.band} ({result.score:.3f})"
        )


def test_the_two_pairs_without_hard_evidence_are_not_reached_pairwise(engine):
    """Reported honestly as 6/8; the closure pass is what reaches these."""
    for pair in ((9, 16), (10, 17)):
        result = engine["by_pair"][pair]
        assert result.attribution.components["H"] == 0.0
        assert result.score < 0.65, (
            "pairwise scoring must not reach a pair with no shared identifier"
        )


def test_no_false_positives_at_any_band(engine):
    """Precision 1.000 — nothing outside the answer key crosses POSSIBLE."""
    for pair, result in engine["by_pair"].items():
        if pair in POSITIVES:
            continue
        assert result.score < 0.45, (
            f"{pair} is not in the answer key but scored "
            f"{result.score:.3f} {result.band}"
        )


@pytest.mark.parametrize("pair", sorted(HARD_NEGATIVES))
def test_designed_near_misses_stay_below_their_ceiling(engine, pair):
    result = engine["by_pair"][pair]
    ceiling = HARD_NEGATIVES[pair]["max_band"]
    assert BAND_ORDER.index(result.band) >= BAND_ORDER.index(ceiling), (
        f"{pair} scored {result.band} ({result.score:.3f}), above its "
        f"{ceiling} ceiling. {HARD_NEGATIVES[pair]['reason']}"
    )


def test_the_resolver_writes_no_actor_assignments(engine):
    """Clustering is a decision, not a similarity measurement."""
    for persona in engine["corpus"].personas.values():
        assert "actor_id" not in persona


# ─────────────────────────────────────────────────────────────────────────────
# Graph and transitive inference
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def linked(engine):
    graph = graph_module.build_graph(engine["results"])
    handles = {pid: p["handle"] for pid, p in engine["corpus"].personas.items()}
    return {
        "graph": graph,
        "components": graph_module.components(graph),
        "derived": graph_module.infer_transitive(graph, handles=handles),
    }


def test_components_recover_the_multi_persona_actors(linked):
    found = {tuple(c) for c in linked["components"]}
    assert found == {(1, 9, 16), (2, 10, 17), (3, 18), (4, 19)}


def test_isolated_personas_are_still_nodes(engine, linked):
    assert linked["graph"].number_of_nodes() == len(engine["corpus"].personas)


def test_evidence_path_finds_the_connecting_persona(linked):
    path = graph_module.evidence_path(linked["graph"], 9, 16)
    assert path is not None
    assert path.nodes == (9, 1, 16)
    assert path.intermediates == (1,)
    assert path.hops == 2

    described = path.describe({1: "Dr3adPirat3"})
    assert "Dr3adPirat3" in described
    assert "9~1" in described and "1~16" in described


def test_a_directly_linked_pair_has_no_path(linked):
    """A direct edge is an observation; asking for a path through it is a bug."""
    assert graph_module.evidence_path(linked["graph"], 1, 16) is None


def test_unconnected_personas_have_no_path(linked):
    assert graph_module.evidence_path(linked["graph"], 1, 20) is None


def test_closure_adds_exactly_the_two_unreachable_pairs(linked):
    assert {r.pair for r in linked["derived"]} == {(9, 16), (10, 17)}


def test_closure_introduces_no_false_positives(linked):
    for result in linked["derived"]:
        assert result.pair in POSITIVES


def test_a_derived_link_scores_below_every_edge_it_came_from(linked):
    for result in linked["derived"]:
        path = next(e for e in result.evidence if e["type"] == "transitive_path")
        assert result.score < min(path["edge_scores"]), (
            "an inference must not be as strong as the evidence behind it"
        )


def test_a_derived_link_is_banded_below_its_weakest_edge(linked, engine):
    for result in linked["derived"]:
        path = next(e for e in result.evidence if e["type"] == "transitive_path")
        nodes = path["path"]
        # BAND_ORDER runs strongest to weakest, so the weakest band is the one
        # with the highest index.
        weakest_band = max(
            (engine["by_pair"][tuple(sorted((nodes[i], nodes[i + 1])))].band
             for i in range(len(nodes) - 1)),
            key=lambda band: BAND_ORDER.index(band),
        )
        assert BAND_ORDER.index(result.band) > BAND_ORDER.index(weakest_band), (
            f"{result.pair} was derived through a {weakest_band} edge and came "
            f"out {result.band}"
        )


def test_derived_links_carry_no_measured_components(linked):
    """An inference is not a measurement of the derived pair."""
    for result in linked["derived"]:
        assert all(v is None for v in result.attribution.components.values())
        assert not result.had_hard_evidence


def test_a_derived_link_states_that_it_was_not_observed(linked):
    for result in linked["derived"]:
        kinds = {e["type"] for e in result.evidence}
        assert {"transitive_path", "transitive_inference",
                "not_directly_observed"} <= kinds


def test_closure_never_touches_a_directly_observed_pair(linked):
    for result in linked["derived"]:
        assert not linked["graph"].has_edge(*result.pair)


def test_skip_pairs_suppresses_an_inference(linked):
    """A direct row always wins: links is unique on the pair."""
    derived = graph_module.infer_transitive(linked["graph"], skip_pairs=[(9, 16)])
    assert {r.pair for r in derived} == {(10, 17)}


def test_a_higher_threshold_breaks_the_clusters(engine):
    """Nothing is derivable once the edges that carried the inference are gone."""
    graph = graph_module.build_graph(engine["results"], threshold=0.95)
    assert graph.number_of_edges() == 0
    assert graph_module.infer_transitive(graph) == []
