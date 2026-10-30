"""Domain enums shared by DB models, API schemas and agents."""

from __future__ import annotations

from enum import StrEnum


class EmailCategory(StrEnum):
    RECRUTEUR = "Recruteur"
    ENTRETIEN = "Entretien"
    REFUS = "Refus"
    OFFRE = "Offre"
    NEWSLETTER = "Newsletter"
    AUTRE = "Autre"

    @property
    def gmail_label(self) -> str:
        return f"JobPilot/{self.value}"


SUSPICIOUS_LABEL = "JobPilot/⚠ Suspicious"
APPLICATION_LABEL = "JobPilot/Candidature"


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    SENT = "sent"
    RECRUITER_CONTACT = "recruiter_contact"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED = "ghosted"


class ApplyChannel(StrEnum):
    EMAIL = "email"
    LINK = "link"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    AUTO_APPLIED = "auto_applied"


class OutgoingStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    FAILED = "failed"


class OAuthScopeSet(StrEnum):
    TRIAGE = "triage"  # gmail.modify, used only to read + add JobPilot labels
    SEND = "send"  # gmail.send, used only by the sender service


class Region(StrEnum):
    TUNISIA = "tunisia"
    MIDDLE_EAST = "middle_east"
    EUROPE = "europe"
    USA = "usa"
    CANADA = "canada"
    WORLDWIDE = "worldwide"
    OTHER = "other"


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class TemplateKind(StrEnum):
    CV = "cv"
    COVER_LETTER = "cover_letter"


class DocumentKind(StrEnum):
    CV = "cv"
    COVER_LETTER = "cover_letter"


class CompileStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TriageBatchStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
