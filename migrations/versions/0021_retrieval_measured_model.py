"""검색 설정: 문서 · 청크의 토큰 수를 잰 토크나이저.

- retrieval_dataset_settings.measured_model: 토큰 수를 잰 토크나이저의 모델 이름(비면 글자 수 어림).
  토크나이저가 생기거나 바뀌면 다시 잰다.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-13 21:31:03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retrieval_dataset_settings",
        sa.Column("measured_model", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("retrieval_dataset_settings", "measured_model")
