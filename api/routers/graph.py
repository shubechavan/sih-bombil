"""graph.py — nodes and edges for the link view.

A node is a persona, not an actor: the graph exists to show *why* personas were
merged, so collapsing them first would hide the evidence. `actor_id` rides along
so a client can colour by cluster.

Every edge carries its full evidence array and its four components in the tagged
form, because clicking an edge is how an analyst answers "why do you think
that?" — and the honest answer for the I term on this corpus is a sentence, not
a number.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, Query  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.deps import get_session  # noqa: E402
from api.queries import (  # noqa: E402
    link_summaries,
    persona_summaries,
    trust_edges_for,
)
from api.schemas import (  # noqa: E402
    EntityEdge,
    EntityGraphPayload,
    EntityNode,
    GraphEdge,
    GraphNode,
    GraphPayload,
)
from db import Persona  # noqa: E402
from link.entity_graph import entity_graph_for  # noqa: E402

router = APIRouter(tags=["graph"])

__all__ = ["router"]

NOTE = (
    "Nodes are personas, not actors — the graph shows why personas were merged, "
    "so it does not collapse them first. Edge thickness is the attribution "
    "score; click an edge for the evidence that produced it."
)

TRUST_NOTE = (
    "Dashed edges are shared buyers, not attribution. Two vendors rated by the "
    "same people may be a supply chain, a migration that kept its customers, or "
    "nothing at all — measured against ground truth the overlap separates true "
    "pairs from false ones worse than chance (ROC-AUC 0.389), so it carries no "
    "score and no band. Read it as a lead to check, never as evidence."
)


@router.get("/graph", response_model=GraphPayload)
def get_graph(
    session=Depends(get_session),
    min_score: float = Query(0.0, ge=0.0, le=1.0,
                             description="drop edges below this score"),
    source_id: int | None = Query(None, description="restrict to one source"),
    include_isolated: bool = Query(True,
                                   description="keep personas with no surviving edge"),
    include_trust: bool = Query(True,
                                description="include buyer-mediated context edges"),
) -> GraphPayload:
    personas = list(session.execute(select(Persona).order_by(Persona.id)).scalars())
    if source_id is not None:
        personas = [p for p in personas if p.source_id == source_id]

    summaries = persona_summaries(session, [p.id for p in personas])
    handles = {pid: s.handle for pid, s in summaries.items()}

    links = link_summaries(session, min_score=min_score, handles=handles)
    allowed = {p.id for p in personas}
    links = [l for l in links if l.persona_a in allowed and l.persona_b in allowed]

    connected = {l.persona_a for l in links} | {l.persona_b for l in links}
    kept = personas if include_isolated else [p for p in personas if p.id in connected]

    nodes = [
        GraphNode(
            id=p.id,
            handle=p.handle,
            source_id=p.source_id,
            source_name=summaries[p.id].source_name if p.id in summaries else None,
            category=p.category,
            actor_id=p.actor_id,
            # Carried onto the node so a refused persona is visibly refused in
            # the graph, not merely a node whose edges happen to be thin.
            stylometry_refused=(
                summaries[p.id].stylometry_refused if p.id in summaries else False
            ),
        )
        for p in kept
    ]

    edges = [
        GraphEdge(
            source=l.persona_a,
            target=l.persona_b,
            score=l.score,
            band=l.band,
            method=l.method,
            components=l.components,
            evidence=l.evidence,
        )
        for l in links
    ]

    trust = trust_edges_for(session, [p.id for p in kept]) if include_trust else []

    return GraphPayload(
        nodes=nodes,
        edges=edges,
        trust_edges=trust,
        trust_note=TRUST_NOTE if trust else "",
        min_score=min_score,
        note=NOTE,
    )


@router.get("/graph/entity", response_model=EntityGraphPayload)
def get_entity_graph(
    session=Depends(get_session),
    source_id: int | None = Query(None, description="restrict to one source"),
    actor_id: int | None = Query(None, description="restrict to one actor"),
    include_trust: bool = Query(True,
                                description="include buyer-mediated context edges"),
    shared_only: bool = Query(False,
                              description="keep only identifiers more than one "
                                          "persona published, and the personas "
                                          "reaching them"),
) -> EntityGraphPayload:
    """The same corpus as `/graph`, projected around identifiers.

    A separate endpoint rather than a mode on `/graph`, because the two payloads
    disagree on what a node *is*: `GraphNode.id` is an integer persona id, and
    an entity node needs a string id and a kind. Widening the existing model to
    carry both would make every client branch on a field to know which shape it
    received, and would change a response other pages already depend on.
    """
    personas = list(session.execute(select(Persona).order_by(Persona.id)).scalars())
    if source_id is not None:
        personas = [p for p in personas if p.source_id == source_id]
    if actor_id is not None:
        personas = [p for p in personas if p.actor_id == actor_id]

    persona_ids = [p.id for p in personas]
    graph = entity_graph_for(session, persona_ids)

    nodes = graph.nodes
    edges = graph.edges
    if shared_only:
        # Drop the leaves, keep the hubs and whatever reaches them. Useful on a
        # projector: the unshared identifiers are the bulk of the nodes and none
        # of the argument.
        hubs = {n.id for n in nodes if n.kind != "persona" and n.shared}
        edges = tuple(e for e in edges if e.target in hubs)
        reachable = hubs | {e.source for e in edges}
        nodes = tuple(n for n in nodes if n.id in reachable)

    kept_personas = [
        int(n.value) for n in nodes if n.kind == "persona"
    ]
    trust = trust_edges_for(session, kept_personas) if include_trust else []

    return EntityGraphPayload(
        nodes=[
            EntityNode(
                id=n.id,
                kind=n.kind,
                label=n.label,
                value=n.value,
                personas=list(n.personas),
                shared=n.shared,
                detail=n.detail,
            )
            for n in nodes
        ],
        edges=[
            EntityEdge(source=e.source, target=e.target, relation=e.relation)
            for e in edges
        ],
        trust_edges=trust,
        trust_note=TRUST_NOTE if trust else "",
        hub_count=sum(1 for n in nodes if n.kind != "persona" and n.shared),
        note=graph.note,
    )
