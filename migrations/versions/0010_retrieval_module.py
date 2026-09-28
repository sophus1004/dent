"""검색(retrieval) 모듈의 표를 만든다.

- retrieval_queries · retrieval_documents · retrieval_judgments: 질의 · 문서(코퍼스) · (질의, 문서) 판정(등급 0~3 · 출처)
- retrieval_dataset_settings: 들어온 모양(입구) · 학습할 모델 · 길이 · 오답 수 · 오답 찾기 기준 · 첫 기준 점수
- retrieval_analyses · retrieval_map_points · retrieval_rankings · retrieval_near_duplicates · retrieval_suggestions:
  뜻 분석(지도 · 기준 검색 순위 · 근접 중복 · 제안). 데이터셋마다 만드는 중인 분석은 하나뿐이다(부분 UNIQUE).
- retrieval_helper_changes: 도우미가 바꾼 것의 전 · 후 (카드 · 실행 단위로 되돌린다)
새 표만 만들므로 있던 데이터는 고치지 않는다.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-26 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """검색 모듈의 표를 만든다."""
    op.create_table(
        "retrieval_dataset_settings",
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("shape", sa.String(length=16), nullable=False),
        sa.Column("target_model", sa.String(length=200), nullable=True),
        sa.Column("query_max_tokens", sa.Integer(), server_default="64", nullable=False),
        sa.Column("doc_max_tokens", sa.Integer(), server_default="512", nullable=False),
        sa.Column("negatives", sa.Integer(), server_default="7", nullable=False),
        sa.Column("mine_rank_from", sa.Integer(), server_default="10", nullable=False),
        sa.Column("mine_rank_to", sa.Integer(), server_default="100", nullable=False),
        sa.Column("mine_margin", sa.REAL(), server_default="0.95", nullable=False),
        sa.Column(
            "baseline",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "shape IN ('documents', 'pair', 'mrc', 'triplet', 'scored')",
            name=op.f("ck_retrieval_dataset_settings_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_retrieval_dataset_settings_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dataset_id", name=op.f("pk_retrieval_dataset_settings")),
    )
    op.create_table(
        "retrieval_analyses",
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
        sa.Column("query_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("document_count", sa.BigInteger(), server_default="0", nullable=False),
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
            name=op.f("ck_retrieval_analyses_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_retrieval_analyses_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["system_jobs.id"],
            name=op.f("fk_retrieval_analyses_job_id_system_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["system_embedding_models.id"],
            name=op.f("fk_retrieval_analyses_model_id_system_embedding_models"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_analyses")),
    )
    op.create_index(
        op.f("ix_retrieval_analyses_dataset_id"), "retrieval_analyses", ["dataset_id"], unique=False
    )
    op.create_index(
        "uq_retrieval_analyses_building",
        "retrieval_analyses",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "retrieval_documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("import_id", sa.BigInteger(), nullable=True),
        sa.Column("doc_key", sa.String(length=200), nullable=True),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_document_id", sa.BigInteger(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("split", sa.String(length=8), nullable=True),
        sa.Column("skip_generation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            "split IN ('train', 'valid', 'test')", name=op.f("ck_retrieval_documents_split")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_retrieval_documents_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["system_imports.id"],
            name=op.f("fk_retrieval_documents_import_id_system_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_documents_source_document_id_retrieval_documents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_documents")),
    )
    op.create_index(
        "ix_retrieval_documents_dataset_id_import_id",
        "retrieval_documents",
        ["dataset_id", "import_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_documents_same_text",
        "retrieval_documents",
        ["dataset_id", "text_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_retrieval_documents_source_document_id"),
        "retrieval_documents",
        ["source_document_id"],
        unique=False,
    )
    op.create_table(
        "retrieval_near_duplicates",
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("item_a", sa.BigInteger(), nullable=False),
        sa.Column("item_b", sa.BigInteger(), nullable=False),
        sa.Column("similarity", sa.REAL(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('query', 'document')", name=op.f("ck_retrieval_near_duplicates_kind")
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["retrieval_analyses.id"],
            name=op.f("fk_retrieval_near_duplicates_analysis_id_retrieval_analyses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "analysis_id", "kind", "item_a", "item_b", name=op.f("pk_retrieval_near_duplicates")
        ),
    )
    op.create_table(
        "retrieval_queries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("import_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("split", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="original", nullable=False),
        sa.Column("source_document_id", sa.BigInteger(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("review", sa.String(length=16), nullable=True),
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
            "exclude_reason IN ('manual', 'duplicate', 'helper')",
            name=op.f("ck_retrieval_queries_exclude_reason"),
        ),
        sa.CheckConstraint(
            "review IN ('pending', 'ok', 'edited', 'dropped')",
            name=op.f("ck_retrieval_queries_review"),
        ),
        sa.CheckConstraint(
            "source IN ('original', 'synthetic', 'human')", name=op.f("ck_retrieval_queries_source")
        ),
        sa.CheckConstraint(
            "split IN ('train', 'valid', 'test')", name=op.f("ck_retrieval_queries_split")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_retrieval_queries_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["system_imports.id"],
            name=op.f("fk_retrieval_queries_import_id_system_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_queries_source_document_id_retrieval_documents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_queries")),
    )
    op.create_index(
        "ix_retrieval_queries_dataset_id_import_id",
        "retrieval_queries",
        ["dataset_id", "import_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_queries_same_text",
        "retrieval_queries",
        ["dataset_id", "text_hash", "split"],
        unique=False,
    )
    op.create_index(
        op.f("ix_retrieval_queries_source_document_id"),
        "retrieval_queries",
        ["source_document_id"],
        unique=False,
    )
    op.create_table(
        "retrieval_helper_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("target", sa.String(length=16), nullable=False),
        sa.Column("query_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("field", sa.String(length=16), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "field IN ('exclude', 'trash', 'text', 'split', 'grade', 'skip_generation', 'chunked', 'created')",
            name=op.f("ck_retrieval_helper_changes_field"),
        ),
        sa.CheckConstraint(
            "target IN ('query', 'document', 'judgment')",
            name=op.f("ck_retrieval_helper_changes_target"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_helper_changes_document_id_retrieval_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["system_helper_events.id"],
            name=op.f("fk_retrieval_helper_changes_event_id_system_helper_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["retrieval_queries.id"],
            name=op.f("fk_retrieval_helper_changes_query_id_retrieval_queries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["system_helper_runs.id"],
            name=op.f("fk_retrieval_helper_changes_run_id_system_helper_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_helper_changes")),
    )
    op.create_index(
        op.f("ix_retrieval_helper_changes_document_id"),
        "retrieval_helper_changes",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_retrieval_helper_changes_event_id"),
        "retrieval_helper_changes",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_retrieval_helper_changes_query_id"),
        "retrieval_helper_changes",
        ["query_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_retrieval_helper_changes_run_id"),
        "retrieval_helper_changes",
        ["run_id"],
        unique=False,
    )
    op.create_table(
        "retrieval_judgments",
        sa.Column("query_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("grade", sa.SmallInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="original", nullable=False),
        sa.Column("conflict", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("teacher_score", sa.REAL(), nullable=True),
        sa.Column("overlap", sa.REAL(), nullable=True),
        sa.Column("import_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('original', 'mined', 'synthetic', 'helper', 'human')",
            name=op.f("ck_retrieval_judgments_source"),
        ),
        sa.CheckConstraint("grade BETWEEN 0 AND 3", name=op.f("ck_retrieval_judgments_grade")),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_retrieval_judgments_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_judgments_document_id_retrieval_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["system_imports.id"],
            name=op.f("fk_retrieval_judgments_import_id_system_imports"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["retrieval_queries.id"],
            name=op.f("fk_retrieval_judgments_query_id_retrieval_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("query_id", "document_id", name=op.f("pk_retrieval_judgments")),
    )
    op.create_index(
        "ix_retrieval_judgments_dataset_id_grade",
        "retrieval_judgments",
        ["dataset_id", "grade"],
        unique=False,
    )
    op.create_index(
        op.f("ix_retrieval_judgments_document_id"),
        "retrieval_judgments",
        ["document_id"],
        unique=False,
    )
    op.create_table(
        "retrieval_map_points",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("query_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("x", sa.REAL(), nullable=False),
        sa.Column("y", sa.REAL(), nullable=False),
        sa.Column("cluster", sa.SmallInteger(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "kind IN ('query', 'document')", name=op.f("ck_retrieval_map_points_kind")
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["retrieval_analyses.id"],
            name=op.f("fk_retrieval_map_points_analysis_id_retrieval_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_map_points_document_id_retrieval_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["retrieval_queries.id"],
            name=op.f("fk_retrieval_map_points_query_id_retrieval_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_map_points")),
    )
    op.create_index(
        op.f("ix_retrieval_map_points_analysis_id"),
        "retrieval_map_points",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_map_points_document_id", "retrieval_map_points", ["document_id"], unique=False
    )
    op.create_index(
        "ix_retrieval_map_points_query_id", "retrieval_map_points", ["query_id"], unique=False
    )
    op.create_table(
        "retrieval_rankings",
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("query_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("similarity", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["retrieval_analyses.id"],
            name=op.f("fk_retrieval_rankings_analysis_id_retrieval_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_rankings_document_id_retrieval_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["retrieval_queries.id"],
            name=op.f("fk_retrieval_rankings_query_id_retrieval_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "analysis_id", "query_id", "document_id", name=op.f("pk_retrieval_rankings")
        ),
    )
    op.create_index(
        op.f("ix_retrieval_rankings_document_id"),
        "retrieval_rankings",
        ["document_id"],
        unique=False,
    )
    op.create_table(
        "retrieval_suggestions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("analysis_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("query_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("similarity", sa.REAL(), nullable=False),
        sa.Column("jev_probability", sa.REAL(), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "decision IN ('accepted', 'kept')", name=op.f("ck_retrieval_suggestions_decision")
        ),
        sa.CheckConstraint(
            "kind IN ('false_negative', 'missing_positive', 'suspect_positive')",
            name=op.f("ck_retrieval_suggestions_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["retrieval_analyses.id"],
            name=op.f("fk_retrieval_suggestions_analysis_id_retrieval_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_suggestions_document_id_retrieval_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["retrieval_queries.id"],
            name=op.f("fk_retrieval_suggestions_query_id_retrieval_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_suggestions")),
    )
    op.create_index(
        op.f("ix_retrieval_suggestions_analysis_id"),
        "retrieval_suggestions",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_suggestions_document_id",
        "retrieval_suggestions",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_suggestions_query_id", "retrieval_suggestions", ["query_id"], unique=False
    )


def downgrade() -> None:
    """검색 모듈의 표를 지운다."""
    op.drop_index("ix_retrieval_suggestions_query_id", table_name="retrieval_suggestions")
    op.drop_index("ix_retrieval_suggestions_document_id", table_name="retrieval_suggestions")
    op.drop_index(op.f("ix_retrieval_suggestions_analysis_id"), table_name="retrieval_suggestions")
    op.drop_table("retrieval_suggestions")
    op.drop_index(op.f("ix_retrieval_rankings_document_id"), table_name="retrieval_rankings")
    op.drop_table("retrieval_rankings")
    op.drop_index("ix_retrieval_map_points_query_id", table_name="retrieval_map_points")
    op.drop_index("ix_retrieval_map_points_document_id", table_name="retrieval_map_points")
    op.drop_index(op.f("ix_retrieval_map_points_analysis_id"), table_name="retrieval_map_points")
    op.drop_table("retrieval_map_points")
    op.drop_index(op.f("ix_retrieval_judgments_document_id"), table_name="retrieval_judgments")
    op.drop_index("ix_retrieval_judgments_dataset_id_grade", table_name="retrieval_judgments")
    op.drop_table("retrieval_judgments")
    op.drop_index(op.f("ix_retrieval_helper_changes_run_id"), table_name="retrieval_helper_changes")
    op.drop_index(
        op.f("ix_retrieval_helper_changes_query_id"), table_name="retrieval_helper_changes"
    )
    op.drop_index(
        op.f("ix_retrieval_helper_changes_event_id"), table_name="retrieval_helper_changes"
    )
    op.drop_index(
        op.f("ix_retrieval_helper_changes_document_id"), table_name="retrieval_helper_changes"
    )
    op.drop_table("retrieval_helper_changes")
    op.drop_index(op.f("ix_retrieval_queries_source_document_id"), table_name="retrieval_queries")
    op.drop_index("ix_retrieval_queries_same_text", table_name="retrieval_queries")
    op.drop_index("ix_retrieval_queries_dataset_id_import_id", table_name="retrieval_queries")
    op.drop_table("retrieval_queries")
    op.drop_table("retrieval_near_duplicates")
    op.drop_index(
        op.f("ix_retrieval_documents_source_document_id"), table_name="retrieval_documents"
    )
    op.drop_index("ix_retrieval_documents_same_text", table_name="retrieval_documents")
    op.drop_index("ix_retrieval_documents_dataset_id_import_id", table_name="retrieval_documents")
    op.drop_table("retrieval_documents")
    op.drop_index(
        "uq_retrieval_analyses_building",
        table_name="retrieval_analyses",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index(op.f("ix_retrieval_analyses_dataset_id"), table_name="retrieval_analyses")
    op.drop_table("retrieval_analyses")
    op.drop_table("retrieval_dataset_settings")
