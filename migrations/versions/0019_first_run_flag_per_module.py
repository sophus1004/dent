"""처음 실행할 때 예시 넣기 표시를 모듈마다 둔다.

표시 하나(first_run_examples)는 DB 전체에 한 번이라, 나중에 붙은 모듈의 예시가 들어가지 않았다.
이제 표시는 'first_run_examples:<모듈>'이다. 옛 표시가 있는 DB는 그때 있던 모듈(분류 · 검색)의 표시로 옮기고,
그 뒤에 붙은 모듈(생성)은 다음에 실행할 때 처음이라 예시가 들어간다.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-29 09:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 옛 표시 (DB 전체에 하나)
OLD_FLAG = "first_run_examples"

# 옛 표시를 남길 때 있던 모듈 (그 뒤 붙은 모듈은 옮기지 않아 처음으로 본다)
MODULES_AT_OLD_FLAG = ("classification", "retrieval")


def upgrade() -> None:
    for module in MODULES_AT_OLD_FLAG:
        op.execute(
            sa.text(
                "INSERT INTO system_flags (key, done_at) "
                "SELECT :new_key, done_at FROM system_flags WHERE key = :old_key "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(new_key=f"{OLD_FLAG}:{module}", old_key=OLD_FLAG)
        )
    op.execute(
        sa.text("DELETE FROM system_flags WHERE key = :old_key").bindparams(old_key=OLD_FLAG)
    )


def downgrade() -> None:
    # 모듈 표시가 하나라도 있으면 옛 표시 하나로 되돌린다(가장 이른 시각).
    op.execute(
        sa.text(
            "INSERT INTO system_flags (key, done_at) "
            "SELECT :old_key, min(done_at) FROM system_flags WHERE key LIKE :pattern "
            "HAVING count(*) > 0 ON CONFLICT (key) DO NOTHING"
        ).bindparams(old_key=OLD_FLAG, pattern=f"{OLD_FLAG}:%")
    )
    op.execute(
        sa.text("DELETE FROM system_flags WHERE key LIKE :pattern").bindparams(
            pattern=f"{OLD_FLAG}:%"
        )
    )
