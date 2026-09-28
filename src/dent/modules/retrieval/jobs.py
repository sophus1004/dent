"""검색 모듈이 작업 실행기에 맡기는 일: 가져오기(import), 뜻 분석(analysis), 도우미(helper),
오답 찾기(mine), 오답 훑기(negative_scan), 반복 구간 살피기(scan), 내보내기(export).

작업 실행기(dent.worker)가 HANDLERS와 FAILURE_HANDLERS를 가져가 쓴다.
처리 함수는 작업 한 줄을 받아 자기 세션을 열고 일을 한다. 실패하면 모듈의 기록(가져오기 · 분석 · 실행 · 내보내기)과
작업을 모두 같은 한국어 문장으로 실패로 적어서, 화면이 '도는 중'에 멈춰 있지 않게 한다.
"""

import logging
from collections.abc import Awaitable, Callable

import httpx

from dent.modules.retrieval import exporting, helper, importing, mining, repeats, semantic
from dent.modules.retrieval import service as retrieval_service
from dent.system import exports as exports_service
from dent.system import helper as helper_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.db import get_sessionmaker
from dent.system.exceptions import AppError
from dent.system.models import HelperRunStatus, Job

logger = logging.getLogger("dent.retrieval")

UNEXPECTED_ERROR_MESSAGE = "가져오는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
INTERRUPTED_MESSAGE = "가져오기가 여러 번 중간에 끊겨 멈췄습니다. 다시 가져와 주세요."
ANALYSIS_UNEXPECTED_ERROR_MESSAGE = "뜻 분석을 만드는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
ANALYSIS_INTERRUPTED_MESSAGE = "뜻 분석이 여러 번 중간에 끊겨 멈췄습니다. 다시 만들어 주세요."
HELPER_UNEXPECTED_ERROR_MESSAGE = "도우미가 뜻밖의 오류로 멈췄습니다. 로그를 확인하세요."
HELPER_INTERRUPTED_MESSAGE = "도우미가 여러 번 중간에 끊겨 멈췄습니다. 다시 시작해 주세요."
MINE_UNEXPECTED_ERROR_MESSAGE = "오답을 찾는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
NEGATIVE_SCAN_UNEXPECTED_ERROR_MESSAGE = "오답을 훑는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
SCAN_UNEXPECTED_ERROR_MESSAGE = "반복 구간을 살피는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
EXPORT_UNEXPECTED_ERROR_MESSAGE = "파일을 만드는 중 뜻밖의 오류가 났습니다. 로그를 확인하세요."
EXPORT_INTERRUPTED_MESSAGE = "내보내기가 여러 번 중간에 끊겨 멈췄습니다. 다시 만들어 주세요."

JobHandler = Callable[[Job], Awaitable[None]]


# ---------- 가져오기 ----------


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
        await _fail_import(import_id=import_id, job_id=job.id, message=error.message)
        return
    except Exception:
        # 다시 던지지 않는다. 던지면 작업 실행기가 작업의 문장을 자기 문장으로 덮어쓴다.
        logger.exception("가져오기 %d번이 뜻밖의 오류로 멈췄습니다.", import_id)
        await _fail_import(import_id=import_id, job_id=job.id, message=UNEXPECTED_ERROR_MESSAGE)
        return
    # 청소는 빠르게 하려는 것일 뿐이다. 못 해도 가져오기는 끝났고 DB가 나중에 한다.
    try:
        await importing.vacuum_tables()
    except Exception:
        logger.warning("가져온 뒤 표를 청소하지 못했습니다. 나중에 DB가 합니다.", exc_info=True)


async def fail_interrupted_import(job: Job) -> None:
    """너무 여러 번 끊겨 실패로 둔 가져오기를 뒷정리한다. 반쯤 들어간 줄을 지운다."""
    import_id = int(job.params["import_id"])
    await _fail_import(import_id=import_id, job_id=job.id, message=job.error or INTERRUPTED_MESSAGE)


async def _fail_import(*, import_id: int, job_id: int, message: str) -> None:
    """새 세션으로 가져오기와 작업을 실패로 끝낸다. 실패한 세션은 쓸 수 없는 상태일 수 있다."""
    async with get_sessionmaker()() as session:
        await importing.fail_import(session, import_id=import_id, job_id=job_id, message=message)


# ---------- 뜻 분석 ----------


async def run_analysis_job(
    job: Job,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """뜻 분석 하나를 만든다. transport · jev_transport는 테스트가 가짜 임베딩 · Jev를 줄 때만."""
    analysis_id = int(job.params["analysis_id"])
    try:
        async with get_sessionmaker()() as session:
            await semantic.build_analysis(
                session,
                analysis_id=analysis_id,
                job_id=job.id,
                transport=transport,
                jev_transport=jev_transport,
            )
    except AppError as error:
        logger.warning("뜻 분석 %d번이 실패했습니다: %s", analysis_id, error.message)
        await _fail_analysis(analysis_id=analysis_id, job_id=job.id, message=error.message)
    except Exception:
        logger.exception("뜻 분석 %d번이 뜻밖의 오류로 멈췄습니다.", analysis_id)
        await _fail_analysis(
            analysis_id=analysis_id, job_id=job.id, message=ANALYSIS_UNEXPECTED_ERROR_MESSAGE
        )


async def fail_interrupted_analysis(job: Job) -> None:
    """너무 여러 번 끊겨 실패로 둔 뜻 분석을 실패로 맞추고 반쯤 적은 결과를 지운다."""
    await _fail_analysis(
        analysis_id=int(job.params["analysis_id"]),
        job_id=job.id,
        message=job.error or ANALYSIS_INTERRUPTED_MESSAGE,
    )


async def _fail_analysis(*, analysis_id: int, job_id: int, message: str) -> None:
    async with get_sessionmaker()() as session:
        await semantic.fail_analysis(
            session, analysis_id=analysis_id, job_id=job_id, message=message
        )


# ---------- 도우미 ----------


async def run_helper_job(
    job: Job,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
    embedding_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """도우미 실행(또는 허락 뒤 · 뜻 분석을 새로 만든 뒤 이어 돌기) 하나를 처리하고, 실행 상태에 맞춰 작업을 끝낸다.

    실패는 작업도 같은 문장으로 실패, 멈춤은 취소, 허락 기다림 · 끝남 · 분석 기다림(도는 중)은
    결과(바꾼 수 · 토큰)와 함께 끝낸다.
    """
    run_id = int(job.params["run_id"])
    resume = job.params.get("resume")
    continue_from = job.params.get("continue_from")
    try:
        async with get_sessionmaker()() as session:
            await helper.run_helper(
                session,
                run_id=run_id,
                resume=str(resume) if resume else None,
                approved=bool(job.params.get("approved", False)),
                continue_from=str(continue_from) if continue_from else None,
                fix=[str(key) for key in job.params["fix"]] if "fix" in job.params else None,
                round_no=int(job.params.get("round", 0)),
                waited_fix=bool(job.params.get("waited", False)),
                transport=transport,
                jev_transport=jev_transport,
                embedding_transport=embedding_transport,
            )
    except Exception:
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
        await session.commit()


async def fail_interrupted_helper(job: Job) -> None:
    """너무 여러 번 끊겨 실패로 둔 도우미 실행을 실패로 맞춘다. 바꾼 것은 변경 기록으로 되돌릴 수 있다."""
    async with get_sessionmaker()() as session:
        await helper.fail_run(
            session,
            run_id=int(job.params["run_id"]),
            message=job.error or HELPER_INTERRUPTED_MESSAGE,
        )


# ---------- 오답 찾기 ----------


async def run_mine_job(
    job: Job,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """오답 찾기(또는 다시 찾기) 작업 하나를 처리한다."""
    dataset_id = int(job.params["dataset_id"])
    try:
        async with get_sessionmaker()() as session:

            async def canceled() -> bool:
                return await jobs.is_cancel_requested(session, job_id=job.id)

            result = await mining.mine_negatives(
                session,
                dataset_id=dataset_id,
                job_id=job.id,
                remine=bool(job.params.get("remine", False)),
                is_canceled=canceled,
                transport=transport,
                jev_transport=jev_transport,
            )
            if result is None:
                await jobs.cancel_job(session, job_id=job.id)
            else:
                await jobs.finish_job(
                    session,
                    job_id=job.id,
                    result={
                        "queries": result.queries,
                        "added": result.added,
                        "jev_rejected": result.jev_rejected,
                        "lacking": result.lacking,
                        "rank_from": result.rank_from,
                        "rank_to": result.rank_to,
                        "new_documents": result.new_documents,
                    },
                )
            await session.commit()
    except AppError as error:
        logger.warning("오답 찾기(데이터셋 %d)가 실패했습니다: %s", dataset_id, error.message)
        await _fail_job(job_id=job.id, message=error.message)
    except Exception:
        logger.exception("오답 찾기(데이터셋 %d)가 뜻밖의 오류로 멈췄습니다.", dataset_id)
        await _fail_job(job_id=job.id, message=MINE_UNEXPECTED_ERROR_MESSAGE)


async def run_negative_scan_job(
    job: Job,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """오답 훑기 작업 하나를 처리한다(결과는 데이터셋 설정에 적는다)."""
    dataset_id = int(job.params["dataset_id"])
    try:
        async with get_sessionmaker()() as session:

            async def canceled() -> bool:
                return await jobs.is_cancel_requested(session, job_id=job.id)

            result = await mining.scan_negatives(
                session,
                dataset_id=dataset_id,
                job_id=job.id,
                is_canceled=canceled,
                transport=transport,
                jev_transport=jev_transport,
            )
            if result is None:
                await jobs.cancel_job(session, job_id=job.id)
            else:
                await jobs.finish_job(
                    session,
                    job_id=job.id,
                    result={"queries": result["queries"], "cells": len(result["cells"])},
                )
            await session.commit()
    except AppError as error:
        logger.warning("오답 훑기(데이터셋 %d)가 실패했습니다: %s", dataset_id, error.message)
        await _fail_job(job_id=job.id, message=error.message)
    except Exception:
        logger.exception("오답 훑기(데이터셋 %d)가 뜻밖의 오류로 멈췄습니다.", dataset_id)
        await _fail_job(job_id=job.id, message=NEGATIVE_SCAN_UNEXPECTED_ERROR_MESSAGE)


# ---------- 반복 구간 살피기 ----------


async def run_scan_job(job: Job) -> None:
    """반복 구간 살피기 작업 하나를 처리한다(모든 문서의 학습 글 · 표시를 다시 만든다)."""
    dataset_id = int(job.params["dataset_id"])
    try:
        async with get_sessionmaker()() as session:
            result = await repeats.scan_dataset(session, dataset_id=dataset_id, job_id=job.id)
            await jobs.finish_job(
                session,
                job_id=job.id,
                result={
                    "documents": result.documents,
                    "sentences": result.sentences,
                    "metas": result.metas,
                    "changed": result.changed,
                },
            )
            await session.commit()
    except AppError as error:
        logger.warning(
            "반복 구간 살피기(데이터셋 %d)가 실패했습니다: %s", dataset_id, error.message
        )
        await _fail_job(job_id=job.id, message=error.message)
    except Exception:
        logger.exception("반복 구간 살피기(데이터셋 %d)가 뜻밖의 오류로 멈췄습니다.", dataset_id)
        await _fail_job(job_id=job.id, message=SCAN_UNEXPECTED_ERROR_MESSAGE)


async def _fail_job(*, job_id: int, message: str) -> None:
    async with get_sessionmaker()() as session:
        await session.rollback()
        await jobs.fail_job(session, job_id=job_id, message=message)


# ---------- 내보내기 ----------


async def run_export_job(
    job: Job, *, jev_transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """내보낼 파일 하나를 만든다."""
    export_id = int(job.params["export_id"])
    try:
        async with get_sessionmaker()() as session:
            await exporting.build_export(
                session, export_id=export_id, job_id=job.id, jev_transport=jev_transport
            )
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


# 이 모듈의 작업 (모듈 이름, 작업 종류)
IMPORT_JOB = (retrieval_service.MODULE_NAME, imports_service.IMPORT_JOB_KIND)
ANALYSIS_JOB = (retrieval_service.MODULE_NAME, semantic.ANALYSIS_JOB_KIND)
HELPER_JOB = (retrieval_service.MODULE_NAME, helper.HELPER_JOB_KIND)
MINE_JOB = (retrieval_service.MODULE_NAME, mining.MINE_JOB_KIND)
NEGATIVE_SCAN_JOB = (retrieval_service.MODULE_NAME, mining.NEGATIVE_SCAN_JOB_KIND)
SCAN_JOB = (retrieval_service.MODULE_NAME, repeats.SCAN_JOB_KIND)
EXPORT_JOB = (retrieval_service.MODULE_NAME, exports_service.EXPORT_JOB_KIND)

# (모듈 이름, 작업 종류)별 처리 함수. 작업 실행기의 HANDLERS에 그대로 더해진다.
HANDLERS: dict[tuple[str, str], JobHandler] = {
    IMPORT_JOB: run_import_job,
    ANALYSIS_JOB: run_analysis_job,
    HELPER_JOB: run_helper_job,
    MINE_JOB: run_mine_job,
    NEGATIVE_SCAN_JOB: run_negative_scan_job,
    SCAN_JOB: run_scan_job,
    EXPORT_JOB: run_export_job,
}

# (모듈 이름, 작업 종류)별 뒷정리 함수. 작업 실행기의 FAILURE_HANDLERS에 그대로 더해진다.
FAILURE_HANDLERS: dict[tuple[str, str], JobHandler] = {
    IMPORT_JOB: fail_interrupted_import,
    ANALYSIS_JOB: fail_interrupted_analysis,
    HELPER_JOB: fail_interrupted_helper,
    EXPORT_JOB: fail_interrupted_export,
}
