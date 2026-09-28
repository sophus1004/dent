"""질의 · 문서 · 판정 다루기: 목록(거르기 · 쪽), 패널(판정 · 순위 · 쓰는 질의), 고치기(글 · 분할 · 학습 제외 · 휴지통), 판정 두기.

목록은 한 번에 한 쪽만 읽는다(수백만 건이어도). 문제 거르기는 problems.py의 조건을 써서 진단의 수와 맞는다.
한 쪽의 표시(marks)는 그 쪽 질의 · 문서에만 문제 조건을 한 번씩 물어 붙인다.
사람이 판정을 바꾸면 출처를 human으로 적고, 데이터셋의 수정 시각을 새로 적는다(진단 · 분석 이후 변경).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import edits, problems, repeats
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    Document,
    ExcludeReason,
    Judgment,
    JudgmentSource,
    Query,
    QuerySource,
    Ranking,
    Suggestion,
)
from dent.modules.retrieval.schemas import (
    DocumentBriefRead,
    DocumentBulkUpdate,
    DocumentUpdate,
    QueryBulkUpdate,
    QueryUpdate,
)
from dent.modules.retrieval.service import (
    DOCUMENT_ACTIVE,
    DOCUMENT_TRASHED,
    IS_NEGATIVE,
    IS_POSITIVE,
    QUERY_ACTIVE,
    QUERY_EXCLUDED,
    QUERY_INCLUDED,
    QUERY_TRASHED,
)
from dent.modules.retrieval.text_rules import lexical_overlap
from dent.system import datasets as datasets_service
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.text import make_text_hash

# 한 쪽의 최대 크기
MAX_PAGE_SIZE = 200

# 목록 한 줄에 보일 정답 문서 수 · 본문 앞부분 글자 수
POSITIVES_SHOWN = 3
SNIPPET_LENGTH = 160

# 질의 패널의 기준 검색 상위 수
RANKED_TOP = 10

# 문서 패널에 보일 쓰는 질의 · 가까운 질의 · 만든 질의 수
USES_SHOWN = 50
NEARBY_SHOWN = 5

QUERY_NOT_FOUND_MESSAGE = "질의를 찾을 수 없습니다."
DOCUMENT_NOT_FOUND_MESSAGE = "문서를 찾을 수 없습니다."
STALE_MESSAGE = "그 사이 다른 곳에서 고쳤습니다. 다시 불러온 뒤 고치세요."
OTHER_DATASET_MESSAGE = "같은 데이터셋의 질의 · 문서가 아닙니다."


@dataclass(frozen=True)
class QueryRow:
    """질의 목록 한 줄의 재료."""

    query: Query
    positive_count: int
    negative_count: int
    positives: list[DocumentBriefRead]
    marks: list[str]


@dataclass(frozen=True)
class DocumentRow:
    """문서 목록 한 줄의 재료."""

    document: Document
    positive_count: int
    negative_count: int
    synthetic_count: int
    marks: list[str]


def brief(document: Document) -> DocumentBriefRead:
    """문서를 한 줄로 (제목 · 본문 앞부분 · 토큰)."""
    snippet = " ".join(document.text.split())[:SNIPPET_LENGTH]
    return DocumentBriefRead(
        id=document.id, title=document.title, snippet=snippet, token_count=document.token_count
    )


# ---------- 질의 목록 ----------


async def list_queries(
    db: AsyncSession,
    *,
    dataset_id: int,
    q: str | None,
    source: QuerySource | None,
    status: str,
    problem: str | None,
    limit: int,
    offset: int,
) -> tuple[list[QueryRow], int]:
    """질의 한 쪽과 조건에 맞는 전체 수. 데이터셋이 없으면 NotFoundError."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
    analysis_id = analysis.id if analysis else None
    conditions = [Query.dataset_id == dataset_id, _query_status(status)]
    if q:
        conditions.append(Query.text.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if source is not None:
        conditions.append(Query.source == source.value)
    if problem:
        conditions.append(
            problems.query_problem(problem, settings=settings, analysis_id=analysis_id)
        )
    total = int(await db.scalar(select(func.count()).select_from(Query).where(*conditions)) or 0)
    page = list(
        (
            await db.scalars(
                select(Query)
                .where(*conditions)
                .order_by(Query.id)
                .limit(min(limit, MAX_PAGE_SIZE))
                .offset(offset)
            )
        ).all()
    )
    return await query_rows(db, page, settings=settings, analysis_id=analysis_id), total


async def query_rows(
    db: AsyncSession, queries: list[Query], *, settings: object, analysis_id: int | None
) -> list[QueryRow]:
    """질의들에 정답 · 오답 수, 정답 문서 몇 개, 표시(문제 이름)를 붙인다."""
    ids = [query.id for query in queries]
    if not ids:
        return []
    counts = {
        row[0]: row[1:]
        for row in (
            await db.execute(
                select(
                    Judgment.query_id,
                    func.count().filter(IS_POSITIVE),
                    func.count().filter(IS_NEGATIVE),
                )
                .join(Document, Document.id == Judgment.document_id)
                .where(Judgment.query_id.in_(ids), DOCUMENT_ACTIVE)
                .group_by(Judgment.query_id)
            )
        ).all()
    }
    positives: dict[int, list[DocumentBriefRead]] = {}
    for query_id, document in (
        await db.execute(
            select(Judgment.query_id, Document)
            .join(Document, Document.id == Judgment.document_id)
            .where(Judgment.query_id.in_(ids), IS_POSITIVE, DOCUMENT_ACTIVE)
            .order_by(Judgment.query_id, Judgment.grade.desc(), Document.id)
        )
    ).tuples():
        shown = positives.setdefault(query_id, [])
        if len(shown) < POSITIVES_SHOWN:
            shown.append(brief(document))
    marks: dict[int, list[str]] = {query_id: [] for query_id in ids}
    for problem in problems.QUERY_PROBLEMS:
        condition = problems.query_problem(problem, settings=settings, analysis_id=analysis_id)  # type: ignore[arg-type]
        for query_id in (
            await db.scalars(select(Query.id).where(Query.id.in_(ids), condition))
        ).all():
            marks[query_id].append(problem)
    return [
        QueryRow(
            query=query,
            positive_count=int(counts.get(query.id, (0, 0))[0]),
            negative_count=int(counts.get(query.id, (0, 0))[1]),
            positives=positives.get(query.id, []),
            marks=marks[query.id],
        )
        for query in queries
    ]


async def get_query(db: AsyncSession, *, query_id: int) -> Query:
    """질의 하나. 없으면 NotFoundError."""
    query = await db.get(Query, query_id, populate_existing=True)
    if query is None:
        raise NotFoundError(QUERY_NOT_FOUND_MESSAGE)
    return query


@dataclass(frozen=True)
class RankedItem:
    """질의 패널의 한 줄 재료."""

    document: Document
    grade: int | None
    source: str | None
    rank: int | None
    similarity: float | None
    flag: str | None
    jev_probability: float | None


@dataclass(frozen=True)
class QueryDetail:
    """질의 패널의 재료."""

    row: QueryRow
    first_positive_rank: int | None
    ranked: list[RankedItem]
    outside: list[RankedItem]
    has_ranking: bool


async def get_query_detail(db: AsyncSession, *, query_id: int) -> QueryDetail:
    """질의 패널: 판정과 기준 검색 순위(상위 10 + 10위 밖 판정). 없으면 NotFoundError."""
    query = await get_query(db, query_id=query_id)
    settings = await retrieval_service.find_settings(db, dataset_id=query.dataset_id)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=query.dataset_id)
    analysis_id = analysis.id if analysis else None
    [row] = await query_rows(db, [query], settings=settings, analysis_id=analysis_id)
    judged = {
        judgment.document_id: judgment
        for judgment in (
            await db.scalars(select(Judgment).where(Judgment.query_id == query_id))
        ).all()
    }
    flags: dict[int, tuple[str, float | None]] = {}
    if analysis_id is not None:
        for suggestion in (
            await db.scalars(
                select(Suggestion).where(
                    Suggestion.analysis_id == analysis_id,
                    Suggestion.query_id == query_id,
                    Suggestion.decision.is_(None),
                )
            )
        ).all():
            flags[suggestion.document_id] = (suggestion.kind, suggestion.jev_probability)

    def item(document: Document, rank: int | None, similarity: float | None) -> RankedItem:
        judgment = judged.get(document.id)
        flag = flags.get(document.id)
        return RankedItem(
            document=document,
            grade=judgment.grade if judgment else None,
            source=judgment.source if judgment else None,
            rank=rank,
            similarity=similarity,
            flag=flag[0] if flag else None,
            jev_probability=flag[1] if flag else None,
        )

    if analysis_id is None:
        documents = {
            document.id: document
            for document in (
                await db.scalars(
                    select(Document).where(Document.id.in_(list(judged)), DOCUMENT_ACTIVE)
                )
            ).all()
        }
        ordered = sorted(
            documents.values(), key=lambda document: (-(judged[document.id].grade), document.id)
        )
        return QueryDetail(
            row=row,
            first_positive_rank=None,
            ranked=[item(d, None, None) for d in ordered],
            outside=[],
            has_ranking=False,
        )

    rankings = (
        (
            await db.execute(
                select(Ranking.rank, Ranking.similarity, Document)
                .join(Document, Document.id == Ranking.document_id)
                .where(
                    Ranking.analysis_id == analysis_id,
                    Ranking.query_id == query_id,
                    DOCUMENT_ACTIVE,
                )
                .order_by(Ranking.rank)
            )
        )
        .tuples()
        .all()
    )
    top = [
        item(document, rank, similarity)
        for rank, similarity, document in rankings
        if rank <= RANKED_TOP
    ]
    outside = [
        item(document, rank, similarity)
        for rank, similarity, document in rankings
        if rank > RANKED_TOP and document.id in judged
    ]
    positive_ranks = [
        ranked.rank
        for ranked in [*top, *outside]
        if ranked.grade is not None and ranked.grade >= 1 and ranked.rank
    ]
    return QueryDetail(
        row=row,
        first_positive_rank=min(positive_ranks) if positive_ranks else None,
        ranked=top,
        outside=outside,
        has_ranking=True,
    )


async def update_query(db: AsyncSession, *, query_id: int, data: QueryUpdate) -> Query:
    """질의 하나를 고친다. 없으면 NotFoundError, 그 사이 고쳤으면 ConflictError."""
    query = await get_query(db, query_id=query_id)
    if query.row_version != data.row_version:
        raise ConflictError(STALE_MESSAGE)
    now = datetime.now(UTC)
    if data.text is not None and data.text != query.text:
        query.text = data.text
        query.text_hash = make_text_hash(data.text)
        query.source = (
            QuerySource.HUMAN.value if query.source == QuerySource.ORIGINAL.value else query.source
        )
        await _refresh_overlap(db, query=query)
    if data.excluded is not None:
        query.exclude_reason = ExcludeReason.MANUAL.value if data.excluded else None
    if data.trashed is not None:
        query.trashed_at = now if data.trashed else None
    query.row_version += 1
    await datasets_service.touch_dataset(db, dataset_id=query.dataset_id)
    await db.commit()
    return await get_query(db, query_id=query_id)


async def bulk_update_queries(db: AsyncSession, *, dataset_id: int, data: QueryBulkUpdate) -> int:
    """여러 질의에 한 번에 한다. 실제로 바뀐 수를 돌려준다."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    base = and_(Query.dataset_id == dataset_id, Query.id.in_(data.query_ids))
    now = datetime.now(UTC)
    if data.action == "exclude":
        statement = (
            update(Query)
            .where(base, QUERY_ACTIVE, Query.exclude_reason.is_(None))
            .values(exclude_reason=ExcludeReason.MANUAL.value)
        )
    elif data.action == "include":
        statement = (
            update(Query).where(base, Query.exclude_reason.is_not(None)).values(exclude_reason=None)
        )
    elif data.action == "trash":
        statement = update(Query).where(base, QUERY_ACTIVE).values(trashed_at=now)
    else:
        # restore
        statement = update(Query).where(base, QUERY_TRASHED).values(trashed_at=None)
    result = await db.execute(
        statement.values(row_version=Query.row_version + 1).execution_options(
            synchronize_session=False
        )
    )
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ---------- 문서 목록 ----------


async def list_documents(
    db: AsyncSession,
    *,
    dataset_id: int,
    q: str | None,
    use: str | None,
    status: str,
    problem: str | None,
    limit: int,
    offset: int,
) -> tuple[list[DocumentRow], int]:
    """문서 한 쪽과 전체 수. 데이터셋이 없으면 NotFoundError."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
    analysis_id = analysis.id if analysis else None
    conditions = [Document.dataset_id == dataset_id, _document_status(status)]
    if q:
        pattern = f"%{_escape_like(q)}%"
        conditions.append(
            or_(
                Document.text.ilike(pattern, escape="\\"),
                Document.title.ilike(pattern, escape="\\"),
            )
        )
    if use:
        conditions.append(problems.document_use(use))
    if problem:
        conditions.append(
            problems.document_problem(problem, settings=settings, analysis_id=analysis_id)
        )
    total = int(await db.scalar(select(func.count()).select_from(Document).where(*conditions)) or 0)
    page = list(
        (
            await db.scalars(
                select(Document)
                .where(*conditions)
                .order_by(Document.id)
                .limit(min(limit, MAX_PAGE_SIZE))
                .offset(offset)
            )
        ).all()
    )
    return await document_rows(db, page, settings=settings, analysis_id=analysis_id), total


async def document_rows(
    db: AsyncSession,
    documents: list[Document],
    *,
    settings: object,
    analysis_id: int | None,
) -> list[DocumentRow]:
    """문서들에 정답 · 오답으로 쓰인 수, 만든 질의 수, 표시를 붙인다."""
    ids = [document.id for document in documents]
    if not ids:
        return []
    counts = {
        row[0]: row[1:]
        for row in (
            await db.execute(
                select(
                    Judgment.document_id,
                    func.count().filter(IS_POSITIVE),
                    func.count().filter(IS_NEGATIVE),
                )
                .join(Query, Query.id == Judgment.query_id)
                .where(Judgment.document_id.in_(ids), QUERY_ACTIVE)
                .group_by(Judgment.document_id)
            )
        ).all()
    }
    synthetic = dict(
        (
            await db.execute(
                select(Query.source_document_id, func.count())
                .where(Query.source_document_id.in_(ids), QUERY_ACTIVE)
                .group_by(Query.source_document_id)
            )
        )
        .tuples()
        .all()
    )
    marks: dict[int, list[str]] = {document_id: [] for document_id in ids}
    for problem in problems.DOCUMENT_PROBLEMS:
        condition = problems.document_problem(
            problem,
            settings=settings,  # type: ignore[arg-type]
            analysis_id=analysis_id,
        )
        for document_id in (
            await db.scalars(select(Document.id).where(Document.id.in_(ids), condition))
        ).all():
            marks[document_id].append(problem)
    return [
        DocumentRow(
            document=document,
            positive_count=int(counts.get(document.id, (0, 0))[0]),
            negative_count=int(counts.get(document.id, (0, 0))[1]),
            synthetic_count=int(synthetic.get(document.id, 0)),
            marks=marks[document.id],
        )
        for document in documents
    ]


async def get_document(db: AsyncSession, *, document_id: int) -> Document:
    """문서 하나. 없으면 NotFoundError."""
    document = await db.get(Document, document_id, populate_existing=True)
    if document is None:
        raise NotFoundError(DOCUMENT_NOT_FOUND_MESSAGE)
    return document


@dataclass(frozen=True)
class JudgedQuery:
    """문서 패널의 한 줄 재료."""

    query: Query
    grade: int | None
    rank: int | None
    similarity: float | None
    jev_probability: float | None


@dataclass(frozen=True)
class DocumentDetail:
    """문서 패널의 재료."""

    row: DocumentRow
    uses: list[JudgedQuery]
    nearby: list[JudgedQuery]
    synthetic: list[JudgedQuery]
    siblings: list[Document]


async def get_document_detail(db: AsyncSession, *, document_id: int) -> DocumentDetail:
    """문서 패널: 쓰는 질의 · 가까운 질의(판정 없음, 분석이 있으면) · 만든 질의 · 같은 원문의 청크."""
    document = await get_document(db, document_id=document_id)
    settings = await retrieval_service.find_settings(db, dataset_id=document.dataset_id)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=document.dataset_id)
    analysis_id = analysis.id if analysis else None
    [row] = await document_rows(db, [document], settings=settings, analysis_id=analysis_id)
    ranks: dict[int, tuple[int, float]] = {}
    if analysis_id is not None:
        ranks = {
            query_id: (rank, similarity)
            for query_id, rank, similarity in (
                await db.execute(
                    select(Ranking.query_id, Ranking.rank, Ranking.similarity).where(
                        Ranking.analysis_id == analysis_id, Ranking.document_id == document_id
                    )
                )
            ).tuples()
        }
    flags: dict[int, float | None] = {}
    if analysis_id is not None:
        flags = dict(
            (
                await db.execute(
                    select(Suggestion.query_id, Suggestion.jev_probability).where(
                        Suggestion.analysis_id == analysis_id,
                        Suggestion.document_id == document_id,
                        Suggestion.decision.is_(None),
                    )
                )
            )
            .tuples()
            .all()
        )
    uses = [
        JudgedQuery(
            query=query,
            grade=grade,
            rank=ranks.get(query.id, (None, None))[0],
            similarity=ranks.get(query.id, (None, None))[1],
            jev_probability=flags.get(query.id),
        )
        for query, grade in (
            await db.execute(
                select(Query, Judgment.grade)
                .join(Judgment, Judgment.query_id == Query.id)
                .where(Judgment.document_id == document_id, QUERY_ACTIVE)
                .order_by(Judgment.grade.desc(), Query.id)
                .limit(USES_SHOWN)
            )
        ).tuples()
    ]
    used_ids = {use.query.id for use in uses}
    nearby_ids = [
        query_id
        for query_id, _ in sorted(ranks.items(), key=lambda pair: pair[1][0])
        if query_id not in used_ids
    ][:NEARBY_SHOWN]
    nearby_queries = {
        query.id: query
        for query in (
            await db.scalars(select(Query).where(Query.id.in_(nearby_ids), QUERY_ACTIVE))
        ).all()
    }
    nearby = [
        JudgedQuery(
            query=nearby_queries[query_id],
            grade=None,
            rank=ranks[query_id][0],
            similarity=ranks[query_id][1],
            jev_probability=flags.get(query_id),
        )
        for query_id in nearby_ids
        if query_id in nearby_queries
    ]
    synthetic = [
        JudgedQuery(
            query=query,
            grade=None,
            rank=ranks.get(query.id, (None, None))[0],
            similarity=None,
            jev_probability=None,
        )
        for query in (
            await db.scalars(
                select(Query)
                .where(Query.source_document_id == document_id, QUERY_ACTIVE)
                .order_by(Query.id)
                .limit(USES_SHOWN)
            )
        ).all()
    ]
    siblings: list[Document] = []
    if document.source_document_id is not None:
        siblings = list(
            (
                await db.scalars(
                    select(Document)
                    .where(
                        Document.source_document_id == document.source_document_id,
                        Document.id != document.id,
                        DOCUMENT_ACTIVE,
                    )
                    .order_by(Document.chunk_index)
                )
            ).all()
        )
    return DocumentDetail(row=row, uses=uses, nearby=nearby, synthetic=synthetic, siblings=siblings)


async def update_document(db: AsyncSession, *, document_id: int, data: DocumentUpdate) -> Document:
    """문서 하나를 고친다. 없으면 NotFoundError, 그 사이 고쳤으면 ConflictError."""
    document = await get_document(db, document_id=document_id)
    if document.row_version != data.row_version:
        raise ConflictError(STALE_MESSAGE)
    is_text_changed = (data.title is not None and data.title != document.title) or (
        data.text is not None and data.text != document.text
    )
    if data.title is not None:
        document.title = data.title
    if data.text is not None:
        document.text = data.text
    if is_text_changed:
        # 학습 글 · 표시 · 든 반복 구간을 다시 만든다(임베딩 · 진단이 바뀐 글을 쓰게).
        await repeats.refresh_documents(db, dataset_id=document.dataset_id, documents=[document])
    if data.skip_generation is not None:
        document.skip_generation = data.skip_generation
    if data.trashed is not None:
        document.trashed_at = datetime.now(UTC) if data.trashed else None
    document.row_version += 1
    await datasets_service.touch_dataset(db, dataset_id=document.dataset_id)
    await db.commit()
    return await get_document(db, document_id=document_id)


async def bulk_update_documents(
    db: AsyncSession, *, dataset_id: int, data: DocumentBulkUpdate
) -> int:
    """여러 문서에 한 번에 한다. 실제로 바뀐 수를 돌려준다."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    base = and_(Document.dataset_id == dataset_id, Document.id.in_(data.document_ids))
    now = datetime.now(UTC)
    if data.action == "trash":
        statement = (
            update(Document).where(base, Document.trashed_at.is_(None)).values(trashed_at=now)
        )
    elif data.action == "restore":
        statement = (
            update(Document).where(base, Document.trashed_at.is_not(None)).values(trashed_at=None)
        )
    elif data.action == "skip_generation":
        statement = (
            update(Document)
            .where(base, Document.skip_generation.is_(False))
            .values(skip_generation=True)
        )
    else:
        # allow_generation
        statement = (
            update(Document)
            .where(base, Document.skip_generation.is_(True))
            .values(skip_generation=False)
        )
    result = await db.execute(
        statement.values(row_version=Document.row_version + 1).execution_options(
            synchronize_session=False
        )
    )
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ---------- 판정 ----------


async def set_judgment(
    db: AsyncSession, *, query_id: int, document_id: int, grade: int
) -> Judgment:
    """(질의, 문서) 판정을 둔다(사람). 같은 데이터셋이 아니면 InvalidInputError, 없으면 NotFoundError.

    이 쌍의 대기 중인 제안은 사람이 정한 것으로 보고 수락으로 닫는다.
    """
    query = await get_query(db, query_id=query_id)
    document = await get_document(db, document_id=document_id)
    if query.dataset_id != document.dataset_id:
        raise InvalidInputError(OTHER_DATASET_MESSAGE)
    statement = pg_insert(Judgment).values(
        query_id=query_id,
        document_id=document_id,
        dataset_id=query.dataset_id,
        grade=grade,
        source=JudgmentSource.HUMAN.value,
        overlap=lexical_overlap(query.text, document.text),
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["query_id", "document_id"],
            set_={"grade": grade, "source": JudgmentSource.HUMAN.value, "conflict": False},
        )
    )
    await _close_suggestions(db, query_id=query_id, document_id=document_id)
    await datasets_service.touch_dataset(db, dataset_id=query.dataset_id)
    await db.commit()
    judgment = await db.get(Judgment, (query_id, document_id), populate_existing=True)
    assert judgment is not None
    return judgment


async def delete_judgment(db: AsyncSession, *, query_id: int, document_id: int) -> None:
    """(질의, 문서) 판정을 뗀다(모름으로). 판정이 없으면 NotFoundError."""
    query = await get_query(db, query_id=query_id)
    result = await db.execute(
        delete(Judgment).where(Judgment.query_id == query_id, Judgment.document_id == document_id)
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFoundError("판정을 찾을 수 없습니다.")
    await _close_suggestions(db, query_id=query_id, document_id=document_id)
    await datasets_service.touch_dataset(db, dataset_id=query.dataset_id)
    await db.commit()


async def split_document(db: AsyncSession, *, document_id: int) -> int:
    """문서 나누기(사람): 원문을 가리고 청크를 더한다. 만든 청크 수. 길지 않으면 InvalidInputError."""
    pieces = await edits.split_document(db, document_id=document_id)
    await db.commit()
    return len(pieces)


async def unsplit_document(db: AsyncSession, *, document_id: int) -> int:
    """나누기 되돌리기(사람): 청크를 지우고 원문을 되살린다. 지운 청크 수."""
    removed = await edits.unsplit_document(db, document_id=document_id)
    await db.commit()
    return removed


async def empty_trash(db: AsyncSession, *, dataset_id: int, kind: str) -> int:
    """휴지통 비우기: 휴지통의 질의(또는 문서)를 영구히 지운다(판정 · 지도의 점도 함께). 지운 수."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    if kind == "documents":
        result = await db.execute(
            delete(Document).where(Document.dataset_id == dataset_id, DOCUMENT_TRASHED)
        )
    else:
        result = await db.execute(
            delete(Query).where(Query.dataset_id == dataset_id, QUERY_TRASHED)
        )
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ---------- 안에서만 쓰는 함수 ----------


def _query_status(status: str):
    return {
        "active": QUERY_ACTIVE,
        "included": QUERY_INCLUDED,
        "excluded": QUERY_EXCLUDED,
        "trash": QUERY_TRASHED,
    }.get(status, true())


def _document_status(status: str):
    if status == "trash":
        return Document.trashed_at.is_not(None)
    return DOCUMENT_ACTIVE


def _escape_like(text: str) -> str:
    """LIKE 거르기에서 %, _, \\ 를 글자 그대로 찾게 한다."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _close_suggestions(db: AsyncSession, *, query_id: int, document_id: int) -> None:
    """사람이 그 쌍을 정했으니 대기 중인 제안을 수락으로 닫는다."""
    await db.execute(
        update(Suggestion)
        .where(
            Suggestion.query_id == query_id,
            Suggestion.document_id == document_id,
            Suggestion.decision.is_(None),
        )
        .values(decision="accepted", decided_at=datetime.now(UTC))
    )


async def _refresh_overlap(db: AsyncSession, *, query: Query) -> None:
    """질의 글이 바뀌면 그 질의 판정의 글자 겹침을 다시 적는다."""
    rows = (
        await db.execute(
            select(Judgment, Document.text)
            .join(Document, Document.id == Judgment.document_id)
            .where(Judgment.query_id == query.id)
        )
    ).tuples()
    for judgment, text in rows:
        judgment.overlap = lexical_overlap(query.text, text)
