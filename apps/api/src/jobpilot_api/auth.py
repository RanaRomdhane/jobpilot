"""Single-user dashboard authentication.

- The password is verified against an Argon2 hash from `.env` (`make password`).
- Sessions live in Redis; the cookie holds a random id, Redis stores only its SHA-256.
- Every mutating request must send `X-CSRF-Token` matching the session's token (the cookie is
  also SameSite=Strict; the header is defence in depth).
- Login attempts are rate limited.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, status

from jobpilot_api.deps import State

COOKIE_NAME = "jp_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_hasher = PasswordHasher()


@dataclass(frozen=True, slots=True)
class SessionData:
    csrf: str
    created: float


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return bool(_hasher.verify(password_hash, password))
    except (VerificationError, InvalidHashError):
        return False


def _key(session_id: str) -> str:
    return "session:" + hashlib.sha256(session_id.encode()).hexdigest()


async def create_session(state: State) -> tuple[str, SessionData]:
    session_id = secrets.token_urlsafe(32)
    data = SessionData(csrf=secrets.token_urlsafe(32), created=time.time())
    ttl = state.settings.session_ttl_hours * 3600
    await state.redis.set(_key(session_id), json.dumps(asdict(data)), ex=ttl)
    return session_id, data


async def load_session(state: State, session_id: str | None) -> SessionData | None:
    if not session_id:
        return None
    raw = await state.redis.get(_key(session_id))
    if raw is None:
        return None
    data = json.loads(raw)
    return SessionData(csrf=str(data["csrf"]), created=float(data["created"]))


async def delete_session(state: State, session_id: str | None) -> None:
    if session_id:
        await state.redis.delete(_key(session_id))


async def login_allowed(state: State, client: str) -> bool:
    """Fixed one-minute window per client address."""
    key = f"login:attempts:{client}:{int(time.time() // 60)}"
    count = int(await state.redis.incr(key))
    if count == 1:
        await state.redis.expire(key, 120)
    return count <= state.settings.login_max_attempts_per_minute


async def require_session(request: Request, state: State) -> SessionData:
    if state.settings.dashboard_password_hash is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="password_not_set")
    session = await load_session(state, request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not_authenticated")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent, session.csrf):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="csrf_failed")
    return session


Authenticated = Annotated[SessionData, Depends(require_session)]
