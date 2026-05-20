"""add session_type to conversation_sessions

Revision ID: j4k5l6m7n8o9
Revises: i2j3k4l5m6n7
Branch labels: None
Depends on: None
Create Date: 2026-05-19 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "j4k5l6m7n8o9"
down_revision = "i2j3k4l5m6n7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_sessions",
        sa.Column(
            "session_type",
            sa.String(16),
            nullable=False,
            server_default="chat",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation_sessions", "session_type")
