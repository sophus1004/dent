"""규칙으로 하는 고치기: 문서 글자 정리 · 같은 문서 합치기 · 질의 안 만들 청크 표시 · 문서 나누기와
되돌리기 · 질의 정리(짧은 · 긴 · 중복 여분 · 분할 간 중복) · 같은 오답 떼기 · 제안 수락.
반복 구간(떼기 · 남김)은 본문을 바꾸지 않고 학습 글만 바꾼다(repeats.py).

사람(데이터 탭의 [나누기])과 도우미가 같이 쓴다. 도우미가 부를 때는 ChangeLog를 넘겨 바꾼 줄마다 전 · 후를 모으고,
도우미가 그것을 변경 기록(retrieval_helper_changes)에 적는다(되돌리기). 커밋은 부른 쪽이 한다.
영구 삭제는 없다: 빼기 · 휴지통 · 판정 떼기 · 나누기(원문은 가리기만 한다)뿐이다.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import repeats
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_DOC_MAX_TOKENS,
    POSITIVE_MIN_GRADE,
    Document,
    ExcludeReason,
    HelperField,
    HelperTarget,
    Judgment,
    JudgmentSource,
    Query,
    Shape,
    Suggestion,
    SuggestionDecision,
    SuggestionKind,
)
from dent.modules.retrieval.service import DOCUMENT_ACTIVE, QUERY_ACTIVE
from dent.modules.retrieval.text_rules import (
    HEADER_SEPARATOR,
    SECTION_RESERVE_TOKENS,
    Chunk,
    best_chunk,
    chunk_text,
    clean_text,
    estimate_tokens,
    has_broken_text,
    lexical_overlap,
)
from dent.system import datasets as datasets_service
from dent.system.exceptions import InvalidInputError, NotFoundError
from dent.system.text import make_text_hash

# 한 번에 읽고 고치는 문서 수
EDIT_CHUNK = 2_000

# 나눌 청크 수를 어림할 때 실제 규칙으로 나눠 보는 긴 문서의 최대 수 (나머지는 그 평균으로 센다).
# 구획(번호 단위)으로 나뉘는 문서는 길이만으로 세면 크게 틀린다(규정 22문서: 길이로 56 · 실제 519).
SPLIT_ESTIMATE_SAMPLE = 300

DOCUMENT_NOT_FOUND_MESSAGE = "문서를 찾을 수 없습니다."
NOT_LONG_MESSAGE = "나눌 만큼 긴 문서가 아닙니다."
NOT_SPLIT_MESSAGE = "나눈 문서가 아닙니다."
ALREADY_SPLIT_MESSAGE = "이미 나눈 문서입니다."


@dataclass
class ChangeLog:
    """도우미가 바꾼 줄들의 전 · 후 (retrieval_helper_changes의 줄이 된다)."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        *,
        target: HelperTarget,
        field_name: HelperField,
        before: Any,
        after: Any,
        query_id: int | None = None,
        document_id: int | None = None,
    ) -> None:
        self.entries.append(
            {
                "target": target.value,
                "field": field_name.value,
                "query_id": query_id,
                "document_id": document_id,
                "before": before,
                "after": after,
            }
        )


# ---------- 1 문서 ----------


async def clean_documents(db: AsyncSession, *, dataset_id: int, log: ChangeLog | None) -> int:
    """깨진 글자 · HTML 찌꺼기가 든 문서(표시 broken)의 글을 정리한다. 바꾼 수."""
    changed: list[Document] = []
    documents = await _documents(db, dataset_id, Document.marks.has_key("broken"))
    for document in documents:
        if not has_broken_text(document.text):
            continue
        cleaned = clean_text(document.text)
        if cleaned and cleaned != document.text:
            _set_document_text(document, cleaned, log=log)
            changed.append(document)
    await repeats.refresh_documents(db, dataset_id=dataset_id, documents=changed)
    return len(changed)


async def merge_duplicate_documents(
    db: AsyncSession, *, dataset_id: int, log: ChangeLog | None
) -> int:
    """학습 글이 같은 문서를 하나로 합친다: 번호가 가장 작은 것을 남기고 나머지의 판정을 옮긴 뒤 휴지통으로. 합친 수.

    본문만 같고 머리말(제목 · 구획)이 다른 문서는 운영 풀에서 서로 다른 문서라 합치지 않는다(중복 묶음으로 둔다).
    """
    duplicates = (
        await db.execute(
            select(Document.input_hash, func.array_agg(Document.id))
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
            .group_by(Document.input_hash)
            .having(func.count() > 1)
        )
    ).all()
    now = datetime.now(UTC)
    merged = 0
    for _text_hash, ids in duplicates:
        keeper, *others = sorted(ids)
        for other in others:
            judgments = list(
                await db.scalars(select(Judgment).where(Judgment.document_id == other))
            )
            for judgment in judgments:
                await _copy_judgment(db, judgment, document_id=keeper, log=log)
            document = await db.get(Document, other)
            if document is None:
                continue
            document.trashed_at = now
            document.row_version += 1
            if log is not None:
                log.add(
                    target=HelperTarget.DOCUMENT,
                    field_name=HelperField.TRASH,
                    document_id=other,
                    before=False,
                    after=True,
                )
            merged += 1
    return merged


async def mark_skip_generation(db: AsyncSession, *, dataset_id: int, log: ChangeLog | None) -> int:
    """질의를 만들 거리가 없는 청크(표만 · 목차 · 아주 짧음 · 반복 구간이 대부분)을 '질의 안 만듦'으로 표시한다.

    표시한 수. 까닭은 글을 넣거나 바꿀 때 재 둔 표시(marks.pick)를 쓴다.
    """
    changed = 0
    for document in await _documents(
        db, dataset_id, Document.skip_generation.is_(False), Document.marks.has_key("pick")
    ):
        document.skip_generation = True
        document.row_version += 1
        if log is not None:
            log.add(
                target=HelperTarget.DOCUMENT,
                field_name=HelperField.SKIP_GENERATION,
                document_id=document.id,
                before=False,
                after=True,
            )
        changed += 1
    return changed


def _chunks_of(document: Document, *, limit: int, overlap: int) -> list[Chunk]:
    """문서를 나눌 때의 청크들. 나누기와 어림이 같은 규칙을 쓴다."""
    # 학습 글에는 제목 · 머리말 칸 · 구획 경로가 앞에 붙으므로 그만큼 본문 자리를 줄인다.
    prefix = HEADER_SEPARATOR.join(part for part in (document.title, document.header) if part)
    reserve = estimate_tokens(prefix) + SECTION_RESERVE_TOKENS
    return chunk_text(document.text, max_tokens=max(limit - reserve, limit // 2), overlap=overlap)


@dataclass(frozen=True)
class SplitSample:
    """긴 문서를 실제 규칙으로 나눠 본 평균 (어림에 쓴다)."""

    # 긴 문서 하나가 나뉘는 청크 수 (긴 문서가 없으면 0, 나뉘지 않는 문서는 1)
    chunks_per_document: float

    # 청크 하나의 토큰 수
    tokens_per_chunk: float


async def split_sample(
    db: AsyncSession, *, dataset_id: int, limit: int, overlap: int
) -> SplitSample:
    """limit을 넘는 문서를 실제 규칙으로 나눠 본 평균(최대 SPLIT_ESTIMATE_SAMPLE개)."""
    documents = list(
        await db.scalars(
            select(Document)
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE, Document.token_count > limit)
            .order_by(Document.id)
            .limit(SPLIT_ESTIMATE_SAMPLE)
        )
    )
    chunks = [_chunks_of(document, limit=limit, overlap=overlap) for document in documents]
    pieces = [chunk for found in chunks for chunk in found]
    if not documents or not pieces:
        return SplitSample(chunks_per_document=0.0, tokens_per_chunk=0.0)
    return SplitSample(
        chunks_per_document=sum(len(found) or 1 for found in chunks) / len(documents),
        tokens_per_chunk=sum(chunk.tokens for chunk in pieces) / len(pieces),
    )


async def split_document(
    db: AsyncSession,
    *,
    document_id: int,
    max_tokens: int | None = None,
    log: ChangeLog | None = None,
) -> list[int]:
    """긴 문서 하나를 청크로 나눈다. 원문은 가리고(replaced_at) 청크 문서를 더한다. 청크 번호들.

    구조 표지(제목 줄 · 번호 단위)가 있으면 구획부터 나누고 청크마다 구획 경로를 머리말로 둔다.
    판정은 답이 든 청크(질의의 답 근거가 든 청크, 없으면 글자가 가장 많이 겹치는 청크)로 옮긴다.
    원문의 판정은 그대로 두어 되돌릴 때 쓴다. 없으면 NotFoundError, 나눌 만큼 길지 않으면 InvalidInputError.
    """
    document = await db.get(Document, document_id, populate_existing=True)
    if document is None:
        raise NotFoundError(DOCUMENT_NOT_FOUND_MESSAGE)
    if document.replaced_at is not None:
        raise InvalidInputError(ALREADY_SPLIT_MESSAGE)
    settings = await retrieval_service.find_settings(db, dataset_id=document.dataset_id)
    limit = max_tokens or (settings.doc_max_tokens if settings else DEFAULT_DOC_MAX_TOKENS)
    overlap = settings.chunk_overlap if settings else DEFAULT_CHUNK_OVERLAP
    chunks = _chunks_of(document, limit=limit, overlap=overlap)
    if document.token_count <= limit or len(chunks) < 2:
        raise InvalidInputError(NOT_LONG_MESSAGE)
    pieces = []
    for index, chunk in enumerate(chunks):
        piece = Document(
            dataset_id=document.dataset_id,
            import_id=document.import_id,
            doc_key=document.doc_key,
            title=document.title,
            header=document.header,
            section=chunk.section,
            group_key=document.group_key,
            text=chunk.text,
            text_hash=make_text_hash(chunk.text),
            input_hash=make_text_hash(chunk.text),
            token_count=chunk.tokens,
            source_document_id=document.id,
            chunk_index=index,
            extra=document.extra,
        )
        db.add(piece)
        pieces.append(piece)
    await db.flush()
    await repeats.refresh_documents(db, dataset_id=document.dataset_id, documents=pieces)
    for piece in pieces:
        piece.skip_generation = "pick" in piece.marks
    document.replaced_at = datetime.now(UTC)
    document.row_version += 1
    judgments = (
        await db.execute(
            select(Judgment, Query.text, Query.answer)
            .join(Query, Query.id == Judgment.query_id)
            .where(Judgment.document_id == document.id)
        )
    ).tuples()
    texts = [piece.text for piece in pieces]
    for judgment, query_text, answer in judgments:
        target = pieces[best_chunk(texts, query=query_text, answer=answer)]
        await _copy_judgment(db, judgment, document_id=target.id, log=None)
    piece_ids = [piece.id for piece in pieces]
    if log is not None:
        log.add(
            target=HelperTarget.DOCUMENT,
            field_name=HelperField.CHUNKED,
            document_id=document.id,
            before=None,
            after=piece_ids,
        )
    await datasets_service.touch_dataset(db, dataset_id=document.dataset_id)
    return piece_ids


async def unsplit_document(db: AsyncSession, *, document_id: int) -> int:
    """나누기 되돌리기: 청크(와 그 판정 · 점)을 지우고 원문을 되살린다. 지운 청크 수.

    청크는 사람이 만든 것이 아니라 나누기가 만든 것이라 지운다(원문과 원문의 판정은 그대로 남아 있다).
    나눈 문서가 아니면 InvalidInputError.
    """
    document = await db.get(Document, document_id, populate_existing=True)
    if document is None:
        raise NotFoundError(DOCUMENT_NOT_FOUND_MESSAGE)
    if document.replaced_at is None:
        raise InvalidInputError(NOT_SPLIT_MESSAGE)
    result = await db.execute(delete(Document).where(Document.source_document_id == document.id))
    document.replaced_at = None
    document.row_version += 1
    await datasets_service.touch_dataset(db, dataset_id=document.dataset_id)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def split_long_documents(
    db: AsyncSession, *, dataset_id: int, log: ChangeLog | None
) -> tuple[int, int]:
    """최대 토큰을 넘는 문서를 모두 나눈다. (나눈 문서 수, 만든 청크 수)."""
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    limit = settings.doc_max_tokens if settings else DEFAULT_DOC_MAX_TOKENS
    ids = list(
        await db.scalars(
            select(Document.id)
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE, Document.token_count > limit)
            .order_by(Document.id)
        )
    )
    split_count = 0
    piece_count = 0
    for document_id in ids:
        try:
            pieces = await split_document(db, document_id=document_id, max_tokens=limit, log=log)
        except InvalidInputError:
            # 문장 경계로 나눠도 한 청크뿐인 문서는 그대로 둔다(글자 수로 잘린 청크는 뒤가 잘린다).
            continue
        split_count += 1
        piece_count += len(pieces)
    return split_count, piece_count


# ---------- 2 질의 ----------


async def exclude_queries(
    db: AsyncSession,
    *,
    dataset_id: int,
    condition: Any,
    log: ChangeLog | None,
) -> int:
    """조건에 맞는 학습 포함 질의를 도우미 이유로 뺀다. 뺀 수."""
    queries = list(
        await db.scalars(
            select(Query)
            .where(
                Query.dataset_id == dataset_id,
                QUERY_ACTIVE,
                Query.exclude_reason.is_(None),
                condition,
            )
            .order_by(Query.id)
        )
    )
    for query in queries:
        query.exclude_reason = ExcludeReason.HELPER.value
        query.row_version += 1
        if log is not None:
            log.add(
                target=HelperTarget.QUERY,
                field_name=HelperField.EXCLUDE,
                query_id=query.id,
                before=None,
                after=ExcludeReason.HELPER.value,
            )
    return len(queries)


async def remove_same_negatives(db: AsyncSession, *, dataset_id: int, log: ChangeLog | None) -> int:
    """정답 문서와 같은 중복 묶음(본문이 같거나 근접 중복)인 오답 판정을 뗀다. 뗀 수."""
    roots = await retrieval_service.duplicate_groups(db, dataset_id=dataset_id)
    positive_roots: dict[int, set[int]] = {}
    negatives: list[tuple[int, int]] = []
    for query_id, document_id, grade in await db.execute(
        select(Judgment.query_id, Judgment.document_id, Judgment.grade).where(
            Judgment.dataset_id == dataset_id
        )
    ):
        root = roots.get(document_id)
        if root is None:
            continue
        if grade >= POSITIVE_MIN_GRADE:
            positive_roots.setdefault(query_id, set()).add(root)
        else:
            negatives.append((query_id, document_id))
    same = [
        (query_id, document_id)
        for query_id, document_id in negatives
        if roots[document_id] in positive_roots.get(query_id, set())
    ]
    rows = [
        judgment
        for judgment in [await db.get(Judgment, pair) for pair in same]
        if judgment is not None
    ]
    for judgment in rows:
        await remove_judgment(db, judgment, log=log)
    return len(rows)


async def accept_suggestions(
    db: AsyncSession,
    *,
    dataset_id: int,
    analysis_id: int,
    kind: SuggestionKind,
    confirmed_only: bool,
    log: ChangeLog | None,
) -> int:
    """대기 중인 제안을 수락한다(도우미). 거짓 오답 · 빠진 정답 → 정답, 정답 의심 → 떼기. 수락한 수."""
    conditions = [
        Suggestion.analysis_id == analysis_id,
        Suggestion.kind == kind.value,
        Suggestion.decision.is_(None),
    ]
    if confirmed_only:
        conditions.append(Suggestion.is_confirmed.is_(True))
    suggestions = list(
        await db.scalars(select(Suggestion).where(*conditions).order_by(Suggestion.id))
    )
    for suggestion in suggestions:
        if kind == SuggestionKind.SUSPECT_POSITIVE:
            judgment = await db.get(Judgment, (suggestion.query_id, suggestion.document_id))
            if judgment is not None:
                await remove_judgment(db, judgment, log=log)
        else:
            await set_grade(
                db,
                dataset_id=dataset_id,
                query_id=suggestion.query_id,
                document_id=suggestion.document_id,
                grade=POSITIVE_MIN_GRADE,
                teacher_score=suggestion.jev_probability,
                log=log,
            )
        suggestion.decision = SuggestionDecision.ACCEPTED.value
        suggestion.decided_at = datetime.now(UTC)
    return len(suggestions)


# ---------- 판정 ----------


def judgment_state(judgment: Judgment) -> dict[str, Any]:
    """되돌리기에 남기는 판정 한 줄의 값: 등급 · 출처 · 충돌 · 교사 점수 · 글자 겹침(등급만 남기면 충돌 · 출처를 잃는다)."""
    return {
        "grade": judgment.grade,
        "source": judgment.source,
        "conflict": judgment.conflict,
        "teacher_score": judgment.teacher_score,
        "overlap": judgment.overlap,
    }


async def set_grade(
    db: AsyncSession,
    *,
    dataset_id: int,
    query_id: int,
    document_id: int,
    grade: int,
    teacher_score: float | None = None,
    source: JudgmentSource = JudgmentSource.HELPER,
    log: ChangeLog | None,
) -> None:
    """판정 하나를 둔다(없으면 만들고 있으면 등급을 바꾼다). 변경 기록에는 전 판정의 값(없으면 null)을 남긴다.

    등급이 같아도 충돌을 푼 것이면 기록한다(되돌리면 충돌로 돌아간다).
    """
    existing = await db.get(Judgment, (query_id, document_id), populate_existing=True)
    before = existing.grade if existing is not None else None
    before_state = judgment_state(existing) if existing is not None else None
    was_conflict = existing is not None and existing.conflict
    if existing is not None:
        existing.grade = grade
        existing.source = source.value
        existing.conflict = False
        if teacher_score is not None:
            existing.teacher_score = teacher_score
    else:
        query = await db.get(Query, query_id)
        document = await db.get(Document, document_id)
        overlap = lexical_overlap(query.text, document.text) if query and document else None
        db.add(
            Judgment(
                query_id=query_id,
                document_id=document_id,
                dataset_id=dataset_id,
                grade=grade,
                source=source.value,
                teacher_score=teacher_score,
                overlap=overlap,
            )
        )
    is_changed = before != grade or was_conflict
    if log is not None and is_changed:
        log.add(
            target=HelperTarget.JUDGMENT,
            field_name=HelperField.GRADE,
            query_id=query_id,
            document_id=document_id,
            before=before_state,
            after=grade,
        )


async def remove_judgment(db: AsyncSession, judgment: Judgment, *, log: ChangeLog | None) -> None:
    """판정 하나를 뗀다(모름으로). 변경 기록에는 전 판정의 값을 남긴다."""
    if log is not None:
        log.add(
            target=HelperTarget.JUDGMENT,
            field_name=HelperField.GRADE,
            query_id=judgment.query_id,
            document_id=judgment.document_id,
            before=judgment_state(judgment),
            after=None,
        )
    await db.execute(
        delete(Judgment).where(
            Judgment.query_id == judgment.query_id, Judgment.document_id == judgment.document_id
        )
    )


# ---------- 안에서만 쓰는 함수 ----------


async def _documents(db: AsyncSession, dataset_id: int, *conditions: Any) -> list[Document]:
    """데이터셋의 코퍼스 문서 (조건이 있으면 그것까지, 번호 순)."""
    return list(
        await db.scalars(
            select(Document)
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE, *conditions)
            .order_by(Document.id)
        )
    )


def _set_document_text(document: Document, text: str, *, log: ChangeLog | None) -> None:
    """본문을 바꾼다. 학습 글 · 표시는 부른 쪽이 repeats.refresh_documents로 다시 만든다."""
    if log is not None:
        log.add(
            target=HelperTarget.DOCUMENT,
            field_name=HelperField.TEXT,
            document_id=document.id,
            before=document.text,
            after=text,
        )
    document.text = text
    document.text_hash = make_text_hash(text)
    document.row_version += 1


async def _copy_judgment(
    db: AsyncSession, judgment: Judgment, *, document_id: int, log: ChangeLog | None
) -> None:
    """판정을 다른 문서로 복사한다. 이미 있으면 높은 등급을 남긴다."""
    statement = pg_insert(Judgment).values(
        query_id=judgment.query_id,
        document_id=document_id,
        dataset_id=judgment.dataset_id,
        grade=judgment.grade,
        source=judgment.source,
        conflict=judgment.conflict,
        teacher_score=judgment.teacher_score,
        overlap=judgment.overlap,
        import_id=judgment.import_id,
    )
    existing = await db.get(Judgment, (judgment.query_id, document_id))
    if existing is None and log is not None:
        log.add(
            target=HelperTarget.JUDGMENT,
            field_name=HelperField.GRADE,
            query_id=judgment.query_id,
            document_id=document_id,
            before=None,
            after=judgment.grade,
        )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["query_id", "document_id"],
            set_={"grade": func.greatest(Judgment.__table__.c.grade, statement.excluded.grade)},
        )
    )


async def dataset_shape(db: AsyncSession, *, dataset_id: int) -> Shape | None:
    """데이터셋이 처음 들어온 모양 (설정이 없으면 None)."""
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    return Shape(settings.shape) if settings else None
