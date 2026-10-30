"""Login / logout / session for the single dashboard user."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from jobpilot_api.auth import (
    COOKIE_NAME,
    create_session,
    delete_session,
    load_session,
    login_allowed,
    verify_password,
)
from jobpilot_api.deps import State

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class SessionInfo(BaseModel):
    authenticated: bool
    password_set: bool
    csrf_token: str | None = None


@router.get("/session", response_model=SessionInfo)
async def session_info(request: Request, state: State) -> SessionInfo:
    password_set = state.settings.dashboard_password_hash is not None
    session = await load_session(state, request.cookies.get(COOKIE_NAME))
    return SessionInfo(
        authenticated=session is not None and password_set,
        password_set=password_set,
        csrf_token=session.csrf if session and password_set else None,
    )


@router.post("/login", response_model=SessionInfo)
async def login(
    body: LoginRequest, request: Request, response: Response, state: State
) -> SessionInfo:
    stored = state.settings.dashboard_password_hash
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="password_not_set")
    client = request.client.host if request.client else "unknown"
    if not await login_allowed(state, client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="too_many_attempts")
    if not verify_password(stored.get_secret_value(), body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_password")
    await delete_session(state, request.cookies.get(COOKIE_NAME))  # no session fixation
    session_id, data = await create_session(state)
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        max_age=state.settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="strict",
        secure=state.settings.session_cookie_secure,
        path="/",
    )
    return SessionInfo(authenticated=True, password_set=True, csrf_token=data.csrf)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, state: State) -> None:
    await delete_session(state, request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
