"""데이터셋 목록(system_datasets). 모든 모듈의 데이터셋을 한 곳에 적는다.

이름은 모듈 안에서 겹치지 않는다. 데이터셋 안에 무엇이 있는지(문장·라벨 등)는 모듈이 안다.
데이터셋을 지우면 그 데이터셋을 참조하는 줄(가져오기 기록, 모듈의 문장·라벨 등)이 외래 키(CASCADE)로 함께 지워진다.
"""

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system.db import flush_or_conflict
from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.models import IMPORTING_STATUSES, Dataset, Import
from dent.system.schemas import DatasetUpdate

DATASET_NAME_TAKEN_MESSAGE = "같은 이름의 데이터셋이 이미 있습니다."
DATASET_NOT_FOUND_MESSAGE = "데이터셋을 찾을 수 없습니다."
DATASET_IMPORTING_MESSAGE = (
    "가져오는 중인 데이터셋은 지울 수 없습니다. 가져오기가 끝난 뒤 지우세요."
)


async def list_datasets(db: AsyncSession, *, module: str | None = None) -> list[Dataset]:
    """데이터셋 목록. module을 주면 그 모듈 것만. 최근에 바뀐 것부터(같으면 번호가 큰 것부터)."""
    query = select(Dataset).order_by(Dataset.updated_at.desc(), Dataset.id.desc())
    if module is not None:
        query = query.where(Dataset.module == module)
    return list((await db.scalars(query)).all())


async def get_dataset(db: AsyncSession, *, dataset_id: int, module: str | None = None) -> Dataset:
    """데이터셋 하나. 없거나, module을 줬는데 다른 모듈의 것이면 NotFoundError."""
    # populate_existing: 같은 세션에서 방금 고친 값(수정 시각 등)을 DB에서 새로 읽는다.
    query = (
        select(Dataset).where(Dataset.id == dataset_id).execution_options(populate_existing=True)
    )
    dataset = await db.scalar(query)
    is_other_module = dataset is not None and module is not None and dataset.module != module
    if dataset is None or is_other_module:
        raise NotFoundError(DATASET_NOT_FOUND_MESSAGE)
    return dataset


async def create_dataset(db: AsyncSession, *, module: str, name: str) -> Dataset:
    """새 데이터셋을 만든다. 같은 모듈에 같은 이름이 있으면 ConflictError.

    커밋은 부른 쪽이 한다(가져오기 기록과 한 번에 저장하려고).
    """
    if await _name_taken(db, module=module, name=name):
        raise ConflictError(DATASET_NAME_TAKEN_MESSAGE)
    dataset = Dataset(module=module, name=name)
    db.add(dataset)
    await flush_or_conflict(db, message=DATASET_NAME_TAKEN_MESSAGE)
    return dataset


async def update_dataset(db: AsyncSession, *, dataset_id: int, data: DatasetUpdate) -> Dataset:
    """데이터셋 이름·설명을 고친다. 없으면 NotFoundError, 같은 모듈에 같은 이름이 있으면 ConflictError."""
    dataset = await get_dataset(db, dataset_id=dataset_id)
    is_renaming = data.name is not None and data.name != dataset.name
    if is_renaming and await _name_taken(db, module=dataset.module, name=data.name):
        raise ConflictError(DATASET_NAME_TAKEN_MESSAGE)
    if data.name is not None:
        dataset.name = data.name
    if data.description is not None:
        dataset.description = data.description
    await flush_or_conflict(db, message=DATASET_NAME_TAKEN_MESSAGE)
    await db.commit()
    return await get_dataset(db, dataset_id=dataset_id)


async def delete_dataset(db: AsyncSession, *, dataset_id: int, module: str) -> None:
    """데이터셋을 지운다. 없거나 다른 모듈의 것이면 NotFoundError, 가져오는 중이면 ConflictError.

    가져오기 기록과 모듈의 줄은 외래 키(CASCADE)로 함께 지워진다(문장 12만 7천 건에 0.3초, 2026-09-25 측정).
    커밋은 부른 쪽이 한다(모듈이 자기 확인과 한 번에 지우려고).
    """
    await get_dataset(db, dataset_id=dataset_id, module=module)
    is_importing = await db.scalar(
        select(
            exists().where(Import.dataset_id == dataset_id, Import.status.in_(IMPORTING_STATUSES))
        )
    )
    if is_importing:
        raise ConflictError(DATASET_IMPORTING_MESSAGE)
    await db.execute(delete(Dataset).where(Dataset.id == dataset_id))


async def touch_dataset(db: AsyncSession, *, dataset_id: int) -> None:
    """데이터셋의 수정 시각을 지금으로 적는다. 커밋은 부른 쪽이 한다."""
    await db.execute(
        update(Dataset)
        .where(Dataset.id == dataset_id)
        .values(updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def _name_taken(db: AsyncSession, *, module: str, name: str | None) -> bool:
    """같은 모듈에 같은 이름의 데이터셋이 있는지."""
    found = await db.scalar(
        select(Dataset.id).where(Dataset.module == module, Dataset.name == name)
    )
    return found is not None
