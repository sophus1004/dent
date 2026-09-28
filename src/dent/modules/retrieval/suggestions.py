"""제안(거짓 오답 · 빠진 정답 · 정답 의심) 목록 · 수락 · 유지 · Jev 확인 모두 수락.

제안은 다 만든 뜻 분석의 것이다. 수락 전에는 데이터가 바뀌지 않는다.
수락하면 판정을 바꾼다: 거짓 오답 · 빠진 정답 → 정답(등급 1), 정답 의심 → 판정 떼기(모름). 출처는 사람이다.
유지는 판정을 그대로 두고, 다음 뜻 분석에서도 같은 (종류, 질의, 문서)를 다시 묻지 않는다.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    POSITIVE_MIN_GRADE,
    Document,
    Judgment,
    JudgmentSource,
    Query,
    Suggestion,
    SuggestionDecision,
    SuggestionKind,
)
from dent.modules.retrieval.service import DOCUMENT_ACTIVE, QUERY_ACTIVE
from dent.modules.retrieval.text_rules import lexical_overlap
from dent.system import datasets as datasets_service
from dent.system.exceptions import ConflictError, NotFoundError

NO_ANALYSIS_MESSAGE = "뜻 분석이 없습니다. 먼저 뜻 분석을 만들어 주세요."
NOT_FOUND_MESSAGE = "제안을 찾을 수 없습니다."
ALREADY_DECIDED_MESSAGE = "이미 판단한 제안입니다."


@dataclass(frozen=True)
class SuggestionRow:
    """목록의 한 줄: 제안과 질의 글 · 문서 · 지금 판정."""

    suggestion: Suggestion
    query_text: str
    document: Document
    grade: int | None


@dataclass(frozen=True)
class SuggestionPage:
    """제안 한 쪽과 종류별 대기 수 · Jev 확인 대기 수."""

    rows: list[SuggestionRow]
    total: int
    pending_counts: dict[str, int]
    confirmed_pending: int


async def list_suggestions(
    db: AsyncSession,
    *,
    dataset_id: int,
    kind: SuggestionKind | None,
    decided: bool,
    limit: int,
    offset: int,
) -> SuggestionPage:
    """제안 한 쪽(Jev 확인 → 가까운 것부터). 데이터셋이 없거나 뜻 분석이 없으면 NotFoundError."""
    analysis_id = await _analysis_id(db, dataset_id=dataset_id)
    base = [
        Suggestion.analysis_id == analysis_id,
        QUERY_ACTIVE,
        DOCUMENT_ACTIVE,
        Suggestion.decision.is_not(None) if decided else Suggestion.decision.is_(None),
    ]
    if kind is not None:
        base.append(Suggestion.kind == kind.value)
    joined = (
        select(Suggestion, Query.text, Document, Judgment.grade)
        .join(Query, Query.id == Suggestion.query_id)
        .join(Document, Document.id == Suggestion.document_id)
        .outerjoin(
            Judgment,
            (Judgment.query_id == Suggestion.query_id)
            & (Judgment.document_id == Suggestion.document_id),
        )
        .where(*base)
    )
    total = await db.scalar(select(func.count()).select_from(joined.subquery()))
    rows = (
        await db.execute(
            joined.order_by(
                Suggestion.is_confirmed.desc(),
                Suggestion.similarity.desc(),
                # 동률이면 번호 순으로 한 번 더 정렬해 쪽마다 순서가 흔들리지 않게 한다.
                Suggestion.id,
            )
            .limit(limit)
            .offset(offset)
        )
    ).tuples()
    pending = [Suggestion.analysis_id == analysis_id, Suggestion.decision.is_(None)]
    pending_query = (
        select(Suggestion.kind, func.count(), func.count().filter(Suggestion.is_confirmed))
        .join(Query, Query.id == Suggestion.query_id)
        .join(Document, Document.id == Suggestion.document_id)
        .where(*pending, QUERY_ACTIVE, DOCUMENT_ACTIVE)
        .group_by(Suggestion.kind)
    )
    pending_counts = {item.value: 0 for item in SuggestionKind}
    confirmed = 0
    for kind_name, count, confirmed_count in await db.execute(pending_query):
        pending_counts[kind_name] = count
        confirmed += confirmed_count
    return SuggestionPage(
        rows=[
            SuggestionRow(suggestion=suggestion, query_text=text, document=document, grade=grade)
            for suggestion, text, document, grade in rows
        ],
        total=int(total or 0),
        pending_counts=pending_counts,
        confirmed_pending=confirmed,
    )


async def accept_suggestion(db: AsyncSession, *, suggestion_id: int) -> int:
    """제안을 수락해 판정을 바꾼다. 바꾼 판정 수(1)를 돌려준다. 없으면 NotFoundError, 이미 판단했으면 ConflictError."""
    suggestion = await _open_suggestion(db, suggestion_id=suggestion_id)
    dataset_id = await _dataset_of(db, suggestion=suggestion)
    await _apply(db, suggestion=suggestion, dataset_id=dataset_id)
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return 1


async def keep_suggestion(db: AsyncSession, *, suggestion_id: int) -> int:
    """제안을 유지한다(판정 그대로). 없으면 NotFoundError, 이미 판단했으면 ConflictError."""
    suggestion = await _open_suggestion(db, suggestion_id=suggestion_id)
    suggestion.decision = SuggestionDecision.KEPT.value
    suggestion.decided_at = datetime.now(UTC)
    await db.commit()
    return 0


async def accept_confirmed(db: AsyncSession, *, dataset_id: int) -> int:
    """Jev가 확인한 대기 제안을 모두 수락한다. 수락한 수를 돌려준다. 뜻 분석이 없으면 NotFoundError."""
    analysis_id = await _analysis_id(db, dataset_id=dataset_id)
    confirmed = list(
        await db.scalars(
            select(Suggestion)
            .join(Query, Query.id == Suggestion.query_id)
            .join(Document, Document.id == Suggestion.document_id)
            .where(
                Suggestion.analysis_id == analysis_id,
                Suggestion.decision.is_(None),
                Suggestion.is_confirmed.is_(True),
                QUERY_ACTIVE,
                DOCUMENT_ACTIVE,
            )
            .order_by(Suggestion.id)
        )
    )
    for suggestion in confirmed:
        await _apply(db, suggestion=suggestion, dataset_id=dataset_id)
    if confirmed:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return len(confirmed)


# ---------- 안에서만 쓰는 함수 ----------


async def _analysis_id(db: AsyncSession, *, dataset_id: int) -> int:
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
    if analysis is None:
        raise NotFoundError(NO_ANALYSIS_MESSAGE)
    return analysis.id


async def _open_suggestion(db: AsyncSession, *, suggestion_id: int) -> Suggestion:
    suggestion = await db.get(Suggestion, suggestion_id, populate_existing=True)
    if suggestion is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    if suggestion.decision is not None:
        raise ConflictError(ALREADY_DECIDED_MESSAGE)
    return suggestion


async def _dataset_of(db: AsyncSession, *, suggestion: Suggestion) -> int:
    query = await db.get(Query, suggestion.query_id)
    if query is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    return query.dataset_id


async def _apply(db: AsyncSession, *, suggestion: Suggestion, dataset_id: int) -> None:
    """제안대로 판정을 바꾸고 수락으로 닫는다. 커밋은 부른 쪽이 한다."""
    if suggestion.kind == SuggestionKind.SUSPECT_POSITIVE.value:
        await db.execute(
            delete(Judgment).where(
                Judgment.query_id == suggestion.query_id,
                Judgment.document_id == suggestion.document_id,
            )
        )
    else:
        query = await db.get(Query, suggestion.query_id)
        document = await db.get(Document, suggestion.document_id)
        overlap = lexical_overlap(query.text, document.text) if query and document else None
        statement = pg_insert(Judgment).values(
            query_id=suggestion.query_id,
            document_id=suggestion.document_id,
            dataset_id=dataset_id,
            grade=POSITIVE_MIN_GRADE,
            source=JudgmentSource.HUMAN.value,
            teacher_score=suggestion.jev_probability,
            overlap=overlap,
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["query_id", "document_id"],
                set_={
                    "grade": POSITIVE_MIN_GRADE,
                    "source": JudgmentSource.HUMAN.value,
                    "conflict": False,
                },
            )
        )
    suggestion.decision = SuggestionDecision.ACCEPTED.value
    suggestion.decided_at = datetime.now(UTC)
