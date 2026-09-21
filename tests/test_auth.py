"""Tests for login, roles, and the request audit log.

The claim these protect is the one in CLAUDE.md: "Every scan writes an audit row
with operator identity". Before Phase 7 that identity was $OPERATOR_ID, so the
claim was about an environment variable and nothing tested it. Now it is about
whoever signed in, and these are the tests that say so.

Two things here are deliberately awkward and should stay that way:

  * a wrong password, a missing user and a disabled account all produce the same
    401 with the same sentence, so the response cannot be used to enumerate
    accounts;
  * a denied request (401, 403) is still written to the audit log. An attempt to
    read something you are not allowed to read is the single most interesting
    row in an audit table, and logging only successes would drop exactly it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ANALYST = ("analyst", "analyst-demo")
ADMIN = ("admin", "admin-demo")


@pytest.fixture(scope="module")
def client():
    """The API, or skip if it, the database or the seeded accounts are missing."""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from api.main import app  # noqa: PLC0415

    with fastapi_testclient.TestClient(app) as test_client:
        health = test_client.get("/health")
        if health.status_code != 200:
            pytest.skip(f"API unhealthy: {health.status_code}")
        if health.json().get("database") != "ok":
            pytest.skip("database unavailable")

        probe = test_client.post(
            "/auth/login", json={"username": ANALYST[0], "password": ANALYST[1]}
        )
        if probe.status_code == 503:
            pytest.skip("JWT_SECRET is not set")
        if probe.status_code != 200:
            pytest.skip("demo accounts are not seeded — run scripts/seed_users.py")
        yield test_client


def token(client, credentials) -> str:
    username, password = credentials
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def headers(client, credentials) -> dict:
    return {"Authorization": f"Bearer {token(client, credentials)}"}


# ─────────────────────────────────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────────────────────────────────

def test_login_returns_a_token_and_the_role(client):
    response = client.post(
        "/auth/login", json={"username": ANALYST[0], "password": ANALYST[1]}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["token_type"] == "bearer"
    assert payload["role"] == "analyst"
    assert payload["expires_at"]


def test_a_wrong_password_and_a_missing_user_are_indistinguishable(client):
    """Anything else turns the login form into an account enumerator."""
    wrong = client.post(
        "/auth/login", json={"username": ANALYST[0], "password": "not-the-password"}
    )
    missing = client.post(
        "/auth/login", json={"username": "nobody-at-all", "password": "whatever"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]


def test_the_password_is_never_returned(client):
    """Not in the login response, and not in /auth/me."""
    login = client.post(
        "/auth/login", json={"username": ANALYST[0], "password": ANALYST[1]}
    ).text
    me = client.get("/auth/me", headers=headers(client, ANALYST)).text
    for body in (login, me):
        assert "password" not in body.lower()
        assert "$2b$" not in body, "a bcrypt hash reached the wire"


def test_me_reports_what_this_role_can_do(client):
    analyst = client.get("/auth/me", headers=headers(client, ANALYST)).json()
    admin = client.get("/auth/me", headers=headers(client, ADMIN)).json()

    assert analyst["role"] == "analyst"
    assert analyst["can_scan"] is False
    assert analyst["can_read_audit"] is False

    assert admin["role"] == "admin"
    assert admin["can_scan"] is True
    assert admin["can_read_audit"] is True


# ─────────────────────────────────────────────────────────────────────────────
# What a token is required for
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/actors", "/graph", "/timeline", "/recon",
                                  "/meta", "/audit"])
def test_reads_require_a_token(client, path):
    response = client.get(path)
    assert response.status_code == 401, f"{path} answered without a token"


def test_analysis_requires_a_token(client):
    response = client.post("/analyze", json={"text": "x" * 400})
    assert response.status_code == 401


def test_health_stays_public(client):
    """The Docker HEALTHCHECK polls it; a container that cannot report its own
    health is worse than one that exposes a row count."""
    assert client.get("/health").status_code == 200


def test_a_garbage_token_is_rejected(client):
    response = client.get("/actors", headers={"Authorization": "Bearer nonsense"})
    assert response.status_code == 401


def test_a_token_signed_with_the_wrong_key_is_rejected(client):
    """The signature is the whole security model; prove it is actually checked."""
    import jwt  # noqa: PLC0415

    forged = jwt.encode(
        {"sub": "admin", "role": "admin",
         "exp": 4102444800},   # year 2100
        "not-the-real-secret",
        algorithm="HS256",
    )
    response = client.get("/audit", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401, "a forged token was accepted"


def test_an_expired_token_is_rejected(client):
    import jwt  # noqa: PLC0415

    from api.auth import ALGORITHM, jwt_secret  # noqa: PLC0415

    stale = jwt.encode(
        {"sub": "analyst", "role": "analyst", "exp": 1000000000},  # 2001
        jwt_secret(), algorithm=ALGORITHM,
    )
    response = client.get("/actors", headers={"Authorization": f"Bearer {stale}"})
    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Roles
# ─────────────────────────────────────────────────────────────────────────────

def test_an_analyst_cannot_run_the_pipeline(client):
    """Reading is an analyst's job; changing what everyone else reads is not."""
    response = client.post("/scan", json={"steps": ["ingest"]},
                           headers=headers(client, ANALYST))
    assert response.status_code == 403
    assert "admin" in response.json()["detail"]


def test_an_analyst_cannot_read_the_audit_log(client):
    """A log of what your colleagues have been reading is itself sensitive."""
    response = client.get("/audit", headers=headers(client, ANALYST))
    assert response.status_code == 403


def test_an_admin_can_read_the_audit_log(client):
    response = client.get("/audit", headers=headers(client, ADMIN))
    assert response.status_code == 200
    payload = response.json()
    assert "requests" in payload and "scans" in payload
    assert payload["note"]


def test_the_forbidden_message_names_the_role_required(client):
    """"Forbidden" with no reason is how an analyst files a bug against a
    working permission check."""
    response = client.post("/scan", json={"steps": ["ingest"]},
                           headers=headers(client, ANALYST))
    detail = response.json()["detail"]
    assert "admin" in detail and "analyst" in detail


# ─────────────────────────────────────────────────────────────────────────────
# The audit log
# ─────────────────────────────────────────────────────────────────────────────

def test_a_read_is_recorded_with_the_operator_who_made_it(client):
    """The whole point: the name on the row is a person, not $OPERATOR_ID."""
    client.get("/actors", params={"limit": 1}, headers=headers(client, ANALYST))

    rows = client.get("/audit", params={"limit": 50},
                      headers=headers(client, ADMIN)).json()["requests"]
    mine = [r for r in rows if r["path"] == "/actors" and r["operator_id"] == "analyst"]
    assert mine, "a read by 'analyst' was not recorded"
    assert mine[0]["role"] == "analyst"
    assert mine[0]["method"] == "GET"
    assert mine[0]["status"] == 200
    assert mine[0]["at"]


def test_a_denied_request_is_recorded_too(client):
    """The most interesting row in an audit table is the one that was refused."""
    client.get("/audit", headers=headers(client, ANALYST))   # 403

    rows = client.get("/audit", params={"limit": 50},
                      headers=headers(client, ADMIN)).json()["requests"]
    denied = [r for r in rows
              if r["path"] == "/audit" and r["operator_id"] == "analyst"
              and r["status"] == 403]
    assert denied, "a 403 was not written to the audit log"


def test_the_action_hash_is_reproducible(client):
    """Given a row, you can recompute the hash and check it records that action."""
    from api.auth import action_hash  # noqa: PLC0415

    client.get("/actors", params={"limit": 1}, headers=headers(client, ANALYST))
    rows = client.get("/audit", params={"limit": 50},
                      headers=headers(client, ADMIN)).json()["requests"]
    row = next(r for r in rows
               if r["path"] == "/actors" and r["operator_id"] == "analyst")

    assert row["action_hash"] == action_hash(
        row["operator_id"], row["method"], row["path"], row["query"] or ""
    )


def test_login_writes_a_scan_row(client):
    """Signing in is the moment a name attaches to everything that follows."""
    token(client, ANALYST)
    scans = client.get("/audit", params={"limit": 50},
                       headers=headers(client, ADMIN)).json()["scans"]
    logins = [s for s in scans if s["query"] == "auth.login"]
    assert logins, "a login did not write an audit row"
    assert logins[0]["operator_id"] in {"analyst", "admin"}
    assert logins[0]["mode"] == "api"


def test_health_is_not_audited(client):
    """It is polled every 15 seconds by Docker. Logging it would bury the log."""
    before = client.get("/audit", params={"limit": 1},
                        headers=headers(client, ADMIN)).json()["total_requests"]
    for _ in range(3):
        client.get("/health")
    after = client.get("/audit", params={"limit": 1},
                       headers=headers(client, ADMIN)).json()["total_requests"]

    # The two /audit calls above are themselves logged; the three /health calls
    # must not be. Allow for the audit reads, disallow the health polls.
    assert after - before <= 2, "health polls are being written to the audit log"


# ─────────────────────────────────────────────────────────────────────────────
# Scan jobs, now that they live in the table
# ─────────────────────────────────────────────────────────────────────────────

def test_a_job_is_a_row_not_a_dict(client):
    """The job survives the process, so a second client can still read it."""
    started = client.post("/scan", json={"steps": ["ingest"]},
                          headers=headers(client, ADMIN))
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    # A fresh TestClient is a fresh app lifespan — the old dict would be gone.
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from api.main import app  # noqa: PLC0415

    with fastapi_testclient.TestClient(app) as second:
        login = second.post(
            "/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}
        )
        second.headers.update(
            {"Authorization": f"Bearer {login.json()['access_token']}"}
        )
        response = second.get(f"/scan/{job_id}")

    assert response.status_code == 200, "the job did not survive a restart"
    assert response.json()["steps"] == ["ingest"]


def test_scan_ids_name_only_this_jobs_steps(client):
    """The old filter was "every row since this job started", with no owner.

    A concurrent CLI run or scheduler tick was reported as part of the job.
    """
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    started = client.post("/scan", json={"steps": ["ingest"]},
                          headers=headers(client, ADMIN))
    job_id = started.json()["job_id"]

    # Something else touches the audit table after the job began.
    subprocess.run(
        [sys.executable, "-m", "link.cluster", "--source", "db"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )

    payload = client.get(f"/scan/{job_id}", headers=headers(client, ADMIN)).json()

    from sqlalchemy import select  # noqa: PLC0415

    from db import Scan, session_scope  # noqa: PLC0415

    with session_scope() as session:
        queries = [
            row[0] for row in session.execute(
                select(Scan.query).where(Scan.id.in_(payload["scan_ids"]))
            ).all()
        ]

    assert queries, "the job reported no steps at all"
    assert not any("cluster" in (q or "") for q in queries), (
        f"a concurrent run was reported as part of this job: {queries}"
    )


def test_an_interrupted_job_is_reaped_rather_than_left_running(client):
    """A BackgroundTask cannot outlive its worker; say so instead of lying."""
    from api.routers.scan import reap_interrupted_jobs  # noqa: PLC0415
    from db import record_scan, session_scope  # noqa: PLC0415

    with session_scope() as session:
        row = record_scan(
            session, operator="pytest", mode="api", query="scan job",
            status="running", job_id="pytest-interrupted", steps=["ingest"],
        )
        row.stage = "ingest"

    reaped = reap_interrupted_jobs()
    assert reaped >= 1

    payload = client.get("/scan/pytest-interrupted",
                         headers=headers(client, ADMIN)).json()
    assert payload["status"] == "failed"
    assert "restart" in payload["error"]


def test_an_unknown_job_is_a_404(client):
    response = client.get("/scan/no-such-job", headers=headers(client, ADMIN))
    assert response.status_code == 404

