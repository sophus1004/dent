"""허깅페이스 API (/api/v1/huggingface). 가져오기 전에 데이터셋의 구성·분할과 앞쪽 줄을 보여 준다."""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from dent.system import huggingface as huggingface_service
from dent.system.schemas import HuggingFacePreviewCreate, HuggingFacePreviewRead

router = APIRouter(prefix="/huggingface", tags=["허깅페이스"])


def get_http_transport() -> httpx.AsyncBaseTransport | None:
    """허깅페이스에 붙을 때 쓸 전송 계층. 평소에는 None(실제 인터넷)이고 테스트가 가짜로 바꾼다."""
    return None


@router.post("/preview")
async def preview_huggingface(
    data: HuggingFacePreviewCreate,
    transport: Annotated[httpx.AsyncBaseTransport | None, Depends(get_http_transport)],
) -> HuggingFacePreviewRead:
    """허깅페이스 데이터셋 미리 보기: 구성·분할 목록, 라벨 이름, 앞쪽 몇 줄."""
    return await huggingface_service.preview(
        repo=data.repo, config=data.config, split=data.split, transport=transport
    )
