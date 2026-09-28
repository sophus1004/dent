"""외부 모델 연결에 LLM 공급자와 API 키 칸을 더한다(system_connections.provider · api_key).

- provider: LLM 공급자(openai · vllm · anthropic · google · xai). 공급자마다 부르는 방식이 달라 따로 적는다.
  LLM이 아닌 연결은 null이다. 고정된 값이라 CHECK 제약을 건다(null은 통과).
- api_key: 서버 API 키. 연결 값은 화면에서 저장하고 이 테이블에만 둔다(.env에 두지 않는다).
  API 응답과 로그에는 앞뒤 몇 글자만 보인다.
두 칸 모두 비워도 되는 새 칸이라 있던 연결은 그대로 쓴다.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-25 21:48:35.212591
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """공급자 · 키 칸과 공급자 CHECK 제약을 더한다."""
    op.add_column("system_connections", sa.Column("provider", sa.Text(), nullable=True))
    op.add_column("system_connections", sa.Column("api_key", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_system_connections_provider"),
        "system_connections",
        "provider IN ('openai', 'vllm', 'anthropic', 'google', 'xai')",
    )


def downgrade() -> None:
    """공급자 · 키 칸을 지운다(저장한 키도 함께 지워진다)."""
    op.drop_constraint(op.f("ck_system_connections_provider"), "system_connections", type_="check")
    op.drop_column("system_connections", "api_key")
    op.drop_column("system_connections", "provider")
