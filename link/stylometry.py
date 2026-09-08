"""stylometry.py — a writeprint per persona, and the cosine between two of them.

The writeprint is four feature families concatenated:

    char 3-5 gram TF-IDF   0.75    the workhorse
    function word freqs    0.10
    punctuation ratios     0.08
    orthographic shape     0.07    sentence/word length, TTR, caps, emoji

Each family is L2-normalised and scaled by sqrt(its weight) before being
concatenated, so a single dot product of two unit writeprints *is* the weighted
sum of the per-family cosines. That keeps the cached vector self-sufficient:
`similarity()` needs nothing but the two vectors.

WHY CHAR N-GRAMS CARRY MOST OF THE WEIGHT
-----------------------------------------
Measured on the fixture corpus, char 3-5 grams separate the 8 known positives
from all 180 negatives perfectly (ROC-AUC 1.000) and correctly rate the designed
near-miss 4~15 at 0.32. The other three families are far more easily fooled by
it: punctuation alone scores that pair 0.990 and orthographic shape alone scores
it 0.996, because silk_hands and plainbagel were built to share a clause-joining
rule and a sentence-length band. They earn their place by breaking ties the
n-grams leave open, not by leading — hence 0.75 against 0.25 split three ways.
Raising the minority families measurably worsens the near-misses.

IDENTIFIERS ARE MASKED BEFORE FEATURISING
-----------------------------------------
Shared PGP fingerprints and wallet addresses appear verbatim in post bodies —
up to 456 characters of one persona's text in this corpus. Left in, character
n-grams partly re-encode the identifier overlap, so `0.40·H + 0.20·S` counts the
same evidence twice and the evidence list claims an independence it does not
have. Masking raises intra-actor similarity (0.698 -> 0.710) *and* lowers the
near-miss 2~20 (0.500 -> 0.487): it removes a confound, it does not cost signal.

THE 300-CHARACTER FLOOR
-----------------------
Below `MIN_STYLOMETRY_CHARS` of masked prose, `build()` yields None and the pair
is scored without an S term rather than with a confident number derived from two
sentences. The floor is applied *after* masking, so a persona whose text is
mostly a wallet address is refused on the prose it actually wrote.

CACHING
-------
TF-IDF is corpus-relative: the same text yields a different vector once the
fitted vocabulary changes. `feature_version` therefore carries this module's
version *and* a fingerprint of the exact masked inputs, and vectors whose
versions differ are never compared — they are recomputed.

The fingerprint is taken over the inputs rather than over the fitted vocabulary
on purpose. A vocabulary hash can only be computed *after* the fit, which is the
expensive step, so it could verify a cache but never let you skip one. Hashing
the inputs is equivalent for correctness — same code and same corpus give the
same vocabulary — and it can be computed before fitting, so `load_or_build()`
can look the cache up and skip the fit entirely.
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import MIN_STYLOMETRY_CHARS, Writeprint as WriteprintRow, utcnow  # noqa: E402

__all__ = [
    "FAMILY_WEIGHTS",
    "FEATURE_VERSION",
    "MIN_STYLOMETRY_CHARS",
    "Writeprint",
    "WriteprintSet",
    "build",
    "corpus_version",
    "load",
    "load_or_build",
    "mask_identifiers",
    "store",
]

#: Bump when any feature family changes shape or meaning. Cached vectors from a
#: different version are recomputed, never compared.
FEATURE_VERSION = "sty-1"

FAMILY_WEIGHTS: dict[str, float] = {
    "char_ngram": 0.75,
    "function_words": 0.10,
    "punctuation": 0.08,
    "shape": 0.07,
}

CHAR_NGRAM_RANGE = (3, 5)
CHAR_MAX_FEATURES = 5000

#: Closed-class English words. Frequencies of these track syntax rather than
#: subject matter, which is the point: a vendor's market listings and their
#: forum replies share almost no topic vocabulary.
FUNCTION_WORDS: tuple[str, ...] = tuple("""
a about above after again against all also am an and any are aren't as at be because been before
being below between both but by can cannot could couldn't did didn't do does doesn't doing don't
down during each few for from further had hadn't has hasn't have haven't having he her here hers
herself him himself his how however i if in into is isn't it its itself just me more most much
must my myself no nor not now of off on once only or other ought our ours ourselves out over own
same she should shouldn't since so some still such than that the their theirs them themselves then
there these they this those though through to too under until up very was wasn't we were weren't
what when where whether which while who whom why will with would wouldn't you your yours yourself
yourselves
""".split())

#: Ratios are per character, so this stays comparable across post lengths.
PUNCTUATION: tuple[str, ...] = tuple(".,;:!?'\"()[]{}<>-—–…/\\|&%$#*+=~@^_`")

_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)
_SENTENCE_RE = re.compile(r"[.!?]+|\.{3}|\n+")
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # pictographs, emoticons, transport, symbols
    "☀-➿"           # misc symbols and dingbats
    "⬀-⯿"           # arrows and geometric shapes
    "←-⇿"
    "✀-➿"
    "]"
)

# ─────────────────────────────────────────────────────────────────────────────
# Identifier masking
# ─────────────────────────────────────────────────────────────────────────────

_MASK = " ID "

#: Structural fallbacks, for anything Phase 1 did not extract. Order matters:
#: the longer, more specific forms are tried before the generic base58 shape.
_IDENTIFIER_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"-----BEGIN PGP[\s\S]+?-----END PGP[^-]*-----"),
    re.compile(r"\b[a-z2-7]{56}\.onion\b", re.I),
    re.compile(r"\b05[0-9a-f]{64}\b", re.I),                  # session id
    re.compile(r"\b[A-F0-9]{40}\b", re.I),                    # pgp fingerprint
    re.compile(r"\b(?:bc1|ltc1|tb1)[02-9ac-hj-np-z]{20,80}\b", re.I),
    re.compile(r"\b0x[a-fA-F0-9]{40}\b"),                     # eth
    re.compile(r"\b[48][0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b"),   # xmr
    re.compile(r"\b[13LM3][1-9A-HJ-NP-Za-km-z]{25,34}\b"),    # base58 p2pkh/p2sh
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),          # email / jabber
    re.compile(r"(?<![\w])@[A-Za-z][A-Za-z0-9_]{3,31}\b"),    # telegram
)


def mask_identifiers(text: str, values: Iterable[str] = ()) -> str:
    """Replace identifiers with a sentinel so they cannot shape the writeprint.

    Known values first — those come from `persona_identifiers` and are exact —
    then the structural patterns, which catch anything the extractor missed.
    Longest known value first, so a mirror onion inside a URL is masked whole.
    """
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        text = text.replace(value, _MASK)
    for pattern in _IDENTIFIER_PATTERNS:
        text = pattern.sub(_MASK, text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Feature families
# ─────────────────────────────────────────────────────────────────────────────

def _function_word_vector(text: str) -> np.ndarray:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return np.zeros(len(FUNCTION_WORDS), dtype=np.float64)
    counts: dict[str, int] = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    total = len(words)
    return np.array([counts.get(w, 0) / total for w in FUNCTION_WORDS], dtype=np.float64)


def _punctuation_vector(text: str) -> np.ndarray:
    total = max(len(text), 1)
    return np.array([text.count(p) / total for p in PUNCTUATION], dtype=np.float64)


def _shape_vector(text: str) -> tuple[np.ndarray, dict]:
    """Orthographic habits, plus the readable version for the features column.

    Each entry is scaled into roughly 0..1 so no single one dominates the
    family's cosine purely through its units.
    """
    words = _WORD_RE.findall(text)
    sentences = [s for s in _SENTENCE_RE.split(text) if s.strip()]
    letters = [c for c in text if c.isalpha()]
    emoji = _EMOJI_RE.findall(text)

    n_words = max(len(words), 1)
    n_sentences = max(len(sentences), 1)
    n_letters = max(len(letters), 1)
    n_chars = max(len(text), 1)

    avg_word_len = float(np.mean([len(w) for w in words])) if words else 0.0
    avg_sentence_len = float(np.mean([len(s.split()) for s in sentences])) if sentences else 0.0
    type_token = len({w.lower() for w in words}) / n_words
    caps_ratio = sum(1 for c in letters if c.isupper()) / n_letters
    shout_rate = sum(1 for w in words if len(w) > 2 and w.isupper()) / n_words
    apostrophe_rate = sum(1 for w in words if "'" in w) / n_words
    emoji_rate = len(emoji) / n_sentences
    double_space_rate = text.count("  ") / n_chars
    ellipsis_rate = len(re.findall(r"\.\.\.|…", text)) / n_sentences
    bang_rate = text.count("!") / n_sentences
    semicolon_rate = text.count(";") / n_sentences
    emdash_rate = len(re.findall(r"[—–]", text)) / n_sentences
    comma_rate = text.count(",") / n_sentences

    readable = {
        "avg_word_length": round(avg_word_len, 4),
        "avg_sentence_words": round(avg_sentence_len, 4),
        "type_token_ratio": round(type_token, 4),
        "capitalisation_ratio": round(caps_ratio, 4),
        "shouted_word_rate": round(shout_rate, 4),
        "apostrophe_word_rate": round(apostrophe_rate, 4),
        "emoji_per_sentence": round(emoji_rate, 4),
        "emoji_set": sorted(set(emoji)),
        "double_space_rate": round(double_space_rate, 6),
        "ellipsis_per_sentence": round(ellipsis_rate, 4),
        "exclamation_per_sentence": round(bang_rate, 4),
        "semicolon_per_sentence": round(semicolon_rate, 4),
        "emdash_per_sentence": round(emdash_rate, 4),
        "comma_per_sentence": round(comma_rate, 4),
        "word_count": len(words),
        "sentence_count": len(sentences),
    }

    vector = np.array([
        min(avg_word_len / 12.0, 1.0),
        min(avg_sentence_len / 45.0, 1.0),
        type_token,
        caps_ratio,
        min(shout_rate * 10, 1.0),
        min(apostrophe_rate * 5, 1.0),
        min(emoji_rate, 1.0),
        min(double_space_rate * 50, 1.0),
        min(ellipsis_rate, 1.0),
        min(bang_rate / 2.0, 1.0),
        min(semicolon_rate, 1.0),
        min(emdash_rate, 1.0),
        min(comma_rate / 4.0, 1.0),
    ], dtype=np.float64)
    return np.nan_to_num(vector), readable


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


# ─────────────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Writeprint:
    """One persona's cached writeprint."""

    persona_id: int
    char_count: int
    vector: np.ndarray
    features: dict
    feature_version: str

    def similarity(self, other: "Writeprint") -> float:
        if self.feature_version != other.feature_version:
            raise ValueError(
                f"writeprints were built by different extractors "
                f"({self.feature_version} vs {other.feature_version}); "
                f"recompute both before comparing"
            )
        return float(np.clip(np.dot(self.vector, other.vector), 0.0, 1.0))


@dataclass
class WriteprintSet:
    """Writeprints for a corpus, sharing one fitted vocabulary."""

    prints: dict[int, Writeprint] = field(default_factory=dict)
    refused: dict[int, int] = field(default_factory=dict)   # persona_id -> chars
    feature_version: str = FEATURE_VERSION
    #: True when these vectors were read from the writeprints table rather than
    #: recomputed. Reported by the CLI so a cache that silently never hits is
    #: visible rather than merely slow.
    from_cache: bool = False

    def __contains__(self, persona_id: int) -> bool:
        return persona_id in self.prints

    def get(self, persona_id: int) -> Optional[Writeprint]:
        return self.prints.get(persona_id)

    def similarity(self, a: int, b: int) -> Optional[float]:
        """Cosine between two personas, or None if either was refused."""
        left, right = self.prints.get(a), self.prints.get(b)
        if left is None or right is None:
            return None
        return left.similarity(right)

    def refusal_reason(self, persona_id: int) -> Optional[str]:
        """Why stylometry declined to score this persona, for the evidence list."""
        if persona_id not in self.refused:
            return None
        return (
            f"stylometry did not run — persona {persona_id} has "
            f"{self.refused[persona_id]} characters of prose after identifier "
            f"masking, below the {MIN_STYLOMETRY_CHARS}-character floor"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────────

def _prepare(
    texts: Mapping[int, str],
    identifiers: Mapping[int, Sequence[str]],
    min_chars: int,
) -> tuple[dict[int, str], dict[int, int], str]:
    """Mask, apply the character floor, and derive the feature version.

    Cheap: no TF-IDF fit happens here, which is what lets `load_or_build()`
    resolve a cache hit without paying for the fit it would replace.
    """
    masked: dict[int, str] = {}
    refused: dict[int, int] = {}
    for persona_id, text in texts.items():
        clean = mask_identifiers(
            unicodedata.normalize("NFC", text or ""), identifiers.get(persona_id, ())
        )
        clean = re.sub(r"[ \t]+", " ", clean).strip()
        if len(clean) < min_chars:
            refused[persona_id] = len(clean)
        else:
            masked[persona_id] = clean
    return masked, refused, corpus_version(masked)


def corpus_version(masked_texts: Mapping[int, str]) -> str:
    """`FEATURE_VERSION` plus a fingerprint of the exact masked corpus.

    Two vectors may only be compared when this string matches: same extractor,
    same inputs, therefore the same fitted vocabulary. Change a post, add a
    persona, or bump FEATURE_VERSION and every cached vector is invalidated
    rather than silently mixed with new ones.
    """
    digest = hashlib.sha256()
    for persona_id in sorted(masked_texts):
        digest.update(f"{persona_id}\0".encode("utf-8"))
        digest.update(masked_texts[persona_id].encode("utf-8"))
        digest.update(b"\0")
    return f"{FEATURE_VERSION}:{digest.hexdigest()[:12]}"


def load_or_build(
    texts: Mapping[int, str],
    identifiers: Optional[Mapping[int, Sequence[str]]] = None,
    *,
    session=None,
    min_chars: int = MIN_STYLOMETRY_CHARS,
) -> tuple[WriteprintSet, bool]:
    """Reuse cached writeprints when they match, otherwise build.

    Returns `(writeprints, from_cache)`. The cache is used only when every
    persona that clears the floor already has a row at exactly this
    `feature_version`; a partial match is treated as a miss, because a corpus
    that gained a persona has a different vocabulary and the old vectors are no
    longer comparable.
    """
    if session is None:
        return build(texts, identifiers, min_chars=min_chars), False

    masked, refused, version = _prepare(texts, identifiers or {}, min_chars)
    if masked:
        cached = load(session, version)
        if cached and set(masked) <= set(cached):
            return (
                WriteprintSet(
                    prints={pid: cached[pid] for pid in masked},
                    refused=refused,
                    feature_version=version,
                    from_cache=True,
                ),
                True,
            )
    return build(texts, identifiers, min_chars=min_chars), False


def build(
    texts: Mapping[int, str],
    identifiers: Optional[Mapping[int, Sequence[str]]] = None,
    *,
    min_chars: int = MIN_STYLOMETRY_CHARS,
) -> WriteprintSet:
    """Build writeprints for every persona that clears the character floor.

    Args:
        texts: persona id -> all of that persona's text, concatenated.
        identifiers: persona id -> known identifier values, masked out before
            featurising so S stays independent of H.
        min_chars: the floor, in characters of masked prose.

    Returns:
        A WriteprintSet. Personas below the floor are absent from `.prints` and
        listed in `.refused` with their character count.
    """
    masked, refused, version = _prepare(texts, identifiers or {}, min_chars)

    if not masked:
        return WriteprintSet(prints={}, refused=refused, feature_version=version)

    order = sorted(masked)
    corpus = [masked[pid] for pid in order]

    vectorizer = TfidfVectorizer(
        analyzer="char",          # not char_wb: spacing habits are part of the signal
        ngram_range=CHAR_NGRAM_RANGE,
        max_features=CHAR_MAX_FEATURES,
        sublinear_tf=True,
        lowercase=False,          # capitalisation habits are a feature, not noise
    )
    char_matrix = vectorizer.fit_transform(corpus)

    prints: dict[int, Writeprint] = {}
    for row, persona_id in enumerate(order):
        text = masked[persona_id]
        families = {
            "char_ngram": _unit(np.asarray(char_matrix[row].todense()).ravel()),
            "function_words": _unit(_function_word_vector(text)),
            "punctuation": _unit(_punctuation_vector(text)),
        }
        shape_vector, readable = _shape_vector(text)
        families["shape"] = _unit(shape_vector)

        combined = _unit(np.concatenate([
            families[name] * np.sqrt(FAMILY_WEIGHTS[name])
            for name in ("char_ngram", "function_words", "punctuation", "shape")
        ]))

        prints[persona_id] = Writeprint(
            persona_id=persona_id,
            char_count=len(text),
            vector=combined.astype(np.float32),
            features={
                **readable,
                "family_weights": FAMILY_WEIGHTS,
                "char_ngram_range": list(CHAR_NGRAM_RANGE),
                "vector_dim": int(combined.size),
                "masked_chars": len(texts[persona_id]) - len(text),
                "top_function_words": _top_function_words(text),
            },
            feature_version=version,
        )

    return WriteprintSet(prints=prints, refused=refused, feature_version=version)


def _top_function_words(text: str, limit: int = 8) -> list[list]:
    """The most-used function words, for the readable features column."""
    vector = _function_word_vector(text)
    ranked = np.argsort(vector)[::-1][:limit]
    return [[FUNCTION_WORDS[i], round(float(vector[i]), 5)]
            for i in ranked if vector[i] > 0]


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def store(session, writeprints: WriteprintSet,
          hour_histograms: Optional[Mapping[int, Sequence[float]]] = None) -> int:
    """Upsert the writeprints table. Returns the number of rows written.

    `hour_hist` lives in this table too (see schema_v2.sql), so behaviour's
    histogram is passed through rather than given a table of its own.
    """
    hour_histograms = hour_histograms or {}
    written = 0
    for persona_id, writeprint in writeprints.prints.items():
        row = session.get(WriteprintRow, persona_id)
        if row is None:
            row = WriteprintRow(persona_id=persona_id)
            session.add(row)
        row.char_count = writeprint.char_count
        row.vector = writeprint.vector.astype(np.float32).tobytes()
        row.features = writeprint.features
        histogram = hour_histograms.get(persona_id)
        row.hour_hist = [round(float(x), 6) for x in histogram] if histogram is not None else None
        row.feature_version = writeprint.feature_version
        row.computed_at = utcnow()
        written += 1
    return written


def load(session, feature_version: str) -> dict[int, Writeprint]:
    """Load cached writeprints built by exactly `feature_version`.

    Rows from any other version are ignored rather than returned: comparing a
    vector against one built from a different fitted vocabulary produces a
    number that looks fine and means nothing.
    """
    out: dict[int, Writeprint] = {}
    for row in session.query(WriteprintRow).filter(
        WriteprintRow.feature_version == feature_version
    ):
        if not row.vector:
            continue
        out[row.persona_id] = Writeprint(
            persona_id=row.persona_id,
            char_count=row.char_count or 0,
            vector=np.frombuffer(row.vector, dtype=np.float32),
            features=row.features or {},
            feature_version=row.feature_version,
        )
    return out
