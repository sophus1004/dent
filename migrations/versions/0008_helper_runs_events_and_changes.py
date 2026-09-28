"""LLM 도우미의 실행 · 사건 기록과 분류 모듈의 변경 기록을 만든다.

- system_helper_runs: 도우미 실행 한 번(단계 · 쓴 토큰 · Jev에 물은 수 · 바꾼 수 · 허락을 묻는 계획).
  데이터셋마다 도는 실행은 하나뿐이다(부분 UNIQUE).
- system_helper_events: 실행의 사건(에이전트의 말 · Jev · LLM · 바꿈 · 결과 · 보류 · 허락 · 알림). 쌓기만 한다.
- classification_helper_changes: 도우미가 문장 하나를 바꾼 전 · 후. 카드 · 실행 단위로 되돌린다.
- classification_records.exclude_reason: 도우미가 뺀 것(helper)을 더한다.
새 표만 만들고 CHECK 제약 하나를 넓히므로 있던 데이터는 고치지 않는다.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25 23:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 뺀 이유의 CHECK 제약 (도우미가 뺀 helper를 더하기 전 · 후)
EXCLUDE_REASON_CHECK = "ck_classification_records_exclude_reason"
EXCLUDE_REASONS_BEFORE = "exclude_reason IN ('manual', 'duplicate')"
EXCLUDE_REASONS_AFTER = "exclude_reason IN ('manual', 'duplicate', 'helper')"


def upgrade() -> None:
    """도우미의 세 표를 만들고 뺀 이유에 helper를 더한다."""
    op.create_table(
        "system_helper_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("step_now", sa.Integer(), server_default="0", nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("llm_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("jev_calls", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("changed", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("held", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("stop_requested", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("permission", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'asking', 'done', 'stopped', 'failed')",
            name=op.f("ck_system_helper_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_system_helper_runs_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["system_jobs.id"],
            name=op.f("fk_system_helper_runs_job_id_system_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_helper_runs")),
    )
    op.create_index(
        op.f("ix_system_helper_runs_dataset_id"), "system_helper_runs", ["dataset_id"], unique=False
    )
    op.create_index(
        "uq_system_helper_runs_running",
        "system_helper_runs",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "system_helper_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "payload",
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
        sa.CheckConstraint(
            "kind IN ('say', 'jev', 'llm', 'change', 'result', 'hold', 'permission', 'notice')",
            name=op.f("ck_system_helper_events_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["system_helper_runs.id"],
            name=op.f("fk_system_helper_events_run_id_system_helper_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_helper_events")),
    )
    op.create_index(
        op.f("ix_system_helper_events_run_id"), "system_helper_events", ["run_id"], unique=False
    )
    op.create_table(
        "classification_helper_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("field", sa.String(length=16), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "field IN ('label', 'exclude', 'created')",
            name=op.f("ck_classification_helper_changes_field"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["system_helper_events.id"],
            name=op.f("fk_classification_helper_changes_event_id_system_helper_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["classification_records.id"],
            name=op.f("fk_classification_helper_changes_record_id_classification_records"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["system_helper_runs.id"],
            name=op.f("fk_classification_helper_changes_run_id_system_helper_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classification_helper_changes")),
    )
    op.create_index(
        op.f("ix_classification_helper_changes_event_id"),
        "classification_helper_changes",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_classification_helper_changes_record_id"),
        "classification_helper_changes",
        ["record_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_classification_helper_changes_run_id"),
        "classification_helper_changes",
        ["run_id"],
        unique=False,
    )
    op.drop_constraint(op.f(EXCLUDE_REASON_CHECK), "classification_records", type_="check")
    op.create_check_constraint(
        op.f(EXCLUDE_REASON_CHECK), "classification_records", EXCLUDE_REASONS_AFTER
    )


def downgrade() -> None:
    """도우미의 세 표를 지우고 뺀 이유를 되돌린다. 도우미가 뺀 문장은 직접 뺀 것(manual)으로 바꾼다."""
    op.execute(
        "UPDATE classification_records SET exclude_reason = 'manual' WHERE exclude_reason = 'helper'"
    )
    op.drop_constraint(op.f(EXCLUDE_REASON_CHECK), "classification_records", type_="check")
    op.create_check_constraint(
        op.f(EXCLUDE_REASON_CHECK), "classification_records", EXCLUDE_REASONS_BEFORE
    )
    op.drop_index(
        op.f("ix_classification_helper_changes_run_id"), table_name="classification_helper_changes"
    )
    op.drop_index(
        op.f("ix_classification_helper_changes_record_id"),
        table_name="classification_helper_changes",
    )
    op.drop_index(
        op.f("ix_classification_helper_changes_event_id"),
        table_name="classification_helper_changes",
    )
    op.drop_table("classification_helper_changes")
    op.drop_index(op.f("ix_system_helper_events_run_id"), table_name="system_helper_events")
    op.drop_table("system_helper_events")
    op.drop_index(
        "uq_system_helper_runs_running",
        table_name="system_helper_runs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index(op.f("ix_system_helper_runs_dataset_id"), table_name="system_helper_runs")
    op.drop_table("system_helper_runs")
