"""graph.py — the link graph, and the one place transitive inference happens.

Three jobs:

  * `components()`   — connected components at a score threshold, i.e. the
                       candidate actor clusters.
  * `evidence_path()`— the best supporting path between two personas, with the
                       evidence of every edge along it. This is what turns
                       "these two are connected" into a sentence an analyst can
                       read: *via persona 1 (Dr3adPirat3)*.
  * `infer_transitive()` — derive links for pairs inside a component that share
                       no direct evidence.

WHY TRANSITIVE INFERENCE LIVES HERE AND NOT IN resolve.py
---------------------------------------------------------
A->B and B->C at PROBABLE does not make A->C PROBABLE. Keeping the inference in
a separate module behind a separate flag means the `links` table stays a record
of what was *observed*: every row resolve.py writes is `method='pairwise'`, and
anything derived is `method='transitive'` and says so in its evidence. Nothing
later can average an inference back in as though it were a measurement.

Two guards, both in score/attribution.transitive_score(): the derived score is
the weakest edge on the path decayed by 0.75 per hop, and it is capped one band
below that weakest edge's own band. The stricter of the two wins.

A DIRECT OBSERVATION ALWAYS BEATS AN INFERENCE
----------------------------------------------
`links` is unique on (persona_a, persona_b), so a pair cannot hold both a
pairwise and a transitive row. When both are available the pairwise one is kept
and the inference is dropped — an inference is a way to reach a pair we could
not measure, never a second opinion about one we could.

PATH CHOICE
-----------
The best path is the one maximising `min(edges) x decay^(hops-1)` — not the
fewest hops. The derived score is governed by its bottleneck, so a three-hop
path over strong edges can beat a two-hop path through a weak one. On a graph
this size, enumerating simple paths is cheaper than being clever about it.
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import BAND_THRESHOLDS  # noqa: E402
from link.resolve import PairResult  # noqa: E402
from score.attribution import TRANSITIVE_DECAY, transitive_score  # noqa: E402

__all__ = [
    "DEFAULT_THRESHOLD",
    "MAX_PATH_HOPS",
    "PathEvidence",
    "build_graph",
    "components",
    "evidence_path",
    "infer_transitive",
]

#: Edges at or above this join a component. The PROBABLE floor: a cluster built
#: out of POSSIBLE edges is a rumour, not a lead.
DEFAULT_THRESHOLD: float = dict(BAND_THRESHOLDS)["PROBABLE"]

#: Longest path considered when deriving a link. Beyond three hops the decay has
#: taken any derived score below the POSSIBLE floor anyway.
MAX_PATH_HOPS = 3


# ─────────────────────────────────────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────────────────────────────────────

def build_graph(results: Iterable[PairResult],
                threshold: float = DEFAULT_THRESHOLD) -> nx.Graph:
    """Build the link graph from scored pairs.

    Nodes are persona ids; every persona in `results` becomes a node even if it
    ends up isolated, so an unlinked persona is visibly unlinked rather than
    absent. Edges carry score, band and the full evidence list.
    """
    graph = nx.Graph()
    for result in results:
        graph.add_node(result.persona_a)
        graph.add_node(result.persona_b)
        if result.score >= threshold:
            graph.add_edge(
                result.persona_a, result.persona_b,
                score=result.score,
                band=result.band,
                evidence=result.evidence,
                method="pairwise",
            )
    graph.graph["threshold"] = threshold
    return graph


def components(graph: nx.Graph, *, include_singletons: bool = False) -> list[list[int]]:
    """Connected components, each sorted, ordered by size then lowest id.

    These are candidate actor clusters — candidates, not actors. Writing them to
    the `actors` table is a separate decision an operator makes; nothing here
    touches it.
    """
    found = [sorted(c) for c in nx.connected_components(graph)]
    if not include_singletons:
        found = [c for c in found if len(c) > 1]
    return sorted(found, key=lambda c: (-len(c), c[0]))


# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PathEvidence:
    """One supporting path between two personas."""

    nodes: tuple[int, ...]
    edge_scores: tuple[float, ...]
    edge_bands: tuple[str, ...]
    derived_score: float

    @property
    def hops(self) -> int:
        return len(self.edge_scores)

    @property
    def bottleneck(self) -> float:
        return min(self.edge_scores)

    @property
    def intermediates(self) -> tuple[int, ...]:
        return self.nodes[1:-1]

    def describe(self, handles: Optional[Mapping[int, str]] = None) -> str:
        """A one-line account of the path, for the evidence list."""
        handles = handles or {}

        def name(persona_id: int) -> str:
            handle = handles.get(persona_id)
            return f"persona {persona_id} ({handle})" if handle else f"persona {persona_id}"

        via = ", ".join(name(p) for p in self.intermediates) or "no intermediate"
        steps = "; ".join(
            f"{self.nodes[i]}~{self.nodes[i + 1]} {self.edge_bands[i]} "
            f"{self.edge_scores[i]:.3f}"
            for i in range(self.hops)
        )
        return f"via {via} — {steps}"


def evidence_path(
    graph: nx.Graph,
    a: int,
    b: int,
    *,
    decay: float = TRANSITIVE_DECAY,
    max_hops: int = MAX_PATH_HOPS,
) -> Optional[PathEvidence]:
    """The strongest supporting path between two personas, or None.

    "Strongest" means the highest derivable score — bottleneck decayed per hop —
    rather than the fewest hops. Returns None if the personas are not connected
    within `max_hops`, and also for two personas joined by a direct edge, which
    needs no path.
    """
    if a not in graph or b not in graph:
        return None

    best: Optional[PathEvidence] = None
    for nodes in nx.all_simple_paths(graph, a, b, cutoff=max_hops):
        if len(nodes) < 3:
            continue  # a direct edge is an observation, not a path
        scores = tuple(
            graph[nodes[i]][nodes[i + 1]]["score"] for i in range(len(nodes) - 1)
        )
        bands = tuple(
            graph[nodes[i]][nodes[i + 1]]["band"] for i in range(len(nodes) - 1)
        )
        derived = min(scores) * (decay ** (len(scores) - 1))
        if best is None or derived > best.derived_score:
            best = PathEvidence(
                nodes=tuple(nodes), edge_scores=scores,
                edge_bands=bands, derived_score=derived,
            )
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Transitive inference
# ─────────────────────────────────────────────────────────────────────────────

def infer_transitive(
    graph: nx.Graph,
    *,
    handles: Optional[Mapping[int, str]] = None,
    skip_pairs: Iterable[tuple[int, int]] = (),
    decay: float = TRANSITIVE_DECAY,
    max_hops: int = MAX_PATH_HOPS,
) -> list[PairResult]:
    """Derive links for connected pairs that share no direct evidence.

    Args:
        graph: built by `build_graph` at the chosen threshold.
        handles: persona id -> handle, so the evidence names people not numbers.
        skip_pairs: pairs that already hold a direct row. A direct observation
            always wins over an inference, and `links` is unique on the pair.

    Returns:
        PairResults with `method='transitive'`-shaped evidence, ready for
        `resolve.store_links(..., method="transitive")`. Never includes a pair
        that has a direct edge in the graph.
    """
    handles = handles or {}
    skip = {tuple(sorted(pair)) for pair in skip_pairs}
    derived: list[PairResult] = []

    for cluster in components(graph):
        for a, b in itertools.combinations(cluster, 2):
            if graph.has_edge(a, b):
                continue          # directly observed; resolve.py owns this pair
            if (a, b) in skip:
                continue          # a direct row already exists, even if weaker

            path = evidence_path(graph, a, b, decay=decay, max_hops=max_hops)
            if path is None:
                continue

            attribution = transitive_score(path.edge_scores, decay=decay)
            evidence = [{
                "type": "transitive_path",
                "detail": path.describe(handles),
                "path": list(path.nodes),
                "edge_scores": [round(s, 6) for s in path.edge_scores],
                "hops": path.hops,
            }]
            evidence.extend(attribution.evidence)
            evidence.append({
                "type": "not_directly_observed",
                "detail": (
                    f"personas {a} and {b} share no identifier, and neither "
                    f"stylometry nor behaviour was used to reach this link — it "
                    f"is inferred from the path above and must be corroborated "
                    f"before it is treated as a finding"
                ),
            })

            derived.append(PairResult(
                persona_a=a, persona_b=b,
                attribution=attribution,
                evidence=evidence,
                had_hard_evidence=False,
            ))

    return derived
