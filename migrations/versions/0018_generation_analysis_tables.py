"""생성 모듈 뜻 분석 테이블과 다시 쓰기 결정.

- generation_analyses: 뜻 분석 한 번(상태 · 요약 · 모델 · 작업). 데이터셋마다 만드는 중인 것은 하나(부분 UNIQUE)
- generation_map_points: 지시마다 지도 좌표 · 과제 무리
- generation_near_duplicates: 뜻이 거의 같은 지시 쌍(작은 번호 · 큰 번호)
- generation_judgments: Jev 판정(지시 따름 · 거절이 마땅함)과 지시 ↔ 응답 유사도
- generation_changes.action에 rewrite(LLM 다시 쓰기)를 더하고, 도우미가 바꾼 것이면 실행 · 카드 번호를 적는다

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-28 23:24:40
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 결정 값의 CHECK 제약 (다시 쓰기 앞 · 뒤)
OLD_ACTIONS = "action IN ('drop', 'keep', 'mask', 'cut')"
NEW_ACTIONS = "action IN ('drop', 'keep', 'mask', 'cut', 'rewrite')"


def upgrade() -> None:
    op.create_table(
        "generation_analyses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "checks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("conversation_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("dataset_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'canceled')",
            name=op.f("ck_generation_analyses_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_generation_analyses_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["system_jobs.id"],
            name=op.f("fk_generation_analyses_job_id_system_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["system_embedding_models.id"],
            name=op.f("fk_generation_analyses_model_id_system_embedding_models"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_analyses")),
    )
    op.create_index(
        op.f("ix_generation_analyses_dataset_id"),
        "generation_analyses",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "uq_generation_analyses_building",
        "generation_analyses",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "generation_judgments",
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_id", sa.BigInteger(), nullable=False),
        sa.Column("similarity", sa.REAL(), nullable=True),
        sa.Column("follows", sa.REAL(), nullable=True),
        sa.Column("refusal_ok", sa.REAL(), nullable=True),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["generation_analyses.id"],
            name=op.f("fk_generation_judgments_analysis_id_generation_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_judgments_prompt_id_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("analysis_id", "prompt_id", name=op.f("pk_generation_judgments")),
    )
    op.create_index(
        op.f("ix_generation_judgments_prompt_id"),
        "generation_judgments",
        ["prompt_id"],
        unique=False,
    )
    op.create_table(
        "generation_map_points",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_id", sa.BigInteger(), nullable=False),
        sa.Column("x", sa.REAL(), nullable=False),
        sa.Column("y", sa.REAL(), nullable=False),
        sa.Column("cluster", sa.SmallInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["generation_analyses.id"],
            name=op.f("fk_generation_map_points_analysis_id_generation_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_map_points_prompt_id_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_map_points")),
    )
    op.create_index(
        op.f("ix_generation_map_points_analysis_id"),
        "generation_map_points",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_generation_map_points_prompt_id"),
        "generation_map_points",
        ["prompt_id"],
        unique=False,
    )
    op.create_table(
        "generation_near_duplicates",
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_a", sa.BigInteger(), nullable=False),
        sa.Column("prompt_b", sa.BigInteger(), nullable=False),
        sa.Column("similarity", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["generation_analyses.id"],
            name=op.f("fk_generation_near_duplicates_analysis_id_generation_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_a"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_near_duplicates_prompt_a_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_b"],
            ["generation_prompts.id"],
            name=op.f("fk_generation_near_duplicates_prompt_b_generation_prompts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "analysis_id", "prompt_a", "prompt_b", name=op.f("pk_generation_near_duplicates")
        ),
    )
    op.create_index(
        "ix_generation_near_duplicates_prompt_a",
        "generation_near_duplicates",
        ["prompt_a"],
        unique=False,
    )
    op.create_index(
        "ix_generation_near_duplicates_prompt_b",
        "generation_near_duplicates",
        ["prompt_b"],
        unique=False,
    )
    op.drop_constraint(op.f("ck_generation_changes_action"), "generation_changes", type_="check")
    op.create_check_constraint(
        op.f("ck_generation_changes_action"), "generation_changes", NEW_ACTIONS
    )
    op.add_column("generation_changes", sa.Column("run_id", sa.BigInteger(), nullable=True))
    op.add_column("generation_changes", sa.Column("event_id", sa.BigInteger(), nullable=True))
    op.create_index(
        op.f("ix_generation_changes_run_id"), "generation_changes", ["run_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_generation_changes_run_id_system_helper_runs"),
        "generation_changes",
        "system_helper_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_generation_changes_event_id_system_helper_events"),
        "generation_changes",
        "system_helper_events",
        ["event_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_generation_changes_event_id_system_helper_events"),
        "generation_changes",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_generation_changes_run_id_system_helper_runs"),
        "generation_changes",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_generation_changes_run_id"), table_name="generation_changes")
    op.drop_column("generation_changes", "event_id")
    op.drop_column("generation_changes", "run_id")
    op.execute("DELETE FROM generation_changes WHERE action = 'rewrite'")
    op.drop_constraint(op.f("ck_generation_changes_action"), "generation_changes", type_="check")
    op.create_check_constraint(
        op.f("ck_generation_changes_action"), "generation_changes", OLD_ACTIONS
    )
    op.drop_index("ix_generation_near_duplicates_prompt_b", table_name="generation_near_duplicates")
    op.drop_index("ix_generation_near_duplicates_prompt_a", table_name="generation_near_duplicates")
    op.drop_table("generation_near_duplicates")
    op.drop_index(op.f("ix_generation_map_points_prompt_id"), table_name="generation_map_points")
    op.drop_index(op.f("ix_generation_map_points_analysis_id"), table_name="generation_map_points")
    op.drop_table("generation_map_points")
    op.drop_index(op.f("ix_generation_judgments_prompt_id"), table_name="generation_judgments")
    op.drop_table("generation_judgments")
    op.drop_index(
        "uq_generation_analyses_building",
        table_name="generation_analyses",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index(op.f("ix_generation_analyses_dataset_id"), table_name="generation_analyses")
    op.drop_table("generation_analyses")
