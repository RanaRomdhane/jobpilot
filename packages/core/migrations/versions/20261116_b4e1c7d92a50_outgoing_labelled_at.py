"""outgoing email: when the application label was applied

Revision ID: b4e1c7d92a50
Revises: 8d2f4c6a1b37
Create Date: 2026-11-16 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e1c7d92a50"
down_revision: str | None = "8d2f4c6a1b37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outgoing_email", sa.Column("labelled_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("outgoing_email", "labelled_at")
