from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
import pytest

from jobpilot_core.config import Settings
from jobpilot_core.models import GmailSyncState
from jobpilot_mcp_gmail.gmail_client import (
    GmailAuthExpiredError,
    GmailClient,
    HistoryExpiredError,
    MessageNotFoundError,
    OAuthToken,
    ParsedMessage,
    parse_message,
)
from jobpilot_security import InjectionDetector
from jobpilot_worker.gmail_sync import GmailSync


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def gmail_message(**overrides: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": "m1",
        "threadId": "t1",
        "historyId": "123",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1761830000000",
        "snippet": "Bonjour, nous avons re&#231;u votre candidature",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Talent Team <Jobs@Acme.TN>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Votre candidature"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("plain body")}},
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": b64("<p>Visible</p><div style='display:none'>hidden ask</div>")
                    },
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "cv.pdf",
                    "body": {"attachmentId": "a1"},
                },
            ],
        },
    }
    msg.update(overrides)
    return msg


def test_parse_message_extracts_metadata_and_hidden_text() -> None:
    p = parse_message(gmail_message())
    assert (p.gmail_id, p.thread_id, p.history_id) == ("m1", "t1", 123)
    assert p.from_address == "jobs@acme.tn" and p.from_name == "Talent Team"
    assert p.subject == "Votre candidature"
    assert p.snippet == "Bonjour, nous avons reçu votre candidature"
    assert p.content.text == "Visible"
    assert p.content.hidden == ["hidden ask"]
    assert p.received_at is not None and p.received_at.year == 2025


def make_client(handler: Any, *, expiry: float = 1e12) -> tuple[GmailClient, OAuthToken]:
    token = OAuthToken("old", "refresh", expiry=expiry, client_id="c", client_secret="s")
    return GmailClient(token, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))), token


async def test_refreshes_expired_token_and_persists_it() -> None:
    saved: list[str] = []
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        seen_auth.append(request.headers["authorization"])
        return httpx.Response(200, json={"emailAddress": "me@example.com", "historyId": "9"})

    client, token = make_client(handler, expiry=time.time() - 10)

    async def save(t: OAuthToken) -> None:
        saved.append(t.access_token)

    client._on_refresh = save
    assert (await client.get_profile())["emailAddress"] == "me@example.com"
    assert seen_auth == ["Bearer new"] and saved == ["new"] and token.expiry > time.time()


async def test_revoked_refresh_token_raises_actionable_error() -> None:
    client, _ = make_client(
        lambda r: httpx.Response(400, json={"error": "invalid_grant"}), expiry=0
    )
    with pytest.raises(GmailAuthExpiredError, match="make gmail-auth"):
        await client.get_profile()


async def test_history_404_means_full_sync() -> None:
    client, _ = make_client(lambda r: httpx.Response(404, json={}))
    with pytest.raises(HistoryExpiredError):
        await client.list_history(1)


async def test_deleted_message_404_is_its_own_error() -> None:
    client, _ = make_client(lambda r: httpx.Response(404, json={}))
    with pytest.raises(MessageNotFoundError):
        await client.get_message("gone")


class Rows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def first(self) -> Any:
        return self.rows[0]

    def all(self) -> list[Any]:
        return []  # no email is known yet


async def test_sync_skips_a_message_deleted_after_listing() -> None:
    class Gmail:
        async def list_history(self, start: int) -> tuple[list[str], int]:
            return ["gone", "kept"], 42

        async def get_message(self, gmail_id: str) -> ParsedMessage:
            if gmail_id == "gone":
                raise MessageNotFoundError("404")
            return parse_message(gmail_message(id=gmail_id, labelIds=["SPAM"]))

    class Session:
        def __init__(self, state: Any) -> None:
            self.state = state

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def scalars(self, _: object) -> Rows:
            return Rows([self.state])

        def add(self, obj: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    state = GmailSyncState(account_email="me@example.com", last_history_id=1)
    sync = GmailSync(
        Settings(_env_file=None),
        lambda: Session(state),  # type: ignore[arg-type]
        Gmail(),  # type: ignore[arg-type]
        InjectionDetector(),
        "me@example.com",
    )
    result = await sync.run()
    assert (result.seen, result.added, result.skipped) == (2, 0, 2)
    assert state.last_history_id == 42  # the sync moved past the deleted message


async def test_list_message_ids_paginates() -> None:
    pages = {
        None: {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "c"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "in:inbox newer_than:90d"
        return httpx.Response(200, json=pages[request.url.params.get("pageToken")])

    client, _ = make_client(handler)
    assert await client.list_message_ids("in:inbox newer_than:90d") == ["a", "b", "c"]


async def test_history_collects_added_messages() -> None:
    body = {
        "historyId": "50",
        "history": [
            {"messagesAdded": [{"message": {"id": "x"}}, {"message": {"id": "y"}}]},
            {"messagesAdded": [{"message": {"id": "x"}}]},
        ],
    }
    client, _ = make_client(lambda r: httpx.Response(200, content=json.dumps(body)))
    assert await client.list_history(10) == (["x", "y"], 50)
