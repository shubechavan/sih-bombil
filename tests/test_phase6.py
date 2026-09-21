"""test_phase6.py — live collection, the lab target, and buyer feedback.

Four things here are worth failing a build over.

**The collectors must have no default target.** A crawler that ships with a
list of real marketplaces will crawl them the first time somebody runs it by
accident. There is no list, `--onion` is required, and off-target hosts are
refused — asserted, not promised.

**A crawl must be lossless.** The extractor reads `bio + key_blocks + post
titles + bodies`; one changed character shifts the writeprint vocabulary and
every pair's S moves. The collected text is diffed against `fixtures/` field by
field, and a deliberately corrupted page must be *caught* — a verifier that
only ever passes proves nothing.

**Two sources on one host must stay two sources.** `sources.url` is UNIQUE and
ingest keys on it, so a collector reporting the bare host for co-hosted sources
silently merges them and every persona lands under whichever name arrived
first. That bug shipped once; this is the regression test.

**Feedback must not reach the score.** Buyers are never personas, feedback is
never a link, and the attribution formula has no term for it. Each of those is
a separate assertion because each is a separate way to get it wrong.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

FIXTURES = ROOT / "fixtures"


# ─────────────────────────────────────────────────────────────────────────────
# No default target
# ─────────────────────────────────────────────────────────────────────────────

def test_a_collector_has_no_default_target_list():
    """`--onion` is required and no real host is named anywhere."""
    from collectors import forum_collector, market_collector

    for module in (market_collector, forum_collector):
        result = subprocess.run(
            [sys.executable, "-m", f"collectors.{module.__name__.split('.')[-1]}"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert result.returncode != 0
        assert "--onion" in (result.stderr + result.stdout)


def test_no_onion_address_is_hardcoded_in_the_collectors():
    """Not even as an example. A pasteable address is a target list of one."""
    import re

    onion = re.compile(r"\b[a-z2-7]{56}\.onion\b")
    for path in (ROOT / "collectors").glob("*.py"):
        found = onion.findall(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name} names an onion address: {found}"


def test_off_target_hosts_are_refused():
    from collectors.base import Crawler

    crawler = Crawler("http://example.onion")
    assert crawler.allow("http://example.onion/vendor/x") is True
    assert crawler.allow("http://somewhere-else.onion/vendor/x") is False
    assert crawler.refused == ["http://somewhere-else.onion/vendor/x"]


def test_allow_external_is_opt_in_and_only_that():
    from collectors.base import Crawler

    crawler = Crawler("http://example.onion", allow_external=True)
    assert crawler.allow("http://somewhere-else.onion/x") is True
    assert crawler.refused == []


def test_the_crawler_only_issues_gets():
    """Passive means passive. No POST, no auth header, no credential."""
    source = (ROOT / "collectors" / "base.py").read_text(encoding="utf-8")
    for verb in (".post(", ".put(", ".delete(", ".patch("):
        assert verb not in source
    for secret in ("Authorization", "auth=", "password", "Cookie"):
        assert secret not in source


# ─────────────────────────────────────────────────────────────────────────────
# Two sources on one host
# ─────────────────────────────────────────────────────────────────────────────

def test_co_hosted_sources_get_distinct_urls():
    """The regression test for the bug that merged all three lab sources.

    `ingest.upsert_sources` keys by url and `sources.url` is UNIQUE, so three
    sources reporting `http://host` collapse to one row.
    """
    from collectors.base import Crawler

    crawler = Crawler("http://example.onion")
    urls = {
        crawler.source_url("/market_alpha"),
        crawler.source_url("/forum_beta"),
        crawler.source_url("/market_gamma"),
    }
    assert len(urls) == 3
    assert crawler.source_url("/market_alpha") == "http://example.onion/market_alpha"


def test_a_single_source_target_still_reports_the_bare_host():
    from collectors.base import Crawler

    crawler = Crawler("http://example.onion")
    assert crawler.source_url("/") == "http://example.onion"
    assert crawler.source_url("") == "http://example.onion"


def test_ingest_would_merge_sources_that_share_a_url():
    """Proves the *reason* the rule above exists, rather than assuming it."""
    import ingest

    rows = [
        {"source": "market_alpha", "source_url": "http://host.onion", "handle": "a"},
        {"source": "forum_beta", "source_url": "http://host.onion", "handle": "b"},
    ]
    documents = ingest.documents_from_rows(rows, origin="test")
    assert len({d.source["url"] for d in documents}) == 1, (
        "two sources at one url — upsert_sources keys on url, so this is the "
        "shape that silently becomes a single source row"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fidelity
# ─────────────────────────────────────────────────────────────────────────────

def test_field_text_takes_the_text_verbatim():
    """No strip, no whitespace collapse. Stylometry reads these characters."""
    bs4 = pytest.importorskip("bs4")
    from collectors.base import field_text

    soup = bs4.BeautifulSoup(
        '<div data-f="bio">  spaced  out  </div>', "html.parser"
    )
    assert field_text(soup, "bio") == "  spaced  out  "


def test_verify_catches_a_single_changed_character():
    """A verifier that cannot fail is not a verifier."""
    from collectors.verify import compare

    persona = json.loads(
        (FIXTURES / "market_alpha" / "personas.json").read_text(encoding="utf-8")
    )[0]
    posts = [
        p for p in json.loads(
            (FIXTURES / "market_alpha" / "posts.json").read_text(encoding="utf-8")
        ) if p["persona_id"] == persona["id"]
    ]
    posts.sort(key=lambda p: (p.get("posted_at") or "", p["id"]))
    blocks = [
        b["armored"] for b in json.loads(
            (FIXTURES / "pgp_blocks.json").read_text(encoding="utf-8")
        ) if b["persona_id"] == persona["id"]
    ]

    good = {
        "handle": persona["handle"],
        "bio": persona["bio"],
        "posts": [{"title": p["title"], "body": p["body"]} for p in posts],
        "key_blocks": blocks,
    }
    assert compare([good]) == [], "an exact copy should verify clean"

    corrupted = json.loads(json.dumps(good))
    corrupted["bio"] = (corrupted["bio"] or "x")[:-1] + "!"
    problems = compare([corrupted])
    assert problems, "a changed final character must be reported"
    assert "bio" in problems[0]


def test_verify_says_so_when_there_is_nothing_to_compare_against():
    """Pointed at a real site there is no answer key, and it must not claim one."""
    from collectors.verify import compare

    problems = compare([{"handle": "nobody-in-the-fixtures", "posts": []}])
    assert problems
    assert any("fixture" in line for line in problems)


# ─────────────────────────────────────────────────────────────────────────────
# The lab target
# ─────────────────────────────────────────────────────────────────────────────

def test_the_lab_labels_itself_as_a_lab_target():
    """In the HTML a human reads, not only in a comment."""
    source = (ROOT / "lab" / "app.py").read_text(encoding="utf-8")
    assert "LAB TARGET" in source
    assert "X-Lab-Target" in source
    # robots.txt and a <meta> tag too: three independent places, because
    # somebody will read exactly one of them.
    assert "robots" in source.lower()


def test_the_lab_plants_the_two_documented_misconfigurations():
    """/server-status exposed, and a cert whose SAN is a fixture clearnet host."""
    import re

    source = (ROOT / "lab" / "app.py").read_text(encoding="utf-8")
    assert "/server-status" in source

    san = re.search(r'TLS_SAN\s*=\s*"([^"]+)"', source)
    assert san, "the lab must declare the SAN it plants"
    hosts = {
        hostname
        for path in (FIXTURES / "clearnet_obs").glob("*.json")
        for observation in json.loads(path.read_text(encoding="utf-8"))
        for hostname in observation.get("hostnames", [])
    }
    assert san.group(1) in hosts, (
        f"{san.group(1)} is not in fixtures/clearnet_obs, so the planted "
        f"certificate correlates with nothing"
    )


def test_the_lab_serial_matches_the_clearnet_observation_byte_for_byte():
    """A serial that reads back short loses the 1.00-weight correlation.

    `format(n, "X")` drops a leading zero nibble, so 0F3A… became F3A… and the
    strongest signal in the rubric silently missed.
    """
    import re

    source = (ROOT / "lab" / "app.py").read_text(encoding="utf-8")
    declared = re.search(r"TLS_SERIAL\s*=\s*(0x[0-9A-Fa-f]+)", source)
    assert declared, "the lab must declare the serial it plants"

    as_read = format(int(declared.group(1), 16), "X")
    if len(as_read) % 2:
        as_read = "0" + as_read

    serials = {
        (observation.get("ssl") or {}).get("cert", {}).get("serial")
        for path in (FIXTURES / "clearnet_obs").glob("*.json")
        for observation in json.loads(path.read_text(encoding="utf-8"))
    }
    assert as_read in serials, (
        f"the lab serves serial {as_read}, which no clearnet observation "
        f"carries — the tls_serial match would never fire"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Feedback never reaches the score
# ─────────────────────────────────────────────────────────────────────────────

def test_a_buyer_never_becomes_a_persona():
    """The single most important boundary in Phase 6.

    ~40 buyer handles as personas would put them in the 190-pair loop and the
    engine would start proposing buyers as vendors' alt accounts.
    """
    feedback = json.loads((FIXTURES / "feedback.json").read_text(encoding="utf-8"))
    buyers = {row["buyer_handle"] for row in feedback}
    handles = {
        persona["handle"]
        for name in ("market_alpha", "forum_beta", "market_gamma")
        for persona in json.loads(
            (FIXTURES / name / "personas.json").read_text(encoding="utf-8")
        )
    }
    assert buyers, "the fixture corpus should have buyers to test with"
    assert not (buyers & handles), f"buyer(s) also a persona: {buyers & handles}"


def test_the_attribution_formula_has_no_feedback_term():
    """H, S, B, I — and nothing else. Checked in the component list itself."""
    from score.attribution import COMPONENTS

    assert set(COMPONENTS) == {"H", "S", "B", "I"}
    source = (ROOT / "score" / "attribution.py").read_text(encoding="utf-8")
    for word in ("feedback", "buyer", "trust_edge"):
        assert word not in source.lower(), (
            f"score/attribution.py mentions {word!r}; the measurement says "
            f"buyer overlap is worse than chance at separating true pairs"
        )


def test_the_resolver_never_reads_the_feedback_table():
    """Structural, not incidental: link/resolve.py must not import it."""
    source = (ROOT / "link" / "resolve.py").read_text(encoding="utf-8")
    assert "Feedback" not in source
    assert "feedback" not in source.lower()


def test_a_trust_edge_carries_no_score_and_says_so():
    from link.trust import NOT_A_SCORE, TrustEdge

    edge = TrustEdge(persona_a=1, persona_b=4, handle_a="a", handle_b="b",
                     shared=["x", "y"], buyers_a=6, buyers_b=7)
    payload = edge.to_dict()
    assert payload["affects_score"] is False
    assert "score" not in payload and "band" not in payload
    assert NOT_A_SCORE in payload["note"]
    # Rounded in to_dict; the unrounded value lives on the dataclass.
    assert edge.jaccard == pytest.approx(2 / 11)
    assert payload["overlap"] == pytest.approx(2 / 11, abs=5e-5)


def test_the_trust_note_states_the_measured_finding():
    """Not "may be unreliable" — the number, so a reader can check it."""
    from link.trust import NOT_A_SCORE

    assert "0.389" in NOT_A_SCORE
    assert "chance" in NOT_A_SCORE.lower()


def test_shared_buyer_overlap_really_is_worse_than_chance():
    """Recomputed from the fixtures, so the claim cannot rot in a docstring."""
    import itertools

    from link.trust import _roc_auc

    feedback = json.loads((FIXTURES / "feedback.json").read_text(encoding="utf-8"))
    truth = json.loads((FIXTURES / "ground_truth.json").read_text(encoding="utf-8"))
    true_pairs = {tuple(sorted(map(int, p)))
                  for p in truth["expected_positive_pairs"]}

    buyers: dict[int, set[str]] = {}
    for row in feedback:
        buyers.setdefault(row["persona_id"], set()).add(row["buyer_handle"])

    # The population where the signal exists: personas with any feedback.
    # Over all 190 pairs the 112 with no feedback tie at zero and drag the
    # figure toward 0.5, which measures coverage rather than discrimination.
    ids = sorted(buyers)
    scores, labels = [], []
    for a, b in itertools.combinations(ids, 2):
        union = buyers[a] | buyers[b]
        scores.append(len(buyers[a] & buyers[b]) / len(union) if union else 0.0)
        labels.append(1 if (a, b) in true_pairs else 0)

    auc = _roc_auc(scores, labels)
    assert auc == pytest.approx(0.389, abs=0.001), (
        f"the documented ROC-AUC is 0.389; recomputed {auc:.3f}"
    )
    assert auc < 0.5, "if this ever exceeds chance, revisit the decision"


def test_the_feedback_table_exists_and_is_not_the_persona_table():
    from db import REQUIRED_TABLES, Feedback, Persona

    assert "feedback" in REQUIRED_TABLES
    assert Feedback.__tablename__ == "feedback"
    columns = set(Feedback.__table__.columns.keys())
    assert "buyer_handle" in columns
    # A buyer has no handle_normalized of the persona kind, no bio, no posts.
    assert not (columns & {"bio", "trust_score", "actor_id"})
    assert "buyer_handle" not in Persona.__table__.columns.keys()


# ─────────────────────────────────────────────────────────────────────────────
# The corpus did not move when feedback was added
# ─────────────────────────────────────────────────────────────────────────────

def test_adding_feedback_left_every_other_fixture_byte_identical():
    """The hard condition Phase 6 was allowed to proceed under.

    `gen_fixtures.py` threads one seeded RNG through every persona in order, so
    a single extra draw anywhere in `build_corpus()` rewrites the whole corpus —
    and with it every published number, silently. `build_feedback()` therefore
    takes its own stream (`random.Random(f"{FIXTURE_SEED}:feedback")`) and runs
    after the corpus is built.

    Checked by hash rather than by eye, and pinned here so the guarantee
    survives the next person to touch the generator.
    """
    import hashlib

    pinned = json.loads(
        (ROOT / ".fixture-hashes.json").read_text(encoding="utf-8")
    )
    assert pinned, "the hash manifest should not be empty"

    drifted = []
    for relative, expected in sorted(pinned.items()):
        path = ROOT / relative
        assert path.exists(), f"{relative} is pinned but missing"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            drifted.append(f"{relative}\n      pinned {expected}\n      actual {actual}")

    assert not drifted, (
        "fixture file(s) changed since feedback was added:\n    "
        + "\n    ".join(drifted)
        + "\n  If this was deliberate, every published number "
          "(precision, recall, margin, the pair scores in README.md and "
          "DEMO.md) must be re-measured and the manifest regenerated."
    )
