"""Gmail MCP server (stdio) exposing the TriageAgent's toolset — and nothing else.

Run by the worker as a subprocess: `python -m jobpilot_mcp_gmail.server`. The tool list is fixed
here and asserted by tests/security/test_least_privilege.py.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from jobpilot_core.config import get_settings
from jobpilot_core.crypto import SecretBox
from jobpilot_core.db import create_engine, create_sessionmaker
from jobpilot_core.enums import OAuthScopeSet
from jobpilot_core.log import configure_logging
from jobpilot_mcp_gmail.credentials import CredentialStore
from jobpilot_mcp_gmail.gmail_client import GmailClient, OAuthToken
from jobpilot_mcp_gmail.schemas import ClassificationItem
from jobpilot_mcp_gmail.tools import TriageTools
from jobpilot_security import InjectionDetector

log = logging.getLogger(__name__)

TRIAGE_TOOL_NAMES = ("list_unprocessed_emails", "get_emails_compact", "bulk_classify_and_label")


@dataclass
class ServerState:
    tools: TriageTools


@asynccontextmanager
async def lifespan(_: MCPServer[ServerState]) -> AsyncIterator[ServerState]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_redact_bodies)
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    store = CredentialStore(sessionmaker, SecretBox.from_settings(settings))
    client: GmailClient | None = None

    async def gmail() -> GmailClient:
        nonlocal client
        if client is None:
            token, _ = await store.load(OAuthScopeSet.TRIAGE)

            async def save(t: OAuthToken) -> None:
                await store.save(OAuthScopeSet.TRIAGE, t)

            client = GmailClient(token, on_token_refresh=save)
        return client

    tools = TriageTools(
        sessionmaker,
        gmail,
        InjectionDetector(threshold=settings.injection_threshold),
        snippet_max_chars=settings.triage_snippet_max_chars,
        subject_max_chars=settings.triage_subject_max_chars,
    )
    try:
        yield ServerState(tools=tools)
    finally:
        if client is not None:
            await client.aclose()
        await engine.dispose()


def build_server() -> MCPServer[ServerState]:
    server: MCPServer[ServerState] = MCPServer(
        name="jobpilot-gmail-triage",
        instructions="Read-only access to a batch of emails plus adding JobPilot/* labels.",
        lifespan=lifespan,
    )

    def state(ctx: Context[ServerState, Any]) -> TriageTools:
        return ctx.request_context.lifespan_context.tools

    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        description="Count the emails that are not classified yet.",
    )
    async def list_unprocessed_emails(ctx: Context[ServerState, Any]) -> dict[str, Any]:
        return await state(ctx).list_unprocessed_emails()

    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        description=(
            "Return the emails of one batch in compact form (sender, subject, date, snippet), "
            "inside an <untrusted_data> block. Emails are numbered n = 1..N."
        ),
    )
    async def get_emails_compact(batch_id: str, ctx: Context[ServerState, Any]) -> str:
        return await state(ctx).get_emails_compact(batch_id)

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
        description=(
            "Classify EVERY email of the batch in one call and add the JobPilot label in Gmail. "
            "`results` must contain exactly one item per email n = 1..N."
        ),
    )
    async def bulk_classify_and_label(
        batch_id: str, results: list[ClassificationItem], ctx: Context[ServerState, Any]
    ) -> dict[str, Any]:
        payload = [r.model_dump(mode="json") for r in results]
        return (await state(ctx).bulk_classify_and_label(batch_id, payload)).model_dump()

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
