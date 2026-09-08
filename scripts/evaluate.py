"""evaluate.py — measure the linking engine against fixtures/ground_truth.json.

    python scripts/evaluate.py
    python scripts/evaluate.py --transitive
    python scripts/evaluate.py --preset claude_md
    python scripts/evaluate.py --source db

Every number this prints is **on a synthetic corpus with known ground truth**.
It is a regression measure for the linking engine on 20 personas built to have
findable answers — it is not an accuracy claim about real dark web data, and it
must never be quoted as one.

WHAT IS MEASURED, AND SEPARATELY
--------------------------------
Two blocks, never merged into one headline figure:

  PAIRWISE   direct evidence only. Recall at PROBABLE is 6/8, and that is the
             honest number. Two of the eight expected pairs (9~16 and 10~17)
             share no identifier at all — ground_truth.json says so itself —
             and no amount of pairwise scoring reaches them.

  CLOSURE    what the separate transitive pass adds on top, at a decayed score
             and a capped band. Reported apart from the pairwise figures so the
             6/8 is never quietly inflated into an 8/8.

The second block is the better story anyway. "These two are probably the same
person, and here is the third persona that connects them" is a stronger claim
than an unexplained 8/8, because it comes with the path attached.

EXIT CODE
---------
Non-zero if a designed hard negative exceeds its declared ceiling. That is the
contract the corpus was built to enforce: a system that only ever says yes is
guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The default Windows console codepage is cp1252, which cannot encode the box
# rules or the em dashes below and raises rather than degrading. Ask for UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # already redirected, or not a tty
        pass

from db import MIN_STYLOMETRY_CHARS  # noqa: E402
from link import graph as graph_module  # noqa: E402
from link.resolve import (  # noqa: E402
    PairResult,
    load_corpus,
    load_corpus_from_fixtures,
    resolve_pairs,
)
from score.attribution import (  # noqa: E402
    BAND_ORDER,
    DEFAULT_PRESET,
    weights_for,
)

FIXTURES = ROOT / "fixtures"
BAND_FLOORS = {"CONFIRMED": 0.85, "PROBABLE": 0.65, "POSSIBLE": 0.45}
RULE = "─" * 78


def load_ground_truth() -> dict:
    path = FIXTURES / "ground_truth.json"
    if not path.exists():
        raise SystemExit(f"missing answer key: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────

def score_table(scores: Mapping[tuple[int, int], float],
                positives: set[tuple[int, int]]) -> list[tuple]:
    """Precision / recall / F1 at each band floor, cumulative."""
    rows = []
    for band in ("CONFIRMED", "PROBABLE", "POSSIBLE"):
        floor = BAND_FLOORS[band]
        predicted = {pair for pair, value in scores.items() if value >= floor}
        tp = len(predicted & positives)
        fp = len(predicted - positives)
        fn = len(positives - predicted)
        precision = tp / (tp + fp) if (tp + fp) else float("nan")
        recall = tp / len(positives) if positives else float("nan")
        f1 = (2 * precision * recall / (precision + recall)
              if tp and (precision + recall) else 0.0)
        rows.append((band, floor, precision, recall, f1, tp, fp, fn))
    return rows


def print_table(rows: Sequence[tuple]) -> None:
    print(f"  {'threshold':<20} {'P':>7} {'R':>7} {'F1':>7}   "
          f"{'TP':>3} {'FP':>3} {'FN':>3}")
    for band, floor, precision, recall, f1, tp, fp, fn in rows:
        p = "    n/a" if precision != precision else f"{precision:7.3f}"
        threshold = f">={band} ({floor:.2f})"
        print(f"  {threshold:<20} {p} {recall:7.3f} {f1:7.3f}   "
              f"{tp:3d} {fp:3d} {fn:3d}")


def label(pair: tuple[int, int], handles: Mapping[int, str]) -> str:
    a, b = pair
    return f"{a:>2}~{b:<3} {handles.get(a, '?'):>13} ~ {handles.get(b, ''):<14}"


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

def describe_pair(result: PairResult, handles: Mapping[int, str]) -> str:
    components = result.attribution.components
    def fmt(key: str) -> str:
        value = components.get(key)
        return "  --  " if value is None else f"{value:.3f}"
    return (f"    {label(result.pair, handles)} {result.score:.3f} "
            f"{result.band:<9}  H={fmt('H')} S={fmt('S')} B={fmt('B')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure the linking engine against the answer key."
    )
    parser.add_argument("--source", choices=("fixtures", "db"), default="fixtures",
                        help="read the corpus from fixtures/ (offline, default) "
                             "or from Postgres")
    parser.add_argument("--preset", default=DEFAULT_PRESET,
                        help="weight preset from score/attribution.py")
    parser.add_argument("--no-renormalise", action="store_true",
                        help="count unmeasured components as 0.0")
    parser.add_argument("--transitive", action="store_true",
                        help="also run the closure pass and report what it adds")
    parser.add_argument("--threshold", type=float,
                        default=graph_module.DEFAULT_THRESHOLD,
                        help="minimum score for a graph edge (default: the "
                             "PROBABLE floor)")
    args = parser.parse_args(argv)

    renormalise = not args.no_renormalise
    truth = load_ground_truth()
    positives = {tuple(p) for p in truth["expected_positive_pairs"]}
    hard_negatives = {tuple(c["pair"]): c for c in truth["hard_negatives"]}

    if args.source == "fixtures":
        corpus = load_corpus_from_fixtures()
    else:
        from db import require_schema, session_scope  # noqa: PLC0415
        with session_scope() as session:
            require_schema(session)
            corpus = load_corpus(session)

    results, writeprints, behaviours = resolve_pairs(
        corpus, preset=args.preset, renormalise=renormalise
    )
    by_pair = {r.pair: r for r in results}
    scores = {r.pair: r.score for r in results}
    handles = {pid: p["handle"] for pid, p in corpus.personas.items()}
    weights = weights_for(args.preset)

    # ── header ──────────────────────────────────────────────────────────────
    print(RULE)
    print("Dark Sentinel v2 — linking engine evaluation")
    print("on a synthetic corpus with known ground truth "
          "(fixtures/ground_truth.json)")
    print(RULE)
    print(f"  corpus    {len(corpus.personas)} personas, {len(results)} pairs, "
          f"{truth['actor_count']} actors, {len(positives)} expected positive pairs")
    print(f"  source    {args.source}")
    print(f"  weights   preset {args.preset}: " +
          "  ".join(f"{k} {v:.2f}" for k, v in weights.items()))
    print(f"  unmeasured components renormalised: {renormalise}")
    print("  I is unmeasured for every pair — recon lands in Phase 3, so no pair")
    print("            has certificate, favicon, banner or ETag evidence")

    # ── pairwise ────────────────────────────────────────────────────────────
    print(f"\n{RULE}\nPAIRWISE — direct evidence only (method='pairwise')\n{RULE}")
    print_table(score_table(scores, positives))

    found = sorted((by_pair[p] for p in positives if scores[p] >= BAND_FLOORS["PROBABLE"]),
                   key=lambda r: -r.score)
    missed = sorted((by_pair[p] for p in positives if scores[p] < BAND_FLOORS["PROBABLE"]),
                    key=lambda r: -r.score)

    print(f"\n  reached at PROBABLE or better — {len(found)}/{len(positives)}")
    for result in found:
        print(describe_pair(result, handles))

    if missed:
        print(f"\n  not reached — {len(missed)}/{len(positives)}")
        for result in missed:
            print(describe_pair(result, handles))
        print("\n  Both share no identifier at all, so H = 0 and only S and B remain.")
        print("  The answer key says as much itself: \"9<->16 share nothing hard and")
        print("  must be resolved through the cluster\". Pairwise scoring cannot reach")
        print("  these two and does not pretend to — see the closure block below.")

    # The single most useful robustness number: how much room is there between
    # the weakest pair we accept and the strongest pair we reject? A high recall
    # with a 0.001 margin is one corpus change away from being wrong.
    reachable = [p for p in positives if scores[p] >= BAND_FLOORS["PROBABLE"]]
    negatives = {p: v for p, v in scores.items() if p not in positives}
    worst_negative = max(negatives, key=negatives.get)
    if reachable:
        weakest_positive = min(reachable, key=lambda p: scores[p])
        margin = scores[weakest_positive] - scores[worst_negative]
        print("\n  separation")
        print(f"    weakest accepted positive  {label(weakest_positive, handles)}"
              f" {scores[weakest_positive]:.3f}")
        print(f"    strongest rejected pair    {label(worst_negative, handles)}"
              f" {scores[worst_negative]:.3f}")
        print(f"    margin                     {margin:+.3f}")

    # ── closure ─────────────────────────────────────────────────────────────
    closure: list[PairResult] = []
    if args.transitive:
        graph = graph_module.build_graph(results, threshold=args.threshold)
        clusters = graph_module.components(graph)
        closure = graph_module.infer_transitive(graph, handles=handles)

        print(f"\n{RULE}\nTRANSITIVE CLOSURE — separate pass (method='transitive')"
              f"\n{RULE}")
        print(f"  graph at threshold {args.threshold:.2f}: "
              f"{graph.number_of_edges()} edges, {len(clusters)} multi-persona "
              f"components")
        for cluster in clusters:
            names = ", ".join(f"{p} ({handles.get(p, '?')})" for p in cluster)
            print(f"    component: {names}")

        if not closure:
            print("\n  the closure pass derived no new links")
        else:
            print(f"\n  derived {len(closure)} link(s), each decayed and band-capped:")
            for result in closure:
                mark = "correct" if result.pair in positives else "NOT IN GROUND TRUTH"
                print(f"{describe_pair(result, handles)}   [{mark}]")
                path = next(e for e in result.evidence
                            if e["type"] == "transitive_path")
                print(f"        {path['detail']}")

        combined = dict(scores)
        for result in closure:
            combined[result.pair] = result.score
        print("\n  pairwise + closure, scored together:")
        print_table(score_table(combined, positives))
        false_positives = [r for r in closure if r.pair not in positives]
        print(f"\n  closure added {len(closure)} link(s), "
              f"{len(false_positives)} of them wrong")
        print("  Reported apart from the pairwise block on purpose: an inferred")
        print("  link is a lead with a path attached, not a second measurement.")

    # ── hard negatives ──────────────────────────────────────────────────────
    print(f"\n{RULE}\nHARD NEGATIVES — designed to be refused\n{RULE}")
    failures: list[str] = []
    for pair, case in sorted(hard_negatives.items()):
        result = by_pair[pair]
        ceiling = case["max_band"]
        ok = BAND_ORDER.index(result.band) >= BAND_ORDER.index(ceiling)
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            failures.append(
                f"{pair[0]}~{pair[1]} scored {result.band} ({result.score:.3f}), "
                f"above its {ceiling} ceiling"
            )
        print(f"  [{verdict}] {label(pair, handles)} {result.score:.3f} "
              f"{result.band:<9}  ceiling {ceiling}")
        components = result.attribution.components
        print(f"         H={components['H']:.2f} "
              f"S={components['S']:.3f} B={components['B']:.3f}")
        print(f"         {case['reason']}")

    # ── refusals ────────────────────────────────────────────────────────────
    print(f"\n{RULE}\nREFUSALS — where the engine declines to score\n{RULE}")
    for case in truth.get("refusal_cases", []):
        persona_id = case["persona_id"]
        handle = handles.get(persona_id, "?")
        if persona_id in writeprints.refused:
            print(f"  persona {persona_id} ({handle}): stylometry returned None — "
                  f"{writeprints.refused[persona_id]} characters after identifier")
            print(f"    masking, below the {MIN_STYLOMETRY_CHARS}-character floor. "
                  f"Every pair it appears in is")
            print("    scored with S unmeasured rather than with a number "
                  "from two sentences.")
            continue

        dropped = case.get("dropped_values", [])
        if dropped:
            held = {corpus.identifier_display.get(key)
                    for key in corpus.identifiers.get(persona_id, set())}
            leaked = [value for value in dropped if value in held]
            state = ("STILL PRESENT — checksum validation failed"
                     if leaked else "dropped, as required")
            print(f"  persona {persona_id} ({handle}): "
                  f"{len(dropped)} checksum-failing wallet(s) — {state}")
            for value in dropped:
                print(f"      {value}")

    # ── verdict ─────────────────────────────────────────────────────────────
    print(f"\n{RULE}")
    if failures:
        print("FAILED — a designed near-miss was scored above its ceiling:")
        for line in failures:
            print(f"  {line}")
        print(RULE)
        return 1

    print("All designed hard negatives held below their ceiling.")
    print("Figures above are on a synthetic corpus with known ground truth; they")
    print("measure this engine against this answer key, not real-world accuracy.")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
