"""step8: organizations (invite_code) + session/kb multi-tenant fields

Revision ID: f1a2b3c4d5e6
Revises: d1e2f3a4b5c6
Create Date: 2026-05-15 00:00:00.000000

使用原生 SQL + IF NOT EXISTS / IF EXISTS，确保迁移完全幂等：
无论 create_all 是否提前建过表/列，都能安全运行。
"""
from __future__ import annotations

from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── organizations（幂等建表 + 补列）────────────────────────────────────────
    # 表可能由 create_all 提前建成（早期版本），此处幂等补齐所有列
    op.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id          VARCHAR(36)                  NOT NULL PRIMARY KEY,
            name        VARCHAR(255)                 NOT NULL,
            type        VARCHAR(16)  DEFAULT 'department' NOT NULL,
            invite_code VARCHAR(32)                  NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ  DEFAULT NOW()   NOT NULL
        )
    """)
    # 若表已存在但缺 invite_code（早期 create_all 建表时无此列），补上
    op.execute(
        "ALTER TABLE organizations "
        "ADD COLUMN IF NOT EXISTS invite_code VARCHAR(32)"
    )
    # 为已存在行填充占位值（NOT NULL 约束前先填）
    op.execute(
        "UPDATE organizations SET invite_code = gen_random_uuid()::text "
        "WHERE invite_code IS NULL"
    )
    # 加唯一约束（幂等：先 DROP IF EXISTS 再加）
    op.execute(
        "ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_invite_code_key"
    )
    op.execute(
        "ALTER TABLE organizations ALTER COLUMN invite_code SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE organizations ADD CONSTRAINT organizations_invite_code_key "
        "UNIQUE (invite_code)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_organizations_invite_code "
        "ON organizations (invite_code)"
    )

    # ── users: org_id ────────────────────────────────────────────────────────
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS org_id VARCHAR(36)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_org_id ON users (org_id)"
    )
    # FK：只在不存在时添加
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_users_org_id'
            ) THEN
                ALTER TABLE users
                ADD CONSTRAINT fk_users_org_id
                FOREIGN KEY (org_id) REFERENCES organizations(id)
                ON DELETE SET NULL;
            END IF;
        END $$
    """)

    # ── knowledge_bases: org_id ──────────────────────────────────────────────
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS org_id VARCHAR(36)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_org_id "
        "ON knowledge_bases (org_id)"
    )
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_knowledge_bases_org_id'
            ) THEN
                ALTER TABLE knowledge_bases
                ADD CONSTRAINT fk_knowledge_bases_org_id
                FOREIGN KEY (org_id) REFERENCES organizations(id)
                ON DELETE SET NULL;
            END IF;
        END $$
    """)

    # ── conversation_sessions: kb_ids + updated_at ───────────────────────────
    op.execute(
        "ALTER TABLE conversation_sessions "
        "ADD COLUMN IF NOT EXISTS kb_ids JSONB"
    )
    op.execute(
        "ALTER TABLE conversation_sessions "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE conversation_sessions DROP COLUMN IF EXISTS updated_at"
    )
    op.execute(
        "ALTER TABLE conversation_sessions DROP COLUMN IF EXISTS kb_ids"
    )

    op.execute(
        "ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS fk_knowledge_bases_org_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_knowledge_bases_org_id")
    op.execute(
        "ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS org_id"
    )

    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_org_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_users_org_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS org_id")

    op.execute("DROP INDEX IF EXISTS ix_organizations_invite_code")
    op.execute("DROP TABLE IF EXISTS organizations")
