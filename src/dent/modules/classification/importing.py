"""가져오기 실행. 작업 실행기가 부른다.

원본은 시스템이 줄로 읽어 준다(dent.system.sources). 이 파일은 그 줄을 1만 줄씩 문장으로 넣고,
묶음마다 진행률을 적는다. 다시 돌려도 결과가 같다: 시작할 때 이 가져오기가 전에 넣은 문장을 먼저 지운다.
그래서 작업 실행기가 중간에 종료됐다 실행돼 같은 작업을 다시 해도 문장이 두 번 들어가지 않는다.

한 줄의 규칙
- 문장은 앞뒤 공백을 지운다. 비었으면 '빈 문장', TEXT_MAX_LENGTH보다 길면 '너무 긴 문장'.
  (건너뛴 줄은 줄 번호와 이유를 남긴다.)
- 라벨이 비었으면(허깅페이스 라벨 번호 -1 포함) '라벨 없음'. 처음 보는 라벨은 새로 만든다.
- train · valid · test를 나누지 않는다. 원본에 분할 열이 있거나 허깅페이스의 여러 분할을 가져와도 한 덩어리로 넣는다.
- 문장·라벨 말고 남는 열은 extra에 담는다.
- 같은 문장이 또 나와도 건너뛰지 않는다. 중복은 진단이 찾아 보여 준다.
"""

import itertools
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import delete, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification.models import LABEL_NAME_MAX_LENGTH, TEXT_MAX_LENGTH, Label, Record
from dent.modules.classification.schemas import FieldMappingCreate
from dent.system import imports as imports_service
from dent.system import jobs, sources, tables
from dent.system.db import get_engine
from dent.system.exceptions import InvalidInputError
from dent.system.models import Import
from dent.system.schemas import CellValue
from dent.system.text import make_text_hash

# 한 번에 넣는 줄 수. 묶음마다 커밋하고 진행률을 적는다.
BATCH_ROWS = 10_000

# 한 데이터셋의 라벨 수 상한. 이보다 많으면 분류 라벨이 아니라
# 문장이나 번호 칸을 라벨로 고른 것이다.
# 그대로 두면 줄마다 라벨이 생겨 라벨 표와 화면이 감당하지 못한다.
MAX_LABELS_PER_DATASET = 1_000

# 가져오기 기록의 result에 이 가져오기에서 새로 만든 라벨 이름들을 담는 열쇠
NEW_LABELS_KEY = "new_labels"

# 건너뛰는 이유
SKIP_EMPTY_TEXT = "빈 문장"
SKIP_TOO_LONG_TEXT = "너무 긴 문장"
SKIP_NO_LABEL = "라벨 없음"
SKIP_TOO_LONG_LABEL = "너무 긴 라벨"


@dataclass(frozen=True)
class ParsedRow:
    """문장으로 넣을 수 있는 한 줄."""

    # 문장
    text: str

    # 라벨 이름
    label: str

    # 남는 열들
    extra: dict[str, CellValue]


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
    await _delete_inserted_records(db, dataset_id=dataset_id, import_id=import_id)
    imports_service.start_import(import_row)
    # 전에 만든 라벨은 남아 있으므로 새 라벨 목록은 지우지 않고 이어서 쓴다.
    new_labels = import_row.result.get(NEW_LABELS_KEY, [])
    import_row.result = {**import_row.result, NEW_LABELS_KEY: new_labels}
    await db.commit()

    options = import_row.options
    mapping = FieldMappingCreate.model_validate(options["mapping"])
    async with sources.open_source(db, import_row, transport=transport) as source:
        _check_mapping(mapping, source.columns)
        import_row.rows_total = source.total_rows
        label_ids = await _load_label_ids(db, dataset_id=dataset_id)
        done = 0
        for batch in itertools.batched(source.rows, BATCH_ROWS):
            await _insert_batch(
                db,
                import_row=import_row,
                batch=batch,
                mapping=mapping,
                label_ids=label_ids,
            )
            done += len(batch)
            await jobs.set_progress(db, job_id=job_id, done=done, total=source.total_rows)
            await db.commit()

    await imports_service.finish_import(db, import_row, job_id=job_id)
    await db.commit()
    return import_row


async def fail_import(db: AsyncSession, *, import_id: int, job_id: int, message: str) -> None:
    """가져오기와 작업을 실패로 끝낸다. 반쯤 들어간 문장은 지워 데이터셋을 가져오기 전으로 둔다."""
    await db.rollback()
    import_row = await db.get(Import, import_id)
    if import_row is not None:
        await _delete_inserted_records(db, dataset_id=import_row.dataset_id, import_id=import_id)
    await imports_service.fail_import(db, import_id=import_id, job_id=job_id, message=message)


async def vacuum_records() -> None:
    """문장 표를 VACUUM ANALYZE 한다. 가져오기를 마친 뒤 작업 실행기가 부른다.

    막 넣은 줄은 DB가 청소해 '모두 보이는 페이지'로 적기 전까지 인덱스만 훑어 셀 수 없다.
    그러면 진단과 문제 목록이 표를 다시 뒤져 읽어 몇 배 느리다. 자동 청소를 기다리지 않고 바로 한다.
    """
    engine = get_engine()
    # VACUUM은 트랜잭션 안에서 돌 수 없어서, 문장마다 바로 확정하는 연결을 쓴다.
    async with engine.connect() as conn:
        autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(f"VACUUM (ANALYZE) {Record.__tablename__}"))


def parse_row(row: sources.SourceRow, *, mapping: FieldMappingCreate) -> ParsedRow | str:
    """원본 한 줄을 문장으로 바꾼다. 넣을 수 없는 줄이면 건너뛰는 이유(문자열)를 돌려준다."""
    text = tables.to_text(row.values.get(mapping.text))
    if not text:
        return SKIP_EMPTY_TEXT
    if len(text) > TEXT_MAX_LENGTH:
        return SKIP_TOO_LONG_TEXT
    label = tables.to_text(row.values.get(mapping.label))
    if not label:
        return SKIP_NO_LABEL
    if len(label) > LABEL_NAME_MAX_LENGTH:
        return SKIP_TOO_LONG_LABEL

    used_columns = {mapping.text, mapping.label}
    extra = {name: value for name, value in row.values.items() if name not in used_columns}
    return ParsedRow(
        text=text,
        label=label,
        extra=extra,
    )


# ---------- 안에서만 쓰는 함수 ----------


def _check_mapping(mapping: FieldMappingCreate, columns: list[str]) -> None:
    """필드 맞춤의 열이 원본에 있는지 본다. 없으면 InvalidInputError."""
    for name in (mapping.text, mapping.label):
        is_missing = name is not None and name not in columns
        if is_missing:
            raise InvalidInputError(
                f"'{name}' 열이 원본에 없습니다. 필드 맞추기를 다시 확인하세요."
            )


async def _insert_batch(
    db: AsyncSession,
    *,
    import_row: Import,
    batch: tuple[sources.SourceRow, ...],
    mapping: FieldMappingCreate,
    label_ids: dict[str, int],
) -> None:
    """한 묶음을 문장으로 넣고 가져오기 기록의 수를 늘린다. 커밋은 부른 쪽이 한다."""
    parsed_rows: list[ParsedRow] = []
    skipped: list[dict[str, Any]] = []
    for row in batch:
        parsed = parse_row(row, mapping=mapping)
        if isinstance(parsed, str):
            skipped.append({"line": row.line, "reason": parsed})
        else:
            parsed_rows.append(parsed)

    created = await _create_missing_labels(
        db,
        dataset_id=import_row.dataset_id,
        names=[parsed.label for parsed in parsed_rows],
        label_ids=label_ids,
    )
    if parsed_rows:
        await db.execute(
            insert(Record),
            [
                {
                    "dataset_id": import_row.dataset_id,
                    "import_id": import_row.id,
                    "label_id": label_ids[parsed.label],
                    "text": parsed.text,
                    "text_hash": make_text_hash(parsed.text),
                    "extra": parsed.extra,
                }
                for parsed in parsed_rows
            ],
        )

    imports_service.add_batch(import_row, added=len(parsed_rows), skipped=skipped)
    # JSONB 칼럼은 안의 값을 고쳐도 SQLAlchemy가 모르므로 새 사전으로 바꿔 넣는다.
    new_labels = [*import_row.result.get(NEW_LABELS_KEY, []), *created]
    import_row.result = {**import_row.result, NEW_LABELS_KEY: new_labels}


async def _load_label_ids(db: AsyncSession, *, dataset_id: int) -> dict[str, int]:
    """데이터셋의 라벨 {이름: 번호}."""
    rows = await db.execute(select(Label.name, Label.id).where(Label.dataset_id == dataset_id))
    return dict(rows.tuples().all())


async def _create_missing_labels(
    db: AsyncSession, *, dataset_id: int, names: list[str], label_ids: dict[str, int]
) -> list[str]:
    """처음 보는 라벨을 만들어 label_ids에 채운다. 새로 만든 이름을 처음 나온 순서로 돌려준다.

    라벨이 MAX_LABELS_PER_DATASET개를 넘게 되면 만들지 않고 InvalidInputError.
    """
    missing = [name for name in dict.fromkeys(names) if name not in label_ids]
    if not missing:
        return []
    is_too_many = len(label_ids) + len(missing) > MAX_LABELS_PER_DATASET
    if is_too_many:
        raise InvalidInputError(
            f"라벨이 {MAX_LABELS_PER_DATASET:,}개를 넘습니다. "
            "필드 맞추기에서 라벨 칸을 맞게 골랐는지 확인하세요."
        )
    # 화면에서 같은 이름의 라벨을 막 만들었을 수 있으므로, 이미 있으면 건너뛴다.
    inserted = await db.scalars(
        pg_insert(Label)
        .values([{"dataset_id": dataset_id, "name": name} for name in missing])
        .on_conflict_do_nothing(index_elements=["dataset_id", "name"])
        .returning(Label.name)
    )
    created_names = set(inserted.all())
    found = await db.execute(
        select(Label.name, Label.id).where(Label.dataset_id == dataset_id, Label.name.in_(missing))
    )
    label_ids.update(found.tuples().all())
    return [name for name in missing if name in created_names]


async def _delete_inserted_records(db: AsyncSession, *, dataset_id: int, import_id: int) -> None:
    """이 가져오기가 넣은 문장을 지운다. 커밋은 부른 쪽이 한다."""
    await db.execute(
        delete(Record)
        .where(Record.dataset_id == dataset_id, Record.import_id == import_id)
        .execution_options(synchronize_session=False)
    )
