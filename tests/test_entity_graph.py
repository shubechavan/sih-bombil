"""test_entity_graph.py — the identifier view of the link graph.

Written before `link/entity_graph.py`, per the CLAUDE.md rule for `link/`.

The persona graph answers "which handles are the same person?". This one answers
"what did they publish, and what do two of them share?" — so the assertions that
matter are about *hubs*: a value carried by more than one persona has to come
back as a single node with both personas attached, never as two nodes that
happen to have equal labels.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from link.entity_graph import (  # noqa: E402
    ENTITY_KINDS,
    EntityEdge,
    EntityGraph,
    EntityNode,
    build,
    node_id,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def persona(pid: int, handle: str, normalized: str | None = None, **kw) -> dict:
    return {
        "id": pid,
        "handle": handle,
        "handle_normalized": normalized if normalized is not None else handle.lower(),
        "source_id": kw.get("source_id", 1),
        "source_name": kw.get("source_name", "market_alpha"),
        "actor_id": kw.get("actor_id"),
    }


def identifier(type_: str, value: str, personas: tuple[int, ...], **kw) -> dict:
    return {
        "type": type_,
        "value": value,
        "value_norm": kw.get("value_norm"),
        "personas": personas,
        "meta": kw.get("meta", {}),
    }


def ids(nodes) -> set[str]:
    return {n.id for n in nodes}


# ── shape ─────────────────────────────────────────────────────────────────────

def test_an_empty_corpus_produces_an_empty_graph():
    g = build([], [])
    assert g.nodes == ()
    assert g.edges == ()


def test_a_persona_with_no_identifiers_is_still_a_node():
    """An isolated persona is a fact about the corpus, not a rendering problem."""
    g = build([persona(1, "alice")], [])
    assert [n.kind for n in g.nodes] == ["persona"]
    assert g.edges == ()


def test_one_identifier_makes_two_nodes_and_one_edge():
    g = build([persona(1, "alice")], [identifier("btc", "bc1qexample", (1,))])
    assert len(g.nodes) == 2
    assert len(g.edges) == 1
    edge = g.edges[0]
    assert edge.source == node_id("persona", "1")
    assert edge.target in ids(g.nodes)
    assert edge.relation == "published"


def test_every_edge_endpoint_resolves_to_a_node():
    """A dangling endpoint renders as an invisible edge, which reads as a bug."""
    g = build(
        [persona(1, "alice"), persona(2, "bob")],
        [
            identifier("pgp_fpr", "A" * 40, (1, 2)),
            identifier("email", "a@example.com", (1,)),
            identifier("xmr", "4" + "B" * 94, (2,)),
        ],
    )
    present = ids(g.nodes)
    for edge in g.edges:
        assert edge.source in present, edge
        assert edge.target in present, edge


def test_node_ids_are_unique():
    g = build(
        [persona(1, "alice"), persona(2, "bob")],
        [
            identifier("btc", "bc1qsame", (1,)),
            identifier("btc", "bc1qsame", (2,)),
            identifier("email", "a@example.com", (1,)),
        ],
    )
    all_ids = [n.id for n in g.nodes]
    assert len(all_ids) == len(set(all_ids))


# ── hubs, which are the point of the view ─────────────────────────────────────

def test_a_shared_identifier_is_one_node_carrying_both_personas():
    g = build(
        [persona(1, "alice"), persona(2, "bob")],
        [identifier("pgp_fpr", "F" * 40, (1, 2))],
    )
    pgp = [n for n in g.nodes if n.kind == "pgp"]
    assert len(pgp) == 1, "a shared key must be one hub, not two leaves"
    assert pgp[0].personas == (1, 2)
    assert pgp[0].shared is True
    assert len(g.edges) == 2


def test_an_unshared_identifier_is_not_marked_shared():
    g = build([persona(1, "alice")], [identifier("btc", "bc1qonly", (1,))])
    wallet = [n for n in g.nodes if n.kind == "wallet"][0]
    assert wallet.shared is False
    assert wallet.personas == (1,)


def test_the_same_value_under_two_types_stays_two_nodes():
    """A handle and an email local part can collide; the kind disambiguates."""
    g = build(
        [persona(1, "alice")],
        [identifier("telegram", "dreadpirate", (1,)),
         identifier("session", "dreadpirate", (1,))],
    )
    kinds = sorted(n.kind for n in g.nodes if n.kind != "persona")
    assert kinds == ["session", "telegram"]
    assert len({n.id for n in g.nodes}) == 3


# ── normalised handles ────────────────────────────────────────────────────────

def test_a_shared_normalised_handle_becomes_a_hub():
    """The leetspeak decode is the whole reason normalisation exists."""
    g = build(
        [persona(1, "Dr3adPirat3", "dreadpirate"),
         persona(2, "Dread_P1rate", "dreadpirate")],
        [],
    )
    handles = [n for n in g.nodes if n.kind == "handle"]
    assert len(handles) == 1
    assert handles[0].value == "dreadpirate"
    assert handles[0].personas == (1, 2)
    assert {e.relation for e in g.edges} == {"normalises to"}


def test_a_handle_only_one_persona_carries_is_not_a_node():
    """It would be a leaf mirroring its own persona — noise, not a finding.

    Unlike a PGP key, a normalised handle is derived by us rather than published
    by the actor, so a singleton adds no information the persona node lacks.
    """
    g = build([persona(1, "alice", "alice"), persona(2, "bob", "bob")], [])
    assert [n.kind for n in g.nodes] == ["persona", "persona"]
    assert g.edges == ()


def test_a_missing_normalised_handle_is_skipped_not_guessed():
    g = build([persona(1, "alice", None), persona(2, "bob", None)], [])
    assert not [n for n in g.nodes if n.kind == "handle"]


# ── kinds ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("type_,expected", [
    ("pgp_fpr", "pgp"),
    ("btc", "wallet"),
    ("eth", "wallet"),
    ("xmr", "wallet"),
    ("ltc", "wallet"),
    ("email", "email"),
    ("jabber", "jabber"),
    ("session", "session"),
    ("telegram", "telegram"),
    ("onion_mirror", "onion_mirror"),
])
def test_identifier_types_map_to_entity_kinds(type_, expected):
    g = build([persona(1, "alice")], [identifier(type_, "value-here", (1,))])
    kinds = [n.kind for n in g.nodes if n.kind != "persona"]
    assert kinds == [expected]
    assert expected in ENTITY_KINDS


def test_a_wallet_node_records_which_chain_it_is_on():
    """"wallet" alone loses the difference between a traceable chain and Monero."""
    g = build([persona(1, "alice")], [identifier("xmr", "4" + "B" * 94, (1,))])
    wallet = [n for n in g.nodes if n.kind == "wallet"][0]
    assert wallet.detail["chain"] == "xmr"


def test_an_unknown_identifier_type_is_dropped_not_rendered_as_unknown():
    g = build([persona(1, "alice")], [identifier("carrier_pigeon", "x", (1,))])
    assert [n.kind for n in g.nodes] == ["persona"]


# ── labels ────────────────────────────────────────────────────────────────────

def test_a_long_value_is_abbreviated_for_the_label_but_kept_in_full():
    fpr = "9A1B" * 10
    g = build([persona(1, "alice")], [identifier("pgp_fpr", fpr, (1,))])
    pgp = [n for n in g.nodes if n.kind == "pgp"][0]
    assert pgp.value == fpr
    assert len(pgp.label) < len(fpr)
    assert pgp.label.endswith(fpr[-8:])


def test_a_short_value_is_its_own_label():
    g = build([persona(1, "alice")], [identifier("email", "a@b.com", (1,))])
    email = [n for n in g.nodes if n.kind == "email"][0]
    assert email.label == "a@b.com"


def test_a_persona_node_is_labelled_with_its_raw_handle():
    g = build([persona(1, "Dr3adPirat3", "dreadpirate")], [])
    assert g.nodes[0].label == "Dr3adPirat3"


# ── determinism ───────────────────────────────────────────────────────────────

def test_the_graph_is_ordered_deterministically():
    personas = [persona(2, "bob"), persona(1, "alice"), persona(3, "carol")]
    idents = [
        identifier("email", "z@example.com", (3,)),
        identifier("btc", "bc1qaaa", (1,)),
        identifier("email", "a@example.com", (2,)),
    ]
    first = build(personas, idents)
    second = build(list(reversed(personas)), list(reversed(idents)))
    assert [n.id for n in first.nodes] == [n.id for n in second.nodes]
    assert [(e.source, e.target) for e in first.edges] == \
           [(e.source, e.target) for e in second.edges]


def test_personas_sort_before_identifiers():
    """Stable draw order: the things being explained, then what explains them."""
    g = build([persona(1, "alice")], [identifier("btc", "bc1q", (1,))])
    assert g.nodes[0].kind == "persona"


# ── invariants ────────────────────────────────────────────────────────────────

def test_an_entity_node_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        EntityNode(id="x:1", kind="nonsense", label="x", value="x")


def test_an_edge_rejects_an_unknown_relation():
    with pytest.raises(ValueError):
        EntityEdge(source="persona:1", target="pgp:a", relation="implies")


def test_the_graph_counts_its_hubs():
    g = build(
        [persona(1, "alice"), persona(2, "bob")],
        [identifier("pgp_fpr", "F" * 40, (1, 2)),
         identifier("btc", "bc1qonly", (1,))],
    )
    assert g.hub_count == 1


def test_entity_graph_does_not_touch_attribution():
    """The same guard tests/test_leads.py applies, for the same reason.

    A view that can reach the scorer is a view that can change a number by
    accident. Parse the imports rather than grepping the text, so prose in a
    docstring cannot fail it and an aliased import cannot hide from it.
    """
    tree = ast.parse((ROOT / "link" / "entity_graph.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    for forbidden in ("score", "score.attribution", "link.resolve", "link.cluster"):
        assert not any(
            name == forbidden or name.startswith(forbidden + ".")
            for name in imported
        ), f"entity_graph.py must not import {forbidden}"


def test_the_graph_is_immutable():
    g = build([persona(1, "alice")], [])
    assert isinstance(g, EntityGraph)
    assert isinstance(g.nodes, tuple)
    with pytest.raises((AttributeError, TypeError)):
        g.nodes = ()  # type: ignore[misc]
