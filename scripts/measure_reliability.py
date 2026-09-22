"""measure_reliability.py — should source reliability weight the evidence?

    python scripts/measure_reliability.py

`sources.reliability` has been in the schema since Phase 0 and read by nothing.
The obvious next move is to fold it into the attribution score: evidence from a
source you trust less should count for less. That is a plausible claim, and the
point of this script is that plausible is not the same as measured.

It runs the linking engine over the fixture corpus, then re-scores every pair
under three weighting schemes, and compares each against ground truth on two
numbers that do not depend on where the band thresholds sit:

  ROC-AUC    does it rank true pairs above false ones? 1.0 is perfect, 0.5 is
             a coin flip. Threshold-free, so uniformly shrinking every score
             cannot flatter it.
  margin     weakest positive minus strongest negative, over all 190 pairs.
             Note this is a stricter number than the +0.508 the README quotes:
             that one is measured among the pairs that reached a band, while
             this one includes 9~16 and 10~17, the two positives pairwise
             scoring cannot reach at all and does not pretend to. Both are
             honest; they answer different questions.

Nothing here writes to the database or changes any score. It prints numbers and
a recommendation, and the recommendation on this corpus is "do not adopt" —
see the closing note for why that is a property of the corpus and not a
judgement about the idea.

This mirrors what link/trust.py did for buyer overlap: measure the signal, find
it does not carry, and say so in the place someone would look for it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default Windows console codepage is cp1252, which cannot encode the box
# rules below and raises rather than degrading. Ask for UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

import json  # noqa: E402

from link.resolve import load_corpus_from_fixtures, resolve_pairs  # noqa: E402
from link.trust import _roc_auc  # noqa: E402

WIDTH = 78

#: How a pair's two sources combine into one factor. Each is a defensible
#: reading of "trust the weaker source" through to "average them".
SCHEMES: dict[str, str] = {
    "min": "the weaker of the two sources decides",
    "mean": "the two sources average",
    "geometric": "square root of the product — between the other two",
}


def combine(scheme: str, a: float, b: float) -> float:
    if scheme == "min":
        return min(a, b)
    if scheme == "mean":
        return (a + b) / 2
    return (a * b) ** 0.5


def separation(scores: dict[tuple[int, int], float],
               positives: set[tuple[int, int]]) -> tuple[float, float, float]:
    """(weakest accepted positive, strongest rejected negative, margin)."""
    pos = [s for pair, s in scores.items() if pair in positives]
    neg = [s for pair, s in scores.items() if pair not in positives]
    if not pos or not neg:
        return (0.0, 0.0, 0.0)
    return (min(pos), max(neg), min(pos) - max(neg))


def main() -> int:
    print("─" * WIDTH)
    print("Does source reliability improve attribution? — measured, not assumed")
    print("─" * WIDTH)

    truth = json.loads(
        (ROOT / "fixtures" / "ground_truth.json").read_text(encoding="utf-8")
    )
    positives = {tuple(sorted(p)) for p in truth["expected_positive_pairs"]}

    corpus = load_corpus_from_fixtures()
    results, _writeprints, _behaviours, _infra = resolve_pairs(corpus)

    # The corpus dict carries source_id, not reliability; read it off the
    # fixture source list so this runs with no database at all.
    sources = json.loads(
        (ROOT / "fixtures" / "sources.json").read_text(encoding="utf-8")
    )
    by_source = {s["id"]: float(s.get("reliability", 0.5)) for s in sources}
    reliability = {
        pid: by_source.get(persona.get("source_id"), 0.5)
        for pid, persona in corpus.personas.items()
    }

    spread = sorted(set(by_source.values()))
    print(f"\n  corpus     {len(corpus.personas)} personas, {len(results)} pairs, "
          f"{len(positives)} known positive pairs")
    print(f"  sources    {len(by_source)}, reliability "
          f"{min(spread):.2f}–{max(spread):.2f}  {spread}")

    baseline = {
        tuple(sorted(r.pair)): r.score for r in results
    }
    labels = [1 if pair in positives else 0 for pair in baseline]

    rows: list[tuple[str, float, float, float, float]] = []

    base_auc = _roc_auc(list(baseline.values()), labels)
    low, high, margin = separation(baseline, positives)
    rows.append(("baseline (no weighting)", base_auc, low, high, margin))

    for scheme, _why in SCHEMES.items():
        weighted = {
            pair: score * combine(scheme,
                                  reliability.get(pair[0], 0.5),
                                  reliability.get(pair[1], 0.5))
            for pair, score in baseline.items()
        }
        auc = _roc_auc(list(weighted.values()), labels)
        low, high, margin = separation(weighted, positives)
        rows.append((f"weighted by {scheme}", auc, low, high, margin))

    print()
    print("  Margin below is over all 190 pairs, so it is stricter than the")
    print("  +0.508 the README quotes for pairs that reached a band.")
    print()
    print(f"  {'scheme':<26} {'ROC-AUC':>9} {'min pos':>9} {'max neg':>9} "
          f"{'margin':>9}")
    print("  " + "─" * (WIDTH - 4))
    for name, auc, low, high, margin in rows:
        delta = "" if name.startswith("baseline") else f"  ({auc - base_auc:+.4f})"
        print(f"  {name:<26} {auc:>9.4f} {low:>9.3f} {high:>9.3f} "
              f"{margin:>+9.3f}{delta}")

    best = max(rows[1:], key=lambda r: r[1])
    improved = best[1] > base_auc + 1e-9

    print()
    print("─" * WIDTH)
    print("VERDICT")
    print("─" * WIDTH)
    if improved:
        print(f"  {best[0]} ranks better than the baseline "
              f"({best[1]:.4f} vs {base_auc:.4f}).")
        print("  Worth a second look on a corpus with more sources before it is")
        print("  wired into the score — one corpus is not a calibration.")
    else:
        print(f"  No scheme beats the baseline ROC-AUC of {base_auc:.4f}.")
        print()
        print("  That is what the corpus predicts rather than a surprise. All three")
        print(f"  sources sit between {min(spread):.2f} and {max(spread):.2f} — a span of "
              f"{max(spread) - min(spread):.2f} — so every pair")
        print("  is scaled by nearly the same factor, the ranking does not move at")
        print("  all, and the margin gets worse: multiplying by a number below 1")
        print("  drags the true positives down toward the band floors while the")
        print("  negatives, already low, have less room to fall.")
        print()
        print("  Reliability is displayed on the actor profile and it orders the")
        print("  scheduler's visits. It does not weight any score, and it should")
        print("  not until a corpus exists with enough spread to measure it on.")
    print("─" * WIDTH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
