"""작업 실행기 프로세스를 실행한다: python -m dent.worker

설정 · 로그 · storage · DB · pgvector · 테이블 버전을 확인한 뒤 자리 잡기(하나만 뜨게) · 임베딩 서버 확인 ·
끊긴 작업 되살리기 · 정리 · 처음이면 내장 예시 넣기를 하고, 종료할 때까지 대기열에서 작업을 꺼내 처리한다.
평소에는 런처(uv run dent)가 이것을 띄운다.

임베딩 연결은 system_connections에서 몇 초마다 다시 읽는다. 화면에서 연결을 저장하면
다시 실행하지 않아도 곧 새 연결로 확인하고, 처음 보는 모델이면 등록한다.
"""

import asyncio
import contextlib
import logging
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from dent.modules.classification import examples as classification_examples
from dent.modules.classification import jobs as classification_jobs
from dent.modules.retrieval import examples as retrieval_examples
from dent.modules.retrieval import jobs as retrieval_jobs
from dent.system import connections, embedded_models, embedding, examples, exports, jobs
from dent.system.checks import check_database, check_storage
from dent.system.config import get_settings
from dent.system.connections import ConnectionCheck
from dent.system.db import dispose_engine, get_sessionmaker, init_engine
from dent.system.exceptions import StartupError
from dent.system.logging import report_startup_error, setup_logging
from dent.system.models import ConnectionRole, Job

logger = logging.getLogger("dent.worker")

LABEL = "작업 실행기"

# 대기열이 비었을 때 다시 볼 때까지 쉬는 시간(초)
POLL_INTERVAL_S = 1.0

# 임베딩 연결(system_connections)을 다시 읽는 간격(초). 화면에서 바꾼 연결을 이만큼 안에 알아챈다.
CONNECTION_POLL_INTERVAL_S = 5.0

# 임베딩 서버에 붙지 못했을 때 같은 연결로 다시 확인하는 간격(초)
EMBEDDING_RECHECK_INTERVAL_S = 60.0

# 연결이 없을 때의 확인 결과
NOT_CONNECTED = ConnectionCheck(ok=False, detail="미연결")

# 작업 처리 함수: 꺼낸 작업 한 줄을 받아 처리한다.
JobHandler = Callable[[Job], Awaitable[None]]

# (모듈, 작업 종류)별 처리 함수. 모듈을 더하면 그 모듈의 jobs.HANDLERS를 여기에 한 줄씩 더한다
# (모듈을 붙이는 세 곳 가운데 하나).
HANDLERS: dict[tuple[str, str], JobHandler] = {
    **classification_jobs.HANDLERS,  # 분류: 가져오기 · 의미 지도 · 도우미
    **retrieval_jobs.HANDLERS,  # 검색: 가져오기 · 뜻 분석 · 도우미 · 질의 만들기 · 오답 찾기 · 내보내기
}

# (모듈, 작업 종류)별 뒷정리 함수. 너무 여러 번 끊겨 실패로 둔 작업을 모듈에 알려,
# 모듈이 자기 기록(예: 가져오기 상태)도 실패로 맞추고 반쯤 한 일을 지우게 한다.
FAILURE_HANDLERS: dict[tuple[str, str], JobHandler] = {
    **classification_jobs.FAILURE_HANDLERS,  # 분류: 가져오기 · 의미 지도 · 도우미
    **retrieval_jobs.FAILURE_HANDLERS,  # 검색
}


# 처음 실행할 때 한 번 넣는 모듈의 내장 예시. 모듈을 더하면 그 모듈의 examples.install_all을 한 줄 더한다
# (모듈을 붙이는 곳 가운데 작업 실행기 쪽).
FIRST_RUN_EXAMPLES: tuple[Callable[[AsyncSession], Awaitable[int]], ...] = (
    classification_examples.install_all,  # 분류: 백과 문장 주제 · 문의 의도
    retrieval_examples.install_all,  # 검색: 백과 문서 · 질문과 지문 · 질의와 오답 · 회사 규정과 FAQ
)


@dataclass(frozen=True)
class EmbeddingState:
    """작업 실행기가 마지막으로 확인한 임베딩 연결."""

    # 확인한 연결의 (주소, 모델). 연결이 없었으면 None.
    target: tuple[str, str | None] | None

    # 확인 결과
    check: ConnectionCheck

    # 확인한 시각 (time.monotonic)
    checked_at: float


def _listen_for_stop(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    """종료하라는 신호를 받으면 stop을 켠다.

    Mac · Linux: SIGINT · SIGTERM(런처가 보낸다). Windows는 이벤트 루프가 신호를 받지 못해 signal.signal로 받고,
    런처는 Ctrl+Break(SIGBREAK)로 종료하라고 알린다.
    """
    if sys.platform != "win32":
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        return

    def on_signal(_signum: int, _frame: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signal.signal(getattr(signal, name), on_signal)


async def run() -> int:
    """작업 실행기를 실행하고, 종료할 때까지 작업을 처리한다. 실행할 때 확인에서 멈추면 1을 돌려준다."""
    stop = asyncio.Event()
    _listen_for_stop(asyncio.get_running_loop(), stop)

    try:
        settings = get_settings()
        setup_logging(settings, process="worker", label=LABEL)
        logger.info(settings.describe())
        check_storage(settings)
        engine = init_engine(statement_timeout_ms=None)
        await check_database(engine, settings)
    except StartupError as error:
        report_startup_error(error, label=LABEL)
        await dispose_engine()
        return 1

    lock_conn = await engine.connect()
    has_lock = False
    try:
        has_lock = await jobs.try_acquire_worker_lock(lock_conn)
        # 잠금은 연결에 붙어 있으므로 트랜잭션을 닫아도 남는다. 열린 트랜잭션을 오래 두지 않게 닫는다.
        await lock_conn.commit()
        if not has_lock:
            logger.info("작업 실행기가 이미 실행 중입니다. 새로 띄우지 않고 끝냅니다.")
            return 0

        embedding_state = await _check_embedding(quiet=False)
        await _recover_and_clean()
        await _install_first_run_examples()
        async with get_sessionmaker()() as session:
            queued = await jobs.count_queued(session)
        logger.info(
            "준비됐습니다. 대기 작업 %d건 · 임베딩: %s", queued, embedding_state.check.detail
        )
        await _work(stop, embedding_state)
    except StartupError as error:
        report_startup_error(error, label=LABEL)
        return 1
    finally:
        await _close_lock(lock_conn, has_lock=has_lock)
        await dispose_engine()

    logger.info("작업 실행기를 종료했습니다.")
    return 0


async def _check_embedding(
    *, quiet: bool, transport: httpx.AsyncBaseTransport | None = None
) -> EmbeddingState:
    """저장된 임베딩 연결을 읽어 확인하고, 되면 모델을 등록한다. 예외를 내지 않는다.

    같은 이름의 모델이 다른 차원으로 등록돼 있으면 실패로 두고 계속 돈다(멈추지 않는다).
    """
    async with get_sessionmaker()() as session:
        connection = await connections.get_connection(session, role=ConnectionRole.EMBEDDING)
        if connection is None:
            if not quiet:
                logger.info("임베딩: 미연결. 화면의 연결 설정에서 연결하면 이어서 합니다.")
            return EmbeddingState(target=None, check=NOT_CONNECTED, checked_at=time.monotonic())

        target = (connection.base_url, connection.model)
        result = await embedding.check_embedding(session, connection, transport=transport)
        dim = result.facts.get("dim")
        if result.ok and isinstance(dim, int) and connection.model:
            await embedding.register_model(session, name=connection.model, dim=dim)
            logger.info("임베딩 서버: 연결됨 · %s · %s", connection.model, result.detail)
        elif not quiet:
            _log_embedding_failure(connection.base_url, result.detail)
    return EmbeddingState(target=target, check=result, checked_at=time.monotonic())


def _log_embedding_failure(base_url: str, detail: str) -> None:
    """임베딩 확인이 실패한 것을 남긴다. 내장 모델이면 실행하는 동안(모델을 받거나 불러오는 중)이라 경고로 두지 않는다."""
    is_embedded = embedded_models.chosen_model(ConnectionRole.EMBEDDING) is not None
    if is_embedded:
        logger.info("임베딩: 내장 모델을 준비하는 중입니다(%s). 준비되면 이어서 합니다.", detail)
        return
    logger.warning("임베딩 서버: 실패 · %s · %s", base_url, detail)


async def _refresh_embedding(
    state: EmbeddingState, *, transport: httpx.AsyncBaseTransport | None = None
) -> EmbeddingState:
    """연결이 바뀌었거나, 실패한 뒤 1분이 지났으면 다시 확인한다. 아니면 그대로 둔다."""
    async with get_sessionmaker()() as session:
        connection = await connections.get_connection(session, role=ConnectionRole.EMBEDDING)
    target = None if connection is None else (connection.base_url, connection.model)
    is_changed = target != state.target
    is_retry_due = (
        target is not None
        and not state.check.ok
        and time.monotonic() - state.checked_at >= EMBEDDING_RECHECK_INTERVAL_S
    )
    if not is_changed and not is_retry_due:
        return state
    # 바뀐 연결은 결과를 로그에 남긴다. 같은 연결을 다시 시도할 때는 되었을 때만 남긴다.
    return await _check_embedding(quiet=not is_changed, transport=transport)


async def _recover_and_clean() -> None:
    """지난번에 끊긴 작업을 되살리고, 하루가 지났으면 정리 작업을 돈다."""
    async with get_sessionmaker()() as session:
        recovered = await jobs.recover_interrupted_jobs(session)
    if recovered.requeued or recovered.failed:
        logger.info(
            "지난번에 끊긴 작업을 되살렸습니다: 다시 대기 %d건, 실패 처리 %d건.",
            recovered.requeued,
            len(recovered.failed),
        )
    for job in recovered.failed:
        await _clean_up_failed(job)
    try:
        async with get_sessionmaker()() as session:
            if await jobs.is_cleanup_due(session):
                cleaned = await jobs.run_cleanup(session)
                deleted_exports = await exports.discard_expired_exports(session)
                await session.commit()
                logger.info(
                    "정리 작업을 마쳤습니다: 오래된 작업 기록 %d건 · 가져오지 않은 올린 파일 %d개 · "
                    "보관 기간이 지난 내보낸 파일 %d개 삭제.",
                    cleaned.deleted_jobs,
                    cleaned.deleted_uploads,
                    deleted_exports,
                )
    except Exception:  # 정리는 실패해도 실행을 막지 않는다. 다음에 다시 한다.
        logger.warning("정리 작업을 하지 못했습니다. 다음에 다시 합니다.", exc_info=True)


async def _install_first_run_examples() -> int:
    """처음 실행할 때 딱 한 번 모든 모듈의 내장 예시를 넣는다(외부 · 내장 DB 모두, 가져오기 작업으로). 넣기 시작한 수.

    한 번 한 DB는 표시(system_flags)가 있어 예시를 지워도 다시 넣지 않는다. 이 기능 전부터 데이터셋이 있던 DB는
    쓰던 DB라 넣지 않고 표시만 남긴다. 실패하면 표시를 남기지 않아 다음에 실행할 때 다시 한다(실행은 막지 않는다).
    """
    started = 0
    try:
        async with get_sessionmaker()() as session:
            if await examples.is_first_run_done(session):
                return 0
            is_fresh = not await examples.has_any_dataset(session)
            if is_fresh:
                for install_all in FIRST_RUN_EXAMPLES:
                    started += await install_all(session)
            await examples.mark_first_run_done(session)
            await session.commit()
    except Exception:
        logger.warning("내장 예시를 넣지 못했습니다. 다음에 실행할 때 다시 합니다.", exc_info=True)
        return started
    if started:
        logger.info("처음 실행해서 내장 예시 %d개를 넣습니다(가져오기 작업).", started)
    return started


async def _clean_up_failed(job: Job) -> None:
    """실패로 둔 작업을 그 모듈의 뒷정리 함수에 넘긴다. 뒷정리가 실패해도 실행은 막지 않는다."""
    clean_up = FAILURE_HANDLERS.get((job.module, job.kind))
    if clean_up is None:
        return
    try:
        await clean_up(job)
    except Exception:  # 뒷정리 하나가 실패해도 작업 실행기는 실행돼야 한다. 로그로 알린다.
        logger.exception("작업 %d (%s.%s)을 뒷정리하지 못했습니다.", job.id, job.module, job.kind)


async def _work(stop: asyncio.Event, embedding_state: EmbeddingState) -> None:
    """종료할 때까지 대기열에서 작업을 하나씩 꺼내 처리한다. 사이사이 임베딩 연결이 바뀌었는지 본다."""
    last_connection_poll = time.monotonic()
    while not stop.is_set():
        is_poll_due = time.monotonic() - last_connection_poll >= CONNECTION_POLL_INTERVAL_S
        if is_poll_due:
            embedding_state = await _refresh_embedding(embedding_state)
            last_connection_poll = time.monotonic()

        async with get_sessionmaker()() as session:
            job = await jobs.claim_next_job(session)
        if job is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_S)
            continue
        await _handle(job)


async def _handle(job: Job) -> None:
    """작업 하나를 처리 함수에 넘긴다. 처리 함수가 없거나 실패하면 작업을 실패로 끝낸다."""
    handler = HANDLERS.get((job.module, job.kind))
    if handler is None:
        message = (
            f"처리할 수 없는 작업 종류입니다: {job.module}.{job.kind}. 프로그램을 업데이트하세요."
        )
        logger.warning("작업 %d: %s", job.id, message)
        async with get_sessionmaker()() as session:
            await jobs.fail_job(session, job_id=job.id, message=message)
        return
    try:
        await handler(job)
    except Exception:  # 처리 함수의 실패가 작업 실행기 전체를 멈추지 않게 한다.
        logger.exception("작업 %d (%s.%s)이 실패했습니다.", job.id, job.module, job.kind)
        async with get_sessionmaker()() as session:
            await jobs.fail_job(
                session, job_id=job.id, message="작업 중 오류가 났습니다. 로그를 확인하세요."
            )


async def _close_lock(lock_conn: AsyncConnection, *, has_lock: bool) -> None:
    """작업 실행기 자리를 내놓고 잠금 연결을 닫는다."""
    with contextlib.suppress(Exception):  # 종료할 때 DB가 먼저 사라졌어도 조용히 끝낸다.
        if has_lock:
            await jobs.release_worker_lock(lock_conn)
            await lock_conn.commit()
        await lock_conn.close()


def main() -> int:
    """작업 실행기를 실행한다."""
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
