"""Application emails: template, header safety, approval token, MIME, send-only client."""

from __future__ import annotations

import base64
import email
import json
from email.policy import default
from typing import Any

import httpx
import pytest

from jobpilot_core.enums import DocumentKind
from jobpilot_core.outgoing import (
    attachment_name,
    compose,
    default_signature,
    hash_token,
    header_text,
    new_approval_token,
    same_role,
    token_matches,
    valid_address,
)
from jobpilot_mcp_gmail.gmail_client import GmailError, OAuthToken
from jobpilot_resume.tailoring import after_cut
from jobpilot_worker.sender import Attachment, GmailSender, build_message

SIGNATURE = "Ada Example\n+00 000 000\nhttps://www.linkedin.com/in/ada-example"


def test_french_template_names_the_role_and_company_and_ends_with_the_signature() -> None:
    draft = compose("fr", "Ingénieur IA F/H", "Exemplia", SIGNATURE)
    assert draft.subject == "Candidature : Ingénieur IA F/H"
    assert draft.body.startswith("Madame, Monsieur,")
    assert "au poste de Ingénieur IA F/H chez Exemplia." in draft.body
    assert draft.body.endswith("Cordialement,\n" + SIGNATURE)


def test_english_template_and_missing_company() -> None:
    draft = compose("en", "Data Scientist", None, "Ada Example")
    assert draft.subject == "Application: Data Scientist"
    assert "for the Data Scientist position: my CV" in draft.body
    assert draft.body.endswith("Kind regards,\nAda Example")
    assert compose("de", "Engineer", None, "A").subject.startswith("Candidature")  # French default


def test_a_posting_cannot_inject_headers_through_its_title() -> None:
    draft = compose("en", "Engineer\r\nBcc: someone@example.com", "Evil\nCo", "A")
    assert "\n" not in draft.subject and "\r" not in draft.subject
    assert header_text("a b\x00c") == "a b c"


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("jobs@example.com", True),
        ("first.last+tag@sub.example.co", True),
        ("Name <jobs@example.com>", False),
        ("a@example.com, b@example.com", False),
        ("jobs@example.com\nBcc: x@example.com", False),
        ("not-an-address", False),
        ("", False),
        (None, False),
    ],
)
def test_only_one_plain_address_is_a_recipient(value: str | None, ok: bool) -> None:
    assert valid_address(value) is ok


def test_attachment_names_and_signature() -> None:
    assert attachment_name(DocumentKind.CV, "Adà Exämple", "Exemplia SA") == (
        "CV_Ada_Example_Exemplia_SA.pdf"
    )
    assert attachment_name(DocumentKind.COVER_LETTER, "Ada", None) == "CoverLetter_Ada.pdf"
    assert default_signature("Ada", None, " https://x.example/in/ada ") == (
        "Ada\nhttps://x.example/in/ada"
    )


def test_the_token_is_never_stored_only_its_hash() -> None:
    token, stored = new_approval_token()
    assert stored == hash_token(token) and token not in stored and len(stored) == 64
    assert token_matches(token, stored)
    assert not token_matches(token + "x", stored)
    assert not token_matches(token, None)
    assert new_approval_token()[0] != token


def test_same_role_ignores_case_accents_and_gender_marks() -> None:
    assert same_role("Ingénieur IA F/H", "ingenieur ia")
    assert same_role("Data Scientist (H/F)", "data scientist")
    assert not same_role("Data Scientist", "Data Engineer")
    assert not same_role("", "")


def test_message_has_the_headers_body_and_pdfs() -> None:
    raw = build_message(
        from_name="Ada Example",
        from_address="ada@example.com",
        to="jobs@example.com",
        cc=["cc@example.com"],
        subject="Candidature : Ingénieur\nBcc: x@example.com",
        body="Madame, Monsieur,\n\nBonjour.",
        attachments=[
            Attachment("CV_Ada.pdf", b"%PDF-1.4 cv"),
            Attachment("CoverLetter_Ada.pdf", b"%PDF"),
        ],
    )
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=default)
    assert msg["To"] == "jobs@example.com" and msg["Cc"] == "cc@example.com"
    assert msg["From"] == "Ada Example <ada@example.com>"
    assert msg["Subject"] == "Candidature : Ingénieur Bcc: x@example.com"
    assert msg["Bcc"] is None
    parts = [p for p in msg.iter_attachments()]
    assert [p.get_filename() for p in parts] == ["CV_Ada.pdf", "CoverLetter_Ada.pdf"]
    assert parts[0].get_content() == b"%PDF-1.4 cv"
    body = msg.get_body(preferencelist=("plain",))
    assert body is not None and "Bonjour." in body.get_content()


def token() -> OAuthToken:
    return OAuthToken("access", "refresh", 9e12, "client", "secret")


async def test_sender_posts_once_and_never_retries() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "m1", "threadId": "t1"})

    sender = GmailSender(token(), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await sender.send("cmF3") == ("m1", "t1")
    assert len(calls) == 1 and calls[0].url.path.endswith("/messages/send")
    assert json.loads(calls[0].content) == {"raw": "cmF3"}

    failing: list[Any] = []

    def broken(request: httpx.Request) -> httpx.Response:
        failing.append(request)
        return httpx.Response(503)

    sender = GmailSender(token(), http=httpx.AsyncClient(transport=httpx.MockTransport(broken)))
    with pytest.raises(GmailError):
        await sender.send("cmF3")
    assert len(failing) == 1  # a timeout may already have sent it: no automatic retry


def test_a_sentence_after_a_cut_one_loses_its_also() -> None:
    assert after_cut("J'ai également conçu un modèle.") == "J'ai conçu un modèle."
    assert after_cut("J’ai notamment livré une API.") == "J’ai livré une API."
    assert after_cut("Par ailleurs, lors du stage, j'ai codé.") == "Lors du stage, j'ai codé."
    assert after_cut("I also built a model.") == "I built a model."
    assert after_cut("J'ai conçu un modèle.") == "J'ai conçu un modèle."
