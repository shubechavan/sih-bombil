"""test_phase5.py — autonomous mode and the PDF case report.

Two things are worth failing a build over here.

**The scheduler must not run unless started.** A tool that re-scans hidden
services because a module got imported is a liability, so the import is checked
for side effects rather than trusted.

**The PDF must not print a number for a component nobody measured.** The
report is the artefact most likely to be read months later by someone who was
not in the room, so the rule the API and the UI already enforce is enforced
again on the rendered page — by extracting the text back out of the PDF and
reading it, not by trusting the code that wrote it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


# ─────────────────────────────────────────────────────────────────────────────
# Autonomous mode
# ─────────────────────────────────────────────────────────────────────────────

def test_importing_the_scheduler_starts_nothing():
    """No timer, no thread, no database write — just definitions.

    Run in a subprocess so an already-imported module cannot mask a side
    effect that only happens on first import.
    """
    probe = (
        "import sys, threading; sys.path.insert(0, r'{root}');"
        "sys.path.insert(0, r'{scripts}');"
        "before = threading.active_count();"
        "import scheduler;"
        "print('threads', threading.active_count() - before);"
        "print('has_state_file', scheduler.STATE_FILE.name)"
    ).format(root=ROOT, scripts=ROOT / "scripts")
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                            text=True, timeout=180, cwd=str(ROOT))
    assert result.returncode == 0, result.stderr
    assert "threads 0" in result.stdout, (
        f"importing the scheduler started a thread:\n{result.stdout}"
    )


def test_the_cli_requires_an_explicit_mode():
    """No default action. Running it bare must not begin a schedule."""
    result = subprocess.run(
        [sys.executable, "scripts/scheduler.py"],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT),
    )
    assert result.returncode != 0
    assert "one of the arguments" in (result.stderr + result.stdout)


@pytest.mark.parametrize("value,seconds", [
    ("30s", 30), ("45", 45), ("15m", 900), ("2h", 7200), ("1d", 86400),
])
def test_intervals_parse(value, seconds):
    import scheduler  # noqa: PLC0415

    assert scheduler.parse_interval(value) == seconds


@pytest.mark.parametrize("value", ["", "soon", "5x", "-10", "1s", "29s"])
def test_nonsense_and_sub_floor_intervals_are_refused(value):
    import argparse  # noqa: PLC0415

    import scheduler  # noqa: PLC0415

    with pytest.raises(argparse.ArgumentTypeError):
        scheduler.parse_interval(value)


def test_the_floor_exists_because_rate_limiting_is_per_scan():
    import scheduler  # noqa: PLC0415

    assert scheduler.MIN_INTERVAL_SECONDS >= 30


def test_a_tick_reports_whether_it_relinked_and_why():
    """The decision has to be legible, not a silent skip."""
    import scheduler  # noqa: PLC0415

    try:
        from db import require_schema, session_scope  # noqa: PLC0415

        with session_scope() as session:
            require_schema(session)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres unavailable: {type(exc).__name__}")

    first = scheduler.run_tick(operator="pytest")
    assert first.status == "ok", first.error
    assert first.relink_reason, "a tick must say why it did or did not re-link"
    assert first.action_hash, "every tick needs an action hash for the audit row"

    second = scheduler.run_tick(operator="pytest")
    assert second.status == "ok", second.error
    assert second.corpus_version == first.corpus_version
    assert second.relinked is False, (
        "a tick over an unchanged corpus should skip linking, not redo it"
    )
    assert "unchanged" in second.relink_reason


def test_forcing_a_relink_overrides_the_skip():
    import scheduler  # noqa: PLC0415

    try:
        from db import require_schema, session_scope  # noqa: PLC0415

        with session_scope() as session:
            require_schema(session)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres unavailable: {type(exc).__name__}")

    scheduler.run_tick(operator="pytest")
    forced = scheduler.run_tick(operator="pytest", force=True)
    assert forced.relinked is True
    assert "force" in forced.relink_reason


# ─────────────────────────────────────────────────────────────────────────────
# The PDF case report
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def report_text():
    """Render a report and read the text back out of the PDF."""
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    import io  # noqa: PLC0415

    try:
        from api.deps import get_session  # noqa: PLC0415
        from api.routers.actors import (  # noqa: PLC0415
            collect_actor_detail,
            collect_actors,
        )
        from export.report import build_report  # noqa: PLC0415

        generator = get_session()
        session = next(generator)
        try:
            summaries = collect_actors(session, limit=50)
            if not summaries:
                pytest.skip("no actors — run `python -m link.cluster --source db`")
            merged = [a for a in summaries if a.persona_count > 1][:1]
            single = [a for a in summaries if a.persona_count == 1][:1]
            actors = [collect_actor_detail(session, a.id).model_dump()
                      for a in merged + single]
        finally:
            generator.close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot build a report: {type(exc).__name__}: {exc}")

    payload = build_report(actors, operator="pytest-operator")
    assert payload[:5] == b"%PDF-"
    reader = pypdf.PdfReader(io.BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_an_unmeasured_component_is_words_not_a_number(report_text):
    """The whole point. I is unmeasured on this corpus and must read as prose."""
    assert "not assessed" in report_text
    assert "infrastructure not assessed" in report_text


def test_the_reason_for_the_unmeasured_component_is_carried_in_full(report_text):
    assert "site-level" in report_text
    assert "redistributed" in report_text


def test_the_synthetic_corpus_caveat_is_on_the_report(report_text):
    """Not only in the terminal. A figure quoted from the PDF months later has
    to arrive with the qualification attached."""
    from export.report import CAVEAT  # noqa: PLC0415

    assert "synthetic corpus" in report_text
    assert "not real-world accuracy" in report_text
    assert "ground truth" in CAVEAT


def test_the_report_names_its_operator_and_when_it_was_made(report_text):
    assert "pytest-operator" in report_text
    assert "Generated at" in report_text


def test_the_report_says_its_findings_are_leads(report_text):
    assert "investigative lead" in report_text.lower()


def test_the_report_carries_the_methodology_note(report_text):
    assert "Methodology" in report_text
    assert "noisy-OR" in report_text


def test_evidence_appears_in_plain_language(report_text):
    assert "same PGP fingerprint" in report_text or "same Jabber" in report_text
    assert "writeprint cosine" in report_text
    assert "posting-hour overlap" in report_text


def test_a_never_merged_actor_says_so_rather_than_scoring_weak(report_text):
    assert "NOT MERGED" in report_text


def test_the_graph_image_is_omitted_when_there_is_nothing_to_draw():
    from export.report import graph_image  # noqa: PLC0415

    assert graph_image([], []) is None
    assert graph_image([{"id": 1, "handle": "solo"}], []) is None


def test_the_pdf_endpoint_serves_a_pdf():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    pytest.importorskip("reportlab")
    from api.main import app  # noqa: PLC0415

    with fastapi_testclient.TestClient(app) as client:
        health = client.get("/health").json()
        if not health.get("ready"):
            pytest.skip(health.get("hint") or "pipeline has not been run")
        response = client.get("/export/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content[:5] == b"%PDF-"
        assert "attachment" in response.headers.get("content-disposition", "")


def test_an_unknown_export_format_is_still_refused():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from api.main import app  # noqa: PLC0415

    with fastapi_testclient.TestClient(app) as client:
        response = client.get("/export/docx")
        if response.status_code == 503:
            # The session dependency resolves before the handler, so with the
            # database down every export is a 503 and the format is never
            # reached. Nothing to assert about format handling here.
            pytest.skip("database unavailable")
        assert response.status_code == 400
