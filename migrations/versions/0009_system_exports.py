"""내보낸 파일 기록(system_exports)을 만든다.

- system_exports: 내보내기 한 번(모듈 · 데이터셋 · 형식 · 선택 · 상태 · 파일 이름 · 크기 · 결과 · 작업).
  파일은 storage/exports/{번호}/에 두고 보관 기간이 지나면 정리 작업이 지운다(기록은 남기고 deleted_at).
새 표만 만들므로 있던 데이터는 고치지 않는다.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-26 11:12:52
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """내보낸 파일 표를 만든다."""
    op.create_table(
        "system_exports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("file_name", sa.String(length=300), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name=op.f("ck_system_exports_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_system_exports_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["system_jobs.id"],
            name=op.f("fk_system_exports_job_id_system_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_exports")),
    )
    op.create_index(
        op.f("ix_system_exports_dataset_id"), "system_exports", ["dataset_id"], unique=False
    )


def downgrade() -> None:
    """내보낸 파일 표를 지운다."""
    op.drop_index(op.f("ix_system_exports_dataset_id"), table_name="system_exports")
    op.drop_table("system_exports")
