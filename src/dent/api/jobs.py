"""작업 API (/api/v1/jobs). 화면이 가져오기·분석 같은 긴 작업의 진행률을 묻고, 작업 기록 화면이 목록을 읽는다.

작업 대기열(system_jobs)은 시스템 층의 것이라 어느 모듈에도 속하지 않는다.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import jobs as job_service
from dent.system.db import get_db
from dent.system.models import JobStatus
from dent.system.schemas import JobCountsRead, JobPageRead, JobRead

router = APIRouter(prefix="/jobs", tags=["작업"])

Db = Annotated[AsyncSession, Depends(get_db)]

# 작업 목록 한 쪽의 기본 줄 수
DEFAULT_JOB_PAGE_SIZE = 50

# 시간 거르기(hours)의 상한. 끝난 작업 기록을 남겨 두는 기간과 같다.
MAX_HOURS = int(job_service.FINISHED_JOB_RETENTION / timedelta(hours=1))


@router.get("")
async def list_jobs(
    db: Db,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    module: str | None = None,
    kind: str | None = None,
    dataset_id: int | None = None,
    hours: Annotated[int | None, Query(ge=1, le=MAX_HOURS)] = None,
    finished_after: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=job_service.MAX_JOB_PAGE_SIZE)] = DEFAULT_JOB_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobPageRead:
    """작업 목록. status는 여러 번 줄 수 있다. hours는 최근 n시간에 넣은 것, finished_after는 그 뒤에 끝난 것."""
    since = datetime.now(UTC) - timedelta(hours=hours) if hours is not None else None
    page = await job_service.list_jobs(
        db,
        statuses=status or (),
        module=module,
        kind=kind,
        dataset_id=dataset_id,
        since=since,
        finished_after=finished_after,
        limit=limit,
        offset=offset,
    )
    return JobPageRead(
        items=[JobRead.model_validate(job) for job in page.items],
        total=page.total,
        counts=JobCountsRead(**page.counts),
    )


@router.get("/{job_id}")
async def get_job(job_id: int, db: Db) -> JobRead:
    """작업 하나의 상태와 진행률. 없으면 404."""
    job = await job_service.get_job(db, job_id=job_id)
    return JobRead.model_validate(job)
