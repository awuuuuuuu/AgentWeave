"""step9: add dept_code and mcp_connections to organizations

Revision ID: h1i2j3k4l5m6
Revises: g3h4i5j6k7l8
Branch labels: None
Depends on: None
Create Date: 2026-05-16 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "h1i2j3k4l5m6"
down_revision = "g3h4i5j6k7l8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("dept_code", sa.String(64), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "mcp_connections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "mcp_connections")
    op.drop_column("organizations", "dept_code")
