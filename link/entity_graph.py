"""entity_graph.py — the same corpus, drawn around what was published.

The problem statement asks for "a single relationship graph of handles, PGP
keys, wallets and trust links". `/graph` draws half of that: personas as nodes,
attribution as edges. It is the right default, because the question an analyst
usually has is "are these two the same person?".

This module draws the other half. Identifiers stop being evidence *on* an edge
and become nodes in their own right, with personas hanging off them. The shape
of the answer changes with it:

    persona graph      Dr3adPirat3 ──0.876── BlackSailsRX
                       (why? click the edge, read the evidence array)

    entity graph       Dr3adPirat3 ──┐
                                     ├── 9A1B…C4D2 (PGP)
                       BlackSailsRX ─┘

Nothing is computed here that the persona graph did not already know. The value
is entirely in the projection: a shared key is a *hub you can see* rather than a
sentence inside an edge payload, and a hub with four personas on it is obvious
at a glance in a way that six pairwise edges are not.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
**It does not score anything.** No weights, no confidence, no band. An edge here
means "this persona published this value", which is an observation, not a
judgement. tests/test_entity_graph.py asserts by parsing this file's imports
that the scorer is unreachable from here — the same guard link/leads.py carries.

**It does not merge by resemblance.** Two nodes are one node when their type and
value are equal, full stop. Fuzzy joining of near-identical wallets or
lookalike handles would manufacture hubs, and a manufactured hub in a view
whose entire purpose is "look what they share" is the worst possible place for
one.

**It does not emit a node for a normalised handle only one persona carries.**
Unlike a PGP key, a normalised handle is derived by us rather than published by
the actor, so a singleton would be a leaf mirroring its own persona — pure
node count, no information. A normalised handle that *two* personas share is
the leetspeak decode earning its keep, and that gets a node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

__all__ = [
    "ENTITY_KINDS",
    "IDENTIFIER_KINDS",
    "RELATIONS",
    "EntityEdge",
    "EntityGraph",
    "EntityNode",
    "build",
    "entity_graph_for",
    "node_id",
]

#: Identifier type (as stored in `identifiers.type`) → node kind. Wallets
#: collapse to one kind because they draw identically; which chain a wallet sits
#: on survives in `detail["chain"]`, and it matters — see link/leads.py on why
#: Monero is not the same kind of lead as Bitcoin.
IDENTIFIER_KINDS: Mapping[str, str] = {
    "pgp_fpr": "pgp",
    "btc": "wallet",
    "eth": "wallet",
    "xmr": "wallet",
    "ltc": "wallet",
    "email": "email",
    "jabber": "jabber",
    "session": "session",
    "telegram": "telegram",
    "onion_mirror": "onion_mirror",
}

#: Every kind a node may carry. "persona" and "handle" are not identifier types:
#: the first is the thing being explained, the second is our own normalisation.
ENTITY_KINDS: tuple[str, ...] = (
    "persona",
    "handle",
    "pgp",
    "wallet",
    "email",
    "jabber",
    "session",
    "telegram",
    "onion_mirror",
)

#: What an edge is allowed to assert. Both are observations. Neither is a score.
RELATIONS: tuple[str, ...] = ("published", "normalises to")

#: Draw order for the node list, so a client that renders in order gets the
#: personas laid out before the things they connect to.
_KIND_ORDER: Mapping[str, int] = {kind: i for i, kind in enumerate(ENTITY_KINDS)}

#: Values longer than this get an abbreviated label. A 95-character Monero
#: address rendered in full is not a label, it is a wall.
_LABEL_MAX = 20
_LABEL_HEAD = 6
_LABEL_TAIL = 8

NOTE = (
    "Nodes are personas and the identifiers they published; an edge means "
    "'this persona published this value', which is an observation and carries "
    "no score. A node touched by more than one persona is a hub — that is the "
    "view's whole purpose, and it is the same evidence the persona graph "
    "carries inside its edges, drawn where it can be seen."
)


def node_id(kind: str, value: str) -> str:
    """Stable id for a node.

    `kind` prefixes the value because collisions across kinds are real: a
    Telegram handle and a Session id are both bare strings and one corpus may
    hold the same text as both.
    """
    return f"{kind}:{value}"


def _abbreviate(value: str) -> str:
    if len(value) <= _LABEL_MAX:
        return value
    return f"{value[:_LABEL_HEAD]}…{value[-_LABEL_TAIL:]}"


@dataclass(frozen=True)
class EntityNode:
    """A persona, or something a persona published."""

    id: str
    kind: str
    label: str                       #: short enough to draw
    value: str                       #: the full thing, never truncated
    personas: tuple[int, ...] = ()   #: which personas touch this node
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ENTITY_KINDS:
            raise ValueError(f"{self.kind!r} is not one of {ENTITY_KINDS}")

    @property
    def shared(self) -> bool:
        """True when more than one persona touches it — i.e. it is a hub."""
        return len(self.personas) > 1


@dataclass(frozen=True)
class EntityEdge:
    """persona → identifier. Directed, because the assertion has a direction."""

    source: str
    target: str
    relation: str = "published"

    def __post_init__(self) -> None:
        if self.relation not in RELATIONS:
            raise ValueError(f"{self.relation!r} is not one of {RELATIONS}")


@dataclass(frozen=True)
class EntityGraph:
    nodes: tuple[EntityNode, ...] = ()
    edges: tuple[EntityEdge, ...] = ()
    note: str = NOTE

    @property
    def hub_count(self) -> int:
        return sum(1 for n in self.nodes if n.kind != "persona" and n.shared)


def _persona_sort_key(node: EntityNode) -> tuple[int, int, str]:
    """Personas numerically, everything else by kind then value.

    String-sorting `persona:10` against `persona:2` would put ten before two,
    which is stable but reads as a bug in a rendered legend.
    """
    if node.kind == "persona":
        return (_KIND_ORDER["persona"], int(node.value), "")
    return (_KIND_ORDER[node.kind], 0, node.value)


def build(
    personas: Iterable[Mapping],
    identifiers: Iterable[Mapping],
) -> EntityGraph:
    """Project personas and their identifiers into nodes and edges.

    Pure: no session, no I/O, no clock. `personas` carry `id`, `handle` and
    `handle_normalized`; `identifiers` carry `type`, `value` and a `personas`
    tuple naming the personas that published them.

    An identifier referring to a persona outside `personas` has that reference
    dropped — the caller has scoped the view (to one actor, one source) and a
    node reaching outside that scope would silently widen it.
    """
    personas = list(personas)
    known = {int(p["id"]) for p in personas}

    nodes: dict[str, EntityNode] = {}
    edges: set[tuple[str, str, str]] = set()

    for p in personas:
        pid = int(p["id"])
        nid = node_id("persona", str(pid))
        nodes[nid] = EntityNode(
            id=nid,
            kind="persona",
            label=p.get("handle") or str(pid),
            value=str(pid),
            personas=(pid,),
            detail={
                k: p.get(k)
                for k in ("source_id", "source_name", "actor_id", "category")
                if p.get(k) is not None
            },
        )

    # ── what they published ──────────────────────────────────────────────────
    #
    # Merged by (kind, value) rather than by row, so the same value arriving
    # twice becomes one hub instead of two identical-looking leaves. The
    # database's own UNIQUE(type, value) means this rarely fires in practice;
    # it fires on hand-built input, which is exactly where a silent duplicate
    # would be hardest to notice.
    carried: dict[str, set[int]] = {}
    staged: dict[str, tuple[str, str, dict]] = {}

    for row in identifiers:
        kind = IDENTIFIER_KINDS.get(str(row.get("type", "")))
        if kind is None:
            continue                       # unknown type: drop, do not invent
        value = str(row.get("value_norm") or row.get("value") or "").strip()
        if not value:
            continue
        holders = {int(x) for x in row.get("personas", ()) if int(x) in known}
        if not holders:
            continue

        nid = node_id(kind, value)
        carried.setdefault(nid, set()).update(holders)
        if nid not in staged:
            detail: dict = {}
            if kind == "wallet":
                detail["chain"] = str(row["type"])
            meta = row.get("meta") or {}
            if isinstance(meta, Mapping) and meta.get("uid"):
                detail["uid"] = meta["uid"]
            staged[nid] = (kind, value, detail)

    for nid, (kind, value, detail) in staged.items():
        holders = tuple(sorted(carried[nid]))
        nodes[nid] = EntityNode(
            id=nid,
            kind=kind,
            label=_abbreviate(value),
            value=value,
            personas=holders,
            detail=detail,
        )
        for pid in holders:
            edges.add((node_id("persona", str(pid)), nid, "published"))

    # ── the normalisation, where it did work ─────────────────────────────────
    by_normalised: dict[str, set[int]] = {}
    for p in personas:
        normalised = (p.get("handle_normalized") or "").strip()
        if normalised:
            by_normalised.setdefault(normalised, set()).add(int(p["id"]))

    for normalised, holders in by_normalised.items():
        if len(holders) < 2:
            continue                       # a leaf mirroring its own persona
        nid = node_id("handle", normalised)
        nodes[nid] = EntityNode(
            id=nid,
            kind="handle",
            label=_abbreviate(normalised),
            value=normalised,
            personas=tuple(sorted(holders)),
            detail={"variants": sorted(
                str(p.get("handle")) for p in personas
                if int(p["id"]) in holders
            )},
        )
        for pid in sorted(holders):
            edges.add((node_id("persona", str(pid)), nid, "normalises to"))

    ordered_nodes = tuple(sorted(nodes.values(), key=_persona_sort_key))
    order = {n.id: i for i, n in enumerate(ordered_nodes)}
    ordered_edges = tuple(
        EntityEdge(source=s, target=t, relation=r)
        for s, t, r in sorted(edges, key=lambda e: (order[e[0]], order[e[1]]))
    )
    return EntityGraph(nodes=ordered_nodes, edges=ordered_edges)


def entity_graph_for(
    session,
    persona_ids: Optional[Sequence[int]] = None,
) -> EntityGraph:
    """`build()` over what the database holds.

    `persona_ids` scopes the view; None means the whole corpus.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from db import (  # noqa: PLC0415
        Identifier as IdentifierRow,
        Persona,
        PersonaIdentifier,
        Source,
    )

    query = select(Persona).order_by(Persona.id)
    if persona_ids is not None:
        if not persona_ids:
            return EntityGraph()
        query = query.where(Persona.id.in_(list(persona_ids)))
    persona_rows = list(session.execute(query).scalars())

    source_names = dict(session.execute(select(Source.id, Source.name)).all())

    personas = [
        {
            "id": p.id,
            "handle": p.handle,
            "handle_normalized": p.handle_normalized,
            "source_id": p.source_id,
            "source_name": source_names.get(p.source_id),
            "actor_id": p.actor_id,
            "category": p.category,
        }
        for p in persona_rows
    ]

    ids = [p["id"] for p in personas]
    if not ids:
        return EntityGraph()

    carriers: dict[int, set[int]] = {}
    for persona_id, identifier_id in session.execute(
        select(PersonaIdentifier.persona_id, PersonaIdentifier.identifier_id)
        .where(PersonaIdentifier.persona_id.in_(ids))
    ).all():
        carriers.setdefault(identifier_id, set()).add(persona_id)

    identifiers: list[dict] = []
    if carriers:
        for row in session.execute(
            select(IdentifierRow).where(IdentifierRow.id.in_(list(carriers)))
        ).scalars():
            identifiers.append({
                "type": row.type,
                "value": row.value,
                "value_norm": row.value_norm,
                "meta": row.meta or {},
                "personas": tuple(sorted(carriers.get(row.id, ()))),
            })

    return build(personas, identifiers)
