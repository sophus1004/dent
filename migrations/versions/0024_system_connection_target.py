"""연결 역할에 target(학습할 모델)을 더한다.

학습할 모델이 비교 만들기의 응답을 직접 쓰게 한다(LLM과 같은 방식으로 붙는다, 선택).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-14 00:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 연결 역할의 CHECK 제약 (앞 · 뒤)
OLD_ROLES = "role IN ('embedding', 'jev', 'llm')"
NEW_ROLES = "role IN ('embedding', 'jev', 'llm', 'target')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_system_connections_role"), "system_connections", type_="check")
    op.create_check_constraint(op.f("ck_system_connections_role"), "system_connections", NEW_ROLES)


def downgrade() -> None:
    # 옛 제약에 target이 없어 학습할 모델 연결은 지운다.
    op.execute("DELETE FROM system_connections WHERE role = 'target'")
    op.drop_constraint(op.f("ck_system_connections_role"), "system_connections", type_="check")
    op.create_check_constraint(op.f("ck_system_connections_role"), "system_connections", OLD_ROLES)
