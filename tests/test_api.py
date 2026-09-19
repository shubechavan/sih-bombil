"""test_api.py — the contract the UI is built against.

Most of these exist to defend one property: **the wire never turns "not
assessed" into a number.** Three layers already protect it — attribution.py
refuses to treat None as zero, the links table stores NULL, and api/schemas.py
makes Component a tagged union — and this is the layer that checks a client
actually receives it that way.

Runs against the live database through FastAPI's TestClient, and skips rather
than fails when Postgres is down or the pipeline has not been run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def client():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from api.main import app  # noqa: PLC0415

    with fastapi_testclient.TestClient(app) as test_client:
        response = test_client.get("/health")
        if response.status_code != 200:
            pytest.skip(f"API unhealthy: {response.status_code}")
        payload = response.json()
        if payload.get("database") != "ok":
            pytest.skip(f"database unavailable: {payload.get('database')}")
        if not payload.get("ready"):
            pytest.skip(payload.get("hint") or "pipeline has not been run")
        yield test_client


@pytest.fixture(scope="module")
def actors(client):
    return client.get("/actors", params={"limit": 500}).json()


@pytest.fixture(scope="module")
def links(client, actors):
    multi = [a for a in actors if a["persona_count"] > 1]
    assert multi, "no multi-persona actor to read links from"
    detail = client.get(f"/actors/{multi[0]['id']}").json()
    return detail["links"]


# ─────────────────────────────────────────────────────────────────────────────
# The invariant
# ─────────────────────────────────────────────────────────────────────────────

def test_a_component_is_either_a_value_or_a_reason_never_both(links):
    for link in links:
        for key, component in link["components"].items():
            if component["measured"]:
                assert component["value"] is not None, f"{key} measured with no value"
                assert component["reason"] is None, f"{key} measured but carries a reason"
            else:
                assert component["value"] is None, (
                    f"{key} is unmeasured but carries value {component['value']!r} — "
                    f"a client would render it"
                )
                assert component["reason"], f"{key} unmeasured with no reason"


def test_unmeasured_infrastructure_is_never_served_as_a_number(links):
    """The Phase 3 result, as a client sees it."""
    for link in links:
        component = link["components"]["I"]
        assert component["measured"] is False
        assert component["value"] is None
        assert "infrastructure not assessed" in component["reason"].lower()


def test_an_unmeasured_reason_is_a_sentence_not_a_code(links):
    for link in links:
        for component in link["components"].values():
            if not component["measured"]:
                assert len(component["reason"]) > 40, component["reason"]
                assert " " in component["reason"]


def test_every_component_carries_its_weight(links):
    for link in links:
        weights = {k: c["weight"] for k, c in link["components"].items()}
        assert all(w is not None for w in weights.values()), weights
        assert abs(sum(weights.values()) - 1.0) < 1e-9, weights


# ─────────────────────────────────────────────────────────────────────────────
# Evidence
# ─────────────────────────────────────────────────────────────────────────────

def test_every_link_carries_evidence_in_plain_language(links):
    for link in links:
        assert link["evidence"], f"{link['persona_a']}~{link['persona_b']} has no evidence"
        for entry in link["evidence"]:
            assert entry["type"], "evidence entry with no type"
            assert entry["detail"].strip(), "evidence entry with no detail"


def test_the_unmeasured_component_explains_itself_in_the_evidence_too(links):
    for link in links:
        notes = [
            e for e in link["evidence"]
            if e["type"] == "component_not_assessed" and e["component"] == "I"
        ]
        assert len(notes) == 1
        assert notes[0]["detail"] == link["components"]["I"]["reason"]


def test_identifier_evidence_says_whether_it_was_found_in_prose(links):
    """Some fixture identifiers appear in no bio and no post. A link citing one
    must not imply the extractor discovered it."""
    tagged = [
        e for link in links for e in link["evidence"]
        if e["type"] == "shared_identifier"
    ]
    assert tagged, "no shared_identifier evidence to check"
    for entry in tagged:
        assert entry["derived"] in (True, False), (
            f"{entry['value']!r} has no provenance flag"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Refusals
# ─────────────────────────────────────────────────────────────────────────────

def test_a_refused_persona_is_reported_as_refused_with_its_reason(client, actors):
    refused = []
    for actor in actors:
        detail = client.get(f"/actors/{actor['id']}").json()
        refused.extend(p for p in detail["personas"] if p["stylometry_refused"])
    assert refused, "no refused persona surfaced — persona 7 should be"
    for persona in refused:
        assert persona["stylometry_refused_reason"], "refused with no reason"
        assert persona["char_count"] is not None
        assert "300-character floor" in persona["stylometry_refused_reason"]


def test_the_graph_marks_refused_personas_on_the_node(client):
    payload = client.get("/graph", params={"min_score": 0.0}).json()
    flagged = [n for n in payload["nodes"] if n["stylometry_refused"]]
    assert flagged, "the graph hides the refusal"


# ─────────────────────────────────────────────────────────────────────────────
# Actors
# ─────────────────────────────────────────────────────────────────────────────

def test_a_single_persona_actor_has_no_band_and_no_confidence(actors):
    """It was never merged. A band of WEAK would imply we compared it."""
    singles = [a for a in actors if a["persona_count"] == 1]
    assert singles
    for actor in singles:
        assert actor["band"] is None, f"{actor['label']} banded {actor['band']}"
        assert actor["confidence"] is None


def test_a_merged_actor_reports_its_weakest_link(actors, client):
    multi = [a for a in actors if a["persona_count"] > 1]
    assert multi
    for actor in multi:
        detail = client.get(f"/actors/{actor['id']}").json()
        scores = [l["score"] for l in detail["links"]]
        assert scores
        assert actor["confidence"] == pytest.approx(min(scores), abs=1e-6), (
            "confidence should be the weakest merging edge, not the strongest"
        )


def test_an_unknown_actor_is_a_404(client):
    assert client.get("/actors/999999").status_code == 404


def test_filters_narrow_rather_than_widen(client, actors):
    confirmed = client.get("/actors", params={"band": "CONFIRMED"}).json()
    assert len(confirmed) <= len(actors)
    assert all(a["band"] == "CONFIRMED" for a in confirmed)

    big = client.get("/actors", params={"min_personas": 2}).json()
    assert all(a["persona_count"] >= 2 for a in big)


# ─────────────────────────────────────────────────────────────────────────────
# Graph, timeline, recon, export
# ─────────────────────────────────────────────────────────────────────────────

def test_the_graph_threshold_drops_edges(client):
    loose = client.get("/graph", params={"min_score": 0.0}).json()
    tight = client.get("/graph", params={"min_score": 0.99}).json()
    assert len(tight["edges"]) < len(loose["edges"])
    assert all(e["score"] >= 0.99 for e in tight["edges"])


def test_graph_edges_reference_nodes_that_exist(client):
    payload = client.get("/graph", params={"min_score": 0.0}).json()
    ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["source"] in ids and edge["target"] in ids


def test_timeline_buckets_are_ordered_and_non_empty(client):
    buckets = client.get("/timeline", params={"bucket": "month"}).json()
    assert buckets
    assert [b["bucket"] for b in buckets] == sorted(b["bucket"] for b in buckets)
    assert all(b["posts"] > 0 for b in buckets)


def test_recon_says_why_its_findings_do_not_feed_the_i_term(client):
    reports = client.get("/recon").json()
    assert reports
    for report in reports:
        assert report["attribution_note"], "recon page with no attribution note"
        assert "site-level" in report["attribution_note"]


def test_csv_export_keeps_the_reason_when_it_cannot_write_a_number(client):
    body = client.get("/export/csv", params={"dataset": "links"}).text
    header = body.splitlines()[0].split(",")
    assert "I" in header and "I_reason" in header
    assert "infrastructure not assessed" in body


def test_json_export_respects_a_column_subset(client):
    rows = client.get(
        "/export/json", params={"dataset": "actors", "columns": "id,label"}
    ).json()
    assert rows
    assert all(set(r) == {"id", "label"} for r in rows)


def test_an_unknown_export_column_is_rejected_by_name(client):
    response = client.get("/export/json", params={"dataset": "actors", "columns": "nope"})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_pdf_export_serves_the_case_report(client):
    """Phase 4 returned 400 here naming Phase 5. Phase 5 built it."""
    response = client.get("/export/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    assert "attachment" in response.headers.get("content-disposition", "")


def test_pdf_export_can_be_narrowed_to_one_actor(client, actors):
    whole = client.get("/export/pdf")
    one = client.get("/export/pdf", params={"actor": actors[0]["id"]})
    assert one.status_code == 200
    assert len(one.content) < len(whole.content), (
        "a single-actor report should be smaller than the whole set"
    )


def test_scan_rejects_an_unknown_step(client):
    response = client.post("/scan", json={"steps": ["definitely-not-a-step"]})
    assert response.status_code == 400
    assert "definitely-not-a-step" in response.json()["detail"]


def test_meta_states_the_formula_and_the_infrastructure_caveat(client):
    payload = client.get("/meta").json()
    assert payload["weights"]
    assert abs(sum(payload["weights"].values()) - 1.0) < 1e-9
    assert payload["links_with_unmeasured_infrastructure"] == payload["links"]
    assert "site-level" in payload["infrastructure_note"]
