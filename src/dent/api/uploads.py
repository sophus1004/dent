"""올린 파일 API (/api/v1/uploads). 파일을 받아 저장하고, 가져오기 전에 앞쪽 줄을 보여 준다.

어느 모듈이든 가져오기 전에 이 API로 파일을 올리고, 받은 upload_id를 자기 가져오기 입력에 넣는다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import uploads as uploads_service
from dent.system.db import get_db
from dent.system.schemas import FilePreviewRead

router = APIRouter(prefix="/uploads", tags=["올린 파일"])

# 시트 이름의 최대 길이. 엑셀은 31자까지 받지만 다른 프로그램이 만든 파일도 있어 넉넉히 둔다.
SHEET_NAME_MAX_LENGTH = 200

Db = Annotated[AsyncSession, Depends(get_db)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(file: UploadFile, db: Db) -> FilePreviewRead:
    """파일 올리기(.csv·.tsv·.xlsx). 저장하고 미리 보기를 돌려준다."""
    return await uploads_service.save_upload(db, file_name=file.filename or "", read=file.read)


@router.get("/{upload_id}/preview")
async def preview_upload(
    upload_id: str,
    db: Db,
    sheet: Annotated[str | None, Query(max_length=SHEET_NAME_MAX_LENGTH)] = None,
) -> FilePreviewRead:
    """올린 파일 미리 보기. 엑셀이면 sheet로 다른 시트를 본다."""
    return await uploads_service.preview_upload(db, upload_id=upload_id, sheet=sheet)
