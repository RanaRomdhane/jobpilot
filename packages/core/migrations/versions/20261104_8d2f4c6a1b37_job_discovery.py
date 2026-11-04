"""job discovery: 384-d embeddings, cross-board duplicates, re-rank score

Revision ID: 8d2f4c6a1b37
Revises: 5c1e9a7b2d40
Create Date: 2026-11-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8d2f4c6a1b37"
down_revision: str | None = "5c1e9a7b2d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_dim(dim: int) -> None:
    # embeddings are recomputed by the worker; existing vectors cannot change dimension
    op.drop_index("ix_job_posting_embedding_hnsw", table_name="job_posting")
    op.execute(f"ALTER TABLE job_posting ALTER COLUMN embedding TYPE vector({dim}) USING NULL")
    op.create_index(
        "ix_job_posting_embedding_hnsw",
        "job_posting",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def upgrade() -> None:
    _set_dim(384)
    op.add_column(
        "job_posting",
        sa.Column(
            "also_on",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column("job_match", sa.Column("llm_score", sa.Float(), nullable=True))
    op.add_column(
        "job_match",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("job_match", "updated_at")
    op.drop_column("job_match", "llm_score")
    op.drop_column("job_posting", "also_on")
    _set_dim(768)
