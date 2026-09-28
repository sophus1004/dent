"""분류 모듈이 작업 실행기에 맡기는 일: 가져오기(import), 뜻 분석(map, 의미 지도 포함),
LLM 도우미(helper: 진단을 읽고 고치기 · 사람이 고른 남은 것을 AI로 고치기), 내보내기(export).

작업 실행기(dent.worker)가 HANDLERS와 FAILURE_HANDLERS를 가져가 쓴다.

처리 함수는 작업 한 줄을 받아 자기 세션을 열고 일을 한다. 실패하면 모듈의 기록(가져오기 기록 · 지도)과
작업을 모두 같은 한국어 문장으로 실패로 적어서, 화면이 '가져오는 중' · '만드는 중'에 멈춰 있지 않게 한다.
"""

import logging
from collections.abc import Awaitable, Callable

import httpx

from dent.modules.classification import exporting, helper, importing, semantic_map
from dent.modules.classification import service as classification_service
from dent.system import exports as exports_service
from dent.system import helper as helper_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.db import get_sessionmaker
from dent.system.exceptions import AppError
from dent.system.models import HelperRunStatus, Job

logger = logging.getLogger("dent.classification")

UNEXPECTED_ERROR_MESSAGE = "가져오는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
INTERRUPTED_MESSAGE = "가져오기가 여러 번 중간에 끊겨 멈췄습니다. 다시 가져와 주세요."
MAP_UNEXPECTED_ERROR_MESSAGE = "뜻 분석을 만드는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
MAP_INTERRUPTED_MESSAGE = "뜻 분석이 여러 번 중간에 끊겨 멈췄습니다. 다시 만들어 주세요."
HELPER_UNEXPECTED_ERROR_MESSAGE = "도우미가 일하는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
HELPER_INTERRUPTED_MESSAGE = "도우미가 여러 번 중간에 끊겨 멈췄습니다. 다시 시작해 주세요."
EXPORT_UNEXPECTED_ERROR_MESSAGE = "파일을 만드는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
EXPORT_INTERRUPTED_MESSAGE = "내보내기가 여러 번 중간에 끊겨 멈췄습니다. 다시 만들어 주세요."


async def run_import_job(job: Job, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """가져오기 작업 하나를 처리한다. transport는 테스트가 허깅페이스 대신 가짜 응답을 줄 때만."""
    import_id = int(job.params["import_id"])
    try:
        async with get_sessionmaker()() as session:
            await importing.run_import(
                session, import_id=import_id, job_id=job.id, transport=transport
            )
    except AppError as error:
        # 원본이나 필드 맞춤이 틀린 것: 사용자가 고칠 수 있는 문제라 그 문장을 그대로 보여 준다.
        logger.warning("가져오기 %d번이 실패했습니다: %s", import_id, error.message)
        await _fail(import_id=import_id, job_id=job.id, message=error.message)
        return
    except Exception:
        # 뜻밖의 오류: 자세한 내용은 로그에 남기고, 기록과 작업에는 같은 한국어 문장을 적는다.
        # 다시 던지지 않는다. 던지면 작업 실행기가 작업의 문장을 자기 문장으로 덮어쓴다.
        logger.exception("가져오기 %d번이 뜻밖의 오류로 멈췄습니다.", import_id)
        await _fail(import_id=import_id, job_id=job.id, message=UNEXPECTED_ERROR_MESSAGE)
        return

    # 청소는 빠르게 하려는 것일 뿐이다. 못 해도 가져오기는 끝났고 DB가 나중에 한다.
    try:
        await importing.vacuum_records()
    except Exception:
        logger.warning(
            "가져온 뒤 문장 표를 청소하지 못했습니다. 나중에 DB가 합니다.", exc_info=True
        )


async def fail_interrupted_import(job: Job) -> None:
    """너무 여러 번 끊겨 실패로 둔 가져오기를 뒷정리한다. 반쯤 들어간 문장을 지운다."""
    import_id = int(job.params["import_id"])
    await _fail(import_id=import_id, job_id=job.id, message=job.error or INTERRUPTED_MESSAGE)


async def _fail(*, import_id: int, job_id: int, message: str) -> None:
    """새 세션으로 가져오기와 작업을 실패로 끝낸다. 실패한 세션은 쓸 수 없는 상태일 수 있다."""
    async with get_sessionmaker()() as session:
        await importing.fail_import(session, import_id=import_id, job_id=job_id, message=message)


async def run_map_job(
    job: Job,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """뜻 분석(의미 지도 포함) 작업 하나를 처리한다.

    transport · jev_transport는 테스트가 임베딩 · Jev 서버 대신 가짜 응답을 줄 때만.
    """
    map_id = int(job.params["map_id"])
    try:
        async with get_sessionmaker()() as session:
            await semantic_map.build_map(
                session,
                map_id=map_id,
                job_id=job.id,
                transport=transport,
                jev_transport=jev_transport,
            )
    except AppError as error:
        # 임베딩 서버 · 연결 문제처럼 사용자가 고칠 수 있는 것: 그 문장을 그대로 보여 준다.
        logger.warning("의미 지도 %d번이 실패했습니다: %s", map_id, error.message)
        await _fail_map(map_id=map_id, job_id=job.id, message=error.message)
    except Exception:
        # 다시 던지지 않는다. 던지면 작업 실행기가 작업의 문장을 자기 문장으로 덮어쓴다.
        logger.exception("의미 지도 %d번이 뜻밖의 오류로 멈췄습니다.", map_id)
        await _fail_map(map_id=map_id, job_id=job.id, message=MAP_UNEXPECTED_ERROR_MESSAGE)


async def fail_interrupted_map(job: Job) -> None:
    """너무 여러 번 끊겨 실패로 둔 의미 지도를 뒷정리한다. 지도를 실패로 두고 반쯤 적은 점을 지운다."""
    map_id = int(job.params["map_id"])
    message = job.error or MAP_INTERRUPTED_MESSAGE
    await _fail_map(map_id=map_id, job_id=job.id, message=message)


async def _fail_map(*, map_id: int, job_id: int, message: str) -> None:
    """새 세션으로 지도와 작업을 실패로 끝낸다. 실패한 세션은 쓸 수 없는 상태일 수 있다."""
    async with get_sessionmaker()() as session:
        await semantic_map.fail_map(session, map_id=map_id, job_id=job_id, message=message)


async def run_helper_job(
    job: Job,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
    embedding_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """도우미 실행(또는 AI로 고치기) 하나를 처리하고, 실행 상태에 맞춰 작업을 끝낸다.

    실패(LLM 답 없음 등)는 작업도 같은 문장으로 실패, 멈춤은 취소, 나머지는 결과(바꾼 수 · 토큰)와 함께 끝낸다.
    transport · jev_transport · embedding_transport는 테스트가 서버 대신 가짜 응답을 줄 때만.
    """
    run_id = int(job.params["run_id"])
    try:
        async with get_sessionmaker()() as session:
            await helper.run_helper(
                session,
                run_id=run_id,
                transport=transport,
                jev_transport=jev_transport,
                embedding_transport=embedding_transport,
                fix=[str(key) for key in job.params["fix"]] if "fix" in job.params else None,
                round_no=int(job.params.get("round", 0)),
                adds=job.params.get("adds") or None,
            )
    except Exception:
        # 다시 던지지 않는다. 던지면 작업 실행기가 작업의 문장을 자기 문장으로 덮어쓴다.
        logger.exception("도우미 실행 %d번이 뜻밖의 오류로 멈췄습니다.", run_id)
        async with get_sessionmaker()() as session:
            await helper.fail_run(session, run_id=run_id, message=HELPER_UNEXPECTED_ERROR_MESSAGE)
    async with get_sessionmaker()() as session:
        run = await helper_service.get_run(session, run_id=run_id)
        if run.status == HelperRunStatus.FAILED:
            await jobs.fail_job(
                session, job_id=job.id, message=run.error or HELPER_UNEXPECTED_ERROR_MESSAGE
            )
        elif run.status == HelperRunStatus.STOPPED:
            await jobs.cancel_job(session, job_id=job.id)
        else:
            await jobs.finish_job(
                session,
                job_id=job.id,
                result={
                    "status": run.status,
                    "changed": run.changed,
                    "held": run.held,
                    "llm_tokens": run.llm_tokens,
                    "jev_calls": run.jev_calls,
                },
            )
        # fail_job은 스스로 커밋하고, 취소 · 끝내기는 부른 쪽이 커밋한다.
        await session.commit()


async def fail_interrupted_helper(job: Job) -> None:
    """너무 여러 번 끊겨 실패로 둔 도우미 실행을 실패로 맞춘다. 바꾼 것은 변경 기록으로 되돌릴 수 있다."""
    async with get_sessionmaker()() as session:
        await helper.fail_run(
            session,
            run_id=int(job.params["run_id"]),
            message=job.error or HELPER_INTERRUPTED_MESSAGE,
        )


async def run_export_job(job: Job) -> None:
    """내보낼 파일 하나를 만든다. 실패하면 내보내기와 작업을 같은 문장으로 실패로 적고 반쯤 쓴 파일을 지운다."""
    export_id = int(job.params["export_id"])
    try:
        async with get_sessionmaker()() as session:
            await exporting.build_export(session, export_id=export_id, job_id=job.id)
    except AppError as error:
        logger.warning("내보내기 %d번이 실패했습니다: %s", export_id, error.message)
        await _fail_export(export_id=export_id, job_id=job.id, message=error.message)
    except Exception:
        logger.exception("내보내기 %d번이 뜻밖의 오류로 멈췄습니다.", export_id)
        await _fail_export(
            export_id=export_id, job_id=job.id, message=EXPORT_UNEXPECTED_ERROR_MESSAGE
        )


async def fail_interrupted_export(job: Job) -> None:
    """너무 여러 번 끊겨 실패로 둔 내보내기를 실패로 맞추고 반쯤 쓴 파일을 지운다."""
    await _fail_export(
        export_id=int(job.params["export_id"]),
        job_id=job.id,
        message=job.error or EXPORT_INTERRUPTED_MESSAGE,
    )


async def _fail_export(*, export_id: int, job_id: int, message: str) -> None:
    async with get_sessionmaker()() as session:
        await session.rollback()
        await exports_service.fail_export(
            session, export_id=export_id, job_id=job_id, message=message
        )


# 이 모듈의 작업 (모듈 이름, 작업 종류): 가져오기 · 의미 지도 · 도우미 · 내보내기
IMPORT_JOB = (classification_service.MODULE_NAME, imports_service.IMPORT_JOB_KIND)
MAP_JOB = (classification_service.MODULE_NAME, semantic_map.MAP_JOB_KIND)
HELPER_JOB = (classification_service.MODULE_NAME, helper.HELPER_JOB_KIND)
EXPORT_JOB = (classification_service.MODULE_NAME, exports_service.EXPORT_JOB_KIND)

# (모듈 이름, 작업 종류)별 처리 함수. 작업 실행기의 HANDLERS에 그대로 더해진다.
HANDLERS: dict[tuple[str, str], Callable[[Job], Awaitable[None]]] = {
    IMPORT_JOB: run_import_job,
    MAP_JOB: run_map_job,
    HELPER_JOB: run_helper_job,
    EXPORT_JOB: run_export_job,
}

# (모듈 이름, 작업 종류)별 뒷정리 함수. 작업 실행기가 실행될 때, 너무 여러 번 끊겨 실패로 둔 작업을
# 넘겨 모듈의 기록도 실패로 맞추게 한다. 작업 실행기의 FAILURE_HANDLERS에 그대로 더해진다.
FAILURE_HANDLERS: dict[tuple[str, str], Callable[[Job], Awaitable[None]]] = {
    IMPORT_JOB: fail_interrupted_import,
    MAP_JOB: fail_interrupted_map,
    HELPER_JOB: fail_interrupted_helper,
    EXPORT_JOB: fail_interrupted_export,
}
