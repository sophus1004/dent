"""분류 모듈의 뜻 분석 결과: 지도 줄의 요약(checks)과 근접 중복 · 오라벨 의심 표.

- classification_maps.checks: 뜻 분석 한 번의 요약(기준값, 근접 중복 · 오라벨 의심 수, 라벨마다 의미 쏠림).
  뜻 분석은 지도 만들기를 넓힌 것이라, 지도 한 줄이 뜻 분석 한 번이다. 앞서 만든 지도는 빈 요약으로 남는다.
- classification_near_duplicates: 근접 중복 한 쌍(문장 해시 두 개와 유사도). 뒤쪽 해시로도 찾도록 인덱스를 건다.
- classification_label_suspects: 오라벨 의심 한 건(지금 · 추천 라벨, 분류기 확률, Jev 판정, 사람의 판단).
두 표 모두 지도를 지우면 함께 지워진다(CASCADE). 새 표만 만들고 칸 하나를 더하므로 있던 데이터는 고치지 않는다.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-25 17:36:24.890649
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """뜻 분석 요약 칸과 근접 중복 · 오라벨 의심 표를 만든다."""
    op.create_table(
        "classification_label_suspects",
        sa.Column("map_id", sa.BigInteger(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("label_id", sa.BigInteger(), nullable=False),
        sa.Column("suggested_label_id", sa.BigInteger(), nullable=False),
        sa.Column("label_probability", sa.REAL(), nullable=False),
        sa.Column("suggested_probability", sa.REAL(), nullable=False),
        sa.Column("jev_label_id", sa.BigInteger(), nullable=True),
        sa.Column("jev_label_probability", sa.REAL(), nullable=True),
        sa.Column("jev_confidence", sa.REAL(), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "decision IN ('accepted', 'kept')",
            name=op.f("ck_classification_label_suspects_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["jev_label_id"],
            ["classification_labels.id"],
            name=op.f("fk_classification_label_suspects_jev_label_id_classification_labels"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["classification_labels.id"],
            name=op.f("fk_classification_label_suspects_label_id_classification_labels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["map_id"],
            ["classification_maps.id"],
            name=op.f("fk_classification_label_suspects_map_id_classification_maps"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suggested_label_id"],
            ["classification_labels.id"],
            name=op.f("fk_classification_label_suspects_suggested_label_id_classification_labels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "map_id", "text_hash", name=op.f("pk_classification_label_suspects")
        ),
    )
    op.create_table(
        "classification_near_duplicates",
        sa.Column("map_id", sa.BigInteger(), nullable=False),
        sa.Column("text_hash_a", sa.String(length=64), nullable=False),
        sa.Column("text_hash_b", sa.String(length=64), nullable=False),
        sa.Column("similarity", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(
            ["map_id"],
            ["classification_maps.id"],
            name=op.f("fk_classification_near_duplicates_map_id_classification_maps"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "map_id", "text_hash_a", "text_hash_b", name=op.f("pk_classification_near_duplicates")
        ),
    )
    op.create_index(
        "ix_classification_near_duplicates_map_id_text_hash_b",
        "classification_near_duplicates",
        ["map_id", "text_hash_b"],
        unique=False,
    )
    op.add_column(
        "classification_maps",
        sa.Column(
            "checks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """더한 칸과 표를 뺀다."""
    op.drop_column("classification_maps", "checks")
    op.drop_index(
        "ix_classification_near_duplicates_map_id_text_hash_b",
        table_name="classification_near_duplicates",
    )
    op.drop_table("classification_near_duplicates")
    op.drop_table("classification_label_suspects")
