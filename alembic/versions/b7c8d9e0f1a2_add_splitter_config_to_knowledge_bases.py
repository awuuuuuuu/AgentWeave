"""add_splitter_config_to_knowledge_bases

Revision ID: b7c8d9e0f1a2
Revises: ae551b509b62
Create Date: 2026-05-04 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'ae551b509b62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('knowledge_bases', sa.Column('splitter_type', sa.String(32), nullable=True))
    op.add_column('knowledge_bases', sa.Column('chunk_size', sa.Integer(), nullable=True))
    op.add_column('knowledge_bases', sa.Column('chunk_overlap', sa.Integer(), nullable=True))
    op.add_column('knowledge_bases', sa.Column('separators', sa.JSON(), nullable=True))
    op.add_column('knowledge_bases', sa.Column('child_separators', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_bases', 'child_separators')
    op.drop_column('knowledge_bases', 'separators')
    op.drop_column('knowledge_bases', 'chunk_overlap')
    op.drop_column('knowledge_bases', 'chunk_size')
    op.drop_column('knowledge_bases', 'splitter_type')
