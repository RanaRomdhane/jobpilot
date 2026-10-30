"""`make gmail-auth`: run Google's installed-app consent flow and store the token encrypted.

Runs on the host (it opens your browser). Only the triage scope set exists until Phase 7, where the
separate `gmail.send` credential is added behind a Settings switch.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from jobpilot_core.config import get_settings
from jobpilot_core.crypto import SecretBox
from jobpilot_core.db import create_engine, create_sessionmaker
from jobpilot_core.enums import OAuthScopeSet
from jobpilot_mcp_gmail.credentials import CredentialStore
from jobpilot_mcp_gmail.gmail_client import TRIAGE_SCOPES, GmailClient, OAuthToken

SCOPES: dict[OAuthScopeSet, tuple[str, ...]] = {OAuthScopeSet.TRIAGE: TRIAGE_SCOPES}
DEFAULT_CLIENT_FILE = Path("secrets/google_credentials.json")


def run_consent(client_file: Path, scopes: tuple[str, ...]) -> OAuthToken:
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=list(scopes))
    creds = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        prompt="consent",
        access_type="offline",
        authorization_prompt_message="Opening Google's consent page in your browser...",
        success_message="JobPilot is connected. You can close this tab.",
    )
    if not creds.refresh_token:
        raise SystemExit("Google returned no refresh token; revoke JobPilot's access and retry.")
    granted = set(creds.scopes or scopes)
    if not set(scopes) <= granted:
        raise SystemExit(f"missing scopes: {sorted(set(scopes) - granted)}")
    expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
    return OAuthToken(
        access_token=creds.token,
        refresh_token=creds.refresh_token,
        expiry=expiry,
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        token_uri=creds.token_uri,
        scopes=sorted(granted),
    )


async def store(scope_set: OAuthScopeSet, token: OAuthToken) -> str:
    settings = get_settings()
    engine = create_engine(settings)
    store_ = CredentialStore(create_sessionmaker(engine), SecretBox.from_settings(settings))
    client = GmailClient(token)
    try:
        email = str((await client.get_profile())["emailAddress"]).lower()
        await store_.save(scope_set, token, email)
        return email
    finally:
        await client.aclose()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect Gmail to JobPilot")
    parser.add_argument("--scope", choices=[s.value for s in SCOPES], default="triage")
    parser.add_argument("--client-file", type=Path, default=DEFAULT_CLIENT_FILE)
    args = parser.parse_args()
    if not args.client_file.exists():
        print(
            f"OAuth client file not found: {args.client_file}\n"
            "Follow docs/setup/google-oauth.md to create it.",
            file=sys.stderr,
        )
        return 1
    scope_set = OAuthScopeSet(args.scope)
    token = run_consent(args.client_file, SCOPES[scope_set])
    email = asyncio.run(store(scope_set, token))
    print(f"Connected {email} for '{scope_set}' (token encrypted in the database).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
