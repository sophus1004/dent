"""생성 오염 대조: 뜻으로 찾은 오염(임베딩 후보 + LLM 판정)을 함께 적는다.

- generation_contaminations.method: 찾은 방법(ngram · paraphrase · similar). 있던 줄은 ngram이다.
- generation_contaminations.similarity: 임베딩 코사인 유사도(뜻으로 찾았을 때).
- generation_contaminations.coverage: 뜻으로 찾았으면 비어 있다(8-gram 겹친 몫이 없다).
- generation_dataset_settings.contamination_by: 마지막 대조에 쓴 방법(ngram · embedding · llm).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-13 23:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 찾은 방법의 CHECK 제약
METHODS = "method IN ('ngram', 'paraphrase', 'similar')"


def upgrade() -> None:
    op.add_column(
        "generation_contaminations",
        sa.Column("method", sa.String(length=16), server_default="ngram", nullable=False),
    )
    op.add_column("generation_contaminations", sa.Column("similarity", sa.REAL(), nullable=True))
    op.alter_column("generation_contaminations", "coverage", existing_type=sa.REAL(), nullable=True)
    op.create_check_constraint(
        op.f("ck_generation_contaminations_method"), "generation_contaminations", METHODS
    )
    op.add_column(
        "generation_dataset_settings",
        sa.Column("contamination_by", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generation_dataset_settings", "contamination_by")
    # 뜻으로 찾은 줄은 8-gram 겹친 몫이 없어 옛 모양에 담을 수 없다.
    op.execute("DELETE FROM generation_contaminations WHERE coverage IS NULL")
    op.drop_constraint(
        op.f("ck_generation_contaminations_method"), "generation_contaminations", type_="check"
    )
    op.alter_column(
        "generation_contaminations", "coverage", existing_type=sa.REAL(), nullable=False
    )
    op.drop_column("generation_contaminations", "similarity")
    op.drop_column("generation_contaminations", "method")
