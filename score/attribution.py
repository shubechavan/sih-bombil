"""attribution.py — the confidence formula, isolated.

    A = w_H·H + w_S·S + w_B·B + w_I·I     ->  0.0 .. 1.0

    H  hard identifier overlap      S  stylometric similarity
    B  behavioural similarity       I  infrastructure overlap

    CONFIRMED >=0.85 | PROBABLE 0.65-0.85 | POSSIBLE 0.45-0.65 | WEAK <0.45

Nothing here touches the database, the corpus or an extractor. It takes four
numbers and returns a score, a band, and the reasons for both.

TWO DESIGN POINTS THAT ARE NOT ARITHMETIC
-----------------------------------------

**An unmeasured component is not a component measured as zero.** Pass ``None``
for a component that could not be assessed — infrastructure before Phase 3
lands, stylometry below the 300-character floor — and its weight is
redistributed across the components that *were* measured. Pass ``0.0`` only
when you looked and genuinely found nothing.

That distinction decides whether the top band is reachable at all. With no
recon, ``I`` is unmeasured for every pair in Phase 2. Counting it as 0.0 caps
every score at ``1 - w_I = 0.85``, which is exactly the CONFIRMED floor, so a
pair sharing a full PGP fingerprint *and* writing alike *and* posting in the
same hours still lands in PROBABLE. That is a scoring artefact, not an
intelligence judgement.

**A renormalised score says so out loud.** Every result carries an evidence
entry for each component it could not assess, because an analyst reading a
CONFIRMED row needs to know which components actually contributed to it. The
row explains itself or it is not shippable.

WEIGHTS
-------
Two presets, both convex. ``claude_md`` is the formula exactly as documented.
``measured`` shifts 0.05 from S to B and is the default; see the module note on
WEIGHT_PRESETS for the numbers behind that, and scripts/evaluate.py --preset to
compare them on the corpus.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import BAND_THRESHOLDS, band_for  # noqa: E402  single source of the bands

__all__ = [
    "Attribution",
    "BAND_ORDER",
    "COMPONENTS",
    "DEFAULT_PRESET",
    "RENORMALISE_UNMEASURED",
    "TRANSITIVE_DECAY",
    "WEIGHT_PRESETS",
    "attribution_score",
    "band_for",
    "demote",
    "hard_identifier_score",
    "transitive_score",
    "weights_for",
]

#: Component keys, in the order they appear in the formula.
COMPONENTS: tuple[str, ...] = ("H", "S", "B", "I")

#: Bands strongest to weakest. Index order is used by demote().
BAND_ORDER: tuple[str, ...] = tuple(band for band, _ in BAND_THRESHOLDS)

#: Human labels, used when a component has to explain itself.
COMPONENT_LABELS: dict[str, str] = {
    "H": "hard identifier overlap",
    "S": "stylometric similarity",
    "B": "behavioural similarity",
    "I": "infrastructure overlap",
}

#: Why a component is missing, when the caller does not supply a better reason.
DEFAULT_UNMEASURED_REASONS: dict[str, str] = {
    "H": "no identifier comparison was performed for this pair",
    "S": "stylometry did not run — at least one persona is below the "
         "300-character floor",
    "B": "no timestamped activity for at least one persona",
    "I": "infrastructure not assessed — no recon data for this persona pair",
}


# ─────────────────────────────────────────────────────────────────────────────
# Weights
#
# `claude_md` is the documented formula. `measured` moves 0.05 from S to B.
#
# Measured on the fixture corpus (190 pairs, 8 known positives, 2 designed
# near-misses), both signals rank the positives perfectly once blended
# (ROC-AUC 1.000 each), so neither dominates on ranking alone. The reason to
# prefer B is the near-misses specifically: on 2~20 behaviour scores 0.27 where
# stylometry scores 0.55, and on 4~15 behaviour scores 0.30 where stylometry
# scores 0.47. Shifting 0.05 to B buys margin on exactly the pairs the corpus
# was built to fool the system with, and costs nothing on the true positives.
#
# It is a tuning, not a correction: at every weighting tried, precision and
# recall at PROBABLE were identical. Keep both presets so the claim stays
# checkable — scripts/evaluate.py --preset claude_md reproduces the other one.
# ─────────────────────────────────────────────────────────────────────────────

WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "claude_md": {"H": 0.40, "S": 0.25, "B": 0.20, "I": 0.15},
    "measured":  {"H": 0.40, "S": 0.20, "B": 0.25, "I": 0.15},
}

DEFAULT_PRESET = "measured"

#: Redistribute an unmeasured component's weight over the measured ones.
RENORMALISE_UNMEASURED = True

#: Per-hop multiplier for an inferred (non-adjacent) link. See transitive_score.
TRANSITIVE_DECAY = 0.75


def weights_for(preset: str) -> dict[str, float]:
    """Return a preset's weights, or raise naming the preset that was asked for."""
    try:
        return dict(WEIGHT_PRESETS[preset])
    except KeyError:
        raise KeyError(
            f"unknown weight preset {preset!r}; known presets: "
            f"{', '.join(sorted(WEIGHT_PRESETS))}"
        ) from None


def demote(band: str) -> str:
    """The next band down. WEAK is the floor and demotes to itself."""
    index = BAND_ORDER.index(band)
    return BAND_ORDER[min(index + 1, len(BAND_ORDER) - 1)]


# ─────────────────────────────────────────────────────────────────────────────
# Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Attribution:
    """A score, its band, and everything needed to justify both.

    `evidence` is the list that ends up in `links.evidence`. It always holds at
    least the component breakdown, plus one `component_not_assessed` entry per
    component that could not be measured.
    """

    score: float
    band: str
    components: dict[str, Optional[float]]
    weights: dict[str, float]
    renormalised: bool
    unmeasured: tuple[str, ...]
    evidence: list[dict] = field(default_factory=list)

    def __repr__(self) -> str:
        parts = ", ".join(
            f"{k}={'--' if v is None else format(v, '.3f')}"
            for k, v in self.components.items()
        )
        return f"<Attribution {self.score:.3f} {self.band} ({parts})>"


# ─────────────────────────────────────────────────────────────────────────────
# H — hard identifier overlap
# ─────────────────────────────────────────────────────────────────────────────

def hard_identifier_score(weights: Iterable[float]) -> float:
    """Combine the weights of every shared identifier into one 0..1 term.

    Noisy-OR: ``1 - Π(1 - wᵢ)``. Two shared identifiers reinforce each other
    without the total ever leaving the unit interval — a shared jabber id (0.85)
    plus a normalised handle collision (0.45) gives 0.9175, where summing would
    give 1.30 and clipping would erase the difference between "two good reasons"
    and "four good reasons".

    An empty sequence returns 0.0, which is a *measurement*: we compared the
    identifier sets and they were disjoint. Callers must not translate that into
    an unmeasured H.
    """
    product = 1.0
    for weight in weights:
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"identifier weight {weight} is outside 0..1")
        product *= (1.0 - weight)
    return 1.0 - product


# ─────────────────────────────────────────────────────────────────────────────
# The formula
# ─────────────────────────────────────────────────────────────────────────────

def _validate(key: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"component {key} = {value} is outside 0..1")
    return value


def attribution_score(
    h: Optional[float],
    s: Optional[float],
    b: Optional[float],
    i: Optional[float],
    *,
    preset: str = DEFAULT_PRESET,
    weights: Optional[dict[str, float]] = None,
    renormalise: Optional[bool] = None,
    reasons: Optional[dict[str, str]] = None,
) -> Attribution:
    """Score one persona pair.

    Args:
        h, s, b, i: component values in 0..1, or None where the component could
            not be assessed. None is not zero — see the module docstring.
        preset: named entry in WEIGHT_PRESETS. Ignored if `weights` is given.
        weights: explicit weights, for callers sweeping a grid.
        renormalise: redistribute unmeasured weight over the measured
            components. Defaults to RENORMALISE_UNMEASURED.
        reasons: component key -> why it could not be measured, so the evidence
            entry can name the persona and the number instead of a generic note.

    Raises:
        ValueError: if a component is outside 0..1, or if nothing was measured.
    """
    if renormalise is None:
        renormalise = RENORMALISE_UNMEASURED
    active = dict(weights) if weights is not None else weights_for(preset)
    reasons = reasons or {}

    values = {
        "H": _validate("H", h),
        "S": _validate("S", s),
        "B": _validate("B", b),
        "I": _validate("I", i),
    }
    measured = [k for k in COMPONENTS if values[k] is not None]
    unmeasured = tuple(k for k in COMPONENTS if values[k] is None)

    if not measured:
        raise ValueError(
            "no component was measured; a pair with nothing to compare cannot "
            "be scored and must not be written as a link"
        )

    total = sum(active[k] * values[k] for k in measured)
    divisor = sum(active[k] for k in measured)
    applied = bool(unmeasured) and renormalise
    score = total / divisor if applied else total
    score = min(max(score, 0.0), 1.0)

    evidence: list[dict] = [{
        "type": "component_breakdown",
        "detail": "  ".join(
            f"{k} {'not assessed' if values[k] is None else format(values[k], '.3f')}"
            f" x{active[k]:.2f}"
            for k in COMPONENTS
        ),
        "components": {k: values[k] for k in COMPONENTS},
        "weights": {k: active[k] for k in COMPONENTS},
        "renormalised": applied,
    }]

    for key in unmeasured:
        reason = reasons.get(key) or DEFAULT_UNMEASURED_REASONS[key]
        if applied:
            detail = (
                f"{reason}; its {active[key]:.2f} weight was redistributed over "
                f"{', '.join(measured)}, so this score reflects "
                f"{'/'.join(measured)} only"
            )
        else:
            detail = f"{reason}; counted as 0.0 in the weighted sum"
        evidence.append({
            "type": "component_not_assessed",
            "component": key,
            "label": COMPONENT_LABELS[key],
            "detail": detail,
            "weight": active[key],
        })

    return Attribution(
        score=score,
        band=band_for(score),
        components=values,
        weights=active,
        renormalised=applied,
        unmeasured=unmeasured,
        evidence=evidence,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transitive inference
# ─────────────────────────────────────────────────────────────────────────────

def transitive_score(
    path_scores: Sequence[float],
    *,
    decay: float = TRANSITIVE_DECAY,
) -> Attribution:
    """Score a link inferred through intermediate personas rather than observed.

    A->B and B->C at PROBABLE does not make A->C PROBABLE. Two independent
    guards enforce that, and the stricter one wins:

      1. **Decay.** The derived score is the *weakest* edge on the path — a
         chain is no stronger than its worst link — multiplied by
         ``decay ** (hops - 1)``. At the default 0.75, two PROBABLE edges of
         0.85 produce 0.64, which is POSSIBLE.
      2. **A one-band cap.** The result is capped one band below the weakest
         edge's own band, regardless of what the arithmetic produced. This
         binds when the decay alone would not, and it is what makes the
         guarantee categorical rather than a property of the chosen constant.

    The returned Attribution has no components: an inferred link is not a
    measurement of A and C, and pretending otherwise would let a derived edge
    be averaged into later passes as though it were evidence. `method` on the
    stored row records which pass produced it.

    Raises:
        ValueError: if fewer than two edges are given — a single edge is a
            direct observation, not an inference.
    """
    scores = [float(x) for x in path_scores]
    if len(scores) < 2:
        raise ValueError(
            "a transitive inference needs at least two edges; one edge is a "
            "direct observation and belongs to the pairwise pass"
        )
    for value in scores:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"edge score {value} is outside 0..1")

    weakest = min(scores)
    hops = len(scores)
    derived = weakest * (decay ** (hops - 1))

    ceiling = demote(band_for(weakest))
    band = ceiling if BAND_ORDER.index(band_for(derived)) < BAND_ORDER.index(ceiling) \
        else band_for(derived)

    evidence = [{
        "type": "transitive_inference",
        "detail": (
            f"derived through {hops - 1} intermediate persona"
            f"{'s' if hops > 2 else ''}, not directly observed: weakest edge "
            f"{weakest:.3f} ({band_for(weakest)}) x decay {decay:.2f}^{hops - 1} "
            f"= {derived:.3f}, capped at {ceiling} — one band below the weakest "
            f"edge on the path"
        ),
        "weakest_edge": weakest,
        "hops": hops,
        "decay": decay,
    }]

    return Attribution(
        score=derived,
        band=band,
        components={k: None for k in COMPONENTS},
        weights={},
        renormalised=False,
        unmeasured=COMPONENTS,
        evidence=evidence,
    )
