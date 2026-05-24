"""add a2a_url to organizations

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Branch labels: None
Depends on: None
Create Date: 2026-05-23 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a7f3d8c2e9b1"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("a2a_url", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "a2a_url")
