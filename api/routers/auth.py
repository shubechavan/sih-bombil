"""auth.py — sign in, sign out, and who am I.

Login takes a JSON body rather than the OAuth2 form FastAPI's helpers expect.
That is not a style preference: the form flow needs `python-multipart`, and the
only client is a Next.js route handler that already speaks JSON. One fewer
dependency for a `/token` endpoint nobody would use as OAuth2.

Logout is deliberately thin. The token is stateless, so there is nothing for the
server to revoke — the BFF clears the httpOnly cookie and the token expires on
its own. Pretending otherwise by keeping a denylist would be a session table in
all but name, which is the thing JWT was chosen to avoid.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from api.auth import (  # noqa: E402
    TOKEN_TTL_HOURS,
    Principal,
    authenticate,
    current_user,
    issue_token,
    touch_login,
)
from api.deps import get_session  # noqa: E402
from db import record_scan  # noqa: E402

router = APIRouter(tags=["auth"])

__all__ = ["router"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    username: str
    role: str


class Identity(BaseModel):
    username: str
    role: str
    #: What this role may do, so the UI does not have to hardcode the mapping
    #: and then disagree with the API about it.
    can_scan: bool
    can_read_audit: bool


@router.post("/auth/login", response_model=LoginResponse)
def login(request: LoginRequest, session=Depends(get_session)) -> LoginResponse:
    user = authenticate(session, request.username, request.password)
    if user is None:
        # One message for "no such user", "wrong password" and "disabled".
        raise HTTPException(status_code=401, detail="invalid username or password")

    token, expires = issue_token(user)
    touch_login(session, user)

    # A sign-in is an auditable action in its own right — it is the moment a
    # name attaches to everything that follows.
    record_scan(
        session,
        operator=user.username,
        mode="api",
        query="auth.login",
        status="ok",
    )
    session.commit()

    return LoginResponse(
        access_token=token,
        expires_at=expires,
        username=user.username,
        role=user.role,
    )


@router.post("/auth/logout")
def logout(principal: Principal = Depends(current_user)) -> dict:
    """Acknowledge the sign-out. The BFF clears the cookie."""
    return {
        "ok": True,
        "detail": f"{principal.username} signed out; the token remains valid "
                  f"until it expires, at most {TOKEN_TTL_HOURS} hours after it "
                  f"was issued",
    }


@router.get("/auth/me", response_model=Identity)
def me(principal: Principal = Depends(current_user)) -> Identity:
    return Identity(
        username=principal.username,
        role=principal.role,
        can_scan=principal.is_admin,
        can_read_audit=principal.is_admin,
    )
