"""작업 대기열. API 서버가 작업을 넣고, 작업 실행기가 꺼내 처리한다.

작업 실행기는 하나만 뜬다. PostgreSQL 권고 잠금(advisory lock)으로 자리를 잡고,
API 서버는 그 잠금이 잡혀 있는지로 작업 실행기가 실행 중인지 안다.

여러 단계로 도는 작업(예: 임베딩 → 좌표 계산 → 저장)은 단계 이름을 result의 phase에 적는다.
화면은 진행률(progress_done · progress_total)과 함께 이것을 보인다. 끝나면 finish_job이 result를 덮어쓴다.
멈추기: 대기 중인 작업은 바로 멈춘다(canceled). 도는 작업은 cancel_requested를 적어 두고,
처리 함수가 묶음 사이마다 보고 스스로 멈춘다.

작업 기록: 작업마다 다루는 데이터셋(번호 · 넣을 때 이름)과 사건 목록(events)을 둔다. 사건은 시작 · 단계 ·
다시 보내기 · 쓴 연결을 시각 순으로 쌓고 덮어쓰지 않는다. 작업 기록 화면이 목록(list_jobs)과 함께 읽는다.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, text, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from dent.system import uploads
from dent.system.db import get_sessionmaker
from dent.system.exceptions import NotFoundError
from dent.system.models import Dataset, Job, JobStatus

logger = logging.getLogger("dent.jobs")

# 작업 실행기 자리를 잡는 권고 잠금의 열쇠 두 개. 앞의 것은 'dent'의 ASCII 값이다.
WORKER_LOCK_KEYS = (0x64656E74, 1)

# 끊긴 작업을 다시 시작하는 최대 횟수. 넘으면 실패로 둔다.
MAX_ATTEMPTS = 3

# 끝난 작업 기록을 남겨 두는 날 수
FINISHED_JOB_RETENTION = timedelta(days=90)

# 정리 작업을 도는 간격
CLEANUP_INTERVAL = timedelta(days=1)

# 시스템 층이 넣는 작업의 모듈 이름과, 정리 작업의 종류
SYSTEM_MODULE = "system"
CLEANUP_KIND = "cleanup"

FINISHED_STATUSES = (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELED)

# 사건 종류 (events 한 줄의 type). 화면이 이 이름으로 단계 · 다시 보내기 · 연결을 나눠 보인다.
EVENT_START = "start"
EVENT_PHASE = "phase"
EVENT_RETRY = "retry"
EVENT_CONNECTION = "connection"

# 작업 목록 한 쪽의 최대 줄 수
MAX_JOB_PAGE_SIZE = 200


@dataclass(frozen=True)
class JobPage:
    """작업 목록 한 쪽."""

    # 이 쪽의 작업들. 최근에 넣은 것부터
    items: list[Job]

    # 거르기에 맞는 전체 수
    total: int

    # 상태 거르기만 뺀 같은 조건의 상태별 수 {queued, running, done, failed, canceled}
    counts: dict[str, int]


@dataclass(frozen=True)
class CleanupResult:
    """정리 작업의 결과."""

    # 지운 오래된 작업 기록 수
    deleted_jobs: int

    # 지운 올린 파일 수 (가져오지 않은 채 오래된 것)
    deleted_uploads: int


@dataclass(frozen=True)
class RecoveredJobs:
    """끊긴 작업을 되살린 결과."""

    # 다시 대기로 돌린 작업 수
    requeued: int

    # 너무 여러 번 끊겨 실패로 둔 작업들. 작업 실행기가 모듈에 넘겨 모듈의 기록도 실패로 맞춘다.
    failed: list[Job]


async def try_acquire_worker_lock(conn: AsyncConnection) -> bool:
    """작업 실행기 자리를 잡는다. 이미 다른 작업 실행기가 잡고 있으면 False."""
    acquired = await conn.scalar(
        text("SELECT pg_try_advisory_lock(:class_key, :object_key)"),
        {"class_key": WORKER_LOCK_KEYS[0], "object_key": WORKER_LOCK_KEYS[1]},
    )
    return bool(acquired)


async def release_worker_lock(conn: AsyncConnection) -> None:
    """작업 실행기 자리를 내놓는다. 연결이 끊겨도 저절로 풀리지만, 종료할 때는 먼저 내놓는다."""
    await conn.execute(
        text("SELECT pg_advisory_unlock(:class_key, :object_key)"),
        {"class_key": WORKER_LOCK_KEYS[0], "object_key": WORKER_LOCK_KEYS[1]},
    )


async def is_worker_running(session: AsyncSession) -> bool:
    """이 데이터베이스에서 작업 실행기 자리가 잡혀 있는지 본다."""
    # 권고 잠금은 PostgreSQL 서버 전체에서 보이므로, 같은 데이터베이스의 것만 센다.
    query = text(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
            WHERE locktype = 'advisory'
              AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND classid = :class_key AND objid = :object_key AND objsubid = 2
              AND granted
        )
        """
    )
    running = await session.scalar(
        query, {"class_key": WORKER_LOCK_KEYS[0], "object_key": WORKER_LOCK_KEYS[1]}
    )
    return bool(running)


async def count_queued(session: AsyncSession) -> int:
    """대기 중인 작업 수를 돌려준다."""
    count = await session.scalar(
        select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
    )
    return int(count or 0)


async def recover_interrupted_jobs(session: AsyncSession) -> RecoveredJobs:
    """지난번에 끊긴 작업을 되살린다. 작업 실행기 자리를 잡은 뒤에 부른다."""
    # 작업 실행기는 하나만 뜨므로, 자리를 잡은 뒤에도 '실행 중'으로 남은 작업은 모두 지난번에 끊긴 것이다.
    now = datetime.now(UTC)
    too_many = await session.scalars(
        update(Job)
        .where(Job.status == JobStatus.RUNNING, Job.attempts + 1 > MAX_ATTEMPTS)
        .values(
            status=JobStatus.FAILED,
            finished_at=now,
            error=f"작업이 {MAX_ATTEMPTS}번 넘게 중간에 끊겨 멈췄습니다. 다시 실행해 주세요.",
        )
        .returning(Job)
    )
    failed = list(too_many.all())
    requeued_ids = await session.scalars(
        update(Job)
        .where(Job.status == JobStatus.RUNNING)
        .values(
            status=JobStatus.QUEUED,
            attempts=Job.attempts + 1,
            heartbeat_at=None,
            started_at=None,
        )
        .returning(Job.id)
    )
    requeued = len(requeued_ids.all())
    await session.commit()
    return RecoveredJobs(requeued=requeued, failed=failed)


async def claim_next_job(session: AsyncSession) -> Job | None:
    """대기 중인 작업 하나를 꺼내 '실행 중'으로 바꾼다. 없으면 None."""
    # SKIP LOCKED: 다른 트랜잭션이 잡고 있는 줄은 건너뛴다. 같은 작업을 두 번 꺼내지 않게.
    query = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = await session.scalar(query)
    if job is None:
        await session.rollback()
        return None
    now = datetime.now(UTC)
    job.status = JobStatus.RUNNING
    job.started_at = now
    job.heartbeat_at = now
    # 끊겨서 다시 꺼낸 작업이면 몇 번째 시작인지 보이게 attempts + 1을 적는다.
    job.events = [*job.events, _event(EVENT_START, attempt=job.attempts + 1)]
    await session.commit()
    return job


async def fail_job(session: AsyncSession, *, job_id: int, message: str) -> None:
    """작업을 실패로 끝낸다. message는 사용자에게 보여줄 문장이다."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.FAILED, error=message, finished_at=datetime.now(UTC))
    )
    await session.commit()


async def get_job(session: AsyncSession, *, job_id: int) -> Job:
    """작업 하나를 돌려준다. 없으면 NotFoundError."""
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError("작업을 찾을 수 없습니다.")
    return job


async def enqueue_job(
    session: AsyncSession,
    *,
    module: str,
    kind: str,
    params: dict[str, Any],
    dataset_id: int | None = None,
) -> Job:
    """작업을 대기열에 넣는다. 데이터셋을 주면 번호와 지금 이름을 함께 적는다.

    커밋은 부른 쪽이 한다(모듈의 기록과 한 트랜잭션으로 묶으려고).
    """
    dataset_name = None
    if dataset_id is not None:
        dataset_name = await session.scalar(select(Dataset.name).where(Dataset.id == dataset_id))
    job = Job(
        module=module,
        kind=kind,
        params=params,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
    )
    session.add(job)
    # 번호를 바로 쓸 수 있게 DB에 보내 둔다. 커밋 전이라 부른 쪽이 되돌릴 수 있다.
    await session.flush()
    return job


async def set_progress(session: AsyncSession, *, job_id: int, done: int, total: int | None) -> None:
    """작업 진행률을 적고 살아 있다고 알린다. 커밋은 부른 쪽이 한다(처리한 묶음과 함께 저장)."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(progress_done=done, progress_total=total, heartbeat_at=datetime.now(UTC))
    )


async def start_phase(session: AsyncSession, *, job_id: int, phase: str, total: int | None) -> None:
    """작업의 단계를 적고 진행률을 0부터 다시 센다. 커밋은 부른 쪽이 한다.

    result = {"phase": 단계 이름, "phase_started_at": 시작 시각}. 화면은 이 시각과 진행률로 남은 시간을 짐작한다.
    사건 목록에도 단계를 쌓는다(result는 다음 단계나 끝날 때 덮어써지므로).
    """
    now = datetime.now(UTC)
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            result={"phase": phase, "phase_started_at": now.isoformat()},
            progress_done=0,
            progress_total=total,
            heartbeat_at=now,
            events=_events_plus(_event(EVENT_PHASE, phase=phase, total=total)),
        )
    )


async def add_event(session: AsyncSession, *, job_id: int, event_type: str, **fields: Any) -> None:
    """작업의 사건 목록 끝에 한 줄을 붙인다(시각은 지금). 커밋은 부른 쪽이 한다."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(events=_events_plus(_event(event_type, **fields)))
    )


async def record_event(*, job_id: int, event_type: str, **fields: Any) -> None:
    """작업의 사건 한 줄을 따로 된 세션으로 바로 적는다. 적지 못해도 작업은 계속한다(로그만 남긴다).

    처리 함수의 세션이 다른 일을 하는 중에 쓴다(예: 임베딩을 다시 보낼 때 본 세션은 앞 묶음을 저장하는 중).
    """
    try:
        async with get_sessionmaker()() as session:
            await add_event(session, job_id=job_id, event_type=event_type, **fields)
            await session.commit()
    except Exception:
        logger.warning("작업 %d번의 사건(%s)을 적지 못했습니다.", job_id, event_type, exc_info=True)


async def list_jobs(
    session: AsyncSession,
    *,
    statuses: Sequence[str] = (),
    module: str | None = None,
    kind: str | None = None,
    dataset_id: int | None = None,
    since: datetime | None = None,
    finished_after: datetime | None = None,
    limit: int = MAX_JOB_PAGE_SIZE,
    offset: int = 0,
) -> JobPage:
    """작업 목록 한 쪽. 최근에 넣은 것부터(같으면 번호가 큰 것부터).

    since는 넣은 시각, finished_after는 끝난 시각으로 거른다. counts는 상태 거르기만 뺀 같은 조건의 상태별 수다
    (거르기 칸의 건수와 요약 숫자로 쓴다).
    """
    scope: list[ColumnElement[bool]] = []
    if module is not None:
        scope.append(Job.module == module)
    if kind is not None:
        scope.append(Job.kind == kind)
    if dataset_id is not None:
        scope.append(Job.dataset_id == dataset_id)
    if since is not None:
        scope.append(Job.created_at >= since)
    if finished_after is not None:
        scope.append(Job.finished_at > finished_after)
    matching = [*scope, Job.status.in_(statuses)] if statuses else scope

    items = await session.scalars(
        select(Job)
        .where(*matching)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(min(limit, MAX_JOB_PAGE_SIZE))
        .offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(Job).where(*matching))
    by_status = await session.execute(
        select(Job.status, func.count()).where(*scope).group_by(Job.status)
    )
    counts = {status.value: 0 for status in JobStatus} | dict(by_status.tuples().all())
    return JobPage(items=list(items.all()), total=int(total or 0), counts=counts)


async def request_cancel(session: AsyncSession, *, job_id: int) -> bool:
    """작업을 멈추라고 한다. 아직 대기 중이면 바로 멈추고(canceled) True, 돌고 있으면 표시만 하고 False.

    커밋은 부른 쪽이 한다(모듈의 기록과 함께 멈추려고).
    """
    # 작업 실행기가 막 꺼냈을 수 있어서, '대기 중일 때만' 조건을 한 문장에 건다.
    canceled_ids = await session.scalars(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.CANCELED, cancel_requested=True, finished_at=datetime.now(UTC))
        .returning(Job.id)
    )
    if canceled_ids.first() is not None:
        return True
    await session.execute(update(Job).where(Job.id == job_id).values(cancel_requested=True))
    return False


async def is_cancel_requested(session: AsyncSession, *, job_id: int) -> bool:
    """사용자가 이 작업을 멈추라고 했는지. 처리 함수가 묶음 사이마다 본다."""
    requested = await session.scalar(select(Job.cancel_requested).where(Job.id == job_id))
    return bool(requested)


async def cancel_job(session: AsyncSession, *, job_id: int) -> None:
    """작업을 멈춘 것으로 끝낸다. 처리 함수가 멈추라는 표시를 보고 부른다. 커밋은 부른 쪽이 한다."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.CANCELED, finished_at=datetime.now(UTC))
    )


async def finish_job(session: AsyncSession, *, job_id: int, result: dict[str, Any]) -> None:
    """작업을 성공으로 끝낸다. 커밋은 부른 쪽이 한다(모듈의 기록과 함께 끝내려고)."""
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.DONE, result=result, error=None, finished_at=datetime.now(UTC))
    )


async def is_cleanup_due(session: AsyncSession) -> bool:
    """마지막 정리에서 하루가 지났는지 본다. 한 번도 안 했으면 True."""
    last = await session.scalar(
        select(func.max(Job.finished_at)).where(
            Job.module == SYSTEM_MODULE,
            Job.kind == CLEANUP_KIND,
            Job.status == JobStatus.DONE,
        )
    )
    return last is None or datetime.now(UTC) - last >= CLEANUP_INTERVAL


async def run_cleanup(session: AsyncSession) -> CleanupResult:
    """오래된 작업 기록과 가져오지 않은 채 오래된 올린 파일을 지우고, 정리했다는 기록을 남긴다."""
    # 모듈의 정리(휴지통, 내보낸 파일, 옛 분석 결과)는 모듈이 생기면 여기서 함께 부른다.
    now = datetime.now(UTC)
    deleted = await session.scalars(
        delete(Job)
        .where(Job.status.in_(FINISHED_STATUSES), Job.finished_at < now - FINISHED_JOB_RETENTION)
        .returning(Job.id)
    )
    result = CleanupResult(
        deleted_jobs=len(deleted.all()),
        deleted_uploads=await uploads.discard_unused_uploads(session),
    )
    session.add(
        Job(
            module=SYSTEM_MODULE,
            kind=CLEANUP_KIND,
            status=JobStatus.DONE,
            result={"deleted_jobs": result.deleted_jobs, "deleted_uploads": result.deleted_uploads},
            started_at=now,
            finished_at=now,
        )
    )
    await session.commit()
    return result


def _event(event_type: str, **fields: Any) -> dict[str, Any]:
    """사건 한 줄. 시각은 지금(UTC, ISO)."""
    return {"type": event_type, "at": datetime.now(UTC).isoformat(), **fields}


def _events_plus(event: dict[str, Any]) -> ColumnElement[Any]:
    """사건 목록 끝에 한 줄을 붙이는 식(jsonb ||). 읽고 다시 쓰지 않아 동시에 붙여도 잃지 않는다."""
    return Job.events.op("||")(type_coerce([event], JSONB))
