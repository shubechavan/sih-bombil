"""behaviour.py — what a persona does, as opposed to how they write.

    B = 0.70·posting hours + 0.20·category + 0.05·trade vocabulary + 0.05·day of week

Four sub-signals, all cosine or Jaccard in 0..1. The weights are lopsided on
purpose and every one of them was measured on the fixture corpus (190 pairs,
8 known positives, 2 designed near-misses) before being chosen.

POSTING HOURS — 0.70, the only strong sub-signal
------------------------------------------------
Intra-actor 0.834 vs inter-actor 0.252, ROC-AUC 0.962. A rebranded vendor keeps
their sleep schedule, so the histogram survives a migration in a way a handle
does not. Smoothed circularly before comparison: hour 23 is adjacent to hour 0,
and actor_001 posts across midnight (22,23,0,1,2,3) — an unsmoothed histogram
treats that as two unrelated clusters. Smoothing lifts intra-actor similarity
from 0.656 to 0.834 and the AUC from 0.957 to 0.962.

It is emphatically *not* sufficient alone: min positive 0.744 sits *below* max
negative 0.933, because unrelated actors keep overlapping office hours. Hours
rank well but do not separate. The other families exist to break exactly those
ties.

CATEGORY — 0.20, and a trap for anyone tuning this later
--------------------------------------------------------
!! Category Jaccard scores BOTH designed hard negatives at exactly 1.000. !!

2~20 (NordicPharm / AtlasMeds) are both pharma; 4~15 (silk_hands / plainbagel)
are both docs. In isolation this sub-signal has a flattering ROC-AUC of 0.936
and would confirm both near-misses without hesitation. It is safe here *only*
because it sits behind a dominant hour term that scores those same pairs 0.08
and 0.18 — the pairs are killed before category is consulted.

The two are complementary, which is why category earns 0.20 rather than a token
weight: the negatives hours cannot separate (unrelated actors sharing office
hours) are mostly cross-category, and category settles them. Removing it costs
real margin — dropping to 0.10 moved the worst true negative from 0.333 to
0.364 and lost a CONFIRMED.

But raise it and the near-misses come back. If you are tuning: check what your
change does to pairs 2~20 and 4~15 before you look at anything else. Category
agreement is the single most dangerous input in this module, and a plausible
"category matters, let's weight it properly" edit is how a near-miss gets
confirmed.

TRADE VOCABULARY — 0.05 token weight, because it ranks backwards
-----------------------------------------------------------------
Kept because CLAUDE.md lists it and it is a real signal on live data, but it
does not work on this corpus and it is not allowed to matter here.

ROC-AUC 0.676, and worse, it is *inverted* at the extremes: it scores true
positive 9~16 at 0.190 while scoring hard negative 2~20 at 0.570. The cause is
structural rather than a tuning failure — the vector tracks venue, not author.
Personas 9 and 16 are one vendor's forum account and market account, and forum
posts discuss other people's markets while market posts are listings, so their
trade-term profiles diverge. Personas 2 and 20 are two unrelated vendors both
selling pharma on markets, so theirs converge.

Five repairs were measured and all five stayed inverted (min positive below max
hard negative): binary presence cosine (AUC 0.629), IDF-weighted cosine (0.667),
sublinear tf x idf (0.637), binary x idf (0.621), presence Jaccard (0.629). No
reweighting of a venue-driven vector fixes a venue confound. Demoted rather than
deleted, and it must not be promoted without a corpus where venue and author are
not entangled.

DAY OF WEEK — 0.05 token weight, because it is noise here
----------------------------------------------------------
ROC-AUC 0.505 — indistinguishable from a coin flip. The fixture generator picks
a uniformly random date in each persona's window and overrides only the hour, so
there is no day-of-week signal in the corpus to find. Its floor is also high
(inter-actor mean 0.665), meaning it adds a near-constant to every pair and
inflates scores without discriminating.

Computed and stored because live data plausibly carries a weekday/weekend
rhythm, and because a sub-signal that reads 0.5 is worth being able to *show*.
Weighted so it cannot move a band.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

__all__ = [
    "SUBSIGNAL_WEIGHTS",
    "TRADE_LEXICON",
    "Behaviour",
    "BehaviourSet",
    "build",
    "smooth_circular",
]

SUBSIGNAL_WEIGHTS: dict[str, float] = {
    "posting_hours": 0.70,
    "category": 0.20,
    "trade_vocabulary": 0.05,
    "day_of_week": 0.05,
}

#: Circular smoothing kernel over the hour histogram. Deliberately narrow: a
#: wider kernel washes out the difference between a six-hour window and a
#: twelve-hour one, which is most of the signal.
HOUR_KERNEL: tuple[float, float, float] = (0.25, 0.50, 0.25)

#: Terms a dark-market trader uses habitually. Matched whole-word,
#: case-insensitively; multi-word entries are matched as phrases.
TRADE_LEXICON: tuple[str, ...] = (
    "escrow", "finalize early", "fe", "multisig", "vendor bond", "feedback",
    "refund", "dispute", "exit scam", "tumbler", "mixer", "cashout",
    "stealth", "reship", "dead drop", "drop", "vacuum sealed", "mylar",
    "moisture barrier", "decoy", "discreet packaging", "discreet",
    "tracking", "courier", "customs", "seizure", "return to sender",
    "autoshop", "fresh drop", "batch", "purity", "lab tested", "uncut",
    "bulk", "sample", "opsec", "burner", "mirror",
)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-signals
# ─────────────────────────────────────────────────────────────────────────────

def smooth_circular(histogram: Sequence[float],
                    kernel: Sequence[float] = HOUR_KERNEL) -> np.ndarray:
    """Smooth a 24-bucket histogram treating hour 23 as adjacent to hour 0.

    Without the wrap, an actor posting 22:00-03:00 looks like two separate
    clusters and compares poorly against themselves on another site.
    """
    values = np.asarray(histogram, dtype=np.float64)
    width = len(kernel)
    offset = width // 2
    out = np.zeros(len(values), dtype=np.float64)
    for index in range(len(values)):
        out[index] = sum(
            kernel[k] * values[(index + k - offset) % len(values)]
            for k in range(width)
        )
    return out


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    if not a.any() or not b.any():
        return 0.0
    return float(np.clip(np.dot(_unit(a), _unit(b)), 0.0, 1.0))


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    union = left | right
    return len(left & right) / len(union) if union else 0.0


_TRADE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (term, re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", re.I))
    for term in TRADE_LEXICON
)


def _trade_vector(text: str) -> np.ndarray:
    return np.array([len(pattern.findall(text)) for _, pattern in _TRADE_PATTERNS],
                    dtype=np.float64)


# ─────────────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Behaviour:
    """One persona's behavioural profile."""

    persona_id: int
    hour_histogram: np.ndarray        # 24 buckets, normalised, unsmoothed
    hour_smoothed: np.ndarray         # what comparison actually uses
    day_histogram: np.ndarray         # 7 buckets, normalised
    categories: frozenset[str]
    trade_vector: np.ndarray
    post_count: int

    @property
    def peak_hours(self) -> list[int]:
        """The hours holding most of this persona's activity, for the evidence list."""
        if not self.hour_histogram.any():
            return []
        threshold = float(self.hour_histogram.max()) * 0.5
        return [h for h in range(24) if self.hour_histogram[h] >= threshold]

    @property
    def trade_terms(self) -> list[str]:
        return [TRADE_LEXICON[i] for i in np.nonzero(self.trade_vector)[0]]


@dataclass
class BehaviourSet:
    """Behavioural profiles for a corpus."""

    profiles: dict[int, Behaviour] = field(default_factory=dict)
    refused: dict[int, str] = field(default_factory=dict)

    def get(self, persona_id: int) -> Optional[Behaviour]:
        return self.profiles.get(persona_id)

    def refusal_reason(self, persona_id: int) -> Optional[str]:
        return self.refused.get(persona_id)

    def components(self, a: int, b: int) -> Optional[dict[str, float]]:
        """Every sub-signal for one pair, unweighted. None if either is missing."""
        left, right = self.profiles.get(a), self.profiles.get(b)
        if left is None or right is None:
            return None
        return {
            "posting_hours": _cosine(left.hour_smoothed, right.hour_smoothed),
            "category": _jaccard(left.categories, right.categories),
            "trade_vocabulary": _cosine(left.trade_vector, right.trade_vector),
            "day_of_week": _cosine(left.day_histogram, right.day_histogram),
        }

    def similarity(self, a: int, b: int) -> Optional[float]:
        """The blended B term, or None if either persona has no activity."""
        parts = self.components(a, b)
        if parts is None:
            return None
        return float(np.clip(
            sum(SUBSIGNAL_WEIGHTS[name] * value for name, value in parts.items()),
            0.0, 1.0,
        ))

    def explain(self, a: int, b: int) -> list[str]:
        """Plain-language reasons, for the evidence list."""
        parts = self.components(a, b)
        if parts is None:
            return []
        left, right = self.profiles[a], self.profiles[b]
        lines = [
            f"posting-hour overlap {parts['posting_hours']:.0%} "
            f"(peaks {_hours_text(left.peak_hours)} vs {_hours_text(right.peak_hours)} UTC)"
        ]
        shared_categories = left.categories & right.categories
        if shared_categories:
            lines.append(
                f"category overlap {parts['category']:.2f} "
                f"({', '.join(sorted(shared_categories))})"
            )
        shared_terms = set(left.trade_terms) & set(right.trade_terms)
        if shared_terms:
            lines.append(
                f"trade vocabulary {parts['trade_vocabulary']:.2f} "
                f"({', '.join(sorted(shared_terms)[:5])})"
            )
        lines.append(f"day-of-week profile {parts['day_of_week']:.2f}")
        return lines


def _hours_text(hours: Sequence[int]) -> str:
    if not hours:
        return "none"
    return "/".join(f"{h:02d}" for h in hours[:6]) + ("…" if len(hours) > 6 else "")


# ─────────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────────

def build(posts: Mapping[int, Sequence[Mapping]]) -> BehaviourSet:
    """Build behavioural profiles.

    Args:
        posts: persona id -> that persona's posts, each a mapping with
            `posted_at` (datetime), `body` (str) and optionally `category`.

    Returns:
        A BehaviourSet. Personas with no timestamped posts are refused — B is
        then unmeasured for every pair they appear in, not zero.

    Note there is no character floor here. Behaviour is a count of when someone
    acted, not a judgement about how they write, so a persona with two posts
    still has a usable (if thin) hour histogram where stylometry would refuse.
    """
    profiles: dict[int, Behaviour] = {}
    refused: dict[int, str] = {}

    for persona_id, rows in posts.items():
        timestamps = [r["posted_at"] for r in rows
                      if isinstance(r.get("posted_at"), datetime)]
        if not timestamps:
            refused[persona_id] = (
                f"behaviour not assessed — persona {persona_id} has no "
                f"timestamped posts"
            )
            continue

        hours = np.zeros(24, dtype=np.float64)
        days = np.zeros(7, dtype=np.float64)
        for moment in timestamps:
            hours[moment.hour] += 1
            days[moment.weekday()] += 1

        categories = Counter(
            r["category"] for r in rows if r.get("category")
        )
        text = " ".join(r.get("body") or "" for r in rows)

        profiles[persona_id] = Behaviour(
            persona_id=persona_id,
            hour_histogram=hours / hours.sum(),
            hour_smoothed=smooth_circular(hours / hours.sum()),
            day_histogram=days / days.sum(),
            categories=frozenset(categories),
            trade_vector=_trade_vector(text),
            post_count=len(rows),
        )

    return BehaviourSet(profiles=profiles, refused=refused)
