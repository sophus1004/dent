"""생성 모듈: 응답 여럿 · 선호 쌍 · 도구 호출 · 데이터셋 설정 · 오염 · 안전 판정.

- generation_responses: 주 응답 표시(is_main, 지시마다 하나 · 부분 UNIQUE) · 응답이 부른 도구(tool_calls) · 원본 점수(score)
- generation_prompts.tools: 대화에서 쓸 수 있는 도구 정의
- generation_pairs: 선호 쌍(고른 응답 · 버린 응답 · 출처 · 쌍 표시 · 그대로 둔 검사 · 응답마다 Jev 판정 · 휴지통)
- generation_dataset_settings: 만들 데이터(SFT · 선호) · 평가셋 표시 · 학습할 모델(토크나이저) · 최대 길이 ·
  긴 대화를 잰 토크나이저 · 오염 대조 표시
- generation_contaminations: 평가셋과 겹친 지시와 겹친 몫
- generation_judgments.unsafe: Jev 안전 판정
- generation_changes: 쌍 번호(pair_id)와 결정 flip(뒤집기) · answer(응답 만들기) · pair(비교 만들기)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-12 13:34:14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 결정 값의 CHECK 제약 (선호 · 응답 만들기 앞 · 뒤)
OLD_ACTIONS = "action IN ('drop', 'keep', 'mask', 'cut', 'rewrite')"
NEW_ACTIONS = "action IN ('drop', 'keep', 'mask', 'cut', 'rewrite', 'flip', 'answer', 'pair')"


def upgrade() -> None:
    op.create_table(
        "generation_dataset_settings",
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("goal", sa.String(length=16), server_default="sft", nullable=False),
        sa.Column("is_eval", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tokenizer_model", sa.String(length=200), nullable=True),
        sa.Column("max_tokens", sa.Integer(), server_default="4096", nullable=False),
        sa.Column("measured_model", sa.String(length=200), nullable=True),
        sa.Column("measured_max_tokens", sa.Integer(), nullable=True),
        sa.Column("contamination_signature", sa.Text(), nullable=True),
        sa.Column("contamination_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "goal IN ('sft', 'preference')", name=op.f("ck_generation_dataset_settings_goal")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_generation_dataset_settings_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dataset_id", name=op.f("pk_generation_dataset_settings")),
    )
    op.create_table(
        "generation_contaminations",
        sa.Column("prompt_id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("eval_prompt_id", sa.BigInteger(), nullable=False),
        sa.Column("coverage", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_generation_contaminations_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["eval_prompt_id"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_contaminations_eval_prompt_id_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_contaminations_prompt_id_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("prompt_id", name=op.f("pk_generation_contaminations")),
    )
    op.create_index(
        op.f("ix_generation_contaminations_dataset_id"),
        "generation_contaminations",
        ["dataset_id"],
    )
    op.create_index(
        op.f("ix_generation_contaminations_eval_prompt_id"),
        "generation_contaminations",
        ["eval_prompt_id"],
    )

    op.add_column(
        "generation_responses",
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "generation_responses",
        sa.Column("is_main", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("generation_responses", sa.Column("score", sa.REAL(), nullable=True))
    op.create_index(
        "uq_generation_responses_main",
        "generation_responses",
        ["prompt_id"],
        unique=True,
        postgresql_where=sa.text("is_main"),
    )
    op.add_column(
        "generation_prompts",
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("generation_judgments", sa.Column("unsafe", sa.REAL(), nullable=True))

    op.create_table(
        "generation_pairs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_id", sa.BigInteger(), nullable=False),
        sa.Column("chosen_id", sa.BigInteger(), nullable=False),
        sa.Column("rejected_id", sa.BigInteger(), nullable=False),
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
        sa.Column("judge_chosen", sa.REAL(), nullable=True),
        sa.Column("judge_rejected", sa.REAL(), nullable=True),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
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
            "source IN ('original', 'synthetic', 'human')", name=op.f("ck_generation_pairs_source")
        ),
        sa.ForeignKeyConstraint(
            ["chosen_id"],
            ["generation_responses.id"],
            name=op.f("fk_generation_pairs_chosen_id_generation_responses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_generation_pairs_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_pairs_prompt_id_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_id"],
            ["generation_responses.id"],
            name=op.f("fk_generation_pairs_rejected_id_generation_responses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_pairs")),
        sa.UniqueConstraint(
            "chosen_id", "rejected_id", name=op.f("uq_generation_pairs_chosen_id_rejected_id")
        ),
    )
    op.create_index(op.f("ix_generation_pairs_dataset_id"), "generation_pairs", ["dataset_id"])
    op.create_index(op.f("ix_generation_pairs_prompt_id"), "generation_pairs", ["prompt_id"])

    op.add_column("generation_changes", sa.Column("pair_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_generation_changes_pair_id"), "generation_changes", ["pair_id"])
    op.create_foreign_key(
        op.f("fk_generation_changes_pair_id_generation_pairs"),
        "generation_changes",
        "generation_pairs",
        ["pair_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(op.f("ck_generation_changes_action"), "generation_changes", type_="check")
    op.create_check_constraint(
        op.f("ck_generation_changes_action"), "generation_changes", NEW_ACTIONS
    )


def downgrade() -> None:
    # 새 결정의 기록은 옛 제약에 맞지 않아 먼저 지운다.
    op.execute("DELETE FROM generation_changes WHERE action IN ('flip', 'answer', 'pair')")
    op.drop_constraint(op.f("ck_generation_changes_action"), "generation_changes", type_="check")
    op.create_check_constraint(
        op.f("ck_generation_changes_action"), "generation_changes", OLD_ACTIONS
    )
    op.drop_constraint(
        op.f("fk_generation_changes_pair_id_generation_pairs"),
        "generation_changes",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_generation_changes_pair_id"), table_name="generation_changes")
    op.drop_column("generation_changes", "pair_id")
    op.drop_index(op.f("ix_generation_pairs_prompt_id"), table_name="generation_pairs")
    op.drop_index(op.f("ix_generation_pairs_dataset_id"), table_name="generation_pairs")
    op.drop_table("generation_pairs")
    op.drop_column("generation_judgments", "unsafe")
    op.drop_column("generation_prompts", "tools")
    # 주 응답이 아닌 응답(선호 쌍의 다른 응답 · 비교 만들기)은 옛 모양(지시마다 응답 하나)에 없다.
    op.execute("DELETE FROM generation_responses WHERE NOT is_main")
    op.drop_index(
        "uq_generation_responses_main",
        table_name="generation_responses",
        postgresql_where=sa.text("is_main"),
    )
    op.drop_column("generation_responses", "score")
    op.drop_column("generation_responses", "is_main")
    op.drop_column("generation_responses", "tool_calls")
    op.drop_index(
        op.f("ix_generation_contaminations_eval_prompt_id"),
        table_name="generation_contaminations",
    )
    op.drop_index(
        op.f("ix_generation_contaminations_dataset_id"), table_name="generation_contaminations"
    )
    op.drop_table("generation_contaminations")
    op.drop_table("generation_dataset_settings")
