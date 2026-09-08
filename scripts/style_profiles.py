"""
style_profiles.py — the writing-style layer of the synthetic corpus.

A style profile is a concrete tuple of orthographic and syntactic habits, not a
"formal vs casual" tone knob: a fixed greeting and signoff, a specific set of
recurring misspellings, a contraction policy, a capitalisation mode, a sentence
terminator, a clause-joining rule that drives sentence length, discourse markers,
and an emoji set.

`render()` applies those habits to prose that was generated independently of them
(see content_templates.py). That separation is what makes the stylometric signal
real rather than circular: the same varied content rendered through two different
profiles produces genuinely different text, and the signal survives stripping the
greeting and signoff — which tests/test_fixtures.py checks explicitly.

Personas belonging to the same actor share a profile exactly, so a vendor who
rebrands on a new market still writes the same way.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# Contraction tables
# ─────────────────────────────────────────────────────────────────────────────

_CONTRACTIONS = {
    "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "won't": "will not", "can't": "cannot", "isn't": "is not",
    "aren't": "are not", "wasn't": "was not", "it's": "it is",
    "that's": "that is", "there's": "there is", "we're": "we are",
    "you're": "you are", "they're": "they are", "i'm": "I am",
    "i'll": "I will", "we'll": "we will", "you'll": "you will",
    "i've": "I have", "we've": "we have", "you've": "you have",
    "haven't": "have not", "hasn't": "has not", "wouldn't": "would not",
    "shouldn't": "should not", "couldn't": "could not", "let's": "let us",
}

_CONTRACTION_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_CONTRACTIONS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_APOSTROPHE_RE = re.compile(r"\b(\w+)'(\w+)\b")


@dataclass(frozen=True)
class StyleProfile:
    """One author's writing habits.

    Attributes:
        key: short identifier, used in error messages and the disjointness check.
        greeting: prepended to every post. Stripped by the test's ablation run.
        signoff: appended to every post. Stripped by the test's ablation run.
        misspellings: correct -> misspelled. Applied whole-word, case-preserving.
        lexicon: idiolect substitutions on high-frequency words (you -> u,
            send -> dispatch). Unlike the misspellings, these target words that
            occur in every kind of post, so the habit shows up in a forum reply
            as much as in a product listing — which is what lets a vendor's
            market persona and forum persona be recognised as one writer.
        contractions: "normal" | "expand" (never contract) | "drop_apostrophe".
        capitalisation: "sentence" | "lower" (never capitalise) | "title_nouns".
        terminator: "period" | "ellipsis" | "bang" | "double_space".
        joiner: how adjacent sentences are merged, which drives sentence length:
            "period" | "semicolon" | "comma_splice" | "emdash".
        sentence_words: (min, max) target words per rendered sentence.
        markers: discourse markers inserted at the head of some sentences.
        marker_rate: probability a sentence gets one.
        emoji / emoji_rate: appended to some sentences.
        caps_emphasis_rate: probability a post gets one word shouted in ALL CAPS.
        trade_vocab: trade terms this author habitually uses (behavioural signal).
        hours: UTC hours this author posts in. Survives a migration, so it is a
            timezone hint linking personas across sources.
        categories: what this author deals in.
    """

    key: str
    greeting: str
    signoff: str
    misspellings: dict[str, str]
    lexicon: dict[str, str]
    contractions: str
    capitalisation: str
    terminator: str
    joiner: str
    sentence_words: tuple[int, int]
    markers: tuple[str, ...]
    marker_rate: float
    emoji: tuple[str, ...]
    emoji_rate: float
    caps_emphasis_rate: float
    trade_vocab: tuple[str, ...]
    hours: tuple[int, ...]
    categories: tuple[str, ...]
    #: Traits this profile intentionally shares with another, and with which.
    #: Declared so the disjointness check can allow them instead of failing.
    near_miss_of: str = ""
    shared_traits: tuple[str, ...] = field(default_factory=tuple)


# ─────────────────────────────────────────────────────────────────────────────
# Transforms
# ─────────────────────────────────────────────────────────────────────────────

def _apply_word_table(text: str, table: dict[str, str]) -> str:
    """Replace whole words, preserving a leading capital."""
    for correct, wrong in table.items():
        pattern = re.compile(r"\b" + re.escape(correct) + r"\b", re.IGNORECASE)

        def _sub(match: re.Match, wrong=wrong) -> str:
            found = match.group(0)
            if found[:1].isupper():
                return wrong[:1].upper() + wrong[1:]
            return wrong

        text = pattern.sub(_sub, text)
    return text


def _apply_contractions(text: str, policy: str) -> str:
    if policy == "expand":
        def _sub(match: re.Match) -> str:
            found = match.group(0)
            expanded = _CONTRACTIONS[found.lower()]
            if found[:1].isupper():
                return expanded[:1].upper() + expanded[1:]
            return expanded

        return _CONTRACTION_RE.sub(_sub, text)
    if policy == "drop_apostrophe":
        return _APOSTROPHE_RE.sub(r"\1\2", text)
    return text


def _merge_sentences(sentences: list[str], profile: StyleProfile,
                     rng: random.Random) -> list[str]:
    """Join adjacent sentences until each reaches the profile's word target."""
    low, high = profile.sentence_words
    merged: list[str] = []
    buffer: list[str] = []

    def _flush() -> None:
        if not buffer:
            return
        if len(buffer) == 1 or profile.joiner == "period":
            merged.extend(buffer)
        elif profile.joiner == "semicolon":
            merged.append("; ".join(buffer))
        elif profile.joiner == "comma_splice":
            merged.append(", ".join(buffer))
        elif profile.joiner == "emdash":
            merged.append(" — ".join(buffer))
        else:
            merged.extend(buffer)
        buffer.clear()

    for sentence in sentences:
        buffer.append(sentence)
        words = sum(len(s.split()) for s in buffer)
        target = rng.randint(low, high)
        if words >= target:
            _flush()
    _flush()

    # A "period" joiner still has to respect the target: split anything far over.
    if profile.joiner == "period":
        split: list[str] = []
        for sentence in merged:
            words = sentence.split()
            if len(words) > high * 1.6:
                cut = len(words) // 2
                split.append(" ".join(words[:cut]))
                split.append(" ".join(words[cut:]))
            else:
                split.append(sentence)
        merged = split
    return merged


def _terminate(sentence: str, profile: StyleProfile, rng: random.Random) -> str:
    sentence = sentence.rstrip(" .!?")
    if profile.terminator == "ellipsis":
        return sentence + "..."
    if profile.terminator == "bang":
        return sentence + ("!!!" if rng.random() < 0.7 else "!")
    return sentence + "."


_MASK_START = "\x00"
_MASK_END = "\x01"


def _mask(text: str, table: dict[str, str]) -> str:
    """Replace verbatim spans with sentinels so no transform can touch them.

    A PGP fingerprint, a Monero address or a session id must survive rendering
    byte-for-byte: an author who writes in all lowercase still pastes their key
    exactly as it was generated, and a lowercased wallet address is a different
    address. Sentinels use control characters, which no transform matches.
    """
    for placeholder, original in table.items():
        text = text.replace(original, placeholder)
    return text


def _unmask(text: str, table: dict[str, str]) -> str:
    for placeholder, original in table.items():
        text = text.replace(placeholder, original)
    return text


_STANDALONE_I = re.compile(r"\bi\b")


def _capitalise(text: str, profile: StyleProfile, nouns: tuple[str, ...]) -> str:
    if profile.capitalisation == "lower":
        return text.lower()

    # anyone who capitalises at all also capitalises the first person pronoun;
    # writing "i" is one of the habits that separates the lowercase authors
    text = _STANDALONE_I.sub("I", text)

    if profile.capitalisation == "title_nouns":
        for noun in nouns:
            text = re.sub(
                r"\b" + re.escape(noun) + r"\b",
                noun.title(),
                text,
                flags=re.IGNORECASE,
            )
    # capitalise the first letter of each sentence
    out = []
    capitalise_next = True
    for ch in text:
        if ch == _MASK_START:
            # a masked identifier opens the sentence; leave what follows alone
            capitalise_next = False
            out.append(ch)
            continue
        if capitalise_next and ch.isalpha():
            out.append(ch.upper())
            capitalise_next = False
        else:
            out.append(ch)
        if ch in ".!?\n":
            capitalise_next = True
    return "".join(out)


def render(sentences: list[str], profile: StyleProfile, rng: random.Random,
           title_nouns: tuple[str, ...] = (),
           verbatim: tuple[str, ...] = ()) -> str:
    """Render style-free sentences into one post written in `profile`'s voice.

    Args:
        verbatim: spans that must survive rendering unchanged — identifiers,
            addresses, fingerprints.
    """
    sentences = [s.strip() for s in sentences if s and s.strip()]
    if not sentences:
        return ""

    # longest first, so a value containing another is masked as a whole
    mask_table = {
        f"{_MASK_START}{i}{_MASK_END}": value
        for i, value in enumerate(sorted(set(verbatim), key=len, reverse=True))
        if value
    }
    if mask_table:
        sentences = [_mask(s, mask_table) for s in sentences]

    merged = _merge_sentences(sentences, profile, rng)

    styled: list[str] = []
    for sentence in merged:
        if profile.markers and rng.random() < profile.marker_rate:
            marker = rng.choice(profile.markers)
            sentence = f"{marker} {sentence}"
        sentence = _terminate(sentence, profile, rng)
        if profile.emoji and rng.random() < profile.emoji_rate:
            sentence = f"{sentence} {rng.choice(profile.emoji)}"
        styled.append(sentence)

    separator = "  " if profile.terminator == "double_space" else " "
    body = separator.join(styled)

    body = _apply_word_table(body, profile.lexicon)
    body = _apply_word_table(body, profile.misspellings)
    body = _apply_contractions(body, profile.contractions)

    if profile.caps_emphasis_rate and rng.random() < profile.caps_emphasis_rate:
        words = body.split(" ")
        candidates = [i for i, w in enumerate(words) if len(w.strip(".,;!?—")) > 4]
        if candidates:
            i = rng.choice(candidates)
            words[i] = words[i].upper()
            body = " ".join(words)

    parts = []
    if profile.greeting:
        parts.append(profile.greeting)
    parts.append(body)
    if profile.signoff:
        parts.append(profile.signoff)
    text = _capitalise(" ".join(parts), profile, title_nouns).strip()
    return _unmask(text, mask_table) if mask_table else text


def strip_frame(text: str, profile: StyleProfile) -> str:
    """Remove the greeting and signoff.

    Used by the test's ablation run: if the stylometric gap only exists because a
    fixed greeting and signoff repeat, it is an artefact of the generator rather
    than a property of the prose, and the corpus is not fit for Phase 2.
    """
    out = text
    for fragment in (profile.greeting, profile.signoff):
        if not fragment:
            continue
        for variant in {fragment, fragment.lower(), fragment.title()}:
            out = out.replace(variant, " ")
    return re.sub(r"\s+", " ", out).strip()


# ─────────────────────────────────────────────────────────────────────────────
# The cast
#
# One profile per actor. Personas of the same actor reuse the profile unchanged.
# ─────────────────────────────────────────────────────────────────────────────

PROFILES: dict[str, StyleProfile] = {
    # ── the four vendors who migrate from market_alpha to market_gamma ───────
    "actor_001": StyleProfile(
        key="actor_001",
        greeting="yo fam",
        signoff="stay safe out there",
        misspellings={"receive": "recieve", "separate": "seperate",
                      "tomorrow": "tommorow", "because": "becuase"},
        lexicon={"you": "u", "your": "ur", "message": "msg", "before": "b4"},
        contractions="drop_apostrophe",
        capitalisation="lower",
        terminator="ellipsis",
        joiner="period",
        sentence_words=(8, 14),
        markers=("look,", "real talk,", "listen,"),
        marker_rate=0.5,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("stealth", "reship", "dead drop"),
        hours=(22, 23, 0, 1, 2, 3),
        categories=("drugs",),
    ),
    "actor_002": StyleProfile(
        key="actor_002",
        greeting="Greetings,",
        signoff="Kind regards,",
        misspellings={"occurred": "occured", "address": "adress",
                      "necessary": "neccessary", "available": "availible"},
        lexicon={"send": "dispatch", "check": "verify", "only": "solely",
                 "every": "each"},
        contractions="expand",
        capitalisation="title_nouns",
        terminator="period",
        joiner="semicolon",
        sentence_words=(18, 28),
        markers=("Please note,", "For clarity,", "As previously stated,"),
        marker_rate=0.4,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("escrow", "vacuum sealed", "discreet packaging"),
        hours=(6, 7, 8, 9, 10, 11),
        categories=("pharma",),
    ),
    "actor_003": StyleProfile(
        key="actor_003",
        greeting="oi",
        signoff="— V",
        misspellings={"definitely": "definately", "acquired": "aquired",
                      "guaranteed": "garanteed", "quantity": "quantety"},
        lexicon={"new": "fresh", "order": "drop", "anything": "whatever",
                 "last": "latest"},
        contractions="normal",
        capitalisation="sentence",
        terminator="bang",
        joiner="period",
        sentence_words=(4, 8),
        markers=("straight up,", "no games,"),
        marker_rate=0.45,
        emoji=("\U0001F525", "⚡"),
        emoji_rate=0.45,
        caps_emphasis_rate=0.85,
        trade_vocab=("FE", "autoshop", "fresh drop"),
        hours=(13, 14, 15, 16, 17, 18),
        categories=("data",),
    ),
    "actor_004": StyleProfile(
        key="actor_004",
        greeting="alright mate",
        signoff="cheers",
        misspellings={"a lot": "alot", "business": "buisness",
                      "recommend": "reccomend", "until": "untill"},
        lexicon={"my": "me", "was": "were", "never": "not once",
                 "go": "head over"},
        contractions="normal",
        capitalisation="sentence",
        terminator="double_space",
        joiner="comma_splice",
        sentence_words=(30, 42),
        markers=("basically,", "to be honest,", "at the end of the day,"),
        marker_rate=0.55,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("escrow", "FE", "tracking"),
        hours=(16, 17, 18, 19, 20, 21),
        categories=("docs",),
    ),

    # ── singletons on market_alpha ───────────────────────────────────────────
    "actor_005": StyleProfile(
        key="actor_005",
        greeting="hello",
        signoff="regards",
        misspellings={"delivery": "delivary", "package": "packege"},
        lexicon={"verify": "confirm", "moved": "relocated"},
        contractions="expand",
        capitalisation="sentence",
        terminator="period",
        joiner="period",
        sentence_words=(12, 18),
        markers=("note that,",),
        marker_rate=0.25,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("tracking", "reship"),
        hours=(8, 9, 10, 11, 12, 13),
        categories=("drugs",),
    ),
    "actor_006": StyleProfile(
        key="actor_006",
        greeting="lab notice",
        signoff="obsidian",
        misspellings={"synthesis": "synthesys", "purity": "purety"},
        lexicon={"same": "identical", "lost": "misplaced"},
        contractions="normal",
        capitalisation="lower",
        terminator="period",
        joiner="semicolon",
        sentence_words=(20, 30),
        markers=("for reference,",),
        marker_rate=0.3,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("vacuum sealed", "escrow"),
        hours=(2, 3, 4, 5, 6, 7),
        categories=("pharma",),
    ),
    "actor_007": StyleProfile(  # paperghost — barely writes anything at all
        key="actor_007",
        greeting="",
        signoff="",
        misspellings={},
        lexicon={},
        contractions="normal",
        capitalisation="sentence",
        terminator="period",
        joiner="period",
        sentence_words=(6, 10),
        markers=(),
        marker_rate=0.0,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("tracking",),
        hours=(19, 20, 21),
        categories=("docs",),
    ),
    "actor_008": StyleProfile(
        key="actor_008",
        greeting="cryo here",
        signoff="vault out",
        misspellings={"temperature": "temprature", "storage": "storeage"},
        lexicon={"if": "when", "had": "got"},
        contractions="normal",
        capitalisation="sentence",
        terminator="bang",
        joiner="period",
        sentence_words=(6, 10),
        markers=("heads up,",),
        marker_rate=0.3,
        emoji=("❄",),
        emoji_rate=0.3,
        caps_emphasis_rate=0.2,
        trade_vocab=("vacuum sealed", "autoshop"),
        hours=(11, 12, 13, 14, 15),
        categories=("pharma",),
    ),

    # ── the new market_gamma vendor: a near-miss for actor_002 ───────────────
    "actor_009": StyleProfile(
        key="actor_009",
        greeting="Greetings,",           # deliberately the same as actor_002
        signoff="Best wishes,",
        misspellings={"liaise": "liase", "packaging": "packageing",
                      "receipt": "reciept"},
        lexicon={"are": "remain", "but": "however"},
        contractions="normal",           # actor_002 never contracts
        capitalisation="sentence",       # actor_002 title-cases product nouns
        terminator="period",
        joiner="emdash",                 # actor_002 uses semicolons
        sentence_words=(18, 28),         # deliberately the same band
        markers=("Kindly note,", "In addition,"),
        marker_rate=0.4,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("escrow", "discreet packaging"),
        hours=(13, 14, 15, 16, 17),      # actor_002 posts in the morning
        categories=("pharma",),
        near_miss_of="actor_002",
        shared_traits=("greeting", "sentence_words", "categories"),
    ),

    # ── singletons on forum_beta ─────────────────────────────────────────────
    "actor_010": StyleProfile(
        key="actor_010",
        greeting="salute",
        signoff="hx",
        misspellings={"encryption": "encription", "vulnerability": "vulnrability"},
        lexicon={"in": "within", "on": "upon"},
        contractions="drop_apostrophe",
        capitalisation="sentence",
        terminator="period",
        joiner="period",
        sentence_words=(10, 16),
        markers=("in theory,",),
        marker_rate=0.3,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("autoshop",),
        hours=(20, 21, 22, 23),
        categories=("hacking",),
    ),
    "actor_011": StyleProfile(
        key="actor_011",
        greeting="good day",
        signoff="MTL",
        misspellings={"transfer": "tranfer", "currency": "curency"},
        lexicon={"me": "myself", "has": "holds"},
        contractions="expand",
        capitalisation="title_nouns",
        terminator="period",
        joiner="semicolon",
        sentence_words=(16, 24),
        markers=("for the record,",),
        marker_rate=0.3,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("escrow",),
        hours=(9, 10, 11, 12, 13, 14),
        categories=("laundering",),
    ),
    "actor_012": StyleProfile(
        key="actor_012",
        greeting="sup",
        signoff="zc",
        misspellings={"probably": "probly", "obviously": "obvsly"},
        lexicon={"that": "tht", "with": "wit"},
        contractions="drop_apostrophe",
        capitalisation="lower",
        terminator="bang",
        joiner="period",
        sentence_words=(5, 9),
        markers=("lol,", "ngl,"),
        marker_rate=0.4,
        emoji=("\U0001F480",),
        emoji_rate=0.4,
        caps_emphasis_rate=0.3,
        trade_vocab=("fresh drop",),
        hours=(0, 1, 2, 3, 4, 5),
        categories=("hacking",),
    ),
    "actor_013": StyleProfile(
        key="actor_013",
        greeting="morning all",
        signoff="gp",
        misspellings={"experience": "experiance", "advice": "advise"},
        lexicon={"support": "the staff", "fees": "charges"},
        contractions="normal",
        capitalisation="sentence",
        terminator="period",
        joiner="period",
        sentence_words=(14, 20),
        markers=("in my experience,",),
        marker_rate=0.3,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("stealth", "tracking"),
        hours=(6, 7, 8, 9, 10),
        categories=("drugs",),
    ),

    # ── the forum near-miss for actor_004 ────────────────────────────────────
    "actor_014": StyleProfile(
        key="actor_014",
        greeting="hey all",
        signoff="later",
        misspellings={},                 # spells correctly; actor_004 does not
        lexicon={},                      # and has none of actor_004's idiolect
        contractions="normal",
        capitalisation="sentence",
        terminator="period",             # actor_004 double-spaces after periods
        joiner="comma_splice",           # deliberately the same
        sentence_words=(30, 42),         # deliberately the same band
        markers=("basically,", "to be honest,"),   # two of actor_004's three
        marker_rate=0.55,
        emoji=(),
        emoji_rate=0.0,
        caps_emphasis_rate=0.0,
        trade_vocab=("escrow", "tracking"),
        hours=(5, 6, 7, 8, 9),           # actor_004 posts in the evening
        categories=("docs",),
        near_miss_of="actor_004",
        shared_traits=("joiner", "sentence_words", "markers", "categories"),
    ),
}


def check_profiles_distinct() -> None:
    """Fail if two unrelated actors would collide by accident.

    Overlaps are allowed only where a profile declares itself a near-miss of
    another and names the shared trait; every other collision is a generator bug
    that would quietly inflate inter-actor similarity.
    """
    keys = sorted(PROFILES)
    for i, a_key in enumerate(keys):
        for b_key in keys[i + 1:]:
            a, b = PROFILES[a_key], PROFILES[b_key]
            declared = (a.near_miss_of == b_key) or (b.near_miss_of == a_key)
            shared = set(a.shared_traits) | set(b.shared_traits)

            overlap = set(a.misspellings) & set(b.misspellings)
            if overlap and not (declared and "misspellings" in shared):
                raise AssertionError(
                    f"{a_key} and {b_key} share misspelled words {sorted(overlap)}"
                )

            lex_overlap = set(a.lexicon) & set(b.lexicon)
            if lex_overlap and not (declared and "lexicon" in shared):
                raise AssertionError(
                    f"{a_key} and {b_key} both substitute {sorted(lex_overlap)}; "
                    f"two authors rewriting the same word the same way would be an "
                    f"accidental collision, not a real signal"
                )

            if a.greeting and a.greeting == b.greeting:
                if not (declared and "greeting" in shared):
                    raise AssertionError(
                        f"{a_key} and {b_key} share the greeting {a.greeting!r}"
                    )

            if a.signoff and a.signoff == b.signoff:
                raise AssertionError(
                    f"{a_key} and {b_key} share the signoff {a.signoff!r}"
                )

            marker_overlap = set(a.markers) & set(b.markers)
            if marker_overlap and not (declared and "markers" in shared):
                raise AssertionError(
                    f"{a_key} and {b_key} share discourse markers {sorted(marker_overlap)}"
                )


if __name__ == "__main__":
    check_profiles_distinct()
    print(f"style_profiles: {len(PROFILES)} profiles, all distinct")
