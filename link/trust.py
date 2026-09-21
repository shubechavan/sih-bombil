"""trust.py — buyer-mediated vendor relationships. Context, never a score.

    python -m link.trust --source db
    python -m link.trust --source db --measure     # the argument, re-run

WHAT THIS IS
------------
Two vendors rated by the same buyers have a relationship worth an analyst's
attention: a shared customer base, a supply chain, a market migration that
brought its buyers along. `trust_edges()` finds those and reports the overlap
with the buyers that produced it.

WHAT THIS IS NOT
----------------
It is not an attribution signal, and `--measure` is in this module so the claim
can be re-checked rather than believed. Measured against
`fixtures/ground_truth.json`, over the 78 pairs among the 13 personas that have
any feedback — the fair population, because a pair with no feedback on either
side is not a failure of the signal, it is an absence of one:

    ROC-AUC                 0.389    — worse than a coin flip
    mean Jaccard, true      0.0250
    mean Jaccard, false     0.0852   — the wrong pairs score *higher*
    measurable positives    4 of 8   — forum_beta sells nothing, so no pair
                                       involving it has any buyer overlap at all

Over all 190 pairs it reads 0.475, which is closer to chance only because the
112 pairs with no feedback tie at zero; that number measures coverage, not
discrimination, so `--measure` prints both and leads with the honest one.

Ranked by overlap the top of the list is (18,20) 0.444, (1,4) 0.444, (7,8)
0.429, (3,4) 0.429 — same-market vendors drawing from one buyer pool, and not
one of them a true pair. The mechanism is not subtle: buyers shop around. A
vendor and their own alt account both being rated by `runegate50` says that
`runegate50` buys a lot.

That is the whole reason this module returns an `evidence`-shaped structure and
writes nothing to `links`. Feeding it into A would be the site-broadcast
mistake a second time — a signal that shifts a whole block without re-ranking
anything inside it.

There is also a design reason to keep buyers out of `personas`: forty-odd buyer
handles in the pairwise loop would have the engine proposing that buyers are
vendors' alt accounts, and every one of those proposals would be noise.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from sqlalchemy import select  # noqa: E402

from db import Feedback, Persona  # noqa: E402

__all__ = [
    "TrustEdge",
    "buyer_sets",
    "trust_edges",
    "MIN_SHARED_BUYERS",
    "NOT_A_SCORE",
]

#: One shared buyer is a coincidence on a market with an 18-buyer pool. Two is
#: the smallest number that is worth an analyst's time, and it is a display
#: threshold — nothing downstream branches on it.
MIN_SHARED_BUYERS = 2

NOT_A_SCORE = (
    "Shared-buyer overlap is relationship context, not evidence of identity. "
    "Measured against ground truth it separates true pairs from false ones "
    "worse than chance (ROC-AUC 0.389), so it contributes nothing to the "
    "attribution score."
)


@dataclass
class TrustEdge:
    """Two vendors and the buyers they have in common."""

    persona_a: int
    persona_b: int
    handle_a: str
    handle_b: str
    shared: list[str] = field(default_factory=list)
    buyers_a: int = 0
    buyers_b: int = 0

    @property
    def jaccard(self) -> float:
        union = self.buyers_a + self.buyers_b - len(self.shared)
        return len(self.shared) / union if union else 0.0

    def explain(self) -> str:
        names = ", ".join(self.shared[:4])
        if len(self.shared) > 4:
            names += f", +{len(self.shared) - 4} more"
        return (f"{len(self.shared)} shared buyer(s) — {names}. "
                f"{self.handle_a} has {self.buyers_a}, "
                f"{self.handle_b} has {self.buyers_b}.")

    def to_dict(self) -> dict:
        return {
            "persona_a": self.persona_a,
            "persona_b": self.persona_b,
            "handle_a": self.handle_a,
            "handle_b": self.handle_b,
            "shared_buyers": list(self.shared),
            "shared_count": len(self.shared),
            "buyers_a": self.buyers_a,
            "buyers_b": self.buyers_b,
            "overlap": round(self.jaccard, 4),
            "detail": self.explain(),
            # Travels with the row. A number in a payload gets used; a number
            # in a payload that carries this string has to be argued with.
            "affects_score": False,
            "note": NOT_A_SCORE,
        }


def buyer_sets(session) -> tuple[dict[int, set[str]], dict[int, str]]:
    """persona_id -> the normalised buyers who rated them, and their handles."""
    buyers: dict[int, set[str]] = {}
    for persona_id, buyer in session.execute(
        select(Feedback.persona_id, Feedback.buyer_normalized)
    ):
        if buyer:
            buyers.setdefault(persona_id, set()).add(buyer)
    handles = {
        pid: handle
        for pid, handle in session.execute(select(Persona.id, Persona.handle))
    }
    return buyers, handles


def trust_edges(session, *, min_shared: int = MIN_SHARED_BUYERS) -> list[TrustEdge]:
    """Every vendor pair sharing at least `min_shared` buyers, strongest first."""
    buyers, handles = buyer_sets(session)
    edges: list[TrustEdge] = []
    for a, b in itertools.combinations(sorted(buyers), 2):
        shared = buyers[a] & buyers[b]
        if len(shared) < min_shared:
            continue
        edges.append(TrustEdge(
            persona_a=a, persona_b=b,
            handle_a=handles.get(a, str(a)), handle_b=handles.get(b, str(b)),
            shared=sorted(shared),
            buyers_a=len(buyers[a]), buyers_b=len(buyers[b]),
        ))
    edges.sort(key=lambda e: (-e.jaccard, -len(e.shared), e.persona_a, e.persona_b))
    return edges


# ─────────────────────────────────────────────────────────────────────────────
# The measurement, kept runnable
# ─────────────────────────────────────────────────────────────────────────────

def _roc_auc(scores: list[float], labels: list[int]) -> float:
    """Rank-based AUC with tie correction. No sklearn import for eight pairs."""
    pairs = sorted(zip(scores, labels))
    ranks: list[float] = [0.0] * len(pairs)
    index = 0
    while index < len(pairs):
        stop = index
        while stop + 1 < len(pairs) and pairs[stop + 1][0] == pairs[index][0]:
            stop += 1
        average = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[position] = average
        index = stop + 1
    positives = sum(1 for _, label in pairs if label)
    negatives = len(pairs) - positives
    if not positives or not negatives:
        return float("nan")
    rank_sum = sum(r for r, (_, label) in zip(ranks, pairs) if label)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def measure(session) -> int:
    """Re-run the argument against ground_truth.json and print it."""
    truth_path = ROOT / "fixtures" / "ground_truth.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    true_pairs = {
        tuple(sorted((int(p[0]), int(p[1]))))
        for p in truth["expected_positive_pairs"]
    }

    buyers, handles = buyer_sets(session)
    everyone = sorted(pid for (pid,) in session.execute(select(Persona.id)))
    #: Personas a buyer could possibly have rated. Everyone else is a forum
    #: author, and a forum sells nothing.
    sellers = sorted(pid for pid in everyone if buyers.get(pid))

    def score_population(ids: Sequence[int]):
        scores, labels, rows = [], [], []
        for a, b in itertools.combinations(ids, 2):
            set_a, set_b = buyers.get(a, set()), buyers.get(b, set())
            union = set_a | set_b
            overlap = len(set_a & set_b) / len(union) if union else 0.0
            label = 1 if (a, b) in true_pairs else 0
            scores.append(overlap)
            labels.append(label)
            rows.append((overlap, (a, b), label))
        return scores, labels, rows

    print("\n  shared-buyer overlap as an identity signal")
    print("  " + "─" * 68)
    print(f"    personas                {len(everyone)}, of which {len(sellers)} "
          f"have any feedback at all")
    print(f"    true pairs              {len(true_pairs)}")

    headline = None
    for ids, title, gloss in (
        (sellers, "vendors with feedback",
         "the fair test: pairs where the signal exists"),
        (everyone, "every persona",
         "coverage, not discrimination — the 112 pairs with no feedback\n"
         "                            on either side all tie at 0.000, which "
         "drags AUC toward 0.5"),
    ):
        scores, labels, rows = score_population(ids)
        positives = sum(labels)
        true_scores = [s for s, l in zip(scores, labels) if l]
        false_scores = [s for s, l in zip(scores, labels) if not l]
        auc = _roc_auc(scores, labels)
        if headline is None:
            headline = (auc, rows)
        print(f"\n    over {title} — {gloss}")
        print(f"      pairs scored          {len(scores)}   "
              f"({positives} of the true pairs are in here)")
        print(f"      ROC-AUC               {auc:.3f}   (0.500 is a coin flip)")
        for label, values in (("true ", true_scores), ("false", false_scores)):
            mean = f"{sum(values)/len(values):.4f}" if values else "n/a"
            print(f"      mean overlap, {label}   {mean}")

    print("\n    strongest overlaps, whole corpus:")
    for overlap, (a, b), label in sorted(headline[1], reverse=True)[:5]:
        mark = "TRUE PAIR" if label else "not a pair"
        print(f"      {handles.get(a, a):>14} ~ {handles.get(b, b):<14} "
              f"{overlap:.3f}  {mark}")
    print(f"\n  {NOT_A_SCORE}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Buyer-mediated vendor relationships. Does not score."
    )
    parser.add_argument("--source", choices=("db",), default="db",
                        help="feedback lives in the database; there is no "
                             "fixtures path because there is nothing to compute "
                             "offline that load_feedback has not already stored")
    parser.add_argument("--min-shared", type=int, default=MIN_SHARED_BUYERS)
    parser.add_argument("--measure", action="store_true",
                        help="re-run the ground-truth measurement behind the "
                             "decision not to score this")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from db import require_schema, session_scope  # noqa: PLC0415

    with session_scope() as session:
        require_schema(session)
        if args.measure:
            return measure(session)

        edges = trust_edges(session, min_shared=args.min_shared)
        if args.json:
            print(json.dumps([e.to_dict() for e in edges], indent=2))
            return 0

        print(f"\n  {len(edges)} vendor pair(s) share at least "
              f"{args.min_shared} buyer(s)")
        print("  " + "─" * 66)
        for edge in edges[:20]:
            print(f"    {edge.handle_a:>14} ~ {edge.handle_b:<14} "
                  f"overlap {edge.jaccard:.3f}  {edge.explain()}")
        print(f"\n  {NOT_A_SCORE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
