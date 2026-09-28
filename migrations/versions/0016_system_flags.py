"""한 번만 하는 일의 표시: 처음 켤 때 내장 예시 넣기처럼 설치한 DB에서 한 번만 하는 일을 했다는 표시.

- system_flags: 표시 이름(key) · 한 시각(done_at). 줄이 있으면 다시 하지 않는다.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27 21:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_flags",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "done_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_flags")),
    )


def downgrade() -> None:
    op.drop_table("system_flags")
