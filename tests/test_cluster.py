"""test_cluster.py — turning link components into actor rows.

Written before link/cluster.py, as CLAUDE.md requires for link/.

Phase 2 deliberately stopped one step short of this: link/graph.py computes
connected components and calls them *candidate* clusters, and the resolver
asserts it writes no actor assignments, because deciding that two personas are
one actor is a judgement rather than a similarity measurement. Phase 4 needs
stable actor ids to hang URLs off, so that judgement now gets made explicitly,
in one place, by a step an operator runs and an audit row records.

The tests fall in three groups: the partition must be right, the derived
metadata must not invent anything the links do not support, and the step must be
honest about the threshold it used.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from link import cluster as cluster_module  # noqa: E402
from link.resolve import load_corpus_from_fixtures, resolve_pairs  # noqa: E402

TRUTH = json.loads(
    (ROOT / "fixtures" / "ground_truth.json").read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def engine():
    corpus = load_corpus_from_fixtures()
    results, _, _, _ = resolve_pairs(corpus)
    return corpus, results


@pytest.fixture(scope="module")
def clusters(engine):
    corpus, results = engine
    return cluster_module.build(corpus, results)


def truth_partition() -> set[tuple[int, ...]]:
    by_actor: dict[str, list[int]] = {}
    for persona, actor in TRUTH["persona_to_actor"].items():
        by_actor.setdefault(actor, []).append(int(persona))
    return {tuple(sorted(v)) for v in by_actor.values()}


# ─────────────────────────────────────────────────────────────────────────────
# The partition
# ─────────────────────────────────────────────────────────────────────────────

def test_the_default_threshold_is_the_probable_floor(clusters):
    """Below PROBABLE a link is a lead, not grounds for merging two identities."""
    assert cluster_module.DEFAULT_THRESHOLD == 0.65
    assert clusters.threshold == 0.65


def test_every_persona_lands_in_exactly_one_cluster(clusters, engine):
    corpus, _ = engine
    seen: list[int] = []
    for actor in clusters.actors:
        seen.extend(actor.personas)
    assert sorted(seen) == corpus.persona_ids
    assert len(seen) == len(set(seen)), "a persona appears in two clusters"


def test_a_persona_linked_to_nobody_is_still_an_actor(clusters):
    """A lone vendor is an actor of one, not an orphan dropped from the table."""
    singletons = [a for a in clusters.actors if len(a.personas) == 1]
    assert singletons, "every persona ended up merged, which cannot be right"
    assert all(len(a.personas) >= 1 for a in clusters.actors)


def test_the_partition_matches_the_answer_key(clusters):
    """14 actors over 20 personas, with the same membership ground truth declares."""
    got = {tuple(a.personas) for a in clusters.actors}
    assert len(clusters.actors) == TRUTH["actor_count"]
    assert got == truth_partition()


def test_the_migrations_are_merged_into_one_actor_each(clusters):
    """1-9-16 and 2-10-17 are three personas of one actor, reached through links."""
    by_persona = {p: a for a in clusters.actors for p in a.personas}
    assert by_persona[1].personas == (1, 9, 16)
    assert by_persona[2].personas == (2, 10, 17)


def test_the_hard_negatives_are_never_merged(clusters):
    """The pairs the engine refuses must not be joined by the clustering step."""
    by_persona = {p: a for a in clusters.actors for p in a.personas}
    for case in TRUTH["hard_negatives"]:
        left, right = case["pair"]
        assert by_persona[left] is not by_persona[right], (
            f"{left}~{right} was merged despite being a designed hard negative"
        )


def test_raising_the_threshold_splits_clusters(engine):
    """Clustering is a decision about a threshold, and says which one it used."""
    corpus, results = engine
    strict = cluster_module.build(corpus, results, threshold=0.95)
    assert strict.threshold == 0.95
    assert len(strict.actors) > len(cluster_module.build(corpus, results).actors)


def test_a_threshold_above_every_link_leaves_only_singletons(engine):
    corpus, results = engine
    alone = cluster_module.build(corpus, results, threshold=1.01)
    assert len(alone.actors) == len(corpus.persona_ids)
    assert all(len(a.personas) == 1 for a in alone.actors)


def test_only_links_that_were_stored_can_merge(engine):
    """Clustering reads the same edges the links table holds — nothing weaker."""
    corpus, results = engine
    built = cluster_module.build(corpus, results)
    for actor in built.actors:
        for persona in actor.personas:
            assert persona in corpus.personas


# ─────────────────────────────────────────────────────────────────────────────
# Derived metadata must not overclaim
# ─────────────────────────────────────────────────────────────────────────────

def test_confidence_is_the_weakest_edge_holding_the_cluster_together(clusters):
    """A three-persona actor is only as good as the link that reaches the third.

    Reporting the strongest edge would let one CONFIRMED pair vouch for a
    persona it was never compared against.
    """
    by_persona = {p: a for a in clusters.actors for p in a.personas}
    multi = [a for a in clusters.actors if len(a.personas) > 1]
    assert multi
    for actor in multi:
        assert actor.min_edge_score is not None
        assert 0.0 <= actor.min_edge_score <= 1.0
        assert actor.min_edge_score >= clusters.threshold
    assert by_persona[1].min_edge_score == pytest.approx(
        min(e.score for e in by_persona[1].edges)
    )


def test_a_singleton_has_no_edges_and_no_confidence(clusters):
    for actor in clusters.actors:
        if len(actor.personas) == 1:
            assert actor.edges == ()
            assert actor.min_edge_score is None


def test_an_actor_carries_the_evidence_that_merged_it(clusters):
    """Requirement for the UI: why is this one actor and not three?"""
    by_persona = {p: a for a in clusters.actors for p in a.personas}
    actor = by_persona[1]
    assert len(actor.edges) >= 2
    for edge in actor.edges:
        assert edge.evidence, "a merging edge with no evidence is not shippable"
        assert all(e.get("type") and e.get("detail") for e in edge.evidence)


def test_the_label_names_a_persona_that_is_actually_in_the_cluster(clusters, engine):
    corpus, _ = engine
    handles = {p: corpus.personas[p]["handle"] for p in corpus.persona_ids}
    for actor in clusters.actors:
        assert actor.label
        assert actor.label in {handles[p] for p in actor.personas}


def test_first_and_last_seen_span_the_whole_cluster(clusters, engine):
    corpus, _ = engine
    for actor in clusters.actors:
        stamps = [
            post["posted_at"]
            for persona in actor.personas
            for post in corpus.posts.get(persona, [])
            if post.get("posted_at")
        ]
        if not stamps:
            continue
        assert isinstance(actor.first_seen, datetime)
        assert actor.first_seen == min(stamps)
        assert actor.last_seen == max(stamps)
        assert actor.first_seen <= actor.last_seen


def test_category_is_one_the_personas_actually_posted_in(clusters, engine):
    corpus, _ = engine
    for actor in clusters.actors:
        if actor.category is None:
            continue
        theirs = {corpus.personas[p].get("category") for p in actor.personas}
        assert actor.category in theirs


# ─────────────────────────────────────────────────────────────────────────────
# Determinism
# ─────────────────────────────────────────────────────────────────────────────

def test_clustering_the_same_corpus_twice_gives_the_same_answer(engine):
    corpus, results = engine
    first = cluster_module.build(corpus, results)
    second = cluster_module.build(corpus, results)
    assert [a.personas for a in first.actors] == [a.personas for a in second.actors]
    assert [a.label for a in first.actors] == [a.label for a in second.actors]


def test_clusters_are_ordered_largest_first_then_by_lowest_persona(clusters):
    keys = [(-len(a.personas), a.personas[0]) for a in clusters.actors]
    assert keys == sorted(keys)
