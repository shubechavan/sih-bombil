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
from api.queries import link_summaries, persona_summaries  # noqa: E402
from api.schemas import GraphEdge, GraphNode, GraphPayload  # noqa: E402
from db import Persona  # noqa: E402

router = APIRouter(tags=["graph"])

__all__ = ["router"]

NOTE = (
    "Nodes are personas, not actors — the graph shows why personas were merged, "
    "so it does not collapse them first. Edge thickness is the attribution "
    "score; click an edge for the evidence that produced it."
)


@router.get("/graph", response_model=GraphPayload)
def get_graph(
    session=Depends(get_session),
    min_score: float = Query(0.0, ge=0.0, le=1.0,
                             description="drop edges below this score"),
    source_id: int | None = Query(None, description="restrict to one source"),
    include_isolated: bool = Query(True,
                                   description="keep personas with no surviving edge"),
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

    return GraphPayload(nodes=nodes, edges=edges, min_score=min_score, note=NOTE)
