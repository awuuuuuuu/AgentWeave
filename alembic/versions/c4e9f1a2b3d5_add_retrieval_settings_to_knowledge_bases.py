"""add_retrieval_settings_to_knowledge_bases

Revision ID: c4e9f1a2b3d5
Revises: 085b5c6006f6
Create Date: 2026-05-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e9f1a2b3d5'
down_revision: Union[str, Sequence[str], None] = '085b5c6006f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add per-KB retrieval settings columns."""
    op.add_column('knowledge_bases', sa.Column(
        'retrieval_mode', sa.String(16), nullable=False, server_default='hybrid'))
    op.add_column('knowledge_bases', sa.Column(
        'use_rerank', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('knowledge_bases', sa.Column(
        'top_k', sa.Integer(), nullable=False, server_default='5'))
    op.add_column('knowledge_bases', sa.Column(
        'score_threshold', sa.Float(), nullable=False, server_default='0.0'))


def downgrade() -> None:
    """Remove per-KB retrieval settings columns."""
    op.drop_column('knowledge_bases', 'score_threshold')
    op.drop_column('knowledge_bases', 'top_k')
    op.drop_column('knowledge_bases', 'use_rerank')
    op.drop_column('knowledge_bases', 'retrieval_mode')
