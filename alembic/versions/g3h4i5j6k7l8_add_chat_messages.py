"""add chat_messages

Revision ID: g3h4i5j6k7l8
Revises: f1a2b3c4d5e6
Branch_labels = None
Depends_on = None
Create Date: 2026-05-15 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "g3h4i5j6k7l8"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("seq", sa.Integer, nullable=False, server_default="0"),
        sa.Column("agent", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("citations", sa.JSON, nullable=True),
        sa.Column("hitl_data", sa.JSON, nullable=True),
        sa.Column("reply_to", sa.JSON, nullable=True),
        sa.Column("is_final_answer", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
