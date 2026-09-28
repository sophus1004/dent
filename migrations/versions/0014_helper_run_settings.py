"""도우미 실행 설정: 도우미를 시작하기 전에 고르는 값을 데이터셋 설정에 둔다.

- retrieval_dataset_settings: 문서 나누기 오버랩(토큰, 0 = 없음) · 청크마다 질의 수(2) · 질문형 몫(%, 60).
- classification_dataset_settings(새 표): 새 문장(라벨 균형)의 목표 배율(1.5). 줄이 없으면 기본값으로 본다.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-27 11:05:06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "classification_dataset_settings",
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("balance_target", sa.Double(), server_default="1.5", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_classification_dataset_settings_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dataset_id", name=op.f("pk_classification_dataset_settings")),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("chunk_overlap", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("queries_per_chunk", sa.Integer(), server_default="2", nullable=False),
    )
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("question_share", sa.Integer(), server_default="60", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("retrieval_dataset_settings", "question_share")
    op.drop_column("retrieval_dataset_settings", "queries_per_chunk")
    op.drop_column("retrieval_dataset_settings", "chunk_overlap")
    op.drop_table("classification_dataset_settings")
