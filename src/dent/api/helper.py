"""LLM 도우미 API (/api/v1/helper). 도우미 창이 실행 상태와 사건을 1초마다 이어 읽고, [멈추기]를 보낸다.

시작 · 되돌리기 · 허락은 무엇을 고치는지 아는 모듈의 API에 있다(예: /api/v1/classification/datasets/{id}/helper).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import helper as helper_service
from dent.system.db import get_db
from dent.system.schemas import HelperEventRead, HelperRunRead

router = APIRouter(prefix="/helper", tags=["도우미"])

Db = Annotated[AsyncSession, Depends(get_db)]

# 사건을 한 번에 읽는 기본 수
DEFAULT_EVENT_PAGE_SIZE = 200


@router.get("/runs/{run_id}")
async def get_run(run_id: int, db: Db) -> HelperRunRead:
    """실행 한 번의 상태 · 단계 줄 · 사용량. 없으면 404."""
    run = await helper_service.get_run(db, run_id=run_id)
    return HelperRunRead.model_validate(run)


@router.get("/runs/{run_id}/events")
async def list_events(
    run_id: int,
    db: Db,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[
        int, Query(ge=1, le=helper_service.MAX_EVENT_PAGE_SIZE)
    ] = DEFAULT_EVENT_PAGE_SIZE,
) -> list[HelperEventRead]:
    """after 번호 다음 사건들(번호 순서). 실행이 없으면 404."""
    events = await helper_service.list_events(db, run_id=run_id, after_id=after, limit=limit)
    return [HelperEventRead.model_validate(event) for event in events]


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: int, db: Db) -> HelperRunRead:
    """[멈추기]. 도우미는 다음 도구를 부르기 전에 멈춘다. 도는 중이 아니면 409."""
    run = await helper_service.request_stop(db, run_id=run_id)
    return HelperRunRead.model_validate(run)
