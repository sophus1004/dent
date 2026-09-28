"""임베딩 캐시(system_embeddings)와 분류 모듈의 의미 지도(classification_maps · classification_map_points).

- system_embeddings: (모델, 문장 해시)마다 벡터 하나. 같은 문장은 모델마다 한 번만 계산하고
  모든 데이터셋 · 모듈이 같이 쓴다. 벡터는 반 정밀도(halfvec)로 두어 저장 공간을 절반으로 줄인다.
  모델마다 차원이 달라서 차원을 정하지 않은 halfvec 칸이다. 가까운 벡터 찾기 인덱스(ANN)는 아직 없다.
- classification_maps: 데이터셋마다 의미 지도 만들기 한 번. 데이터셋마다 만드는 중인 지도는 하나뿐이라
  (queued · running) 부분 UNIQUE 인덱스로 막는다.
- classification_map_points: 문장 한 건의 좌표. 문장을 지울 때 DB가 점을 찾도록 record_id에 인덱스를 건다.

새 테이블만 만들므로 있던 데이터는 고치지 않는다. halfvec은 pgvector 0.7 이상에 있다(0001이 vector 확장을 켠다).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25 02:52:06.263226
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """임베딩 캐시와 의미 지도 테이블을 만든다."""
    # 시스템 층: 임베딩 캐시
    op.create_table(
        "system_embeddings",
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("vector", HALFVEC(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["system_embedding_models.id"],
            name=op.f("fk_system_embeddings_model_id_system_embedding_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("model_id", "text_hash", name=op.f("pk_system_embeddings")),
    )

    # 분류 모듈: 의미 지도
    op.create_table(
        "classification_maps",
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
        sa.Column("text_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("point_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("record_count", sa.BigInteger(), server_default="0", nullable=False),
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
            name=op.f("ck_classification_maps_status"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["system_datasets.id"],
            name=op.f("fk_classification_maps_dataset_id_system_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["system_jobs.id"],
            name=op.f("fk_classification_maps_job_id_system_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["system_embedding_models.id"],
            name=op.f("fk_classification_maps_model_id_system_embedding_models"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classification_maps")),
    )
    op.create_index(
        op.f("ix_classification_maps_dataset_id"), "classification_maps", ["dataset_id"]
    )
    op.create_index(
        "uq_classification_maps_building",
        "classification_maps",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "classification_map_points",
        sa.Column("map_id", sa.BigInteger(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("x", sa.REAL(), nullable=False),
        sa.Column("y", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(
            ["map_id"],
            ["classification_maps.id"],
            name=op.f("fk_classification_map_points_map_id_classification_maps"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["classification_records.id"],
            name=op.f("fk_classification_map_points_record_id_classification_records"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("map_id", "record_id", name=op.f("pk_classification_map_points")),
    )
    op.create_index(
        op.f("ix_classification_map_points_record_id"), "classification_map_points", ["record_id"]
    )


def downgrade() -> None:
    """의미 지도와 임베딩 캐시 테이블을 지운다(인덱스는 테이블과 함께 지워진다)."""
    op.drop_table("classification_map_points")
    op.drop_table("classification_maps")
    op.drop_table("system_embeddings")
