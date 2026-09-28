"""LLM 도우미 실행의 바탕: 실행 한 줄(system_helper_runs)과 사건 기록(system_helper_events).

도우미는 채팅이 아니다. 진단을 읽고, 스스로 묻고 답하며, 규칙 · Jev · LLM으로 데이터를 고친다.
무엇을 어떻게 고치는지(단계 · 도구 · 바꾼 데이터의 전 · 후)는 모듈이 정하고, 여기에는 모든 모듈이 같이 쓰는 것만 둔다:
실행 상태와 단계 줄, 쓴 토큰 · Jev · 바꾼 수, 멈추기, 사건 쌓기 · 이어 읽기.

사건은 쌓기만 한다. 화면은 마지막으로 읽은 사건 번호 다음부터 1초마다 이어 읽는다(실시간 보기).
처리 함수(작업 실행기)는 사건 하나를 적을 때마다 커밋해서 화면이 바로 보게 한다.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system.exceptions import ConflictError, NotFoundError
from dent.system.models import HelperEvent, HelperEventKind, HelperRun, HelperRunStatus

RUN_NOT_FOUND_MESSAGE = "도우미 실행을 찾을 수 없습니다."
NOT_RUNNING_MESSAGE = "도는 중인 도우미가 아닙니다."

# 사건을 한 번에 읽는 최대 수 (화면이 1초마다 이어 읽는다)
MAX_EVENT_PAGE_SIZE = 500

# 끝난 상태 (다시 돌지 않는다)
FINISHED_STATUSES = (HelperRunStatus.DONE, HelperRunStatus.STOPPED, HelperRunStatus.FAILED)


def new_run(
    *, module: str, dataset_id: int, model: str | None, steps: list[dict[str, Any]]
) -> HelperRun:
    """저장하지 않은 실행 한 줄. 부른 쪽이 작업과 함께 넣고 커밋한다."""
    return HelperRun(
        module=module,
        dataset_id=dataset_id,
        model=model,
        steps=steps,
        status=HelperRunStatus.RUNNING.value,
    )


async def get_run(db: AsyncSession, *, run_id: int) -> HelperRun:
    """실행 한 줄. 없으면 NotFoundError."""
    # 처리 함수(작업 실행기)가 다른 세션에서 바꾸므로 세션이 기억한 값 대신 DB 값을 읽는다.
    run = await db.get(HelperRun, run_id, populate_existing=True)
    if run is None:
        raise NotFoundError(RUN_NOT_FOUND_MESSAGE)
    return run


async def latest_run(db: AsyncSession, *, module: str, dataset_id: int) -> HelperRun | None:
    """데이터셋의 가장 최근 실행. 없으면 None."""
    return await db.scalar(
        select(HelperRun)
        .where(HelperRun.module == module, HelperRun.dataset_id == dataset_id)
        # 같은 시각이면 나중 번호가 최근이다.
        .order_by(HelperRun.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


async def list_events(
    db: AsyncSession, *, run_id: int, after_id: int, limit: int
) -> list[HelperEvent]:
    """after_id 다음 사건들을 번호 순서로. 실행이 없으면 NotFoundError."""
    await get_run(db, run_id=run_id)
    return list(
        await db.scalars(
            select(HelperEvent)
            .where(HelperEvent.run_id == run_id, HelperEvent.id > after_id)
            .order_by(HelperEvent.id)
            .limit(min(limit, MAX_EVENT_PAGE_SIZE))
        )
    )


async def add_event(
    db: AsyncSession, *, run_id: int, step: int, kind: HelperEventKind, payload: dict[str, Any]
) -> HelperEvent:
    """사건 하나를 적고 번호를 받는다(flush). 커밋은 부른 쪽이 한다."""
    event = HelperEvent(run_id=run_id, step=step, kind=kind.value, payload=payload)
    db.add(event)
    await db.flush()
    return event


async def set_steps(
    db: AsyncSession, *, run_id: int, steps: list[dict[str, Any]], step_now: int
) -> None:
    """단계 줄과 지금 단계를 바꾼다. 커밋은 부른 쪽이 한다."""
    await db.execute(
        update(HelperRun).where(HelperRun.id == run_id).values(steps=steps, step_now=step_now)
    )


async def add_usage(
    db: AsyncSession,
    *,
    run_id: int,
    llm_tokens: int = 0,
    jev_calls: int = 0,
    changed: int = 0,
    held: int = 0,
) -> None:
    """쓴 토큰 · Jev에 물은 수 · 바꾼 수 · 사람에게 넘긴 수를 더한다(빼면 되돌림). 커밋은 부른 쪽이 한다."""
    # 되돌리기(API 서버)와 처리 함수(작업 실행기)가 같이 바꿀 수 있어 읽고 쓰지 않고 DB에서 더한다.
    await db.execute(
        update(HelperRun)
        .where(HelperRun.id == run_id)
        .values(
            llm_tokens=HelperRun.llm_tokens + llm_tokens,
            jev_calls=HelperRun.jev_calls + jev_calls,
            changed=HelperRun.changed + changed,
            held=HelperRun.held + held,
        )
    )


async def request_stop(db: AsyncSession, *, run_id: int) -> HelperRun:
    """[멈추기]: 도는 중인 실행에 멈추라고 적는다. 도우미는 다음 도구를 부르기 전에 멈춘다.

    실행이 없으면 NotFoundError, 도는 중이 아니면 ConflictError.
    """
    run = await get_run(db, run_id=run_id)
    if run.status != HelperRunStatus.RUNNING.value:
        raise ConflictError(NOT_RUNNING_MESSAGE)
    run.stop_requested = True
    await db.commit()
    return run


async def is_stop_requested(db: AsyncSession, *, run_id: int) -> bool:
    """사람이 [멈추기]를 눌렀는지."""
    return bool(await db.scalar(select(HelperRun.stop_requested).where(HelperRun.id == run_id)))


async def finish_run(
    db: AsyncSession,
    *,
    run_id: int,
    status: HelperRunStatus,
    error: str | None = None,
    permission: dict[str, Any] | None = None,
) -> None:
    """실행을 끝내거나(done · stopped · failed) 허락을 기다리게 한다(asking). 커밋은 부른 쪽이 한다."""
    is_finished = status in FINISHED_STATUSES
    await db.execute(
        update(HelperRun)
        .where(HelperRun.id == run_id)
        .values(
            status=status.value,
            error=error,
            permission=permission,
            stop_requested=False,
            finished_at=datetime.now(UTC) if is_finished else None,
        )
    )


async def resume_run(db: AsyncSession, *, run_id: int, job_id: int) -> None:
    """허락을 받은(또는 AI로 고치기를 붙인) 실행을 다시 돌게 한다(새 작업 번호). 커밋은 부른 쪽이 한다."""
    await db.execute(
        update(HelperRun)
        .where(HelperRun.id == run_id)
        .values(
            status=HelperRunStatus.RUNNING.value,
            job_id=job_id,
            finished_at=None,
            error=None,
            stop_requested=False,
        )
    )
