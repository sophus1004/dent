"""생성 모듈 테이블: LLM 학습 데이터의 지시 · 응답과 검토 결정 기록.

- generation_prompts: 지시(마지막 응답 앞까지의 맥락 메시지) · 같은 지시 해시 · 출처 · 문제 표시(marks)
  · 그대로 둔 검사(kept) · 남은 검사(open_checks) · 휴지통
- generation_responses: 지시에 대한 마지막 응답 글 · 생각 과정(다음 판) · 출처
- generation_changes: 검토 결정(빼기 · 그대로 두기 · 가리기 · 자르기)의 전 · 후 (되돌리기)

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28 22:24:04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_prompts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("import_id", sa.BigInteger(), nullable=True),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="original", nullable=False),
        sa.Column(
            "marks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "kept",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "open_checks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('original', 'synthetic', 'human')",
            name=op.f("ck_generation_prompts_source"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_generation_prompts_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["system_imports.id"],
            name=op.f("fk_generation_prompts_import_id_system_imports"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_prompts")),
    )
    op.create_index(
        "ix_generation_prompts_dataset_id_import_id",
        "generation_prompts",
        ["dataset_id", "import_id"],
        unique=False,
    )
    op.create_index(
        "ix_generation_prompts_same_text",
        "generation_prompts",
        ["dataset_id", "text_hash"],
        unique=False,
    )
    op.create_table(
        "generation_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_id", sa.BigInteger(), nullable=False),
        sa.Column("check_key", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action IN ('drop', 'keep', 'mask', 'cut')", name=op.f("ck_generation_changes_action")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_generation_changes_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_changes_prompt_id_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_changes")),
    )
    op.create_index(
        "ix_generation_changes_dataset_id_id",
        "generation_changes",
        ["dataset_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_generation_changes_prompt_id"), "generation_changes", ["prompt_id"], unique=False
    )
    op.create_table(
        "generation_responses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), server_default="original", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('original', 'synthetic', 'human')",
            name=op.f("ck_generation_responses_source"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_generation_responses_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_responses_prompt_id_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_responses")),
    )
    op.create_index(
        op.f("ix_generation_responses_prompt_id"),
        "generation_responses",
        ["prompt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generation_responses_prompt_id"), table_name="generation_responses")
    op.drop_table("generation_responses")
    op.drop_index(op.f("ix_generation_changes_prompt_id"), table_name="generation_changes")
    op.drop_index("ix_generation_changes_dataset_id_id", table_name="generation_changes")
    op.drop_table("generation_changes")
    op.drop_index("ix_generation_prompts_same_text", table_name="generation_prompts")
    op.drop_index("ix_generation_prompts_dataset_id_import_id", table_name="generation_prompts")
    op.drop_table("generation_prompts")
