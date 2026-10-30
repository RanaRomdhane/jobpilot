"""Gmail OAuth tokens, stored Fernet-encrypted in `oauth_credentials` (one row per scope set)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from jobpilot_core.crypto import SecretBox
from jobpilot_core.enums import OAuthScopeSet
from jobpilot_core.models import OAuthCredential
from jobpilot_mcp_gmail.gmail_client import OAuthToken

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class GmailNotConnectedError(RuntimeError):
    def __init__(self, scope_set: OAuthScopeSet) -> None:
        super().__init__(f"Gmail is not connected for '{scope_set}': run `make gmail-auth`")


class CredentialStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    async def load(self, scope_set: OAuthScopeSet) -> tuple[OAuthToken, str | None]:
        async with self._sessionmaker() as session:
            row = await self._row(session, scope_set)
            if row is None:
                raise GmailNotConnectedError(scope_set)
            token = OAuthToken.from_dict(json.loads(self._box.decrypt(row.encrypted_token)))
            return token, row.account_email

    async def save(
        self, scope_set: OAuthScopeSet, token: OAuthToken, account_email: str | None = None
    ) -> None:
        async with self._sessionmaker() as session:
            row = await self._row(session, scope_set)
            if row is None:
                row = OAuthCredential(provider="google", scope_set=scope_set)
                session.add(row)
            row.encrypted_token = self._box.encrypt(json.dumps(token.to_dict()))
            row.scopes = list(token.scopes)
            row.expires_at = datetime.fromtimestamp(token.expiry, UTC)
            if account_email is not None:
                row.account_email = account_email
            await session.commit()

    async def status(self) -> dict[str, str | None]:
        async with self._sessionmaker() as session:
            rows = (await session.scalars(select(OAuthCredential))).all()
            return {r.scope_set.value: r.account_email for r in rows}

    @staticmethod
    async def _row(session: AsyncSession, scope_set: OAuthScopeSet) -> OAuthCredential | None:
        result = await session.scalars(
            select(OAuthCredential).where(
                OAuthCredential.provider == "google", OAuthCredential.scope_set == scope_set
            )
        )
        return result.first()
