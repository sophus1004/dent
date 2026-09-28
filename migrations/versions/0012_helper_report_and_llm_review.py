"""도우미 보고 · AI로 고치기.

- system_helper_events: 사건 종류에 보고(report)를 더한다.
- retrieval_queries: 평가 질의 확인 상태에 LLM 확인(llm_ok)을 더한다(사람 확인과 구분해 보인다).
- retrieval_helper_changes: 도우미가 바꾼 칸에 평가 질의 확인(review)을 더한다(AI로 고치기의 되돌리기).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_KIND_CHECK = "ck_system_helper_events_kind"
EVENT_KINDS_BEFORE = (
    "kind IN ('say', 'jev', 'llm', 'change', 'result', 'hold', 'permission', 'notice')"
)
EVENT_KINDS_AFTER = (
    "kind IN ('say', 'jev', 'llm', 'change', 'result', 'hold', 'permission', 'notice', 'report')"
)

FIELD_CHECK = "ck_retrieval_helper_changes_field"
FIELDS_BEFORE = "field IN ('exclude', 'trash', 'text', 'split', 'grade', 'skip_generation', 'chunked', 'created')"
FIELDS_AFTER = (
    "field IN ('exclude', 'trash', 'text', 'split', 'grade', 'skip_generation', 'chunked', "
    "'created', 'review')"
)

REVIEW_CHECK = "ck_retrieval_queries_review"
REVIEWS_BEFORE = "review IN ('pending', 'ok', 'edited', 'dropped')"
REVIEWS_AFTER = "review IN ('pending', 'ok', 'edited', 'llm_ok', 'dropped')"


def upgrade() -> None:
    """세 칸의 허락 값을 늘린다."""
    op.drop_constraint(op.f(EVENT_KIND_CHECK), "system_helper_events", type_="check")
    op.create_check_constraint(op.f(EVENT_KIND_CHECK), "system_helper_events", EVENT_KINDS_AFTER)
    op.drop_constraint(op.f(REVIEW_CHECK), "retrieval_queries", type_="check")
    op.create_check_constraint(op.f(REVIEW_CHECK), "retrieval_queries", REVIEWS_AFTER)
    op.drop_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", type_="check")
    op.create_check_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", FIELDS_AFTER)


def downgrade() -> None:
    """보고 사건은 알림으로, LLM 확인은 맞음으로 바꾸고, 확인 바꾼 기록은 지우고 허락 값을 되돌린다."""
    op.execute("UPDATE system_helper_events SET kind = 'notice' WHERE kind = 'report'")
    op.drop_constraint(op.f(EVENT_KIND_CHECK), "system_helper_events", type_="check")
    op.create_check_constraint(op.f(EVENT_KIND_CHECK), "system_helper_events", EVENT_KINDS_BEFORE)
    op.execute("UPDATE retrieval_queries SET review = 'ok' WHERE review = 'llm_ok'")
    op.drop_constraint(op.f(REVIEW_CHECK), "retrieval_queries", type_="check")
    op.create_check_constraint(op.f(REVIEW_CHECK), "retrieval_queries", REVIEWS_BEFORE)
    op.execute("DELETE FROM retrieval_helper_changes WHERE field = 'review'")
    op.drop_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", type_="check")
    op.create_check_constraint(op.f(FIELD_CHECK), "retrieval_helper_changes", FIELDS_BEFORE)
