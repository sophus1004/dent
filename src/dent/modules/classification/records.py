"""문장 업무 규칙: 목록 거르기, 검색어에 맞는 번호 찾기, 하나 고치기, 여러 개 한 번에 바꾸기, 중복 정리,
휴지통 비우기(영구 삭제).

"같은 문장"은 text_hash가 같은 것이다. 해시는 시스템의 make_text_hash로 만든다(유니코드 정규화(NFC)하고
공백을 하나로 줄인 문장의 sha256). 같은 문장을 중복·충돌 가운데 무엇으로 볼지는 여기서 정한다.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    ScalarSelect,
    Select,
    and_,
    delete,
    distinct,
    func,
    select,
    tuple_,
    union,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dent.modules.classification import service as classification_service
from dent.modules.classification.models import (
    ExcludeReason,
    Label,
    LabelSuspect,
    Map,
    MapStatus,
    NearDuplicate,
    Record,
)
from dent.modules.classification.schemas import (
    ProblemFilter,
    RecordBulkUpdate,
    RecordStatusFilter,
    RecordUpdate,
)
from dent.system import datasets as datasets_service
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.text import make_text_hash

# 한 쪽에 보여 줄 수 있는 최대 문장 수
MAX_PAGE_SIZE = 200

# 이 글자 수보다 짧은 문장은 '짧은 문장' 문제로 본다. 인사말·단어 하나로는 의도를 알기 어렵다.
SHORT_TEXT_LENGTH = 5

# 같은 문장 무리로 찾는 문제. 목록에서 한 무리가 붙어 보이게 해시 순으로 늘어놓는다.
GROUPED_PROBLEMS = ("duplicate", "conflict")

# 중복 무리의 열쇠: 문장·라벨이 모두 같은 것. 중복 빼기는 무리마다 한 건만 남긴다.
# 라벨이 다르면 '라벨 충돌'로 따로 보고 사람이 고친다.
DUPLICATE_KEY = (Record.text_hash, Record.label_id)

STALE_RECORD_MESSAGE = "다른 곳에서 먼저 고쳤습니다. 새로 불러오세요."
FOREIGN_LABEL_MESSAGE = "이 데이터셋의 라벨이 아닙니다."

# 상태 거르기 이름 → 조건
STATUS_CONDITIONS: dict[str, ColumnElement[bool]] = {
    "active": classification_service.IS_ACTIVE,
    "included": classification_service.IS_INCLUDED,
    "excluded": classification_service.IS_EXCLUDED,
    "trash": classification_service.IS_TRASHED,
}


@dataclass(frozen=True)
class RecordItem:
    """화면에 보여 줄 문장 한 건: 문장과, 데이터셋 안의 같은 문장 무리 정보."""

    # 문장 (라벨을 미리 불러 둔 것)
    record: Record

    # 같은 문장인 다른 문장 수 (휴지통 제외)
    duplicate_count: int

    # 같은 문장이 다른 라벨로도 있는지 (휴지통 제외)
    has_conflict: bool


@dataclass(frozen=True)
class RecordPage:
    """문장 목록 한 쪽."""

    # 이 쪽의 문장들
    items: list[RecordItem]

    # 조건에 맞는 전체 문장 수
    total: int


async def list_records(
    db: AsyncSession,
    *,
    dataset_id: int,
    status: RecordStatusFilter,
    label_id: int | None,
    problem: ProblemFilter | None,
    q: str | None,
    same_as: int | None,
    limit: int,
    offset: int,
) -> RecordPage:
    """조건에 맞는 문장 한 쪽을 돌려준다. 데이터셋이 없으면 NotFoundError.

    문제 거르기(중복·충돌)의 무리는 고른 상태(status) 안에서 찾는다.
    예: status=included면 학습에 쓰는 문장 안에서만 무리를 찾는다(진단 화면의 수와 같다).
    same_as를 주면 그 문장과 같은 문장(글자까지 같은 것)만 본다.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    in_status = STATUS_CONDITIONS[status]
    conditions = [Record.dataset_id == dataset_id, in_status]
    if label_id is not None:
        conditions.append(Record.label_id == label_id)
    if q:
        conditions.append(Record.text.ilike(f"%{_escape_like(q)}%", escape="\\"))
    if problem is not None:
        conditions.append(_problem_condition(problem, dataset_id=dataset_id, in_status=in_status))
    if same_as is not None:
        # 이 데이터셋에 없는 번호면 해시가 비어(NULL) 아무것도 나오지 않는다.
        same_text = select(Record.text_hash).where(
            Record.id == same_as, Record.dataset_id == dataset_id
        )
        conditions.append(Record.text_hash == same_text.scalar_subquery())

    total = await db.scalar(select(func.count()).select_from(Record).where(*conditions))
    is_grouped = problem in GROUPED_PROBLEMS or same_as is not None
    order = (Record.text_hash, Record.id) if is_grouped else (Record.id,)
    query = (
        select(Record)
        .options(selectinload(Record.label))
        .where(*conditions)
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    )
    records = list((await db.scalars(query)).all())
    items = await _with_group_info(db, dataset_id=dataset_id, records=records)
    return RecordPage(items=items, total=int(total or 0))


async def search_record_ids(db: AsyncSession, *, dataset_id: int, q: str) -> list[int]:
    """휴지통 밖에서 q가 든 문장 번호들(작은 것부터). 목록의 검색과 같은 규칙이다. 데이터셋이 없으면 NotFoundError.

    의미 지도가 검색어에 맞는 점을 강조할 때 쓴다. 번호만 돌려주므로 수만 건이어도 가볍다.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    query = (
        select(Record.id)
        .where(
            Record.dataset_id == dataset_id,
            classification_service.IS_ACTIVE,
            Record.text.ilike(f"%{_escape_like(q)}%", escape="\\"),
        )
        .order_by(Record.id)
    )
    return list(await db.scalars(query))


async def get_record(db: AsyncSession, *, record_id: int) -> Record:
    """문장 하나를 라벨과 함께 돌려준다. 없으면 NotFoundError."""
    # populate_existing: 같은 세션에서 방금 UPDATE한 값을 DB에서 새로 읽는다.
    query = (
        select(Record)
        .options(selectinload(Record.label))
        .where(Record.id == record_id)
        .execution_options(populate_existing=True)
    )
    record = await db.scalar(query)
    if record is None:
        raise NotFoundError("문장을 찾을 수 없습니다.")
    return record


async def get_record_item(db: AsyncSession, *, record_id: int) -> RecordItem:
    """문장 하나를 같은 문장 무리 정보와 함께 돌려준다. 없으면 NotFoundError."""
    record = await get_record(db, record_id=record_id)
    [item] = await _with_group_info(db, dataset_id=record.dataset_id, records=[record])
    return item


async def update_record(db: AsyncSession, *, record_id: int, data: RecordUpdate) -> RecordItem:
    """문장·라벨을 고친다.

    없으면 NotFoundError, 그 사이 다른 곳에서 고쳤으면 ConflictError,
    다른 데이터셋의 라벨이면 InvalidInputError.
    """
    record = await get_record(db, record_id=record_id)
    is_stale = record.row_version != data.row_version
    if is_stale:
        raise ConflictError(STALE_RECORD_MESSAGE)

    changes: dict[str, Any] = {}
    if data.text is not None:
        changes["text"] = data.text
        changes["text_hash"] = make_text_hash(data.text)
    if data.label_id is not None:
        await check_label_belongs(db, label_id=data.label_id, dataset_id=record.dataset_id)
        changes["label_id"] = data.label_id

    # 확인과 저장 사이에 다른 요청이 먼저 고칠 수 있어서, 저장할 때도 수정 번호를 조건으로 건다.
    updated = await db.scalars(
        update(Record)
        .where(Record.id == record_id, Record.row_version == data.row_version)
        .values(**changes, row_version=Record.row_version + 1, updated_at=func.now())
        .returning(Record.id)
        .execution_options(synchronize_session=False)
    )
    if updated.first() is None:
        await db.rollback()
        raise ConflictError(STALE_RECORD_MESSAGE)
    await datasets_service.touch_dataset(db, dataset_id=record.dataset_id)
    await db.commit()
    return await get_record_item(db, record_id=record_id)


async def bulk_update(db: AsyncSession, *, dataset_id: int, data: RecordBulkUpdate) -> int:
    """여러 문장에 한 가지 일을 한다. 실제로 바뀐 수를 돌려준다.

    데이터셋이 없으면 NotFoundError, set_label에 값이 없거나 틀리면 InvalidInputError.
    이 데이터셋에 없는 번호와 이미 그 상태인 문장은 건드리지 않는다.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    needs_change, changes = await _bulk_change(db, dataset_id=dataset_id, data=data)
    changed_ids = await db.scalars(
        update(Record)
        .where(Record.dataset_id == dataset_id, Record.id.in_(data.record_ids), needs_change)
        .values(**changes, row_version=Record.row_version + 1, updated_at=func.now())
        .returning(Record.id)
        .execution_options(synchronize_session=False)
    )
    changed = len(changed_ids.all())
    if changed:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return changed


async def cleanup_duplicates(db: AsyncSession, *, dataset_id: int) -> int:
    """학습에 쓰는 문장에서 중복 무리(문장·라벨이 같음)마다 번호가 가장 작은 것만 남기고 뺀다.

    뺀 문장에는 이유 'duplicate'를 적어 되돌리기가 이것만 되돌리게 한다. 뺀 수를 돌려준다.
    데이터셋이 없으면 NotFoundError.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    # 무리 안 순번(번호 순)을 매겨 2번째부터가 뺄 문장이다.
    ranked = (
        select(
            Record.id,
            func.row_number()
            .over(partition_by=DUPLICATE_KEY, order_by=Record.id)
            .label("position"),
        )
        .where(Record.dataset_id == dataset_id, classification_service.IS_INCLUDED)
        .subquery()
    )
    extra_ids = select(ranked.c.id).where(ranked.c.position > 1)
    changed_ids = await db.scalars(
        update(Record)
        .where(Record.dataset_id == dataset_id, Record.id.in_(extra_ids))
        .values(
            exclude_reason=ExcludeReason.DUPLICATE,
            row_version=Record.row_version + 1,
            updated_at=func.now(),
        )
        .returning(Record.id)
        .execution_options(synchronize_session=False)
    )
    changed = len(changed_ids.all())
    if changed:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return changed


async def undo_duplicate_cleanup(db: AsyncSession, *, dataset_id: int) -> int:
    """중복 정리로 뺀 문장(이유 'duplicate')을 다시 학습에 넣는다. 되돌린 수를 돌려준다."""
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    changed_ids = await db.scalars(
        update(Record)
        .where(Record.dataset_id == dataset_id, Record.exclude_reason == ExcludeReason.DUPLICATE)
        .values(exclude_reason=None, row_version=Record.row_version + 1, updated_at=func.now())
        .returning(Record.id)
        .execution_options(synchronize_session=False)
    )
    changed = len(changed_ids.all())
    if changed:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return changed


async def empty_trash(db: AsyncSession, *, dataset_id: int) -> int:
    """휴지통의 문장을 모두 영구히 지우고 지운 수를 돌려준다. 데이터셋이 없으면 NotFoundError.

    되돌릴 수 없다. 의미 지도에 찍힌 그 문장의 점도 외래 키(CASCADE)로 함께 지워진다.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    deleted = await db.execute(
        delete(Record)
        .where(Record.dataset_id == dataset_id, classification_service.IS_TRASHED)
        .execution_options(synchronize_session=False)
    )
    if deleted.rowcount:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return deleted.rowcount


def done_map_id(dataset_id: int) -> ScalarSelect[int]:
    """데이터셋의 다 만든 뜻 분석(지도) 번호를 고르는 식. 없으면 NULL(아무 결과에도 맞지 않는다)."""
    return (
        select(Map.id)
        .where(Map.dataset_id == dataset_id, Map.status == MapStatus.DONE)
        .order_by(Map.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def near_duplicate_hashes(map_id: int | ScalarSelect[int]) -> Select[tuple[str]]:
    """뜻 분석 한 번에서 근접 중복 쌍에 든 문장 해시들(쌍의 앞 · 뒤 모두)."""
    front = select(NearDuplicate.text_hash_a).where(NearDuplicate.map_id == map_id)
    back = select(NearDuplicate.text_hash_b).where(NearDuplicate.map_id == map_id)
    return select(union(front, back).subquery().c[0])


def open_suspect_keys(map_id: int | ScalarSelect[int]) -> Select[tuple[str, int]]:
    """뜻 분석 한 번에서 아직 판단하지 않은 오라벨 의심의 (문장 해시, 그때 라벨)."""
    return select(LabelSuspect.text_hash, LabelSuspect.label_id).where(
        LabelSuspect.map_id == map_id, LabelSuspect.decision.is_(None)
    )


def included_problem_condition(problem: ProblemFilter, *, dataset_id: int) -> ColumnElement[bool]:
    """학습에 쓰는 문장 가운데 그 문제에 든 것의 조건 (진단 · 데이터 탭과 같은 규칙). 도우미가 쓴다."""
    in_scope = and_(Record.dataset_id == dataset_id, classification_service.IS_INCLUDED)
    condition = _problem_condition(
        problem, dataset_id=dataset_id, in_status=classification_service.IS_INCLUDED
    )
    return and_(in_scope, condition)


async def check_label_belongs(db: AsyncSession, *, label_id: int, dataset_id: int) -> None:
    """라벨이 이 데이터셋의 것인지 본다. 아니면 InvalidInputError."""
    label = await db.get(Label, label_id)
    belongs = label is not None and label.dataset_id == dataset_id
    if not belongs:
        raise InvalidInputError(FOREIGN_LABEL_MESSAGE)


# ---------- 안에서만 쓰는 함수 ----------


def _escape_like(text: str) -> str:
    """LIKE 검색에서 %, _, \\를 글자 그대로 찾게 한다."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _problem_condition(
    problem: ProblemFilter, *, dataset_id: int, in_status: ColumnElement[bool]
) -> ColumnElement[bool]:
    """문제 거르기 조건. 짧은 문장은 길이로, 근접 중복 · 오라벨 의심은 가장 최근 뜻 분석의 결과로,
    나머지는 같은 문장 무리로 찾는다(진단과 같은 규칙)."""
    if problem == "short":
        return func.char_length(Record.text) < SHORT_TEXT_LENGTH
    if problem == "near_duplicate":
        return Record.text_hash.in_(near_duplicate_hashes(done_map_id(dataset_id)))
    if problem == "suspect":
        # 오라벨 의심은 (문장, 그때 라벨)이 한 건이다. 라벨을 이미 바꾼 문장은 빠진다.
        return tuple_(Record.text_hash, Record.label_id).in_(
            open_suspect_keys(done_map_id(dataset_id))
        )

    in_scope = (Record.dataset_id == dataset_id, in_status)
    if problem == "duplicate":
        # 중복: 문장·라벨이 모두 같은 것이 2건 이상인 무리. 중복 빼기가 빼는 것과 같다.
        # (라벨이 빈 문장은 무리로 묶이지 않는다. 라벨 없이 들어오는 길이 없어 생기지 않는다.)
        duplicate_keys = (
            select(*DUPLICATE_KEY)
            .where(*in_scope)
            .group_by(*DUPLICATE_KEY)
            .having(func.count() > 1)
        )
        return tuple_(*DUPLICATE_KEY).in_(duplicate_keys)

    # 라벨 충돌: 라벨이 둘 이상. count(DISTINCT)는 정렬이 필요해 느려서 가장 작은 번호와 큰 번호를 견준다.
    texts = (
        select(Record.text_hash)
        .where(*in_scope)
        .group_by(Record.text_hash)
        .having(func.min(Record.label_id) != func.max(Record.label_id))
    )
    return Record.text_hash.in_(texts)


async def _with_group_info(
    db: AsyncSession, *, dataset_id: int, records: list[Record]
) -> list[RecordItem]:
    """문장마다 같은 문장이 몇 건 더 있는지, 다른 라벨로도 있는지 붙인다. 이 쪽 해시만 센다."""
    hashes = {record.text_hash for record in records}
    if not hashes:
        return []
    query = (
        select(Record.text_hash, func.count(), func.array_agg(distinct(Record.label_id)))
        .where(
            Record.dataset_id == dataset_id,
            classification_service.IS_ACTIVE,
            Record.text_hash.in_(hashes),
        )
        .group_by(Record.text_hash)
    )
    groups = {text_hash: (count, labels) for text_hash, count, labels in await db.execute(query)}

    items = []
    for record in records:
        active_count, label_ids = groups.get(record.text_hash, (0, []))
        # 휴지통 밖 문장이면 자기 자신도 센 수라서 하나를 뺀다.
        is_active = record.trashed_at is None
        others = active_count - 1 if is_active else active_count
        has_other_label = any(label_id != record.label_id for label_id in label_ids)
        items.append(
            RecordItem(record=record, duplicate_count=others, has_conflict=has_other_label)
        )
    return items


async def _bulk_change(
    db: AsyncSession, *, dataset_id: int, data: RecordBulkUpdate
) -> tuple[ColumnElement[bool], dict[str, Any]]:
    """일괄 작업의 (바꿀 필요가 있는 문장 조건, 바꿀 값). 이미 그 상태인 문장은 조건에서 빠진다."""
    if data.action == "exclude":
        needs_change = Record.exclude_reason.is_distinct_from(ExcludeReason.MANUAL)
        return needs_change, {"exclude_reason": ExcludeReason.MANUAL}
    if data.action == "include":
        return Record.exclude_reason.is_not(None), {"exclude_reason": None}
    if data.action == "trash":
        return Record.trashed_at.is_(None), {"trashed_at": func.now()}
    if data.action == "restore":
        return Record.trashed_at.is_not(None), {"trashed_at": None}
    # set_label
    if data.label_id is None:
        raise InvalidInputError("붙일 라벨(label_id)을 주세요.")
    await check_label_belongs(db, label_id=data.label_id, dataset_id=dataset_id)
    return Record.label_id.is_distinct_from(data.label_id), {"label_id": data.label_id}
