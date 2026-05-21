"""create hit_testing_logs with retrieved_chunks

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
    op.create_table(
        'hit_testing_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('kb_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('result_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('retrieved_chunks', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hit_testing_logs_kb_id'), 'hit_testing_logs', ['kb_id'], unique=False)
    op.create_index(op.f('ix_hit_testing_logs_user_id'), 'hit_testing_logs', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_hit_testing_logs_user_id'), table_name='hit_testing_logs')
    op.drop_index(op.f('ix_hit_testing_logs_kb_id'), table_name='hit_testing_logs')
    op.drop_table('hit_testing_logs')
