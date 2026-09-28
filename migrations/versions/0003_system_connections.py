"""외부 모델 서버 연결 테이블(system_connections)을 만든다.

임베딩 · Jev · LLM 서버의 주소와 모델 이름을 화면의 연결 설정에서 저장한다.
이전에는 .env의 EMBEDDING_*에서 읽었지만, 이제 연결 값은 이 테이블에만 둔다(다시 켜지 않아도 바로 쓴다).
역할마다 연결은 하나라서 역할 이름이 번호다. 줄이 없으면 그 역할은 미연결이다.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25 02:22:01.717867
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """연결 테이블을 만든다. 새 테이블이라 있던 데이터를 고치지 않는다."""
    op.create_table(
        "system_connections",
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('embedding', 'jev', 'llm')", name=op.f("ck_system_connections_role")
        ),
        sa.PrimaryKeyConstraint("role", name=op.f("pk_system_connections")),
    )


def downgrade() -> None:
    """연결 테이블을 지운다."""
    op.drop_table("system_connections")
