"""작업 실행기 API (/api/v1/workers). 화면이 실행기 수를 보고 바꾸며, 번호마다 상태와 도는 작업을 읽는다.

수를 저장하면 관리 프로세스가 몇 초 안에 실행기를 늘리거나(빈 번호를 띄움) 줄인다(큰 번호부터 하던 작업을 끝내면 종료).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import workers as workers_service
from dent.system.config import get_settings
from dent.system.db import get_db
from dent.system.schemas import JobRead, WorkerSlotRead, WorkersRead, WorkersUpdate

router = APIRouter(prefix="/workers", tags=["작업 실행기"])

Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def get_workers(db: Db) -> WorkersRead:
    """정한 실행기 수 · 번호마다 상태와 도는 작업 · 도는 작업과 대기 작업 수."""
    return _workers_read(await workers_service.read_workers(db, settings=get_settings()))


@router.put("")
async def update_workers(data: WorkersUpdate, db: Db) -> WorkersRead:
    """실행기 수를 바꾼다. 범위 밖이면 422."""
    await workers_service.set_worker_count(db, count=data.count)
    return _workers_read(await workers_service.read_workers(db, settings=get_settings()))


def _workers_read(view: workers_service.WorkersView) -> WorkersRead:
    """응답 모양으로 바꾼다."""
    return WorkersRead(
        count=view.count,
        min_count=workers_service.MIN_WORKERS,
        max_count=workers_service.MAX_WORKERS,
        managed=view.managed,
        running=view.running,
        busy=view.busy,
        queued=view.queued,
        slots=[
            WorkerSlotRead(
                slot=slot.slot,
                state=slot.state.value,
                job=None if slot.job is None else JobRead.model_validate(slot.job),
            )
            for slot in view.slots
        ],
    )
