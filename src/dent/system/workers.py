"""작업 실행기 수와 번호마다 상태. 실행기 수는 실행 중에 화면에서 바꾸고 system_settings에 둔다.

- 실행기 수: system_settings의 workers(1~MAX_WORKERS, 줄이 없으면 DEFAULT_WORKERS). .env에는 두지 않는다
  (실행 중에 바꾸는 값이라 외부 모델 연결처럼 화면에서 저장한다). 다음에 실행할 때도 이 수로 띄운다.
- 관리 프로세스(supervisor)만 실행기를 띄우고 끈다. 몇 초마다 이 수를 읽어 늘리거나(빈 번호를 띄움)
  줄인다(큰 번호부터 하던 작업을 끝내면 종료). 번호마다 관리 프로세스가 보는 상태(띄우는 중 · 끝나면 종료 ·
  다시 띄우는 중 · 멈춤)를 storage/run/workers.json에 적는다.
- 화면은 그 상태를 DB의 잠금(실행 중인 번호) · 번호마다 도는 작업과 합쳐 읽는다(read_workers).
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import psutil
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import jobs
from dent.system.config import Settings
from dent.system.exceptions import InvalidInputError
from dent.system.models import MAX_WORKERS, MIN_WORKERS, Job, JobStatus, Setting

# system_settings에서 실행기 수를 두는 열쇠
WORKERS_KEY = "workers"

# 실행기 수의 기본값. 데이터셋 넷을 함께 돌린다. 범위는 models의 MIN_WORKERS ~ MAX_WORKERS.
DEFAULT_WORKERS = 4

WORKER_COUNT_MESSAGE = f"작업 실행기 수는 {MIN_WORKERS}~{MAX_WORKERS}입니다."

# 관리 프로세스가 번호마다 상태를 적는 파일 (storage/run/workers.json). run 폴더는 관리 프로세스의 기록 폴더다.
RUN_DIR_NAME = "run"
POOL_FILE_NAME = "workers.json"

# 같은 프로세스인지 가를 때 만든 시각이 달라도 되는 차이(초). 번호는 다시 쓰일 수 있어 만든 시각도 본다.
CREATE_TIME_TOLERANCE_S = 1.0


class SlotState(StrEnum):
    """작업 실행기 번호 하나의 상태."""

    # 자리를 잡고 실행 중
    RUNNING = "running"

    # 띄우는 중 (아직 자리를 잡지 않음)
    STARTING = "starting"

    # 실행기 수를 줄여서, 하던 작업을 끝내면 종료한다
    DRAINING = "draining"

    # 멈춰서 다시 띄우는 중
    RESTARTING = "restarting"

    # 너무 자주 멈춰 다시 띄우지 않는다
    FAILED = "failed"

    # 실행 중이 아니다
    STOPPED = "stopped"


@dataclass(frozen=True)
class SlotView:
    """화면에 보이는 번호 하나."""

    # 번호 (1부터)
    slot: int

    # 상태
    state: SlotState

    # 이 번호가 도는 작업. 없으면 None
    job: Job | None


@dataclass(frozen=True)
class WorkersView:
    """작업 실행기 전체 모습."""

    # 정한 실행기 수
    count: int

    # 관리 프로세스가 지켜보는지. 아니면(실행기를 손으로 띄움) 수를 바꿔도 저장만 한다.
    managed: bool

    # 번호마다 (1부터, 정한 수와 실행 중인 번호 가운데 큰 것까지)
    slots: list[SlotView]

    # 자리를 잡고 실행 중인 실행기 수
    running: int

    # 도는 작업 수
    busy: int

    # 대기 작업 수
    queued: int


async def get_worker_count(db: AsyncSession) -> int:
    """정한 작업 실행기 수. 저장한 적 없거나 범위 밖이면 기본값."""
    value = await db.scalar(select(Setting.value).where(Setting.key == WORKERS_KEY))
    is_valid = isinstance(value, int) and MIN_WORKERS <= value <= MAX_WORKERS
    return value if is_valid else DEFAULT_WORKERS


async def set_worker_count(db: AsyncSession, *, count: int) -> int:
    """작업 실행기 수를 저장한다. 범위 밖이면 InvalidInputError. 관리 프로세스가 곧 따른다."""
    if not MIN_WORKERS <= count <= MAX_WORKERS:
        raise InvalidInputError(WORKER_COUNT_MESSAGE)
    await db.execute(
        insert(Setting)
        .values(key=WORKERS_KEY, value=count)
        .on_conflict_do_update(index_elements=[Setting.key], set_={"value": count})
    )
    await db.commit()
    return count


def pool_file(settings: Settings) -> Path:
    """관리 프로세스가 번호마다 상태를 적는 파일."""
    return settings.storage_path / RUN_DIR_NAME / POOL_FILE_NAME


def write_pool_state(settings: Settings, *, slots: dict[int, SlotState]) -> None:
    """관리 프로세스가 번호마다 상태를 적는다(지금 프로세스가 관리 프로세스다). 반쯤 쓴 파일을 읽지 않게 옮겨 넣는다."""
    path = pool_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    me = psutil.Process()
    data = {
        "pid": me.pid,
        "created": me.create_time(),
        "slots": {str(slot): state.value for slot, state in sorted(slots.items())},
    }
    temporary = path.with_name(f"{POOL_FILE_NAME}.tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)


def remove_pool_state(settings: Settings) -> None:
    """번호마다 상태 파일을 지운다(관리 프로세스가 종료할 때)."""
    pool_file(settings).unlink(missing_ok=True)


def read_pool_state(settings: Settings) -> dict[int, SlotState] | None:
    """관리 프로세스가 적은 번호마다 상태. 파일이 없거나 적은 관리 프로세스가 끝났으면 None."""
    try:
        data = json.loads(pool_file(settings).read_text(encoding="utf-8"))
        process = psutil.Process(int(data["pid"]))
        is_same_process = abs(process.create_time() - float(data["created"])) < (
            CREATE_TIME_TOLERANCE_S
        )
        if not is_same_process:
            return None
        return {int(slot): SlotState(state) for slot, state in data["slots"].items()}
    except (OSError, ValueError, TypeError, KeyError, psutil.Error):
        return None


async def read_workers(db: AsyncSession, *, settings: Settings) -> WorkersView:
    """정한 수 · 관리 프로세스가 보는 상태 · DB의 잠금 · 번호마다 도는 작업을 합친 모습."""
    count = await get_worker_count(db)
    held = set(await jobs.running_worker_slots(db))
    pool = read_pool_state(settings)
    running_jobs = (
        await db.scalars(
            select(Job).where(Job.status == JobStatus.RUNNING, Job.worker.is_not(None))
        )
    ).all()
    job_of = {job.worker: job for job in running_jobs}
    known = {*held, *(pool or {})}
    last = max([count, *known])
    slots = [
        SlotView(slot=slot, state=_slot_state(slot, held=held, pool=pool), job=job_of.get(slot))
        for slot in range(1, last + 1)
    ]
    return WorkersView(
        count=count,
        managed=pool is not None,
        slots=slots,
        running=len(held),
        busy=await jobs.count_running(db),
        queued=await jobs.count_queued(db),
    )


def _slot_state(slot: int, *, held: set[int], pool: dict[int, SlotState] | None) -> SlotState:
    """번호 하나의 상태: 관리 프로세스가 줄이는 중이면 '끝나면 종료', 잠금이 잡혀 있으면 실행 중, 아니면 관리 프로세스가 본 것."""
    told = (pool or {}).get(slot)
    if told is SlotState.DRAINING:
        return SlotState.DRAINING
    if slot in held:
        return SlotState.RUNNING
    if told in (SlotState.STARTING, SlotState.RESTARTING, SlotState.FAILED):
        return told
    return SlotState.STOPPED
