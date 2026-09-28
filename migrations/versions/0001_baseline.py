"""첫 테이블: 시스템 층(system_)과 분류 모듈(classification_).

시스템: 작업 대기열(system_jobs), 임베딩 모델(system_embedding_models), 올린 파일(system_files),
데이터셋 목록(system_datasets), 가져오기 기록(system_imports).
분류: 라벨(classification_labels), 문장(classification_records).

같은 문장 찾기(중복·충돌·분할 간 중복)는 데이터셋의 문장을 해시로 묶어 센다. 그래서 문장 표의 해시 인덱스에
라벨·분할과 상태 칸(휴지통·뺀 이유)까지 담아(covering index) DB가 표를 읽지 않고 인덱스만 훑게 한다.

Revision ID: 0001
Revises:
Create Date: 2026-09-24 22:16:13.815783
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """테이블을 만든다. vector 확장은 관리자가 미리 켜 두므로, 여기서는 없을 때만 켠다."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 시스템 층 (system_)
    op.create_table(
        "system_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("progress_done", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("progress_total", sa.BigInteger(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'canceled')",
            name=op.f("ck_system_jobs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_jobs")),
    )
    op.create_index(
        "ix_system_jobs_queued",
        "system_jobs",
        ["id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_table(
        "system_embedding_models",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_embedding_models")),
        sa.UniqueConstraint("name", name=op.f("uq_system_embedding_models_name")),
    )
    op.create_table(
        "system_files",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_files")),
    )
    op.create_index(op.f("ix_system_files_sha256"), "system_files", ["sha256"], unique=False)
    op.create_table(
        "system_datasets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_datasets")),
        sa.UniqueConstraint("module", "name", name=op.f("uq_system_datasets_module_name")),
    )
    op.create_table(
        "system_imports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_name", sa.String(length=500), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("rows_total", sa.BigInteger(), nullable=True),
        sa.Column("rows_added", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("rows_skipped", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "skipped_lines",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source IN ('file', 'huggingface')", name=op.f("ck_system_imports_source")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name=op.f("ck_system_imports_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_system_imports_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["system_jobs.id"],
            name=op.f("fk_system_imports_job_id_system_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_imports")),
    )
    op.create_index(
        op.f("ix_system_imports_dataset_id"), "system_imports", ["dataset_id"], unique=False
    )

    # 분류 모듈 (classification_). 시스템 테이블(데이터셋·가져오기)을 참조한다.
    op.create_table(
        "classification_labels",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_classification_labels_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classification_labels")),
        sa.UniqueConstraint(
            "dataset_id", "name", name=op.f("uq_classification_labels_dataset_id_name")
        ),
    )
    op.create_index(
        op.f("ix_classification_labels_dataset_id"),
        "classification_labels",
        ["dataset_id"],
        unique=False,
    )
    op.create_table(
        "classification_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("import_id", sa.BigInteger(), nullable=True),
        sa.Column("label_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("split", sa.String(length=8), nullable=False),
        sa.Column("exclude_reason", sa.String(length=16), nullable=True),
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
            "exclude_reason IN ('manual', 'duplicate')",
            name=op.f("ck_classification_records_exclude_reason"),
        ),
        sa.CheckConstraint(
            "split IN ('train', 'valid', 'test')", name=op.f("ck_classification_records_split")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_classification_records_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["system_imports.id"],
            name=op.f("fk_classification_records_import_id_system_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["classification_labels.id"],
            name=op.f("fk_classification_records_label_id_classification_labels"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classification_records")),
    )
    op.create_index(
        "ix_classification_records_dataset_id_import_id",
        "classification_records",
        ["dataset_id", "import_id"],
        unique=False,
    )
    op.create_index(
        "ix_classification_records_dataset_id_label_id",
        "classification_records",
        ["dataset_id", "label_id"],
        unique=False,
    )
    op.create_index(
        "ix_classification_records_same_text",
        "classification_records",
        ["dataset_id", "text_hash", "label_id", "split"],
        unique=False,
        postgresql_include=["trashed_at", "exclude_reason"],
    )


def downgrade() -> None:
    """테이블을 지운다. 참조하는 쪽(모듈)부터 지운다. vector 확장은 관리자가 켠 것이라 지우지 않는다."""
    op.drop_index(
        "ix_classification_records_same_text",
        table_name="classification_records",
        postgresql_include=["trashed_at", "exclude_reason"],
    )
    op.drop_index(
        "ix_classification_records_dataset_id_label_id", table_name="classification_records"
    )
    op.drop_index(
        "ix_classification_records_dataset_id_import_id", table_name="classification_records"
    )
    op.drop_table("classification_records")
    op.drop_index(op.f("ix_classification_labels_dataset_id"), table_name="classification_labels")
    op.drop_table("classification_labels")
    op.drop_index(op.f("ix_system_imports_dataset_id"), table_name="system_imports")
    op.drop_table("system_imports")
    op.drop_table("system_datasets")
    op.drop_index(op.f("ix_system_files_sha256"), table_name="system_files")
    op.drop_table("system_files")
    op.drop_table("system_embedding_models")
    op.drop_index(
        "ix_system_jobs_queued",
        table_name="system_jobs",
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.drop_table("system_jobs")
