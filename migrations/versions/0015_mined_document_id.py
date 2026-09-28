"""오답 다시 찾기: 모든 질의의 오답을 마지막으로 찾을 때 코퍼스의 가장 큰 문서 번호를 검색 설정에 둔다.

- retrieval_dataset_settings.mined_document_id: 그 뒤 번호의 코퍼스 문서가 생기면(데이터 추가 · 나누기)
  문서 풀이 늘어 오답을 다시 찾는다. 이미 찾은 데이터셋은 비워 둔다(다음 처음 찾기 · 다시 찾기 때 적는다).

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27 16:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retrieval_dataset_settings", sa.Column("mined_document_id", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("retrieval_dataset_settings", "mined_document_id")
