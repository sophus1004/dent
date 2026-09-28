"""가져오기 실행 (검색). 작업 실행기가 부른다.

원본은 시스템이 줄로 읽어 준다(dent.system.sources). 이 파일은 그 줄을 모양에 따라 질의 · 문서 · 판정으로 넣는다.
다시 돌려도 결과가 같다: 시작할 때 이 가져오기가 전에 넣은 질의 · 문서 · 판정을 먼저 지운다.

모양마다 한 줄의 규칙
- 문서만: 본문 → 문서 하나 (제목 · 원래 번호 칸은 있으면)
- 쌍 · MRC: 질의 · 정답 문서 → 질의, 문서, 판정(등급 1). MRC의 답은 질의의 답 근거로 둔다.
- 세 쌍: 질의 · 정답 · 오답 칸들 → 질의, 문서들, 판정(정답 1 · 오답 0). 빈 오답 칸은 건너뛴다.
- 점수: 질의 · 문서 · 점수 → 질의, 문서, 판정(0~3 정수는 그대로, 0~1 실수는 0.5 이상이면 1).
머리말 칸 · 묶음 칸(고른 경우)은 문서만이면 그 문서에, 질의가 있는 모양이면 정답 문서에 붙인다.
합치기
- 문서: 문서만 모양은 학습 글(제목 · 머리말 칸 + 본문)이, 질의가 있는 모양은 본문이 같으면 하나로 합친다
  (같은 본문이 정답에는 제목과, 오답에는 제목 없이 나오기 때문). 데이터셋에 이미 있는 문서도 다시 넣지 않는다.
  이미 청크로 나눈 원문과 같은 문서도 다시 넣지 않는다(긴 원문이 다시 생기지 않게). 그 판정은 원문에 적고(되돌릴 때 쓴다),
  나누기와 같은 규칙으로 답이 든 청크(없으면 글자가 가장 많이 겹치는 청크)에도 적는다.
- 질의: 글이 같으면 하나로 합치고 판정도 모은다. train · valid · test는 나누지 않는다(원본에 분할이 있어도
  허깅페이스의 여러 분할을 가져와도 한 덩어리로 합친다).
- 판정: 같은 (질의, 문서)가 다시 나오면 높은 등급을 남기고, 정답 · 오답이 엇갈리면 '판정 충돌'로 적는다.
건너뛰는 줄: 빈 질의 · 너무 긴 질의 · 빈 문서 · 너무 긴 문서 · 알 수 없는 점수 (줄 번호와 이유를 남긴다).
다 넣으면 반복 구간을 살피고 모든 문서의 학습 글 · 표시를 만든다(repeats.scan_dataset).
"""

import itertools
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import delete, insert, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import repeats
from dent.modules.retrieval.models import (
    DOC_KEY_MAX_LENGTH,
    DOCUMENT_MAX_LENGTH,
    GRADE_MAX,
    GROUP_KEY_MAX_LENGTH,
    HEADER_MAX_LENGTH,
    POSITIVE_MIN_GRADE,
    QUERY_MAX_LENGTH,
    DatasetSettings,
    Document,
    Judgment,
    JudgmentSource,
    Query,
    Shape,
)
from dent.modules.retrieval.schemas import FieldMappingCreate
from dent.modules.retrieval.service import DOCUMENT_ACTIVE, DOCUMENT_SPLIT, QUERY_ACTIVE
from dent.modules.retrieval.text_rules import (
    best_chunk,
    document_input,
    estimate_tokens,
    has_broken_text,
    lexical_overlap,
    skip_generation_reason,
)
from dent.system import imports as imports_service
from dent.system import jobs, sources, tables
from dent.system.db import get_engine
from dent.system.exceptions import InvalidInputError
from dent.system.models import Import
from dent.system.schemas import CellValue
from dent.system.text import make_text_hash

# 한 번에 읽는 줄 수. 묶음마다 커밋하고 진행률을 적는다.
BATCH_ROWS = 5_000

# 판정을 한 문장으로 넣을 때의 줄 수. 한 문장의 값 자리는 32,767개까지라서(asyncpg) 줄마다 8칸 × 2,000.
JUDGMENT_INSERT_ROWS = 2_000

# 점수가 실수(0~1)일 때 정답으로 보는 가장 낮은 값
SCORE_POSITIVE_MIN = 0.5

# 가져오기 기록의 result에 담는 수의 열쇠
RESULT_KEYS = (
    "queries_added",
    "documents_added",
    "documents_merged",
    "judgments_added",
    "conflicts",
)

# 건너뛰는 이유
SKIP_EMPTY_QUERY = "빈 질의"
SKIP_TOO_LONG_QUERY = "너무 긴 질의"
SKIP_EMPTY_DOCUMENT = "빈 문서"
SKIP_TOO_LONG_DOCUMENT = "너무 긴 문서"
SKIP_BAD_SCORE = "알 수 없는 점수"


# 머리말 칸 값을 이을 때의 구분
HEADER_JOIN = " · "


@dataclass(frozen=True)
class DocText:
    """문서 하나로 넣을 글과 칸들."""

    text: str
    title: str
    doc_key: str | None

    # 머리말 칸 값 · 묶음 칸 값 · 남는 열 (문서만 모양)
    header: str = ""
    group_key: str | None = None
    extra: dict[str, CellValue] = field(default_factory=dict)

    def input_text(self) -> str:
        """가져올 때의 학습 글 (제목 · 머리말 칸 + 본문). 반복 구간은 살핀 뒤에 뗀다."""
        return document_input(
            title=self.title, header=self.header, section="", body=self.text, section_header=False
        )


@dataclass
class ParsedRow:
    """넣을 수 있는 한 줄: 문서만이면 document, 아니면 질의와 판정할 문서들."""

    # 문서만 모양의 문서
    document: DocText | None = None

    # 질의 글 · 답 근거
    query: str | None = None
    answer: str | None = None

    # (문서, 등급) 판정들
    judged: list[tuple[DocText, int]] = field(default_factory=list)

    # 남는 열들
    extra: dict[str, CellValue] = field(default_factory=dict)


@dataclass
class _Counts:
    """이번 가져오기에서 센 수 (가져오기 기록의 result)."""

    queries_added: int = 0
    documents_added: int = 0
    documents_merged: int = 0
    judgments_added: int = 0
    conflicts: int = 0


async def run_import(
    db: AsyncSession,
    *,
    import_id: int,
    job_id: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Import:
    """가져오기 하나를 처음부터 끝까지 한다. 원본을 읽을 수 없으면 도메인 예외를 낸다."""
    import_row = await imports_service.get_import(db, import_id=import_id)
    dataset_id = import_row.dataset_id
    await delete_inserted(db, dataset_id=dataset_id, import_id=import_id)
    imports_service.start_import(import_row)
    await db.commit()

    options = import_row.options
    mapping = FieldMappingCreate.model_validate(options["mapping"])
    await _ensure_settings(db, dataset_id=dataset_id, shape=mapping.shape)
    counts = _Counts()
    async with sources.open_source(db, import_row, transport=transport) as source:
        _check_mapping(mapping, source.columns)
        import_row.rows_total = source.total_rows
        document_ids = await _load_document_ids(
            db, dataset_id=dataset_id, by_input=mapping.shape == Shape.DOCUMENTS
        )
        query_ids = await _load_query_ids(db, dataset_id=dataset_id)
        split_chunks = await _load_split_chunks(db, dataset_id=dataset_id)
        done = 0
        for batch in itertools.batched(source.rows, BATCH_ROWS):
            await _insert_batch(
                db,
                import_row=import_row,
                batch=batch,
                mapping=mapping,
                document_ids=document_ids,
                query_ids=query_ids,
                split_chunks=split_chunks,
                counts=counts,
            )
            done += len(batch)
            import_row.result = {name: getattr(counts, name) for name in RESULT_KEYS}
            await jobs.set_progress(db, job_id=job_id, done=done, total=source.total_rows)
            await db.commit()

    await repeats.scan_dataset(db, dataset_id=dataset_id, job_id=job_id)
    await imports_service.finish_import(db, import_row, job_id=job_id)
    await db.commit()
    return import_row


async def fail_import(db: AsyncSession, *, import_id: int, job_id: int, message: str) -> None:
    """가져오기와 작업을 실패로 끝낸다. 반쯤 들어간 질의 · 문서 · 판정은 지워 가져오기 전으로 둔다."""
    await db.rollback()
    import_row = await db.get(Import, import_id)
    if import_row is not None:
        await delete_inserted(db, dataset_id=import_row.dataset_id, import_id=import_id)
    await imports_service.fail_import(db, import_id=import_id, job_id=job_id, message=message)


async def vacuum_tables() -> None:
    """질의 · 문서 · 판정 표를 VACUUM ANALYZE 한다. 가져오기를 마친 뒤 작업 실행기가 부른다(분류와 같은 까닭)."""
    engine = get_engine()
    # VACUUM은 트랜잭션 안에서 돌 수 없어서, 문장마다 바로 확정하는 연결을 쓴다.
    async with engine.connect() as conn:
        autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for table in (Query.__tablename__, Document.__tablename__, Judgment.__tablename__):
            await autocommit.execute(text(f"VACUUM (ANALYZE) {table}"))


def parse_row(row: sources.SourceRow, *, mapping: FieldMappingCreate) -> ParsedRow | str:
    """원본 한 줄을 모양에 따라 바꾼다. 넣을 수 없는 줄이면 건너뛰는 이유(문자열)를 돌려준다."""
    values = row.values
    title = tables.to_text(values.get(mapping.title)) if mapping.title else ""
    extra = {name: value for name, value in values.items() if name not in mapping.used_columns()}
    header = HEADER_JOIN.join(
        text
        for text in (tables.to_text(values.get(name)) for name in mapping.header_columns)
        if text
    )[:HEADER_MAX_LENGTH]
    group_key = (
        tables.to_text(values.get(mapping.group_column))[:GROUP_KEY_MAX_LENGTH] or None
        if mapping.group_column
        else None
    )

    if mapping.shape == Shape.DOCUMENTS:
        document = _document(
            values.get(mapping.text),
            title=title,
            doc_key=_doc_key(values, mapping),
            header=header,
            group_key=group_key,
            extra=extra,
        )
        if isinstance(document, str):
            return document
        return ParsedRow(document=document, extra=extra)

    query = tables.to_text(values.get(mapping.query))
    if not query:
        return SKIP_EMPTY_QUERY
    if len(query) > QUERY_MAX_LENGTH:
        return SKIP_TOO_LONG_QUERY
    parsed = ParsedRow(query=query, extra=extra)
    if mapping.shape == Shape.SCORED:
        document = _document(
            values.get(mapping.document),
            title=title,
            doc_key=None,
            header=header,
            group_key=group_key,
        )
        if isinstance(document, str):
            return document
        grade = _grade(values.get(mapping.score))
        if grade is None:
            return SKIP_BAD_SCORE
        parsed.judged.append((document, grade))
        return parsed

    positive = _document(
        values.get(mapping.positive), title=title, doc_key=None, header=header, group_key=group_key
    )
    if isinstance(positive, str):
        return positive
    parsed.judged.append((positive, POSITIVE_MIN_GRADE))
    if mapping.shape == Shape.MRC and mapping.answer:
        parsed.answer = _answer_text(values.get(mapping.answer)) or None
    if mapping.shape == Shape.TRIPLET:
        for column in mapping.negatives:
            negative = _document(values.get(column), title="", doc_key=None)
            # 빈 오답 칸은 건너뛴다(줄은 넣는다).
            if not isinstance(negative, str):
                parsed.judged.append((negative, 0))
    return parsed


async def delete_inserted(db: AsyncSession, *, dataset_id: int, import_id: int) -> None:
    """이 가져오기가 넣은 판정 · 질의 · 문서를 지운다. 커밋은 부른 쪽이 한다."""
    for model in (Judgment, Query, Document):
        await db.execute(
            delete(model)
            .where(model.dataset_id == dataset_id, model.import_id == import_id)
            .execution_options(synchronize_session=False)
        )


# ---------- 안에서만 쓰는 함수 ----------


def _document(
    value: CellValue,
    *,
    title: str,
    doc_key: str | None,
    header: str = "",
    group_key: str | None = None,
    extra: dict[str, CellValue] | None = None,
) -> DocText | str:
    """문서 칸 값 → 문서 글. 비었거나 너무 길면 건너뛰는 이유."""
    body = tables.to_text(value)
    if not body:
        return SKIP_EMPTY_DOCUMENT
    if len(body) > DOCUMENT_MAX_LENGTH:
        return SKIP_TOO_LONG_DOCUMENT
    return DocText(
        text=body,
        title=title,
        doc_key=doc_key,
        header=header,
        group_key=group_key,
        extra=extra or {},
    )


def _doc_key(values: dict[str, CellValue], mapping: FieldMappingCreate) -> str | None:
    if not mapping.doc_key:
        return None
    key = tables.to_text(values.get(mapping.doc_key))
    return key[:DOC_KEY_MAX_LENGTH] or None


def _grade(value: CellValue) -> int | None:
    """점수 → 등급. 0~3 정수는 그대로, 0~1 실수는 SCORE_POSITIVE_MIN 이상이면 1. 알 수 없으면 None."""
    try:
        number = float(tables.to_text(value))
    except ValueError:
        return None
    if number.is_integer() and 0 <= number <= GRADE_MAX:
        return int(number)
    if 0 <= number <= 1:
        return POSITIVE_MIN_GRADE if number >= SCORE_POSITIVE_MIN else 0
    return None


def _answer_text(value: CellValue) -> str:
    """MRC 답 칸 → 답 글. 허깅페이스의 {"text": [...], "answer_start": [...]} 모양이면 첫 답."""
    raw = tables.to_text(value)
    if raw.startswith("{"):
        try:
            parsed: Any = json.loads(raw)
        except ValueError:
            return raw
        answers = parsed.get("text") if isinstance(parsed, dict) else None
        if isinstance(answers, list) and answers:
            return str(answers[0]).strip()
    return raw


def _check_mapping(mapping: FieldMappingCreate, columns: list[str]) -> None:
    """필드 맞춤의 열이 원본에 있는지 본다. 없으면 InvalidInputError."""
    for name in sorted(mapping.used_columns()):
        if name not in columns:
            raise InvalidInputError(
                f"'{name}' 열이 원본에 없습니다. 필드 맞추기를 다시 확인하세요."
            )


async def _ensure_settings(db: AsyncSession, *, dataset_id: int, shape: Shape) -> None:
    """데이터셋 설정이 없으면 이 모양으로 만든다(처음 가져온 모양이 입구를 정한다)."""
    await db.execute(
        pg_insert(DatasetSettings)
        .values(dataset_id=dataset_id, shape=shape.value)
        .on_conflict_do_nothing(index_elements=["dataset_id"])
    )


async def _load_document_ids(
    db: AsyncSession, *, dataset_id: int, by_input: bool
) -> dict[str, int]:
    """데이터셋의 문서 {열쇠: 번호}. 열쇠는 학습 글 해시(문서만) 또는 본문 해시.

    코퍼스 문서와 나눈 원문을 담는다. 같은 열쇠면 코퍼스 문서, 그 안에서는 번호가 작은 것이 이긴다.
    """
    key = Document.input_hash if by_input else Document.text_hash
    rows = await db.execute(
        select(key, Document.id)
        .where(Document.dataset_id == dataset_id, or_(DOCUMENT_ACTIVE, DOCUMENT_SPLIT))
        # 뒤에 오는 것이 사전에 남으므로 나눈 원문 → 코퍼스 문서, 번호가 큰 것 → 작은 것 순으로 읽는다.
        .order_by(Document.replaced_at.is_(None), Document.id.desc())
    )
    return dict(rows.tuples().all())


async def _load_split_chunks(
    db: AsyncSession, *, dataset_id: int
) -> dict[int, list[tuple[int, str]]]:
    """나눈 원문마다 코퍼스에 있는 청크 [(번호, 본문)] (청크 순)."""
    rows = await db.execute(
        select(Document.source_document_id, Document.id, Document.text)
        .where(
            Document.dataset_id == dataset_id,
            DOCUMENT_ACTIVE,
            Document.source_document_id.is_not(None),
        )
        .order_by(Document.source_document_id, Document.chunk_index, Document.id)
    )
    chunks: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for original_id, chunk_id, chunk in rows:
        chunks[original_id].append((chunk_id, chunk))
    return dict(chunks)


async def _load_query_ids(db: AsyncSession, *, dataset_id: int) -> dict[str, int]:
    """데이터셋의 질의 {해시: 번호}. 같은 것이 여럿이면 번호가 작은 것."""
    rows = await db.execute(
        select(Query.text_hash, Query.id)
        .where(Query.dataset_id == dataset_id, QUERY_ACTIVE)
        .order_by(Query.id.desc())
    )
    return dict(rows.tuples().all())


async def _insert_batch(
    db: AsyncSession,
    *,
    import_row: Import,
    batch: tuple[sources.SourceRow, ...],
    mapping: FieldMappingCreate,
    document_ids: dict[str, int],
    query_ids: dict[str, int],
    split_chunks: dict[int, list[tuple[int, str]]],
    counts: _Counts,
) -> None:
    """한 묶음을 질의 · 문서 · 판정으로 넣고 가져오기 기록의 수를 늘린다. 커밋은 부른 쪽이 한다."""
    parsed_rows: list[ParsedRow] = []
    skipped: list[dict[str, Any]] = []
    for row in batch:
        parsed = parse_row(row, mapping=mapping)
        if isinstance(parsed, str):
            skipped.append({"line": row.line, "reason": parsed})
        else:
            parsed_rows.append(parsed)

    # 1) 문서: 처음 보는 것만 넣는다(문서만은 학습 글, 나머지는 본문으로 가린다).
    by_input = mapping.shape == Shape.DOCUMENTS
    new_documents: dict[str, DocText] = {}
    for parsed in parsed_rows:
        texts = [parsed.document] if parsed.document else [doc for doc, _ in parsed.judged]
        for doc in texts:
            doc_key = _merge_key(doc, by_input=by_input)
            if doc_key in document_ids or doc_key in new_documents:
                counts.documents_merged += 1
            else:
                new_documents[doc_key] = doc
    if new_documents:
        key_column = Document.input_hash if by_input else Document.text_hash
        inserted = await db.execute(
            insert(Document).returning(key_column, Document.id),
            [_document_row(import_row, doc) for doc in new_documents.values()],
        )
        document_ids.update(inserted.tuples().all())
        counts.documents_added += len(new_documents)

    # 2) 질의: 처음 보는 글만 넣는다.
    new_queries: dict[str, ParsedRow] = {}
    for parsed in parsed_rows:
        if parsed.query is None:
            continue
        key = make_text_hash(parsed.query)
        if key not in query_ids and key not in new_queries:
            new_queries[key] = parsed
    if new_queries:
        inserted = await db.execute(
            insert(Query).returning(Query.text_hash, Query.id),
            [
                {
                    "dataset_id": import_row.dataset_id,
                    "import_id": import_row.id,
                    "text": parsed.query,
                    "text_hash": key,
                    "answer": parsed.answer,
                    "extra": parsed.extra,
                }
                for key, parsed in new_queries.items()
            ],
        )
        query_ids.update(dict(inserted.tuples().all()))
        counts.queries_added += len(new_queries)

    # 3) 판정: 같은 쌍은 묶음 안에서 먼저 합친다(한 문장이 같은 줄을 두 번 고칠 수 없다).
    #    나눈 원문의 판정은 원문과, 나누기처럼 답이 든 청크에 함께 적는다.
    merged: dict[tuple[int, int], tuple[int, bool, float]] = {}
    # 원본 줄이 준 판정 (청크에 함께 적은 것은 세지 않는다)
    row_keys: set[tuple[int, int]] = set()
    for parsed in parsed_rows:
        if parsed.query is None:
            continue
        query_id = query_ids[make_text_hash(parsed.query)]
        for doc, grade in parsed.judged:
            document_id = document_ids[_merge_key(doc, by_input=by_input)]
            _merge_judgment(merged, (query_id, document_id), grade, parsed.query, doc.text)
            row_keys.add((query_id, document_id))
            pieces = split_chunks.get(document_id)
            if pieces:
                texts = [piece for _, piece in pieces]
                piece_id, piece = pieces[
                    best_chunk(texts, query=parsed.query, answer=parsed.answer)
                ]
                _merge_judgment(merged, (query_id, piece_id), grade, parsed.query, piece)
    for chunk in itertools.batched(merged.items(), JUDGMENT_INSERT_ROWS):
        await db.execute(_judgment_upsert(import_row, chunk))
    counts.judgments_added += len(row_keys)
    counts.conflicts += sum(1 for key in row_keys if merged[key][1])

    imports_service.add_batch(import_row, added=len(parsed_rows), skipped=skipped)


def _merge_judgment(
    merged: dict[tuple[int, int], tuple[int, bool, float]],
    key: tuple[int, int],
    grade: int,
    query: str,
    document: str,
) -> None:
    """묶음 안의 판정 하나를 더한다. 같은 쌍이면 높은 등급을 남기고, 정답 · 오답이 엇갈리면 충돌로 둔다."""
    if key in merged:
        old_grade, old_conflict, overlap = merged[key]
        is_conflict = old_conflict or (old_grade >= POSITIVE_MIN_GRADE) != (
            grade >= POSITIVE_MIN_GRADE
        )
        merged[key] = (max(old_grade, grade), is_conflict, overlap)
    else:
        merged[key] = (grade, False, lexical_overlap(query, document))


def _merge_key(doc: DocText, *, by_input: bool) -> str:
    """같은 문서로 합치는 열쇠: 학습 글 해시(문서만) 또는 본문 해시."""
    return make_text_hash(doc.input_text() if by_input else doc.text)


def _document_row(import_row: Import, doc: DocText) -> dict[str, Any]:
    """넣을 문서 한 줄. 표시(marks)는 반복 구간 없이 먼저 재고, 살피기가 다시 잰다."""
    training = doc.input_text()
    marks: dict[str, Any] = {}
    if has_broken_text(doc.text):
        marks["broken"] = True
    reason = skip_generation_reason(doc.text)
    if reason is not None:
        marks["pick"] = reason
    return {
        "dataset_id": import_row.dataset_id,
        "import_id": import_row.id,
        "doc_key": doc.doc_key,
        "title": doc.title,
        "header": doc.header,
        "group_key": doc.group_key,
        "text": doc.text,
        "text_hash": make_text_hash(doc.text),
        "input_text": None if training == doc.text else training,
        "input_hash": make_text_hash(training),
        "token_count": estimate_tokens(training),
        "marks": marks,
        "extra": doc.extra,
    }


def _judgment_upsert(
    import_row: Import, chunk: tuple[tuple[tuple[int, int], tuple[int, bool, float]], ...]
) -> Any:
    """판정 묶음을 넣는 문장. 이미 있는 쌍은 높은 등급을 남기고, 정답 · 오답이 엇갈리면 충돌로 적는다."""
    statement = pg_insert(Judgment).values(
        [
            {
                "query_id": query_id,
                "document_id": document_id,
                "dataset_id": import_row.dataset_id,
                "grade": grade,
                "source": JudgmentSource.ORIGINAL.value,
                "conflict": conflict,
                "overlap": overlap,
                "import_id": import_row.id,
            }
            for (query_id, document_id), (grade, conflict, overlap) in chunk
        ]
    )
    existing = Judgment.__table__.c
    return statement.on_conflict_do_update(
        index_elements=["query_id", "document_id"],
        set_={
            "grade": text("GREATEST(retrieval_judgments.grade, excluded.grade)"),
            "conflict": existing.conflict
            | statement.excluded.conflict
            | (
                (existing.grade >= POSITIVE_MIN_GRADE)
                != (statement.excluded.grade >= POSITIVE_MIN_GRADE)
            ),
        },
    )
