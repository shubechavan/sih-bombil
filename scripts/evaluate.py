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
from link import infra as infra_module  # noqa: E402
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

def component(result: PairResult, key: str) -> str:
    """A component value, or a dash where it was not assessed.

    Every component is Optional. Formatting one with `:.3f` without checking is
    how a report crashes on exactly the pair that was most interesting.
    """
    value = result.attribution.components.get(key)
    return "  --  " if value is None else f"{value:.3f}"


def describe_pair(result: PairResult, handles: Mapping[int, str]) -> str:
    return (f"    {label(result.pair, handles)} {result.score:.3f} "
            f"{result.band:<9}  H={component(result, 'H')} "
            f"S={component(result, 'S')} B={component(result, 'B')} "
            f"I={component(result, 'I')}")


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
    parser.add_argument("--infra", choices=("off", "site-broadcast"),
                        default="off",
                        help="off (default): I is measured only where a persona "
                             "controls a fingerprinted host, which on this "
                             "corpus is nowhere. site-broadcast: a deliberately "
                             "UNSOUND mode that hands every vendor its market's "
                             "fingerprint, kept only to measure the harm")
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

    findings = infra_module.load_findings()
    baseline_infra = infra_module.build(
        corpus.personas, findings, identifiers=corpus.identifiers
    )
    infra = baseline_infra
    if args.infra == "site-broadcast":
        infra = infra_module.build(
            corpus.personas, findings, identifiers=corpus.identifiers,
            mode="site-broadcast",
        )

    results, writeprints, behaviours, infra = resolve_pairs(
        corpus, preset=args.preset, renormalise=renormalise, infra=infra
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
    print(f"  infra     mode {args.infra}: "
          f"{len(infra.hosts)}/{len(corpus.personas)} personas control a "
          f"fingerprinted host")

    if args.infra == "off":
        print("  Recon ran and produced findings for all 3 sources, but they are")
        print("  site-level: they fingerprint the marketplace, not the vendor. No")
        print("  persona here controls a host of their own, so I is unmeasured on")
        print("  every pair and its 0.15 weight is redistributed. See link/infra.py.")
    else:
        print(f"\n{RULE}")
        print("  !! site-broadcast is DELIBERATELY UNSOUND. It is not a setting.")
        print(RULE)
        print("  It hands every vendor the fingerprint of the market they post on,")
        print("  which makes I a constant across the 40 alpha×gamma pairs — the same")
        print("  number for the 4 real migrations and the 36 that are not, so it")
        print("  cannot rank anything. It exists so the harm below is measured")
        print("  rather than asserted. Never turn it on to score a real case.")

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

    # ── what site-broadcast cost ────────────────────────────────────────────
    if args.infra == "site-broadcast":
        base_results, _, _, _ = resolve_pairs(
            corpus, preset=args.preset, renormalise=renormalise,
            infra=baseline_infra, writeprints=writeprints, behaviours=behaviours,
        )
        base = {r.pair: r for r in base_results}

        print(f"\n{RULE}\nWHAT SITE-BROADCAST DID — every pair, against "
              f"--infra off\n{RULE}")
        print_table(score_table({p: r.score for p, r in base.items()}, positives))
        print("  ↑ --infra off        ↓ --infra site-broadcast")
        print_table(score_table(scores, positives))

        moved = [
            (p, base[p], by_pair[p]) for p in sorted(base)
            if base[p].band != by_pair[p].band
        ]
        promoted = [m for m in moved
                    if BAND_ORDER.index(m[2].band) < BAND_ORDER.index(m[1].band)]
        demoted = [m for m in moved
                   if BAND_ORDER.index(m[2].band) > BAND_ORDER.index(m[1].band)]

        wrong = [m for m in promoted if m[0] not in positives]
        print(f"\n  falsely promoted — {len(wrong)} pair(s) not in the answer key "
              f"moved UP a band")
        for pair, before, after in wrong:
            print(f"    {label(pair, handles)} {before.band} "
                  f"({before.score:.3f}) → {after.band} ({after.score:.3f})"
                  f"   I={component(after, 'I')}")
        if not wrong:
            print("    none")

        real = [m for m in demoted if m[0] in positives]
        print(f"\n  demoted true positives — {len(real)} real migration(s) "
              f"moved DOWN a band")
        for pair, before, after in real:
            print(f"    {label(pair, handles)} {before.band} "
                  f"({before.score:.3f}) → {after.band} ({after.score:.3f})"
                  f"   I={component(after, 'I')}")
        if not real:
            print("    none")

        # A band change is a coarse instrument. The pairs that did not cross a
        # floor still moved, and on this corpus that is where the damage is:
        # precision survives while the room underneath it disappears.
        base_scores = {p: r.score for p, r in base.items()}
        base_negatives = {p: v for p, v in base_scores.items() if p not in positives}
        base_worst = max(base_negatives, key=base_negatives.get)
        base_reachable = [p for p in positives
                          if base_scores[p] >= BAND_FLOORS["PROBABLE"]]
        base_weakest = min(base_reachable, key=lambda p: base_scores[p])
        base_margin = base_scores[base_weakest] - base_scores[base_worst]
        new_margin = scores[weakest_positive] - scores[worst_negative] \
            if reachable else float("nan")

        print(f"\n  separation margin  {base_margin:+.3f} → {new_margin:+.3f}"
              f"   ({new_margin - base_margin:+.3f})")
        print(f"    strongest rejected pair {label(worst_negative, handles)}"
              f" {base_scores[worst_negative]:.3f} → {scores[worst_negative]:.3f}")

        climbers = sorted(
            ((by_pair[p].score - base_scores[p], p) for p in base_negatives),
            reverse=True,
        )[:5]
        print("\n  biggest gains among pairs that are NOT in the answer key:")
        for delta, pair in climbers:
            print(f"    {label(pair, handles)} {base_scores[pair]:.3f} → "
                  f"{by_pair[pair].score:.3f}  ({delta:+.3f})"
                  f"   I={component(by_pair[pair], 'I')}")

        thin = [p for _, p in climbers
                if any(pid in writeprints.refused for pid in p)]
        if thin:
            print("\n  Note what is at the top of that list. Those pairs contain the")
            print("  persona stylometry REFUSED for having too little text, so their")
            print("  S is unmeasured and renormalisation was carrying them. Giving")
            print("  them a measured I of 0.96 replaces 'we do not know' with a")
            print("  number — manufacturing confidence about the one persona the")
            print("  system was right to say nothing about.")

        print("\n  Every effect above has one cause. A vendor does not run the")
        print("  market's web server, so I here is a property of the site: constant")
        print("  across every alpha×gamma pair, and a measured 0.0 for anyone who")
        print("  migrated to the forum instead. It lifts pairs that share a landlord")
        print("  and penalises pairs that do not — which is the opposite of the")
        print("  question 'are these two the same person'.")

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
        print(f"         H={component(result, 'H')} S={component(result, 'S')} "
              f"B={component(result, 'B')} I={component(result, 'I')}")
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
