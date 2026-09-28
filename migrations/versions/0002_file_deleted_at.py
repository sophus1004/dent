"""올린 파일 목록(system_files)에 원본을 지운 시각(deleted_at)을 더한다.

원본 파일은 가져오기가 끝나면(성공·실패) 지우고, 가져오지 않은 채 하루가 지나면 정리 작업이 지운다.
목록의 한 줄(이름·크기·해시)은 어디서 가져왔는지 알려고 남기고, 지운 시각만 적는다.
있던 줄은 아직 파일이 있다고 보고 비워 둔다.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24 23:18:54.448215
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """지운 시각 칸을 더한다. 비어 있어도 되는 칸이라 있던 줄을 고치지 않는다."""
    op.add_column(
        "system_files", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """지운 시각 칸을 뺀다."""
    op.drop_column("system_files", "deleted_at")
