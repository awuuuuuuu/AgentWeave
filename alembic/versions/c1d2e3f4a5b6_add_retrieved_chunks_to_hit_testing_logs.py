"""add_retrieved_chunks_to_hit_testing_logs

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-05-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('hit_testing_logs', sa.Column('retrieved_chunks', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('hit_testing_logs', 'retrieved_chunks')
