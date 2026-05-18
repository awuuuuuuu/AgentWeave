"""add dept_prompts to organizations

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Branch labels: None
Depends on: None
Create Date: 2026-05-18 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "i2j3k4l5m6n7"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("dept_prompts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "dept_prompts")
