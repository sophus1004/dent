"""작업 실행기 여럿이 데이터셋끼리 작업을 함께 돌린다.

- system_jobs.worker: 작업을 꺼낸 작업 실행기 번호. 끊긴 작업 되살리기는 그 번호의 잠금이 풀린 작업만 한다.
- system_jobs.dedupe_key: 대기 중인 같은 일은 하나만(부분 UNIQUE).
- 데이터셋마다 도는 작업은 하나(부분 UNIQUE), 데이터셋마다 가장 앞선 대기 작업을 찾는 부분 인덱스.
- system_settings: 실행 중에 화면에서 바꾸는 시스템 설정 (작업 실행기 수).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-17 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 작업 열쇠의 최대 길이 (models.DEDUPE_KEY_MAX_LENGTH)
DEDUPE_KEY_MAX_LENGTH = 100


def upgrade() -> None:
    op.add_column("system_jobs", sa.Column("worker", sa.SmallInteger(), nullable=True))
    op.add_column(
        "system_jobs", sa.Column("dedupe_key", sa.String(DEDUPE_KEY_MAX_LENGTH), nullable=True)
    )
    op.create_index(
        "ix_system_jobs_queued_dataset",
        "system_jobs",
        ["dataset_id", "id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "uq_system_jobs_running_dataset",
        "system_jobs",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_system_jobs_queued_dedupe",
        "system_jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_settings")),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_index("uq_system_jobs_queued_dedupe", table_name="system_jobs")
    op.drop_index("uq_system_jobs_running_dataset", table_name="system_jobs")
    op.drop_index("ix_system_jobs_queued_dataset", table_name="system_jobs")
    op.drop_column("system_jobs", "dedupe_key")
    op.drop_column("system_jobs", "worker")
