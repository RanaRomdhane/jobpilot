"""resume parsing state, layout signals and injection evidence

Revision ID: 5c1e9a7b2d40
Revises: 3a47a823cd41
Create Date: 2026-10-31 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5c1e9a7b2d40"
down_revision: str | None = "3a47a823cd41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "resume",
        sa.Column("status", sa.String(length=20), server_default="uploaded", nullable=False),
    )
    op.add_column("resume", sa.Column("error", sa.String(length=64), nullable=True))
    op.add_column("resume", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("resume", sa.Column("extraction", JSONB, server_default="{}", nullable=False))
    op.add_column("resume", sa.Column("parse_meta", JSONB, server_default="{}", nullable=False))
    op.add_column("resume", sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("resume", sa.Column("injection_score", sa.Float(), nullable=True))
    op.add_column(
        "resume", sa.Column("injection_reasons", JSONB, server_default="[]", nullable=False)
    )


def downgrade() -> None:
    for col in (
        "injection_reasons",
        "injection_score",
        "parsed_at",
        "parse_meta",
        "extraction",
        "page_count",
        "error",
        "status",
    ):
        op.drop_column("resume", col)
