"""Least-privilege async Gmail REST client (read + add JobPilot labels).

This module deliberately has no code path that sends, drafts, trashes, deletes, forwards, archives,
creates filters or removes labels. Label writes go through `add_labels`, which only accepts label
ids resolved from the `JobPilot/` namespace and never sends `removeLabelIds`. Sending lives in a
separate service with a separate OAuth credential (ADR-0002, ADR-0003).
"""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

import httpx

from jobpilot_security import CleanText, clean_plain, html_to_text

log = logging.getLogger(__name__)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TRIAGE_SCOPES = ("https://www.googleapis.com/auth/gmail.modify",)
LABEL_PREFIX = "JobPilot/"
MAX_BATCH_MODIFY = 1000


class GmailError(RuntimeError):
    pass


class GmailAuthExpiredError(GmailError):
    """Refresh token revoked or expired (apps in Testing mode: after 7 days)."""

    def __init__(self) -> None:
        super().__init__("gmail auth expired or revoked: run `make gmail-auth` again")


class HistoryExpiredError(GmailError):
    """startHistoryId is too old; a full sync is needed."""


class LabelNotAllowedError(GmailError):
    pass


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str
    expiry: float  # unix seconds
    client_id: str
    client_secret: str
    token_uri: str = "https://oauth2.googleapis.com/token"  # noqa: S105 - an URL, not a secret
    scopes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OAuthToken:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


TokenSaver = Callable[[OAuthToken], Awaitable[None]]


@dataclass(slots=True)
class ParsedMessage:
    gmail_id: str
    thread_id: str
    history_id: int | None
    label_ids: list[str]
    from_address: str | None
    from_name: str | None
    to_address: str | None
    subject: str
    snippet: str
    received_at: datetime | None
    content: CleanText  # visible text + hidden segments, for local injection scanning


class GmailClient:
    def __init__(
        self,
        token: OAuthToken,
        *,
        on_token_refresh: TokenSaver | None = None,
        http: httpx.AsyncClient | None = None,
        max_retries: int = 4,
    ) -> None:
        self._token = token
        self._on_refresh = on_token_refresh
        self._http = http or httpx.AsyncClient(timeout=30)
        self._max_retries = max_retries
        self._refresh_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ auth
    async def _access_token(self) -> str:
        async with self._refresh_lock:
            if self._token.expiry - time.time() > 60:
                return self._token.access_token
            resp = await self._http.post(
                self._token.token_uri,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._token.refresh_token,
                    "client_id": self._token.client_id,
                    "client_secret": self._token.client_secret,
                },
            )
            if resp.status_code in (400, 401):
                raise GmailAuthExpiredError()
            resp.raise_for_status()
            data = resp.json()
            self._token.access_token = data["access_token"]
            self._token.expiry = time.time() + int(data.get("expires_in", 3600))
            if self._on_refresh is not None:
                await self._on_refresh(self._token)
            return self._token.access_token

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            headers = {"Authorization": f"Bearer {await self._access_token()}"}
            resp = await self._http.request(method, f"{API}{path}", headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 0:
                self._token.expiry = 0  # force a refresh once
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                await asyncio.sleep(min(30.0, 2**attempt + random.random()))  # noqa: S311
                continue
            if resp.status_code == 404 and path.startswith("/history"):
                raise HistoryExpiredError("history id too old")
            if resp.status_code >= 400:
                raise GmailError(f"gmail {method} {path.split('?')[0]} -> {resp.status_code}")
            return resp.json() if resp.content else {}
        raise GmailError("gmail retries exhausted")

    # ------------------------------------------------------------------ read
    async def get_profile(self) -> dict[str, Any]:
        return await self._request("GET", "/profile")

    async def list_message_ids(self, query: str, *, limit: int = 2000) -> list[str]:
        ids: list[str] = []
        page: str | None = None
        while len(ids) < limit:
            params: dict[str, Any] = {"q": query, "maxResults": min(500, limit - len(ids))}
            if page:
                params["pageToken"] = page
            data = await self._request("GET", "/messages", params=params)
            ids += [m["id"] for m in data.get("messages", [])]
            page = data.get("nextPageToken")
            if not page:
                break
        return ids

    async def list_history(self, start_history_id: int) -> tuple[list[str], int]:
        """Message ids added to the inbox since `start_history_id`, and the latest history id."""
        ids: list[str] = []
        page: str | None = None
        latest = start_history_id
        while True:
            params: dict[str, Any] = {
                "startHistoryId": start_history_id,
                "historyTypes": "messageAdded",
                "labelId": "INBOX",
                "maxResults": 500,
            }
            if page:
                params["pageToken"] = page
            data = await self._request("GET", "/history", params=params)
            latest = int(data.get("historyId", latest))
            for h in data.get("history", []):
                ids += [a["message"]["id"] for a in h.get("messagesAdded", [])]
            page = data.get("nextPageToken")
            if not page:
                return list(dict.fromkeys(ids)), latest

    async def get_message(self, message_id: str) -> ParsedMessage:
        data = await self._request("GET", f"/messages/{message_id}", params={"format": "full"})
        return parse_message(data)

    # ------------------------------------------------------------------ labels (add only)
    async def list_labels(self) -> dict[str, str]:
        data = await self._request("GET", "/labels")
        return {lab["name"]: lab["id"] for lab in data.get("labels", [])}

    async def ensure_labels(self, names: Sequence[str]) -> dict[str, str]:
        """Return name → id for the given JobPilot labels, creating the missing ones."""
        for name in names:
            if not name.startswith(LABEL_PREFIX):
                raise LabelNotAllowedError(f"only {LABEL_PREFIX}* labels may be created: {name!r}")
        existing = await self.list_labels()
        out: dict[str, str] = {}
        for name in names:
            if name not in existing:
                created = await self._request(
                    "POST",
                    "/labels",
                    json={
                        "name": name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                existing[name] = created["id"]
            out[name] = existing[name]
        return out

    async def add_labels(
        self, message_ids: Sequence[str], label_ids: Sequence[str], allowed: dict[str, str]
    ) -> None:
        """Add labels to messages. `allowed` is the name → id map returned by `ensure_labels`."""
        allowed_ids = {i for n, i in allowed.items() if n.startswith(LABEL_PREFIX)}
        if not label_ids or not set(label_ids) <= allowed_ids:
            raise LabelNotAllowedError("refusing to apply a label outside the JobPilot namespace")
        for i in range(0, len(message_ids), MAX_BATCH_MODIFY):
            await self._request(
                "POST",
                "/messages/batchModify",
                json={
                    "ids": list(message_ids[i : i + MAX_BATCH_MODIFY]),
                    "addLabelIds": list(label_ids),
                },
            )


# ---------------------------------------------------------------------- parsing


def _b64url(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _walk(part: dict[str, Any], out: dict[str, list[str]]) -> None:
    mime = part.get("mimeType", "")
    body = part.get("body", {})
    if mime in ("text/plain", "text/html") and body.get("data") and not part.get("filename"):
        out.setdefault(mime, []).append(_b64url(body["data"]))
    for child in part.get("parts", []) or []:
        _walk(child, out)


def parse_message(data: dict[str, Any]) -> ParsedMessage:
    payload = data.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    (from_name, from_addr), *_ = getaddresses([headers.get("from", "")]) or [("", "")]
    to_list = getaddresses([headers.get("to", "")])
    received: datetime | None = None
    if data.get("internalDate"):
        received = datetime.fromtimestamp(int(data["internalDate"]) / 1000, UTC)
    elif headers.get("date"):
        try:
            received = parsedate_to_datetime(headers["date"])
        except (TypeError, ValueError):
            received = None
    bodies: dict[str, list[str]] = {}
    _walk(payload, bodies)
    if bodies.get("text/html"):
        content = html_to_text("\n".join(bodies["text/html"]))
        if not content.text and bodies.get("text/plain"):
            content.text = clean_plain("\n".join(bodies["text/plain"])).text
    else:
        content = clean_plain("\n".join(bodies.get("text/plain", [])))
    return ParsedMessage(
        gmail_id=data["id"],
        thread_id=data.get("threadId", data["id"]),
        history_id=int(data["historyId"]) if data.get("historyId") else None,
        label_ids=list(data.get("labelIds", [])),
        from_address=from_addr.lower() or None,
        from_name=from_name or None,
        to_address=to_list[0][1].lower() if to_list and to_list[0][1] else None,
        subject=headers.get("subject", ""),
        snippet=clean_plain(html.unescape(data.get("snippet", ""))).text,  # Gmail escapes it
        received_at=received,
        content=content,
    )
