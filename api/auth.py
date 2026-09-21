"""auth.py — who is making this request, and are they allowed to.

Until Phase 7 the operator on every audit row was whoever set $OPERATOR_ID in
the process environment. For a tool whose central claim is that every action is
attributable to a named person, that made the claim about a variable. This module
replaces it with a login.

TRANSPORT
---------
FastAPI issues and verifies a JWT bearer token. It never sets a cookie and never
keeps a session table: verification is a signature check, so any replica accepts
any token and a restart invalidates nothing.

The browser does not hold that token. The Next.js route handler puts it in an
httpOnly cookie and attaches the Authorization header server-side, so no script
on the page can read it. That is why CORS can stay `allow_credentials=False` —
the browser never calls this API directly, the BFF does.

WHAT IS PUBLIC
--------------
`/health` stays open because the Docker HEALTHCHECK polls it and a container that
cannot report its own health is worse than one that leaks a row count. `/docs`
and `/openapi.json` stay open because they describe the shape of the API, not its
data. Everything else needs a token.

ROLES
-----
Two words, deliberately. `analyst` reads, analyses and exports. `admin` also runs
the pipeline and reads the audit log — the two actions that respectively change
the data and reveal what colleagues have been looking at. Anything finer would be
a permissions system nobody asked for and nobody would configure correctly.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bcrypt  # noqa: E402
import jwt  # noqa: E402
from fastapi import Depends, HTTPException, Request  # noqa: E402
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api.deps import get_session  # noqa: E402
from db import User, utcnow  # noqa: E402

__all__ = [
    "Principal",
    "TOKEN_TTL_HOURS",
    "current_user",
    "decode_token",
    "hash_password",
    "issue_token",
    "jwt_secret",
    "require_role",
    "verify_password",
]

ALGORITHM = "HS256"

#: Long enough for a working session, short enough that a leaked token is not a
#: permanent credential. There is no refresh flow: log in again.
TOKEN_TTL_HOURS = 12

#: Paths that must answer without a token. Kept narrow on purpose.
PUBLIC_PATHS = frozenset({
    "/health", "/docs", "/redoc", "/openapi.json", "/auth/login", "/favicon.ico",
})

_bearer = HTTPBearer(auto_error=False)


def jwt_secret() -> str:
    """The signing key.

    Read at call time rather than import time so a test can set it, and so a
    container that was started without one fails on the first login attempt with
    a message rather than at import with a traceback.
    """
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="JWT_SECRET is not set, so tokens cannot be signed. Set it in "
                   ".env (any long random string) and restart the API.",
        )
    return secret


# ─────────────────────────────────────────────────────────────────────────────
# Passwords
# ─────────────────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """bcrypt, with a per-password salt. Returns the full modular-crypt string."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time compare via bcrypt. A malformed stored hash is a failure,
    not an exception — a corrupted row must not let anyone in, nor 500."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Tokens
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Principal:
    """Who is making this request. The name that lands in the audit row."""

    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def issue_token(user: User) -> tuple[str, datetime]:
    """Sign a token for `user`. Returns the token and its expiry."""
    expires = datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)
    payload = {
        "sub": user.username,
        "role": user.role,
        "iat": datetime.now(timezone.utc),
        "exp": expires,
    }
    return jwt.encode(payload, jwt_secret(), algorithm=ALGORITHM), expires


def decode_token(token: str) -> Principal:
    """Verify a token and return its principal, or raise 401.

    Every failure mode collapses to the same 401. Distinguishing "expired" from
    "bad signature" in the response body tells an attacker which half of their
    guess was right.
    """
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="session expired — sign in again",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    username, role = payload.get("sub"), payload.get("role")
    if not username or not role:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return Principal(username=username, role=role)


# ─────────────────────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────────────────────

def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    """The signed-in operator, or 401.

    Deliberately does not hit the database. The token is the assertion, and a
    lookup per request would make every read depend on the users table being
    reachable. The cost is that disabling an account takes effect when the
    token expires rather than immediately; with a 12-hour TTL and two seeded
    accounts that is the right trade here, and it is the thing to revisit first
    if this ever has real users.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="not signed in",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(credentials.credentials)


def require_role(*roles: str):
    """Dependency factory: allow only these roles through.

    The 403 names the role required. That is not a leak — the caller is already
    authenticated, and "forbidden" with no reason is how an analyst ends up
    filing a bug against a working permission check.
    """
    allowed = frozenset(roles)

    def dependency(principal: Principal = Depends(current_user)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"this action requires the {' or '.join(sorted(allowed))} "
                       f"role; {principal.username} is {principal.role}",
            )
        return principal

    return dependency


def authenticate(session, username: str, password: str) -> Optional[User]:
    """Check a username and password. None on any failure.

    A disabled account and a wrong password are the same answer on purpose.
    """
    user = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if user is None or user.disabled:
        # Still hash something, so a missing username does not return
        # measurably faster than a wrong password.
        verify_password(password, "$2b$12$" + "." * 53)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def principal_from_request(request: Request) -> Optional[Principal]:
    """Best-effort principal for the audit middleware.

    Returns None rather than raising: the middleware runs after the response has
    been produced, and a request that was already rejected as unauthenticated
    should not be turned into a second error on its way out.
    """
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        return decode_token(token)
    except HTTPException:
        return None


def action_hash(operator: str, method: str, path: str, query: str) -> str:
    """SHA-256 over the action, matching how the pipeline hashes its own.

    The point is tamper evidence over the log, not secrecy: given a row you can
    recompute this and see whether the recorded action is the one that happened.
    """
    canonical = "|".join((operator, method, path, query or ""))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def touch_login(session, user: User) -> None:
    """Record a successful sign-in on the user row."""
    user.last_login_at = utcnow()
    session.flush()
