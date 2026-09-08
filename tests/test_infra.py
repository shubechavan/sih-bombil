"""test_infra.py — the I term, and why this corpus cannot supply one.

Written before link/infra.py, as CLAUDE.md requires for link/ and score/.

The tests split in two. The first group pins the *contract*: I is measured only
when both personas control a fingerprinted host, it refuses by rule rather than
by missing data, and it does measure when a corpus actually carries persona-level
infrastructure. The second group is the counter-evidence: it pins the two
structural facts that disqualify the tempting alternative — broadcasting a
market's fingerprint to every vendor standing in it.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from link import infra as infra_module  # noqa: E402
from link.resolve import load_corpus_from_fixtures, resolve_pairs  # noqa: E402
from recon.fingerprint import Fingerprint, read_fixture_findings  # noqa: E402

TRUTH = json.loads(
    (ROOT / "fixtures" / "ground_truth.json").read_text(encoding="utf-8")
)
POSITIVES = {tuple(p) for p in TRUTH["expected_positive_pairs"]}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus_from_fixtures()


@pytest.fixture(scope="module")
def findings():
    return read_fixture_findings()


@pytest.fixture(scope="module")
def controlled(corpus, findings):
    return infra_module.build(corpus.personas, findings,
                              identifiers=corpus.identifiers)


@pytest.fixture(scope="module")
def broadcast(corpus, findings):
    return infra_module.build(corpus.personas, findings,
                              identifiers=corpus.identifiers,
                              mode="site-broadcast")


# ─────────────────────────────────────────────────────────────────────────────
# The contract
# ─────────────────────────────────────────────────────────────────────────────

def test_persona_controlled_is_the_default_mode():
    assert infra_module.DEFAULT_MODE == "persona-controlled"


def test_an_unknown_mode_is_rejected_by_name(corpus, findings):
    with pytest.raises(ValueError, match="wishful"):
        infra_module.build(corpus.personas, findings, mode="wishful")


def test_every_pair_on_this_corpus_is_unmeasured(controlled, corpus):
    """No vendor in the fixtures runs a host we fingerprinted."""
    for a, b in itertools.combinations(corpus.persona_ids, 2):
        assert controlled.similarity(a, b) is None


def test_the_refusal_says_the_findings_are_site_level(controlled, corpus):
    for persona_id in corpus.persona_ids:
        reason = controlled.refusal_reason(persona_id)
        assert reason, f"persona {persona_id} refused with no reason"
        assert "site-level" in reason.lower()


def test_the_refusal_is_not_blamed_on_missing_recon(controlled):
    """Recon ran and produced three findings. The gap is grain, not absence."""
    reason = controlled.refusal_reason(1) or ""
    assert "phase 3" not in reason.lower()
    assert "no recon" not in reason.lower()


def test_an_unmeasured_pair_explains_nothing(controlled):
    assert controlled.explain(1, 16) == []


# ─────────────────────────────────────────────────────────────────────────────
# It refuses by rule, not for want of data: give it a corpus that has the
# evidence and it measures.
# ─────────────────────────────────────────────────────────────────────────────

def _vendor_corpus():
    """Two vendors who each run their own mirror, and one who does not."""
    personas = {
        1: {"id": 1, "source_id": 1, "handle": "a"},
        2: {"id": 2, "source_id": 1, "handle": "b"},
        3: {"id": 3, "source_id": 1, "handle": "c"},
    }
    identifiers = {
        1: {("onion_mirror", "aaaa.onion")},
        2: {("onion_mirror", "bbbb.onion")},
        3: set(),
    }
    findings = [
        Fingerprint(onion_url="http://aaaa.onion", favicon_hash="-42",
                    etag='W/"same"', server_banner="nginx/1.18.0 (Ubuntu)",
                    headers={"Server": "nginx/1.18.0 (Ubuntu)", "ETag": 'W/"same"'}),
        Fingerprint(onion_url="http://bbbb.onion", favicon_hash="-42",
                    etag='W/"same"', server_banner="nginx/1.18.0",
                    headers={"Server": "nginx/1.18.0", "ETag": 'W/"same"'}),
        # the market everyone stands on — must never be attributed to a vendor
        Fingerprint(onion_url="http://market.onion", favicon_hash="-999",
                    source_id=1),
    ]
    return personas, identifiers, findings


def test_two_vendors_running_their_own_hosts_are_measured():
    personas, identifiers, findings = _vendor_corpus()
    infra = infra_module.build(personas, findings, identifiers=identifiers)

    value = infra.similarity(1, 2)
    assert value is not None
    assert value > 0.8, "shared favicon and ETag should be a strong overlap"
    assert infra.explain(1, 2), "a measured pair must say what matched"


def test_a_vendor_without_a_host_still_refuses():
    personas, identifiers, findings = _vendor_corpus()
    infra = infra_module.build(personas, findings, identifiers=identifiers)

    assert infra.similarity(1, 3) is None
    assert infra.refusal_reason(3)
    assert infra.refusal_reason(1) is None


def test_the_market_fingerprint_is_never_borrowed_by_a_vendor():
    """Persona 3 sits on source 1 and must not inherit source 1's favicon."""
    personas, identifiers, findings = _vendor_corpus()
    infra = infra_module.build(personas, findings, identifiers=identifiers)

    assert infra.similarity(1, 3) is None
    assert infra.similarity(2, 3) is None


def test_controlled_hosts_that_share_nothing_measure_zero_not_none():
    personas, identifiers, findings = _vendor_corpus()
    findings[1] = Fingerprint(onion_url="http://bbbb.onion", favicon_hash="777",
                              etag='W/"other"', server_banner="Apache/2.4.41",
                              headers={"Server": "Apache/2.4.41"})
    infra = infra_module.build(personas, findings, identifiers=identifiers)

    value = infra.similarity(1, 2)
    assert value == 0.0, "we compared two fingerprints and they disagreed"
    assert value is not None


def test_overlap_is_symmetric_and_bounded():
    personas, identifiers, findings = _vendor_corpus()
    infra = infra_module.build(personas, findings, identifiers=identifiers)

    assert infra.similarity(1, 2) == infra.similarity(2, 1)
    assert 0.0 <= infra.similarity(1, 2) <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Counter-evidence: what site-broadcast actually does
# ─────────────────────────────────────────────────────────────────────────────

def _cross_market_pairs(corpus):
    return [
        (a, b) for a, b in itertools.combinations(corpus.persona_ids, 2)
        if {corpus.personas[a]["source_id"], corpus.personas[b]["source_id"]} == {1, 3}
    ]


def _same_source_pairs(corpus):
    return [
        (a, b) for a, b in itertools.combinations(corpus.persona_ids, 2)
        if corpus.personas[a]["source_id"] == corpus.personas[b]["source_id"]
    ]


def test_site_broadcast_gives_every_alpha_gamma_pair_the_identical_value(
        broadcast, corpus):
    """40 pairs, one number. A constant cannot rank anything."""
    pairs = _cross_market_pairs(corpus)
    assert len(pairs) == 40
    values = {broadcast.similarity(a, b) for a, b in pairs}
    assert len(values) == 1, "the value varies, so it is not purely site-level"
    assert values.pop() > 0.5, "alpha and gamma do share infrastructure"


def test_site_broadcast_cannot_separate_the_true_pairs_from_the_rest(
        broadcast, corpus):
    """Only 4 of those 40 are real. All 40 score the same, so I ranks none."""
    pairs = _cross_market_pairs(corpus)
    true_pairs = [p for p in pairs if p in POSITIVES]
    assert len(true_pairs) == 4
    assert {broadcast.similarity(*p) for p in true_pairs} == \
           {broadcast.similarity(*p) for p in pairs if p not in POSITIVES}


def test_site_broadcast_scores_same_market_pairs_near_one(broadcast, corpus):
    """59 pairs get a near-perfect infrastructure score for sharing a website.

    Two vendors on one market are compared against a single fingerprint, so
    every signal that host publishes agrees with itself: 0.964 on the two
    plain-HTTP markets, and exactly 1.0 on market_gamma, whose certificate
    serial saturates the noisy-OR on its own.
    """
    pairs = _same_source_pairs(corpus)
    assert len(pairs) == 59
    assert all(broadcast.similarity(a, b) >= 0.96 for a, b in pairs)
    assert not any(p in POSITIVES for p in pairs), (
        "every true positive is cross-source, so this 0.96+ is pure noise"
    )


def test_site_broadcast_measures_zero_where_markets_differ(broadcast, corpus):
    """alpha↔beta share no infrastructure, so I is a measured 0.0 — and a
    measured zero costs the pair its renormalisation."""
    assert broadcast.similarity(1, 9) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# ...and what that does to the answer key, end to end
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def scored(corpus, controlled, broadcast):
    off = {r.pair: r for r in resolve_pairs(corpus, infra=controlled)[0]}
    on = {r.pair: r for r in resolve_pairs(corpus, infra=broadcast)[0]}
    return off, on


def test_the_default_path_leaves_every_score_exactly_where_phase_2_had_it(
        corpus, controlled):
    """I unmeasured means renormalisation still applies and nothing moves."""
    baseline = {r.pair: r.score for r in resolve_pairs(corpus)[0]}
    wired = {r.pair: r.score for r in resolve_pairs(corpus, infra=controlled)[0]}
    assert baseline == wired


def test_site_broadcast_promotes_pairs_that_are_not_in_the_answer_key(scored):
    off, on = scored
    promoted = [p for p in off
                if on[p].score > off[p].score and p not in POSITIVES]
    assert promoted, "if nothing is promoted the mode is harmless, and it is not"


def test_site_broadcast_demotes_the_migrations_that_crossed_to_the_forum(scored):
    """1~9 and 2~10 are alpha↔beta: they lose renormalisation and gain nothing."""
    off, on = scored
    for pair in ((1, 9), (2, 10)):
        assert on[pair].score < off[pair].score


def test_site_broadcast_lifts_the_hard_negative_it_shares_a_market_with(scored):
    """2~20 is alpha↔gamma. The mode cannot lift the true pairs without it."""
    off, on = scored
    assert on[(2, 20)].score > off[(2, 20)].score
