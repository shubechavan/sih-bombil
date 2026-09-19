"""test_persistence.py — what the database is allowed to say about a component.

The engine has drawn one distinction since Phase 2: `None` means *not assessed*
and `0.0` means *compared, and they had nothing in common*. score/attribution.py
refuses to conflate them and renormalises instead. The `links` table quietly
contradicted that — `h_score` and `i_score` were created `DEFAULT 0`, so an
unmeasured component arrived as a measured zero and any reader of the column saw
`0.00` where the truth was "we did not look".

The same gap ran through stylometry: `store()` wrote only the personas it
scored, so a refusal existed in terminal output and nowhere a reader of the
database could find it.

These tests pin both, against a live database, because both defects were
invisible in the in-memory objects and only appeared on the way to Postgres.
They skip rather than fail when Postgres is down — the offline path is covered
elsewhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import MIN_STYLOMETRY_CHARS  # noqa: E402


@pytest.fixture(scope="module")
def db():
    """A session factory, or skip if Postgres is not up."""
    try:
        from db import require_schema, session_scope

        with session_scope() as session:
            require_schema(session)
    except Exception as exc:  # noqa: BLE001 - any connection failure is a skip
        pytest.skip(f"Postgres unavailable: {type(exc).__name__}: {exc}")

    from db import session_scope  # noqa: PLC0415

    return session_scope


@pytest.fixture(scope="module")
def links(db):
    from sqlalchemy import select

    from db import Link

    with db() as session:
        rows = session.execute(select(Link)).scalars().all()
        # detach: read everything we need while the session is open
        out = [
            {
                "pair": (r.persona_a, r.persona_b),
                "h": r.h_score, "s": r.s_score, "b": r.b_score, "i": r.i_score,
                "evidence": list(r.evidence or []),
            }
            for r in rows
        ]
    if not out:
        pytest.skip("links table is empty — run `python -m link.resolve --source db`")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# The column must not invent a measurement
# ─────────────────────────────────────────────────────────────────────────────

def test_no_component_column_carries_a_default(db):
    """A DEFAULT on a score column turns 'not assessed' into 'assessed as 0'."""
    from sqlalchemy import text

    with db() as session:
        defaults = {
            row[0]: row[1]
            for row in session.execute(text(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name = 'links' "
                "AND column_name IN ('h_score','s_score','b_score','i_score')"
            ))
        }
    assert set(defaults) == {"h_score", "s_score", "b_score", "i_score"}
    assert all(value is None for value in defaults.values()), defaults


def test_every_component_column_is_nullable(db):
    from sqlalchemy import text

    with db() as session:
        nullable = {
            row[0]: row[1]
            for row in session.execute(text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'links' "
                "AND column_name IN ('h_score','s_score','b_score','i_score')"
            ))
        }
    assert all(value == "YES" for value in nullable.values()), nullable


def test_unmeasured_infrastructure_is_stored_as_null_not_zero(links):
    """The Phase 3 finding, as the database records it.

    I is unmeasured on this corpus because recon findings are site-level. If it
    round-trips as 0.0 the table asserts the opposite of what the engine decided,
    and any UI reading the column renders a confident zero.
    """
    for row in links:
        assert row["i"] is None, (
            f"{row['pair']} stored i_score={row['i']!r}; unmeasured must be NULL"
        )


def test_the_evidence_agrees_with_the_column(links):
    """Whatever the column says, the evidence list must say the same thing."""
    for row in links:
        breakdown = next(
            (e for e in row["evidence"] if e.get("type") == "component_breakdown"),
            None,
        )
        assert breakdown, f"{row['pair']} has no component_breakdown entry"
        components = breakdown["components"]
        for key, column in (("H", "h"), ("S", "s"), ("B", "b"), ("I", "i")):
            stored, declared = row[column], components[key]
            assert (stored is None) == (declared is None), (
                f"{row['pair']} component {key}: column={stored!r} "
                f"evidence={declared!r} — one says measured, the other does not"
            )


def test_an_unmeasured_component_says_why_in_plain_language(links):
    """Requirement for the UI: a reason to render, not a blank cell."""
    for row in links:
        if row["i"] is not None:
            continue
        note = next(
            (e for e in row["evidence"]
             if e.get("type") == "component_not_assessed" and e.get("component") == "I"),
            None,
        )
        assert note, f"{row['pair']} has I unmeasured with no explanation"
        assert note["detail"].strip(), "the explanation is empty"
        assert "infrastructure not assessed" in note["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# A refusal is a finding, and has to be stored like one
# ─────────────────────────────────────────────────────────────────────────────

def test_every_persona_has_a_writeprint_row_including_the_refused_one(db):
    from sqlalchemy import text

    with db() as session:
        personas = session.execute(text("SELECT COUNT(*) FROM personas")).scalar()
        prints = session.execute(text("SELECT COUNT(*) FROM writeprints")).scalar()
    if not personas:
        pytest.skip("personas table is empty — run scripts/load_fixtures.py first")
    assert prints == personas, (
        f"{personas} personas but {prints} writeprint rows — a persona the engine "
        f"refused to score should still leave a row saying so"
    )


def test_a_refused_persona_is_stored_with_no_vector_and_a_reason(db):
    from sqlalchemy import text

    with db() as session:
        rows = session.execute(text(
            "SELECT persona_id, char_count, refused_reason "
            "FROM writeprints WHERE vector IS NULL ORDER BY persona_id"
        )).all()
    assert rows, "no refusal stored — the 300-char floor should refuse persona 7"
    for persona_id, char_count, reason in rows:
        assert reason and reason.strip(), f"persona {persona_id} refused with no reason"
        assert char_count is not None and char_count < MIN_STYLOMETRY_CHARS, (
            f"persona {persona_id} refused with char_count={char_count!r}, which is "
            f"not below the {MIN_STYLOMETRY_CHARS}-character floor"
        )
        assert str(MIN_STYLOMETRY_CHARS) in reason, (
            "the reason should name the floor it fell below"
        )


def test_a_scored_persona_carries_no_refusal_reason(db):
    from sqlalchemy import text

    with db() as session:
        leaked = session.execute(text(
            "SELECT persona_id FROM writeprints "
            "WHERE vector IS NOT NULL AND refused_reason IS NOT NULL"
        )).all()
    assert not leaked, f"scored personas carrying a refusal reason: {leaked}"


# ─────────────────────────────────────────────────────────────────────────────
# The corpus contains only what the pipeline can derive
# ─────────────────────────────────────────────────────────────────────────────

def test_no_identifier_was_seeded_that_no_extractor_could_find(db):
    """`personas.json` declares what is true; the pipeline knows what is written.

    scripts/load_fixtures.py used to seed both, so `--source db` could cite
    evidence — "3~18 share a mirror onion" — that `--source fixtures` never
    showed and no extractor could produce. Every identifier in the database
    must now carry meta.source = "ingest", meaning Phase 1 found it in prose.
    """
    from sqlalchemy import text

    with db() as session:
        stray = session.execute(text(
            "SELECT type, value FROM identifiers "
            "WHERE meta->>'source' IS DISTINCT FROM 'ingest' ORDER BY type"
        )).all()
    assert not stray, (
        "identifiers present in the database that the extractor never derived: "
        + ", ".join(f"{t} {v[:24]}" for t, v in stray)
    )


def test_the_unreachable_identifiers_are_absent_by_value(db):
    """The six recorded in docs/BUILD_PLAN.md, named so a reseed cannot restore
    them quietly."""
    from sqlalchemy import text

    unreachable = [
        "x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xawjjhi7ny6hhjuspyd.onion",
        "@dread_fam",
        "@nordic_supply",
        "0x6e05b5893F34dd1077cae73F8BB39357759FbE4F",
        "bc1q3wk97fe0ce3w6p3xxsumxkj57ylhy7rxyrva5y",
    ]
    with db() as session:
        found = session.execute(
            text("SELECT value FROM identifiers WHERE value = ANY(:values)"),
            {"values": unreachable},
        ).all()
    assert not found, f"unreachable identifier(s) back in the database: {found}"


def test_dropping_them_left_the_strong_pair_confirmed(links):
    """3~18 was the only shared one, and it saturates H on PGP without it."""
    pair = next((r for r in links if r["pair"] == (3, 18)), None)
    if pair is None:
        pytest.skip("3~18 not stored — run `python -m link.resolve --source db`")
    assert pair["h"] == 1.0
    details = [e.get("detail", "") for e in pair["evidence"]]
    assert any("PGP fingerprint" in d for d in details)
    assert not any("mirror onion" in d for d in details), (
        "the mirror onion is back in the evidence for 3~18"
    )
