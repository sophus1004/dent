"""가져오기 기록 API (/api/v1/imports). 가져오기를 시작하는 것은 모듈 API(/api/v1/{모듈 이름}/imports)다."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import imports as imports_service
from dent.system.db import get_db
from dent.system.schemas import ImportRead

router = APIRouter(prefix="/imports", tags=["가져오기"])


@router.get("/{import_id}")
async def get_import(import_id: int, db: Annotated[AsyncSession, Depends(get_db)]) -> ImportRead:
    """가져오기 기록 하나. 없으면 404."""
    import_row = await imports_service.get_import(db, import_id=import_id)
    return ImportRead.model_validate(import_row)
