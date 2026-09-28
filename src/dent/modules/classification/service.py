"""분류 모듈의 업무 규칙: 분류 데이터셋 요약 · 지우기, 라벨, 가져오기 시작.

데이터셋 목록(system_datasets)과 가져오기 기록(system_imports)은 시스템 층의 것이다. 분류 모듈은 그
데이터셋에 라벨과 문장을 둔다. 같은 모듈 안에서 문장은 records.py, 진단은 overview.py,
가져오기 실행은 importing.py, 필드 맞춤 짐작은 mapping.py가 맡는다.
문장의 상태 조건(IS_ACTIVE 등)은 여기 한 곳에 두고 모두 가져다 쓴다.
"""

from dataclasses import dataclass

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification.models import (
    DEFAULT_BALANCE_TARGET,
    MAP_BUILDING_STATUSES,
    DatasetSettings,
    Label,
    Map,
    Record,
)
from dent.modules.classification.schemas import (
    ImportCreate,
    LabelCreate,
    LabelUpdate,
    SettingsUpdate,
)
from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system import sources
from dent.system.db import flush_or_conflict
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.models import IMPORTING_STATUSES, Dataset, Import

# 이 모듈의 이름. 데이터셋 목록의 module, 작업 대기열의 module, API 주소, 테이블 앞머리가 모두 이 이름이다.
MODULE_NAME = "classification"

LABEL_NAME_TAKEN_MESSAGE = "같은 이름의 라벨이 이미 있습니다."
DATASET_MAPPING_MESSAGE = (
    "의미 지도를 만드는 중인 데이터셋은 지울 수 없습니다. 지도 만들기를 멈춘 뒤 지우세요."
)

# 문장 상태 조건. 휴지통 밖(active) 가운데 뺀 이유가 없으면 학습에 쓰는 것(included)이다.
IS_ACTIVE = Record.trashed_at.is_(None)
IS_TRASHED = Record.trashed_at.is_not(None)
IS_INCLUDED = and_(IS_ACTIVE, Record.exclude_reason.is_(None))
IS_EXCLUDED = and_(IS_ACTIVE, Record.exclude_reason.is_not(None))

# 도우미가 만든 새 문장의 표시 (extra에 이 열쇠 · 값). 분할이 없어 새로 만든 문장을 출처로 가려 두고,
# 내보내기의 출처 칸(synthetic)이 이것으로 가른다.
NEW_SENTENCE_EXTRA = {"도우미": "새 문장"}
IS_SYNTHETIC = Record.extra.contains(NEW_SENTENCE_EXTRA)


@dataclass(frozen=True)
class DatasetSummary:
    """데이터셋 한 건과 그 개수 요약. 목록의 한 줄이 된다."""

    # 데이터셋 (시스템의 데이터셋 목록 한 줄)
    dataset: Dataset

    # 휴지통에 없는 문장 수
    record_count: int

    # 학습에 쓰는 문장 수
    included_count: int

    # 학습에서 뺀 문장 수
    excluded_count: int

    # 휴지통에 있는 문장 수
    trash_count: int

    # 라벨 수
    label_count: int

    # 가져오기가 대기 중이거나 실행 중인지
    importing: bool


@dataclass(frozen=True)
class LabelSummary:
    """라벨 한 건과 학습에 쓰는 문장 수."""

    # 라벨
    label: Label

    # 학습에 쓰는 문장 수
    record_count: int


@dataclass(frozen=True)
class DatasetDetail:
    """데이터셋 하나의 자세한 정보."""

    # 개수 요약
    summary: DatasetSummary

    # 라벨들 (이름 순)
    labels: list[LabelSummary]

    # 도우미 실행 설정 (줄이 없으면 기본값)
    settings: DatasetSettings


# ---------- 데이터셋 ----------


async def list_datasets(db: AsyncSession) -> list[DatasetSummary]:
    """분류 데이터셋 목록을 개수 요약과 함께 돌려준다. 최근에 바뀐 것부터(같으면 번호가 큰 것부터)."""
    datasets = await datasets_service.list_datasets(db, module=MODULE_NAME)
    return await _summarize(db, datasets)


async def get_dataset(db: AsyncSession, *, dataset_id: int) -> Dataset:
    """분류 데이터셋 하나. 없거나 다른 모듈의 데이터셋이면 NotFoundError."""
    return await datasets_service.get_dataset(db, dataset_id=dataset_id, module=MODULE_NAME)


async def delete_dataset(db: AsyncSession, *, dataset_id: int) -> None:
    """데이터셋과 그 안의 문장 · 라벨 · 의미 지도 · 가져오기 기록을 모두 지운다.

    없으면 NotFoundError, 가져오거나 의미 지도를 만드는 중이면 ConflictError.
    임베딩 캐시는 다른 데이터셋과 같이 쓰므로 남긴다.
    """
    await get_dataset(db, dataset_id=dataset_id)
    is_mapping = await db.scalar(
        select(exists().where(Map.dataset_id == dataset_id, Map.status.in_(MAP_BUILDING_STATUSES)))
    )
    if is_mapping:
        raise ConflictError(DATASET_MAPPING_MESSAGE)
    # 문장 · 라벨 · 지도(점까지)는 데이터셋을 참조하는 외래 키(CASCADE)로 함께 지워진다.
    await datasets_service.delete_dataset(db, dataset_id=dataset_id, module=MODULE_NAME)
    await db.commit()


async def get_dataset_detail(db: AsyncSession, *, dataset_id: int) -> DatasetDetail:
    """데이터셋 하나의 개수 요약과 라벨. 없으면 NotFoundError."""
    dataset = await get_dataset(db, dataset_id=dataset_id)
    [summary] = await _summarize(db, [dataset])
    labels = await list_labels(db, dataset_id=dataset_id)
    settings = await get_settings(db, dataset_id=dataset_id)
    return DatasetDetail(summary=summary, labels=labels, settings=settings)


# ---------- 도우미 실행 설정 ----------


async def get_settings(db: AsyncSession, *, dataset_id: int) -> DatasetSettings:
    """데이터셋의 도우미 실행 설정. 고친 적이 없으면 저장하지 않은 기본값을 돌려준다."""
    settings = await db.get(DatasetSettings, dataset_id, populate_existing=True)
    if settings is None:
        return DatasetSettings(dataset_id=dataset_id, balance_target=DEFAULT_BALANCE_TARGET)
    return settings


async def update_settings(
    db: AsyncSession, *, dataset_id: int, data: SettingsUpdate
) -> DatasetSettings:
    """도우미 실행 설정을 고친다(줄이 없으면 만든다). 데이터셋이 없으면 NotFoundError.

    데이터는 그대로라 데이터셋 수정 시각을 건드리지 않는다(건드리면 뜻 분석이 '분석 이후 변경'이 된다).
    """
    await get_dataset(db, dataset_id=dataset_id)
    settings = await db.get(DatasetSettings, dataset_id)
    if settings is None:
        settings = DatasetSettings(dataset_id=dataset_id, balance_target=DEFAULT_BALANCE_TARGET)
        db.add(settings)
    for name, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(settings, name, value)
    await db.commit()
    return await get_settings(db, dataset_id=dataset_id)


# ---------- 라벨 ----------


async def list_labels(db: AsyncSession, *, dataset_id: int) -> list[LabelSummary]:
    """데이터셋의 라벨을 이름 순으로, 학습에 쓰는 문장 수와 함께 돌려준다."""
    labels = (
        await db.scalars(
            select(Label).where(Label.dataset_id == dataset_id).order_by(Label.name, Label.id)
        )
    ).all()
    counts = await _count_included_by_label(db, dataset_id=dataset_id)
    return [LabelSummary(label=label, record_count=counts.get(label.id, 0)) for label in labels]


async def get_label(db: AsyncSession, *, label_id: int) -> Label:
    """라벨 하나. 없으면 NotFoundError."""
    query = select(Label).where(Label.id == label_id).execution_options(populate_existing=True)
    label = await db.scalar(query)
    if label is None:
        raise NotFoundError("라벨을 찾을 수 없습니다.")
    return label


async def create_label(db: AsyncSession, *, dataset_id: int, data: LabelCreate) -> LabelSummary:
    """라벨을 만든다. 데이터셋이 없으면 NotFoundError, 이름이 겹치면 ConflictError."""
    await get_dataset(db, dataset_id=dataset_id)
    if await _label_name_taken(db, dataset_id=dataset_id, name=data.name):
        raise ConflictError(LABEL_NAME_TAKEN_MESSAGE)
    label = Label(dataset_id=dataset_id, name=data.name, description=data.description)
    db.add(label)
    await flush_or_conflict(db, message=LABEL_NAME_TAKEN_MESSAGE)
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    # 새 라벨에는 아직 문장이 없다.
    return LabelSummary(label=await get_label(db, label_id=label.id), record_count=0)


async def update_label(db: AsyncSession, *, label_id: int, data: LabelUpdate) -> LabelSummary:
    """라벨 이름·설명을 고친다. 없으면 NotFoundError, 이름이 겹치면 ConflictError."""
    label = await get_label(db, label_id=label_id)
    is_renaming = data.name is not None and data.name != label.name
    if is_renaming and await _label_name_taken(db, dataset_id=label.dataset_id, name=data.name):
        raise ConflictError(LABEL_NAME_TAKEN_MESSAGE)
    if data.name is not None:
        label.name = data.name
    if data.description is not None:
        label.description = data.description
    await flush_or_conflict(db, message=LABEL_NAME_TAKEN_MESSAGE)
    await datasets_service.touch_dataset(db, dataset_id=label.dataset_id)
    await db.commit()

    counts = await _count_included_by_label(db, dataset_id=label.dataset_id)
    return LabelSummary(label=label, record_count=counts.get(label.id, 0))


# ---------- 가져오기 시작 ----------


async def create_import(db: AsyncSession, *, data: ImportCreate) -> Import:
    """가져오기를 대기열에 넣는다. 새 데이터셋이면 함께 만들고, 기록과 작업을 한 번에 저장한다.

    데이터셋이 없으면 NotFoundError, 새 이름이 겹치면 ConflictError,
    입력이 모자라면 InvalidInputError, 올린 파일이 없으면 NotFoundError.
    """
    has_dataset_id = data.dataset_id is not None
    has_new_name = data.new_dataset_name is not None
    if has_dataset_id == has_new_name:
        raise InvalidInputError("기존 데이터셋 번호와 새 데이터셋 이름 가운데 하나만 주세요.")
    source_name, options = await sources.describe_source(db, data=data)
    options |= {"mapping": data.mapping.model_dump()}

    if data.dataset_id is not None:
        dataset = await get_dataset(db, dataset_id=data.dataset_id)
    else:
        dataset = await datasets_service.create_dataset(
            db, module=MODULE_NAME, name=str(data.new_dataset_name)
        )

    import_row = await imports_service.create_import(
        db,
        module=MODULE_NAME,
        dataset_id=dataset.id,
        source=data.source,
        source_name=source_name,
        options=options,
    )
    await db.commit()
    return await imports_service.get_import(db, import_id=import_row.id)


# ---------- 안에서만 쓰는 함수 ----------


async def _summarize(db: AsyncSession, datasets: list[Dataset]) -> list[DatasetSummary]:
    """데이터셋마다 문장 수·라벨 수·가져오는 중인지를 센다. 데이터셋 수와 상관없이 쿼리 세 번."""
    dataset_ids = [dataset.id for dataset in datasets]
    record_query = (
        select(
            Record.dataset_id,
            func.count().filter(IS_ACTIVE),
            func.count().filter(IS_INCLUDED),
            func.count().filter(IS_EXCLUDED),
            func.count().filter(IS_TRASHED),
        )
        .where(Record.dataset_id.in_(dataset_ids))
        .group_by(Record.dataset_id)
    )
    record_counts = {row[0]: row[1:] for row in (await db.execute(record_query)).all()}

    label_query = (
        select(Label.dataset_id, func.count())
        .where(Label.dataset_id.in_(dataset_ids))
        .group_by(Label.dataset_id)
    )
    label_counts = dict((await db.execute(label_query)).tuples().all())

    importing_query = select(Import.dataset_id).where(
        Import.dataset_id.in_(dataset_ids),
        Import.status.in_(IMPORTING_STATUSES),
    )
    importing_ids = set((await db.scalars(importing_query)).all())

    summaries = []
    for dataset in datasets:
        active, included, excluded, trashed = record_counts.get(dataset.id, (0, 0, 0, 0))
        summaries.append(
            DatasetSummary(
                dataset=dataset,
                record_count=active,
                included_count=included,
                excluded_count=excluded,
                trash_count=trashed,
                label_count=label_counts.get(dataset.id, 0),
                importing=dataset.id in importing_ids,
            )
        )
    return summaries


async def _count_included_by_label(db: AsyncSession, *, dataset_id: int) -> dict[int, int]:
    """라벨별 학습에 쓰는 문장 수 {라벨 번호: 수}."""
    query = (
        select(Record.label_id, func.count())
        .where(Record.dataset_id == dataset_id, IS_INCLUDED, Record.label_id.is_not(None))
        .group_by(Record.label_id)
    )
    return dict((await db.execute(query)).tuples().all())


async def _label_name_taken(db: AsyncSession, *, dataset_id: int, name: str | None) -> bool:
    """데이터셋 안에 같은 이름의 라벨이 있는지."""
    found = await db.scalar(
        select(Label.id).where(Label.dataset_id == dataset_id, Label.name == name)
    )
    return found is not None
