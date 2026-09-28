"""데이터셋 목록 API (/api/v1/datasets). 모든 모듈의 데이터셋을 한 곳에서 보고, 이름·설명을 고친다.

데이터셋 안의 데이터(문장·라벨·진단 등)는 모듈 API(/api/v1/{모듈 이름})가 다룬다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system.db import get_db
from dent.system.schemas import DatasetRead, DatasetUpdate, ImportRead

router = APIRouter(prefix="/datasets", tags=["데이터셋"])

Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def list_datasets(db: Db) -> list[DatasetRead]:
    """모든 모듈의 데이터셋 목록. 최근에 바뀐 것부터."""
    datasets = await datasets_service.list_datasets(db)
    return [DatasetRead.model_validate(dataset) for dataset in datasets]


@router.patch("/{dataset_id}")
async def update_dataset(dataset_id: int, data: DatasetUpdate, db: Db) -> DatasetRead:
    """데이터셋 이름·설명 고치기. 같은 모듈에 같은 이름이 있으면 409."""
    dataset = await datasets_service.update_dataset(db, dataset_id=dataset_id, data=data)
    return DatasetRead.model_validate(dataset)


@router.get("/{dataset_id}/imports")
async def list_imports(dataset_id: int, db: Db) -> list[ImportRead]:
    """데이터셋의 가져오기 기록. 최근 것부터."""
    import_rows = await imports_service.list_imports(db, dataset_id=dataset_id)
    return [ImportRead.model_validate(import_row) for import_row in import_rows]
