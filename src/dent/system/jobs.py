"""작업 대기열. API 서버가 작업을 넣고, 작업 실행기가 꺼내 처리한다.

작업 실행기는 여럿(번호 1~MAX_WORKERS)이 함께 돈다. 데이터셋끼리는 함께 돌고, 같은 데이터셋의 작업은
넣은 순서대로 하나씩 돈다(앞 작업이 끝나야 다음을 꺼낸다). 데이터셋마다 도는 작업은 하나라는 것을 DB가 부분 UNIQUE로 지킨다.
실행기는 번호마다 PostgreSQL 권고 잠금(advisory lock)으로 자리를 잡고, 꺼낸 작업에 그 번호를 적는다.
잠금은 DB 연결에 붙어 있어 프로세스가 죽으면 바로 풀린다. 그래서 '실행 중'인데 번호의 잠금이 풀린 작업은
끊긴 것으로 보고 되살린다. API 서버는 잡힌 잠금으로 실행 중인 실행기를 센다.
대기 중인 같은 일(dedupe_key가 같은 작업)은 하나만 둔다.

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

from sqlalchemy import (
    ColumnElement,
    delete,
    exists,
    func,
    or_,
    select,
    text,
    type_coerce,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import aliased

from dent.system import uploads
from dent.system.db import get_sessionmaker
from dent.system.exceptions import NotFoundError
from dent.system.models import MAX_WORKERS, Dataset, Job, JobStatus

logger = logging.getLogger("dent.jobs")

# 권고 잠금 열쇠 두 개 가운데 앞의 것. 'dent'의 ASCII 값이다. 뒤의 것은 작업 실행기 번호(1~MAX_WORKERS)이거나
# 정리 잠금(MAINTENANCE_LOCK_KEY)이다. 실행기 1의 열쇠는 실행기가 하나뿐이던 때의 열쇠와 같다.
LOCK_CLASS_KEY = 0x64656E74

# 한 번만 할 일(정리 작업 · 처음 실행할 때 내장 예시 넣기)을 실행기 가운데 하나만 하게 잡는 잠금의 뒤 열쇠
MAINTENANCE_LOCK_KEY = 0

# 권고 잠금 가운데 이 DB · 이 열쇠의 것만 고르는 조건(pg_locks). 두 정수 열쇠의 잠금은 objsubid가 2다.
# 권고 잠금은 PostgreSQL 서버 전체에서 보이므로 같은 데이터베이스의 것만 센다.
_HELD_LOCK_WHERE = """
    l.locktype = 'advisory'
    AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
    AND l.classid = :class_key AND l.objsubid = 2 AND l.granted
"""

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

# 대기 중인 작업이 기다리는 까닭: 같은 데이터셋의 앞 작업 · 빈 작업 실행기
WAITING_DATASET = "dataset"
WAITING_WORKER = "worker"


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


async def try_acquire_worker_lock(conn: AsyncConnection, *, slot: int) -> bool:
    """작업 실행기 번호 slot의 자리를 잡는다. 다른 실행기가 그 번호를 잡고 있으면 False."""
    return await _try_lock(conn, slot)


async def acquire_free_worker_slot(conn: AsyncConnection) -> int | None:
    """빈 작업 실행기 번호를 작은 것부터 잡는다(번호를 받지 않고 손으로 띄운 실행기). 모두 잡혀 있으면 None."""
    for slot in range(1, MAX_WORKERS + 1):
        if await _try_lock(conn, slot):
            return slot
    return None


async def release_worker_lock(conn: AsyncConnection, *, slot: int) -> None:
    """작업 실행기 자리를 내놓는다. 연결이 끊겨도 저절로 풀리지만, 종료할 때는 먼저 내놓는다."""
    await _unlock(conn, slot)


async def try_acquire_maintenance_lock(conn: AsyncConnection) -> bool:
    """한 번만 할 일의 잠금을 잡는다. 다른 실행기가 하고 있으면 False(그 실행기에 맡기고 건너뛴다)."""
    return await _try_lock(conn, MAINTENANCE_LOCK_KEY)


async def release_maintenance_lock(conn: AsyncConnection) -> None:
    """한 번만 할 일의 잠금을 내놓는다."""
    await _unlock(conn, MAINTENANCE_LOCK_KEY)


async def running_worker_slots(session: AsyncSession) -> list[int]:
    """자리를 잡고 실행 중인 작업 실행기 번호들(작은 것부터)."""
    query = text(
        f"""
        SELECT l.objid::int AS slot FROM pg_locks l
        WHERE {_HELD_LOCK_WHERE} AND l.objid::int BETWEEN 1 AND :max_workers
        ORDER BY slot
        """
    )
    rows = await session.scalars(query, {"class_key": LOCK_CLASS_KEY, "max_workers": MAX_WORKERS})
    return list(rows.all())


async def is_worker_running(session: AsyncSession) -> bool:
    """이 데이터베이스에서 작업 실행기가 하나라도 자리를 잡고 있는지 본다."""
    return bool(await running_worker_slots(session))


async def _try_lock(conn: AsyncConnection, object_key: int) -> bool:
    """권고 잠금 (LOCK_CLASS_KEY, object_key)를 기다리지 않고 잡는다. 연결이 끝날 때까지 남는다."""
    acquired = await conn.scalar(
        text("SELECT pg_try_advisory_lock(:class_key, :object_key)"),
        {"class_key": LOCK_CLASS_KEY, "object_key": object_key},
    )
    return bool(acquired)


async def _unlock(conn: AsyncConnection, object_key: int) -> None:
    """권고 잠금 (LOCK_CLASS_KEY, object_key)를 내놓는다."""
    await conn.execute(
        text("SELECT pg_advisory_unlock(:class_key, :object_key)"),
        {"class_key": LOCK_CLASS_KEY, "object_key": object_key},
    )


async def count_queued(session: AsyncSession) -> int:
    """대기 중인 작업 수를 돌려준다."""
    count = await session.scalar(
        select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
    )
    return int(count or 0)


async def count_running(session: AsyncSession) -> int:
    """도는 작업 수를 돌려준다."""
    count = await session.scalar(
        select(func.count()).select_from(Job).where(Job.status == JobStatus.RUNNING)
    )
    return int(count or 0)


async def recover_interrupted_jobs(
    session: AsyncSession, *, restarted_slot: int | None = None
) -> RecoveredJobs:
    """끊긴 작업을 되살린다: '실행 중'인데 꺼낸 작업 실행기의 잠금이 풀린 작업을 다시 대기로 돌린다.

    너무 여러 번 끊긴 작업은 실패로 둔다. restarted_slot은 막 자리를 잡은 실행기의 번호다. 그 번호로
    남은 작업은 앞서 같은 번호로 돌던 실행기가 죽어 남긴 것이다(잠금은 지금 이 실행기가 잡고 있다).
    살아 있는 다른 실행기가 도는 작업은 건드리지 않는다.
    """
    # 잠금이 풀렸는지는 줄마다 이 문장 안에서 본다. 미리 읽어 두면 그사이 자리를 잡고 꺼낸 작업을 되돌릴 수 있다.
    lock_released = text(
        f"""
        NOT EXISTS (
            SELECT 1 FROM pg_locks l
            WHERE {_HELD_LOCK_WHERE} AND l.objid::int = system_jobs.worker
        )
        """
    ).bindparams(class_key=LOCK_CLASS_KEY)
    is_orphaned = (
        lock_released
        if restarted_slot is None
        else or_(Job.worker == restarted_slot, lock_released)
    )
    now = datetime.now(UTC)
    too_many = await session.scalars(
        update(Job)
        .where(Job.status == JobStatus.RUNNING, is_orphaned, Job.attempts + 1 > MAX_ATTEMPTS)
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
        .where(Job.status == JobStatus.RUNNING, is_orphaned)
        .values(
            status=JobStatus.QUEUED,
            attempts=Job.attempts + 1,
            heartbeat_at=None,
            started_at=None,
            worker=None,
        )
        .returning(Job.id)
    )
    requeued = len(requeued_ids.all())
    await session.commit()
    return RecoveredJobs(requeued=requeued, failed=failed)


async def claim_next_job(session: AsyncSession, *, worker: int | None = None) -> Job | None:
    """꺼낼 수 있는 작업 하나를 '실행 중'으로 바꾸고 작업 실행기 번호를 적는다. 없으면 None.

    꺼낼 수 있는 작업: 대기 중이고, 그 데이터셋에 도는 작업이 없고, 그 데이터셋에 더 앞선 대기 작업이 없는 것.
    그 가운데 가장 먼저 넣은 것. 그래서 데이터셋끼리는 함께 돌고 같은 데이터셋은 넣은 순서대로 하나씩 돈다.
    데이터셋이 없는 작업(데이터셋을 지운 뒤 남은 기록 등)은 차례를 따지지 않는다.
    """
    running = aliased(Job)
    earlier = aliased(Job)
    has_running = exists().where(
        running.dataset_id == Job.dataset_id, running.status == JobStatus.RUNNING
    )
    has_earlier = exists().where(
        earlier.dataset_id == Job.dataset_id,
        earlier.status == JobStatus.QUEUED,
        earlier.id < Job.id,
    )
    # SKIP LOCKED: 다른 실행기가 막 잡은 줄은 건너뛴다. 그 줄이 아직 대기 중으로 보이므로 같은 데이터셋의
    # 다음 작업은 '앞선 대기 작업'에 걸려 빠지고, 이 실행기는 다른 데이터셋으로 간다.
    query = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED, ~has_running, ~has_earlier)
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=Job)
    )
    job = await session.scalar(query)
    if job is None:
        await session.rollback()
        return None
    now = datetime.now(UTC)
    job.status = JobStatus.RUNNING
    job.worker = worker
    job.started_at = now
    job.heartbeat_at = now
    # 끊겨서 다시 꺼낸 작업이면 몇 번째 시작인지 보이게 attempts + 1을 적는다.
    job.events = [*job.events, _event(EVENT_START, attempt=job.attempts + 1, worker=worker)]
    try:
        await session.commit()
    except IntegrityError:
        # 데이터셋마다 도는 작업은 하나(부분 UNIQUE). 그사이 다른 실행기가 같은 데이터셋을 꺼냈다. 다음에 다시 본다.
        await session.rollback()
        return None
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
    dedupe_key: str | None = None,
) -> Job:
    """작업을 대기열에 넣는다. 데이터셋을 주면 번호와 지금 이름을 함께 적는다.

    dedupe_key를 주면 같은 열쇠의 대기 작업이 이미 있을 때 넣지 않고 그 작업을 돌려준다
    (부분 UNIQUE라 동시에 넣어도 하나만 남는다). 커밋은 부른 쪽이 한다(모듈의 기록과 한 트랜잭션으로 묶으려고).
    """
    dataset_name = None
    if dataset_id is not None:
        dataset_name = await session.scalar(select(Dataset.name).where(Dataset.id == dataset_id))
    if dedupe_key is not None:
        return await _enqueue_once(
            session,
            values={
                "module": module,
                "kind": kind,
                "params": params,
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "dedupe_key": dedupe_key,
            },
        )
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


def dedupe_key_of(module: str, kind: str, dataset_id: int) -> str:
    """데이터셋 하나에 대기 중인 같은 일은 하나만 둘 때의 열쇠: '<모듈>:<종류>:<데이터셋 번호>'."""
    return f"{module}:{kind}:{dataset_id}"


async def _enqueue_once(session: AsyncSession, *, values: dict[str, Any]) -> Job:
    """같은 열쇠의 대기 작업이 없을 때만 넣는다. 있으면 그 작업을 돌려준다."""
    # 있던 대기 작업이 그사이 꺼내져(대기가 아니게 되어) 찾지 못하면 한 번 더 넣어 본다.
    for _ in range(2):
        inserted = await session.scalar(
            insert(Job)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["dedupe_key"], index_where=text("status = 'queued'")
            )
            .returning(Job.id)
        )
        found_id = inserted
        if found_id is None:
            found_id = await session.scalar(
                select(Job.id).where(
                    Job.dedupe_key == values["dedupe_key"], Job.status == JobStatus.QUEUED
                )
            )
        if found_id is not None:
            job = await session.get(Job, found_id)
            if job is not None:
                return job
    raise RuntimeError(f"작업을 넣지 못했습니다: {values['dedupe_key']}")


async def cancel_dataset_jobs(session: AsyncSession, *, dataset_id: int) -> None:
    """데이터셋의 대기 작업은 멈추고, 도는 작업에는 멈추라고 적는다(데이터셋을 지울 때). 커밋은 부른 쪽이 한다."""
    await session.execute(
        update(Job)
        .where(Job.dataset_id == dataset_id, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.CANCELED, cancel_requested=True, finished_at=datetime.now(UTC))
    )
    await session.execute(
        update(Job)
        .where(Job.dataset_id == dataset_id, Job.status == JobStatus.RUNNING)
        .values(cancel_requested=True)
    )


async def waiting_reasons(session: AsyncSession, *, jobs: Sequence[Job]) -> dict[int, str]:
    """대기 중인 작업마다 기다리는 까닭: 같은 데이터셋의 앞 작업(dataset) · 빈 작업 실행기(worker)."""
    queued = [job for job in jobs if job.status == JobStatus.QUEUED]
    dataset_ids = {job.dataset_id for job in queued if job.dataset_id is not None}
    first_queued: dict[int, int] = {}
    busy: set[int] = set()
    if dataset_ids:
        rows = await session.execute(
            select(Job.dataset_id, func.min(Job.id))
            .where(Job.dataset_id.in_(dataset_ids), Job.status == JobStatus.QUEUED)
            .group_by(Job.dataset_id)
        )
        first_queued = {int(dataset_id): int(job_id) for dataset_id, job_id in rows.tuples()}
        busy = set(
            (
                await session.scalars(
                    select(Job.dataset_id).where(
                        Job.dataset_id.in_(dataset_ids), Job.status == JobStatus.RUNNING
                    )
                )
            ).all()
        )
    reasons: dict[int, str] = {}
    for job in queued:
        is_behind_dataset_job = job.dataset_id is not None and (
            job.dataset_id in busy or first_queued.get(job.dataset_id, job.id) < job.id
        )
        reasons[job.id] = WAITING_DATASET if is_behind_dataset_job else WAITING_WORKER
    return reasons


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

    since는 넣은 시각, finished_after는 끝난 시각으로 거른다. finished_after를 주면 끝난 시각 순(먼저 끝난 것부터)이다
    (새로 끝난 작업을 이어 읽는 쪽이 쪽마다 빠짐없이 넘기게). counts는 상태 거르기만 뺀 같은 조건의 상태별 수다
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

    order = (
        (Job.finished_at.asc(), Job.id.asc())
        if finished_after is not None
        else (Job.created_at.desc(), Job.id.desc())
    )
    items = await session.scalars(
        select(Job)
        .where(*matching)
        .order_by(*order)
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
