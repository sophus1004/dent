"""검색 전처리: 학습 글 · 반복 구간 · 문서 표시 · 오답 풀 설정.

- retrieval_documents: 머리말 칸 값(header) · 구획 경로(section) · 묶음 칸 값(group_key) · 학습 글(input_text,
  본문과 같으면 비움) · 학습 글 해시(input_hash) · 한 번 재 둔 표시(marks). 있던 문서는 학습 글 해시를 본문 해시로 채운다
  (제목 · 반복 구간을 넣은 학습 글은 '반복 구간 살피기'가 다시 만든다).
- retrieval_repeats · retrieval_document_repeats: 반복 구간(틀)과 사람의 결정, 문서마다 든 반복 구간.
- retrieval_dataset_settings: 오답 풀 · 구획 제목 붙이기 · 되찾기 거르기 · 쉬운 쌍 상한 · 오답 훑기 결과 · 살핀 시각.
  오답 찾기 끝 순위의 기본값을 100 → 200으로 바꾸고, 기본값(100) 그대로인 데이터셋도 200으로 맞춘다.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-26 20:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 오답 찾기 끝 순위의 옛 기본값 · 새 기본값
OLD_MINE_RANK_TO = 100
NEW_MINE_RANK_TO = 200


def upgrade() -> None:
    """칸과 표를 더하고, 있던 문서의 학습 글 해시를 채운다."""
    op.create_table(
        "retrieval_repeats",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "samples",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("head_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tail_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("suggestion", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('remove', 'keep')", name=op.f("ck_retrieval_repeats_decision")
        ),
        sa.CheckConstraint("kind IN ('sentence', 'meta')", name=op.f("ck_retrieval_repeats_kind")),
        sa.CheckConstraint(
            "suggestion IN ('remove', 'keep')", name=op.f("ck_retrieval_repeats_suggestion")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_retrieval_repeats_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_repeats")),
        sa.UniqueConstraint("dataset_id", "key", name=op.f("uq_retrieval_repeats_dataset_id_key")),
    )
    op.create_index(
        op.f("ix_retrieval_repeats_dataset_id"), "retrieval_repeats", ["dataset_id"], unique=False
    )
    op.create_table(
        "retrieval_document_repeats",
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("repeat_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["retrieval_documents.id"],
            name=op.f("fk_retrieval_document_repeats_document_id_retrieval_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repeat_id"],
            ["retrieval_repeats.id"],
            name=op.f("fk_retrieval_document_repeats_repeat_id_retrieval_repeats"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "document_id", "repeat_id", name=op.f("pk_retrieval_document_repeats")
        ),
    )
    op.create_index(
        op.f("ix_retrieval_document_repeats_repeat_id"),
        "retrieval_document_repeats",
        ["repeat_id"],
        unique=False,
    )

    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("negative_pool", sa.Integer(), server_default="20", nullable=False),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("section_header", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("round_trip", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("easy_pair_cap", sa.REAL(), server_default="0.3", nullable=False),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column(
            "negative_scan",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "retrieval_dataset_settings", "mine_rank_to", server_default=str(NEW_MINE_RANK_TO)
    )
    op.execute(
        f"UPDATE retrieval_dataset_settings SET mine_rank_to = {NEW_MINE_RANK_TO} "
        f"WHERE mine_rank_to = {OLD_MINE_RANK_TO}"
    )

    op.add_column(
        "retrieval_documents", sa.Column("header", sa.Text(), server_default="", nullable=False)
    )
    op.add_column(
        "retrieval_documents", sa.Column("section", sa.Text(), server_default="", nullable=False)
    )
    op.add_column(
        "retrieval_documents", sa.Column("group_key", sa.String(length=200), nullable=True)
    )
    op.add_column("retrieval_documents", sa.Column("input_text", sa.Text(), nullable=True))
    # 있던 줄은 학습 글 = 본문으로 두고 채운 뒤 NOT NULL로 바꾼다.
    op.add_column(
        "retrieval_documents", sa.Column("input_hash", sa.String(length=64), nullable=True)
    )
    op.execute("UPDATE retrieval_documents SET input_hash = text_hash")
    op.alter_column("retrieval_documents", "input_hash", nullable=False)
    op.add_column(
        "retrieval_documents",
        sa.Column(
            "marks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_retrieval_documents_same_input",
        "retrieval_documents",
        ["dataset_id", "input_hash"],
        unique=False,
    )


def downgrade() -> None:
    """더한 칸과 표를 지운다."""
    op.drop_index("ix_retrieval_documents_same_input", table_name="retrieval_documents")
    for column in ("marks", "input_hash", "input_text", "group_key", "section", "header"):
        op.drop_column("retrieval_documents", column)
    op.alter_column(
        "retrieval_dataset_settings", "mine_rank_to", server_default=str(OLD_MINE_RANK_TO)
    )
    for column in (
        "scanned_at",
        "negative_scan",
        "easy_pair_cap",
        "round_trip",
        "section_header",
        "negative_pool",
    ):
        op.drop_column("retrieval_dataset_settings", column)
    op.drop_index(
        op.f("ix_retrieval_document_repeats_repeat_id"), table_name="retrieval_document_repeats"
    )
    op.drop_table("retrieval_document_repeats")
    op.drop_index(op.f("ix_retrieval_repeats_dataset_id"), table_name="retrieval_repeats")
    op.drop_table("retrieval_repeats")
