"""분할을 없앤다: train · valid · test로 나누지 않고 한 덩어리로 다룬다.

- classification_records · retrieval_queries · retrieval_documents: 분할(split) 칸과 제약을 지운다.
  같은 문장 · 질의를 찾는 인덱스에서도 분할을 뺀다. 원본에서 분할이 달랐던 같은 문장은 이제 중복이다.
- retrieval_queries: 평가 질의 확인(review) 칸을 지운다(평가 쪽이 없어 확인할 것이 없다).
  확인 전이라 학습에서 빠져 있던 합성 질의는 다시 학습에 쓴다. 확인에서 '뺌'으로 둔 질의는 이미 휴지통에 있다.
- retrieval_helper_changes: 분할 · 평가 확인을 되돌리던 기록을 지우고 칸 제약에서 뺀다.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-27 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SPLIT_MAX_LENGTH = 8
ENUM_MAX_LENGTH = 16
SPLIT_VALUES = "IN ('train', 'valid', 'test')"
REVIEW_VALUES = "review IN ('pending', 'ok', 'edited', 'llm_ok', 'dropped')"

FIELD_CHECK = "ck_retrieval_helper_changes_field"
FIELDS_BEFORE = (
    "field IN ('exclude', 'trash', 'text', 'split', 'grade', 'skip_generation', 'chunked', "
    "'created', 'review')"
)
FIELDS_AFTER = (
    "field IN ('exclude', 'trash', 'text', 'grade', 'skip_generation', 'chunked', 'created')"
)

RECORDS_INCLUDE = ["trashed_at", "exclude_reason"]


def upgrade() -> None:
    op.drop_index(
        "ix_classification_records_same_text",
        table_name="classification_records",
        postgresql_include=RECORDS_INCLUDE,
    )
    op.drop_constraint(
        op.f("ck_classification_records_split"), "classification_records", type_="check"
    )
    op.drop_column("classification_records", "split")
    op.create_index(
        "ix_classification_records_same_text",
        "classification_records",
        ["dataset_id", "text_hash", "label_id"],
        unique=False,
        postgresql_include=RECORDS_INCLUDE,
    )

    op.drop_index("ix_retrieval_queries_same_text", table_name="retrieval_queries")
    op.drop_constraint(op.f("ck_retrieval_queries_split"), "retrieval_queries", type_="check")
    op.drop_constraint(op.f("ck_retrieval_queries_review"), "retrieval_queries", type_="check")
    op.drop_column("retrieval_queries", "split")
    op.drop_column("retrieval_queries", "review")
    op.create_index(
        "ix_retrieval_queries_same_text",
        "retrieval_queries",
        ["dataset_id", "text_hash"],
        unique=False,
    )

    op.drop_constraint(op.f("ck_retrieval_documents_split"), "retrieval_documents", type_="check")
    op.drop_column("retrieval_documents", "split")

    op.execute("DELETE FROM retrieval_helper_changes WHERE field IN ('split', 'review')")
    op.drop_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", type_="check")
    op.create_check_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", FIELDS_AFTER)


def downgrade() -> None:
    # 지운 분할 · 확인 값은 되살릴 수 없다. 칸만 되살리고 분할은 모두 train으로 둔다.
    op.drop_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", type_="check")
    op.create_check_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", FIELDS_BEFORE)

    op.add_column(
        "retrieval_documents", sa.Column("split", sa.String(SPLIT_MAX_LENGTH), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_retrieval_documents_split"), "retrieval_documents", f"split {SPLIT_VALUES}"
    )

    op.drop_index("ix_retrieval_queries_same_text", table_name="retrieval_queries")
    op.add_column(
        "retrieval_queries", sa.Column("review", sa.String(ENUM_MAX_LENGTH), nullable=True)
    )
    op.add_column(
        "retrieval_queries",
        sa.Column("split", sa.String(SPLIT_MAX_LENGTH), server_default="train", nullable=False),
    )
    op.alter_column("retrieval_queries", "split", server_default=None)
    op.create_check_constraint(
        op.f("ck_retrieval_queries_split"), "retrieval_queries", f"split {SPLIT_VALUES}"
    )
    op.create_check_constraint(
        op.f("ck_retrieval_queries_review"), "retrieval_queries", REVIEW_VALUES
    )
    op.create_index(
        "ix_retrieval_queries_same_text",
        "retrieval_queries",
        ["dataset_id", "text_hash", "split"],
        unique=False,
    )

    op.drop_index(
        "ix_classification_records_same_text",
        table_name="classification_records",
        postgresql_include=RECORDS_INCLUDE,
    )
    op.add_column(
        "classification_records",
        sa.Column("split", sa.String(SPLIT_MAX_LENGTH), server_default="train", nullable=False),
    )
    op.alter_column("classification_records", "split", server_default=None)
    op.create_check_constraint(
        op.f("ck_classification_records_split"),
        "classification_records",
        f"split {SPLIT_VALUES}",
    )
    op.create_index(
        "ix_classification_records_same_text",
        "classification_records",
        ["dataset_id", "text_hash", "label_id", "split"],
        unique=False,
        postgresql_include=RECORDS_INCLUDE,
    )
