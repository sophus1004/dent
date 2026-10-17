"""작업 실행기 프로세스 여럿을 실제로 띄워 보는 테스트: 데이터셋끼리 함께 돌고 같은 데이터셋은 차례로 돈다,
실행기 하나가 강제로 죽어도 다른 실행기의 작업은 그대로이고 죽은 실행기의 작업만 다시 돈다,
종료하라고 하면(실행기 수를 줄일 때) 하던 작업을 끝낸 뒤 끝난다.

실행기마다 시험 작업("test", "sleep")의 처리 함수를 붙여 띄운다(처리 함수 말고는 실제 작업 실행기 그대로).
"""

import asyncio
import signal
import subprocess
import sys
import time
from datetime import datetime

from sqlalchemy import select

from dent.system import datasets as datasets_service
from dent.system import examples as examples_service
from dent.system import jobs
from dent.system.models import Job, JobStatus
from tests.conftest import ROOT

# 시험 작업: params.seconds만큼 쉬고, 끊겨 다시 돌 때(attempts ≥ 1)는 바로 끝난다. 시작 · 끝 시각을 result에 적는다.
WORKER_SCRIPT = """
import asyncio, sys
from datetime import UTC, datetime
from dent import worker
from dent.system import jobs
from dent.system.db import get_sessionmaker

async def sleep_job(job):
    started = datetime.now(UTC).isoformat()
    await asyncio.sleep(job.params["seconds"] if job.attempts == 0 else 0.1)
    async with get_sessionmaker()() as session:
        result = {"started": started, "finished": datetime.now(UTC).isoformat()}
        await jobs.finish_job(session, job_id=job.id, result=result)
        await session.commit()

worker.HANDLERS[("test", "sleep")] = sleep_job
sys.exit(worker.main(sys.argv[1:]))
"""

# 기다리는 최대 시간(초). 실행기는 뜨는 데 몇 초 걸린다(모듈 불러오기 · 실행할 때 확인).
WAIT_TIMEOUT_S = 60.0
POLL_S = 0.2

# 강제로 죽일 실행기의 작업이 도는 시간(초). 죽이기 전에 끝나지 않게 넉넉히.
LONG_JOB_S = 30.0


def _start(slot: int) -> subprocess.Popen[bytes]:
    """작업 실행기 하나를 번호를 주어 띄운다(테스트 DB · storage는 conftest가 넣은 환경 변수로).

    Windows는 관리 프로세스처럼 새 프로세스 묶음으로 띄운다(종료하라는 알림 Ctrl+Break를 받게).
    """
    options = (
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        if sys.platform == "win32"
        else {}
    )
    return subprocess.Popen(
        [sys.executable, "-c", WORKER_SCRIPT, "--slot", str(slot)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **options,
    )


def _ask_to_stop(process: subprocess.Popen[bytes]) -> None:
    """관리 프로세스처럼 종료하라고 알린다: Mac · Linux는 SIGTERM, Windows는 Ctrl+Break."""
    if sys.platform == "win32":
        process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
    else:
        process.terminate()


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    """종료하라고 알리고 기다린다. 제때 끝나지 않으면 강제로 종료한다."""
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


async def _prepare(db_session) -> tuple[int, int]:
    """처음 실행할 때 넣는 내장 예시는 넣은 것으로 두고(시험 작업만 돌게), 데이터셋 둘을 만든다."""
    for module in ("classification", "retrieval", "generation"):
        await examples_service.mark_first_run_done(db_session, module=module)
    a = await datasets_service.create_dataset(db_session, module="classification", name="A")
    b = await datasets_service.create_dataset(db_session, module="classification", name="B")
    await db_session.commit()
    return a.id, b.id


async def _enqueue(db_session, dataset_id: int, seconds: float) -> int:
    job = await jobs.enqueue_job(
        db_session, module="test", kind="sleep", params={"seconds": seconds}, dataset_id=dataset_id
    )
    await db_session.commit()
    return job.id


async def _wait_for(db_session, condition, *, what: str) -> None:
    """조건이 맞을 때까지 DB를 다시 읽는다."""
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        db_session.expire_all()
        if await condition():
            return
        await asyncio.sleep(POLL_S)
    raise AssertionError(f"{WAIT_TIMEOUT_S:.0f}초 안에 {what}")


def _slot_ready(db_session, slot: int):
    """실행기 slot이 자리를 잡았는지 보는 조건."""

    async def ready() -> bool:
        return slot in await jobs.running_worker_slots(db_session)

    return ready


def _both_ready(db_session):
    """실행기 1 · 2가 모두 자리를 잡았는지 보는 조건."""

    async def ready() -> bool:
        return await jobs.running_worker_slots(db_session) == [1, 2]

    return ready


async def _jobs(db_session, ids: list[int]) -> dict[int, Job]:
    found = await db_session.scalars(select(Job).where(Job.id.in_(ids)))
    return {job.id: job for job in found}


def _at(job: Job, key: str) -> datetime:
    assert job.result is not None
    return datetime.fromisoformat(job.result[key])


async def test_workers_run_datasets_together_and_each_dataset_in_order(db_session):
    # 준비: 실행기 둘이 자리를 잡은 뒤, A에 긴 작업 · 짧은 작업, B에 긴 작업을 넣는다
    a, b = await _prepare(db_session)
    processes = [_start(1), _start(2)]
    try:
        await _wait_for(db_session, _both_ready(db_session), what="실행기 둘이 뜨지 않았습니다")
        a1 = await _enqueue(db_session, a, 1.5)
        a2 = await _enqueue(db_session, a, 0.1)
        b1 = await _enqueue(db_session, b, 1.5)
        ids = [a1, a2, b1]

        async def all_done() -> bool:
            found = await _jobs(db_session, ids)
            return all(job.status == JobStatus.DONE for job in found.values())

        # 실행
        await _wait_for(db_session, all_done, what="세 작업이 끝나지 않았습니다")
    finally:
        _stop(processes)

    # 확인: A1과 B1은 서로 다른 실행기에서 겹쳐 돌고, A2는 A1이 끝난 뒤에 시작했다
    found = await _jobs(db_session, ids)
    first_a, second_a, first_b = found[a1], found[a2], found[b1]
    assert {first_a.worker, first_b.worker} == {1, 2}
    assert _at(first_b, "started") < _at(first_a, "finished")
    assert _at(first_a, "started") < _at(first_b, "finished")
    assert _at(second_a, "started") >= _at(first_a, "finished")


async def test_killed_worker_loses_only_its_job_which_runs_again_after_restart(db_session):
    # 준비: 실행기 1이 A의 긴 작업을, 실행기 2가 B의 긴 작업을 돌게 한다
    a, b = await _prepare(db_session)
    a1 = await _enqueue(db_session, a, LONG_JOB_S)
    first = _start(1)
    processes = [first]
    try:

        async def a_running() -> bool:
            job = (await _jobs(db_session, [a1]))[a1]
            return job.status == JobStatus.RUNNING

        await _wait_for(db_session, a_running, what="A의 작업이 시작되지 않았습니다")
        b1 = await _enqueue(db_session, b, LONG_JOB_S)
        processes.append(_start(2))

        async def b_running() -> bool:
            job = (await _jobs(db_session, [b1]))[b1]
            return job.status == JobStatus.RUNNING

        await _wait_for(db_session, b_running, what="B의 작업이 시작되지 않았습니다")

        # 실행: 실행기 1을 강제로 죽이고 같은 번호로 다시 띄운다
        first.kill()
        first.wait()
        processes.append(_start(1))

        async def a_done() -> bool:
            job = (await _jobs(db_session, [a1]))[a1]
            return job.status == JobStatus.DONE

        await _wait_for(db_session, a_done, what="A의 작업이 다시 돌지 않았습니다")

        # 확인: A의 작업은 한 번 끊겨 다시 돌았고, 실행기 2의 B 작업은 건드리지 않았다
        found = await _jobs(db_session, [a1, b1])
        assert found[a1].attempts == 1
        assert (found[b1].status, found[b1].worker, found[b1].attempts) == (
            JobStatus.RUNNING,
            2,
            0,
        )
    finally:
        _stop(processes)


async def test_worker_asked_to_stop_finishes_its_job_before_ending(db_session):
    # 준비: 실행기 1이 2초짜리 작업을 도는 중이다
    (a, _b) = await _prepare(db_session)
    process = _start(1)
    try:
        await _wait_for(
            db_session,
            _slot_ready(db_session, 1),
            what="실행기 1이 뜨지 않았습니다",
        )
        a1 = await _enqueue(db_session, a, 2.0)

        async def running() -> bool:
            return (await _jobs(db_session, [a1]))[a1].status == JobStatus.RUNNING

        await _wait_for(db_session, running, what="작업이 시작되지 않았습니다")

        # 실행: 실행기 수를 줄일 때처럼 종료하라고 알린다
        _ask_to_stop(process)
        code = await asyncio.to_thread(process.wait, 30)
    finally:
        _stop([process])

    # 확인: 하던 작업을 끝까지 하고(끊기지 않음) 깔끔히 끝났다
    job = (await _jobs(db_session, [a1]))[a1]
    assert code == 0
    assert (job.status, job.attempts) == (JobStatus.DONE, 0)
