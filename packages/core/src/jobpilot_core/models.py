"""Database models (single-user, local-first).

Enums are stored as VARCHAR + CHECK constraints instead of native PostgreSQL enums, so adding a
value later is a small migration instead of an `ALTER TYPE` dance.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jobpilot_core.db import Base
from jobpilot_core.enums import (
    ApplicationStatus,
    ApplyChannel,
    CompileStatus,
    DocumentKind,
    EmailCategory,
    OAuthScopeSet,
    OutgoingStatus,
    Region,
    SuggestionStatus,
    TaskStatus,
    TemplateKind,
    TriageBatchStatus,
    WorkMode,
)

EMBEDDING_DIM = 384  # intfloat/multilingual-e5-small (ONNX), chosen in Phase 5


def enum_type(enum: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated_at_col() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# --------------------------------------------------------------------------- user & credentials


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    full_name: Mapped[str | None] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(5), default="fr", server_default="fr")
    timezone: Mapped[str] = mapped_column(
        String(64), default="Africa/Tunis", server_default="Africa/Tunis"
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    default_filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    daily_send_cap: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    sending_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class OAuthCredential(Base):
    """One row per scope set; the token JSON is Fernet-encrypted (see `crypto.SecretBox`)."""

    __tablename__ = "oauth_credentials"
    __table_args__ = (UniqueConstraint("provider", "scope_set"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="google")
    scope_set: Mapped[OAuthScopeSet] = mapped_column(enum_type(OAuthScopeSet, "scope_set"))
    account_email: Mapped[str | None] = mapped_column(String(320))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


# --------------------------------------------------------------------------- inbox


class GmailSyncState(Base):
    __tablename__ = "gmail_sync_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_email: Mapped[str] = mapped_column(String(320), unique=True)
    last_history_id: Mapped[int | None] = mapped_column(BigInteger)
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_incremental_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at_col()


class TriageBatch(Base):
    """One agent run over ≤ N emails; records tool calls and tokens to prove efficiency targets."""

    __tablename__ = "triage_batch"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("task_run.id", ondelete="SET NULL")
    )
    status: Mapped[TriageBatchStatus] = mapped_column(
        enum_type(TriageBatchStatus, "triage_batch_status"), default=TriageBatchStatus.PENDING
    )
    email_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    validation_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailMessage(Base):
    __tablename__ = "email_message"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        Index(
            "ix_email_message_unprocessed", "received_at", postgresql_where=text("category IS NULL")
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    gmail_id: Mapped[str] = mapped_column(String(64), unique=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    history_id: Mapped[int | None] = mapped_column(BigInteger)
    from_address: Mapped[str | None] = mapped_column(String(320), index=True)
    from_name: Mapped[str | None] = mapped_column(String(320))
    to_address: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    gmail_label_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    category: Mapped[EmailCategory | None] = mapped_column(
        enum_type(EmailCategory, "email_category")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    classification_reason: Mapped[str | None] = mapped_column(Text)
    # structured details the classifier extracted (e.g. interview_at), used for pipeline linking
    extracted: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    manual_category: Mapped[EmailCategory | None] = mapped_column(
        enum_type(EmailCategory, "email_manual_category")
    )
    label_applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("triage_batch.id", ondelete="SET NULL")
    )

    injection_flag: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
    injection_score: Mapped[float | None] = mapped_column(Float)
    injection_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )

    created_at: Mapped[datetime] = created_at_col()


# --------------------------------------------------------------------------- pipeline


class Company(Base):
    __tablename__ = "company"
    __table_args__ = (Index("ix_company_name_lower", func.lower(text("name"))),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    website: Mapped[str | None] = mapped_column(String(2048))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()


class Application(Base):
    __tablename__ = "application"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("company.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job_posting.id", ondelete="SET NULL"))
    role_title: Mapped[str] = mapped_column(String(300))
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus, "application_status"),
        default=ApplicationStatus.DRAFT,
        index=True,
    )
    apply_channel: Mapped[ApplyChannel] = mapped_column(
        enum_type(ApplyChannel, "apply_channel"), default=ApplyChannel.EMAIL
    )
    apply_url: Mapped[str | None] = mapped_column(String(2048))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(64))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    company: Mapped[Company | None] = relationship(lazy="raise")
    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application",
        order_by="ApplicationEvent.occurred_at",
        lazy="raise",
        cascade="all, delete-orphan",
    )


class ApplicationEvent(Base):
    """Append-only audit trail of everything that happened to an application."""

    __tablename__ = "application_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    occurred_at: Mapped[datetime] = created_at_col()
    source_email_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_message.id", ondelete="SET NULL")
    )

    application: Mapped[Application] = relationship(back_populates="events", lazy="raise")


class StatusSuggestion(Base):
    __tablename__ = "status_suggestion"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    email_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_message.id", ondelete="SET NULL")
    )
    from_status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus, "suggestion_from_status")
    )
    to_status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus, "suggestion_to_status")
    )
    confidence: Mapped[float] = mapped_column(Float)
    interview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[SuggestionStatus] = mapped_column(
        enum_type(SuggestionStatus, "suggestion_status"),
        default=SuggestionStatus.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = created_at_col()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- resume & templates


class Resume(Base):
    __tablename__ = "resume"
    __table_args__ = (
        # at most one master profile
        Index(
            "uq_resume_single_master", "is_master", unique=True, postgresql_where=text("is_master")
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime: Mapped[str | None] = mapped_column(String(127))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # validated by ResumeProfile
    # uploaded file rows: uploaded -> processing -> parsed | failed; the master row: "master"
    status: Mapped[str] = mapped_column(String(20), default="uploaded", server_default="uploaded")
    error: Mapped[str | None] = mapped_column(String(64))  # stable code, translated in the UI
    page_count: Mapped[int | None] = mapped_column(Integer)
    # layout signals from extraction (jobpilot_resume.LayoutSignals) + hidden-text excerpts
    extraction: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # parser model, grounding warnings, passes; on the master row: the uploads it was built from
    parse_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_master: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    injection_score: Mapped[float | None] = mapped_column(Float)
    injection_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    uploaded_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class ATSReport(Base):
    __tablename__ = "ats_report"
    __table_args__ = (CheckConstraint("score >= 0 AND score <= 100", name="score_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resume.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_document.id", ondelete="CASCADE")
    )
    job_id: Mapped[int | None] = mapped_column(ForeignKey("job_posting.id", ondelete="SET NULL"))
    score: Mapped[int] = mapped_column(Integer)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    generated_at: Mapped[datetime] = created_at_col()


class LatexTemplate(Base):
    __tablename__ = "latex_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[TemplateKind] = mapped_column(enum_type(TemplateKind, "template_kind"))
    name: Mapped[str] = mapped_column(String(200))
    file_path: Mapped[str] = mapped_column(String(1024))
    placeholders: Mapped[list[str]] = mapped_column(JSONB, default=list)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    mapping_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    page_budget: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at_col()


# --------------------------------------------------------------------------- jobs


class JobPosting(Base):
    __tablename__ = "job_posting"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        Index(
            "ix_job_posting_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500))
    company_name: Mapped[str | None] = mapped_column(String(300))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id", ondelete="SET NULL"))
    location_raw: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(2), index=True)  # ISO 3166-1 alpha-2
    region: Mapped[Region | None] = mapped_column(enum_type(Region, "region"), index=True)
    work_mode: Mapped[WorkMode | None] = mapped_column(enum_type(WorkMode, "work_mode"), index=True)
    domain: Mapped[str | None] = mapped_column(String(100), index=True)
    seniority: Mapped[str | None] = mapped_column(String(32))
    contract_type: Mapped[str | None] = mapped_column(String(32))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    language: Mapped[str | None] = mapped_column(String(5))
    description: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scraped_at: Mapped[datetime] = created_at_col()
    dedup_hash: Mapped[str] = mapped_column(String(64), unique=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    injection_score: Mapped[float | None] = mapped_column(Float)
    injection_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    apply_email: Mapped[str | None] = mapped_column(String(320))
    apply_url: Mapped[str | None] = mapped_column(String(2048))
    saved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # the same posting found on other boards (dedup_hash): [{"source", "url", "external_id"}]
    also_on: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")


class JobMatch(Base):
    __tablename__ = "job_match"
    __table_args__ = (UniqueConstraint("job_id", "resume_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_posting.id", ondelete="CASCADE"), index=True
    )
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float, index=True)
    vector_score: Mapped[float | None] = mapped_column(Float)
    keyword_score: Mapped[float | None] = mapped_column(Float)
    matched_keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    missing_keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    rationale: Mapped[str | None] = mapped_column(Text)
    llm_score: Mapped[float | None] = mapped_column(Float)  # set once the job was re-ranked
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class SavedSearch(Base):
    __tablename__ = "saved_search"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    schedule_cron: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_match_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = created_at_col()


# --------------------------------------------------------------------------- documents & sending


class GeneratedDocument(Base):
    __tablename__ = "generated_document"
    __table_args__ = (UniqueConstraint("application_id", "kind", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[DocumentKind] = mapped_column(enum_type(DocumentKind, "document_kind"))
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("latex_template.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # tailored data with source ids
    tex_source_path: Mapped[str | None] = mapped_column(String(1024))
    pdf_path: Mapped[str | None] = mapped_column(String(1024))
    compile_status: Mapped[CompileStatus] = mapped_column(
        enum_type(CompileStatus, "compile_status"), default=CompileStatus.PENDING
    )
    compile_log: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_col()


class OutgoingEmail(Base):
    __tablename__ = "outgoing_email"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    to_address: Mapped[str | None] = mapped_column(String(320))
    cc: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    status: Mapped[OutgoingStatus] = mapped_column(
        enum_type(OutgoingStatus, "outgoing_status"), default=OutgoingStatus.DRAFT, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sent_message_id: Mapped[str | None] = mapped_column(String(64))
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


# --------------------------------------------------------------------------- observability & tasks


class LLMCallLog(Base):
    __tablename__ = "llm_call_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    waited_ms: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class TaskRun(Base):
    """A background job (its id is the arq job id); progress is also streamed over SSE."""

    __tablename__ = "task_run"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[TaskStatus] = mapped_column(
        enum_type(TaskStatus, "task_status"), default=TaskStatus.QUEUED, index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
