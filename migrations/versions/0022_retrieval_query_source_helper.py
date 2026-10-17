"""검색 질의 출처에 helper(도우미가 LLM으로 고쳐 쓴 원본 · 사람 질의)를 더한다.

내보내는 파일의 출처 칸에서 AI가 바꾼 질의를 가를 수 있게 한다.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-13 21:52:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 질의 출처의 CHECK 제약 (앞 · 뒤)
OLD_SOURCES = "source IN ('original', 'synthetic', 'human')"
NEW_SOURCES = "source IN ('original', 'synthetic', 'human', 'helper')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_retrieval_queries_source"), "retrieval_queries", type_="check")
    op.create_check_constraint(
        op.f("ck_retrieval_queries_source"), "retrieval_queries", NEW_SOURCES
    )


def downgrade() -> None:
    # 도우미가 고쳐 쓴 질의는 사람이 고친 것으로 되돌린다(옛 제약에 helper가 없다).
    op.execute("UPDATE retrieval_queries SET source = 'human' WHERE source = 'helper'")
    op.drop_constraint(op.f("ck_retrieval_queries_source"), "retrieval_queries", type_="check")
    op.create_check_constraint(
        op.f("ck_retrieval_queries_source"), "retrieval_queries", OLD_SOURCES
    )
