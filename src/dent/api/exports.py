"""내보낸 파일 API (/api/v1/exports). 내보내기를 시작하는 것은 모듈 API(/api/v1/{모듈 이름}/…/exports)다."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import exports as exports_service
from dent.system.db import get_db
from dent.system.schemas import ExportRead

router = APIRouter(prefix="/exports", tags=["내보내기"])

Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def list_exports(dataset_id: Annotated[int, Query(ge=1)], db: Db) -> list[ExportRead]:
    """데이터셋의 내보내기 기록 (최근 것부터)."""
    exports = await exports_service.list_exports(db, dataset_id=dataset_id)
    return [ExportRead.model_validate(export) for export in exports]


@router.get("/{export_id}")
async def get_export(export_id: int, db: Db) -> ExportRead:
    """내보내기 한 번. 없으면 404."""
    export = await exports_service.get_export(db, export_id=export_id)
    return ExportRead.model_validate(export)


@router.get("/{export_id}/file")
async def download_export(export_id: int, db: Db) -> FileResponse:
    """내보낸 파일 내려받기. 기록이 없거나 파일이 없으면(만드는 중 · 보관 기간 지남) 404."""
    export, path = await exports_service.find_file(db, export_id=export_id)
    return FileResponse(path, filename=export.file_name, media_type="application/octet-stream")
