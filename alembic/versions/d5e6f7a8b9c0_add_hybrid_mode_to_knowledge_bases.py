"""add_hybrid_mode_to_knowledge_bases

Revision ID: d5e6f7a8b9c0
Revises: c4e9f1a2b3d5
Create Date: 2026-05-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4e9f1a2b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('knowledge_bases', sa.Column(
        'hybrid_mode', sa.String(16), nullable=False, server_default='weighted'))
    op.add_column('knowledge_bases', sa.Column(
        'vector_weight', sa.Float(), nullable=False, server_default='0.7'))


def downgrade() -> None:
    op.drop_column('knowledge_bases', 'vector_weight')
    op.drop_column('knowledge_bases', 'hybrid_mode')
