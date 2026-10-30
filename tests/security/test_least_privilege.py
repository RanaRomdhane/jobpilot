"""The triage agent cannot send, delete, forward or un-label — by construction, not by prompt."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import httpx
import pytest

from jobpilot_agents import triage as triage_agent
from jobpilot_mcp_gmail import gmail_client, server, tools
from jobpilot_mcp_gmail.gmail_client import GmailClient, LabelNotAllowedError, OAuthToken

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_ENDPOINTS = re.compile(
    r"/send\b|/trash\b|/untrash\b|/delete\b|/drafts|/forwarding|/filters|/settings|"
    r"removeLabelIds|batchDelete|/import\b|/insert\b",
)
FORBIDDEN_METHOD_WORDS = (
    "send",
    "draft",
    "delete",
    "trash",
    "forward",
    "remove",
    "archive",
    "filter",
    "insert",
    "import",
)


async def test_mcp_server_exposes_exactly_the_triage_toolset() -> None:
    names = sorted(t.name for t in await server.build_server().list_tools())
    assert names == sorted(server.TRIAGE_TOOL_NAMES)
    assert tuple(sorted(triage_agent.TRIAGE_TOOLS)) == tuple(sorted(server.TRIAGE_TOOL_NAMES))


def test_gmail_client_has_no_dangerous_methods() -> None:
    public = [
        n for n, _ in inspect.getmembers(GmailClient, inspect.isfunction) if not n.startswith("_")
    ]
    for name in public:
        assert not any(word in name for word in FORBIDDEN_METHOD_WORDS), name


def code_only(module: object) -> str:
    """Module source without comments and docstrings (they may *mention* forbidden calls)."""
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
    return ast.unparse(tree)


@pytest.mark.parametrize("module", [gmail_client, server, tools, triage_agent])
def test_no_dangerous_gmail_endpoint_in_triage_code(module: object) -> None:
    assert not FORBIDDEN_ENDPOINTS.search(code_only(module))


def test_triage_scope_is_modify_only() -> None:
    assert gmail_client.TRIAGE_SCOPES == ("https://www.googleapis.com/auth/gmail.modify",)


def test_mcp_subprocess_does_not_inherit_llm_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_should_not_leak")
    srv = triage_agent.triage_mcp_server()
    env = srv.params.env or {}
    assert "GROQ_API_KEY" not in env and "OPENAI_API_KEY" not in env


def _client(handler: httpx.MockTransport) -> GmailClient:
    token = OAuthToken("at", "rt", expiry=1e12, client_id="c", client_secret="s")
    return GmailClient(token, http=httpx.AsyncClient(transport=handler))


async def test_add_labels_only_adds_and_only_jobpilot_labels() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        sent.append({"path": request.url.path, "body": json.loads(request.content or b"{}")})
        return httpx.Response(200, json={})

    client = _client(httpx.MockTransport(handler))
    allowed = {"JobPilot/Offre": "L1", "INBOX": "INBOX"}
    await client.add_labels(["m1", "m2"], ["L1"], allowed)
    assert sent == [
        {
            "path": "/gmail/v1/users/me/messages/batchModify",
            "body": {"ids": ["m1", "m2"], "addLabelIds": ["L1"]},
        }
    ]
    with pytest.raises(LabelNotAllowedError):
        await client.add_labels(["m1"], ["INBOX"], allowed)
    with pytest.raises(LabelNotAllowedError):
        await client.add_labels(["m1"], ["TRASH"], allowed)
    with pytest.raises(LabelNotAllowedError):
        await client.ensure_labels(["Important"])
    await client.aclose()
