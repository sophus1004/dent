"""검색 모듈 테스트 도우미: 파일을 올려 끝까지 가져오기, 가짜 임베딩 · Jev 연결, 뜻 분석 끝까지 돌리기.

파일 올리기와 가짜 외부 서버는 시스템 도우미(tests.system.helpers)를 쓴다.
"""

import csv
import io
import json
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import semantic
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.jobs import run_analysis_job, run_import_job
from dent.modules.retrieval.schemas import ImportCreate
from dent.system import connections as connections_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.models import ConnectionRole, Import
from tests.system.helpers import EMBEDDING_MODEL, EMBEDDING_URL, JEV_URL, upload


def csv_bytes(header: list[str], rows: list[list[Any]]) -> bytes:
    """머리 줄과 줄들로 CSV 내용을 만든다."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode()


async def import_and_run(
    db_session: AsyncSession,
    data: ImportCreate,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Import:
    """가져오기를 대기열에 넣고, 작업 실행기처럼 처리 함수를 불러 끝까지 돌린다."""
    created = await retrieval_service.create_import(db_session, data=data)
    assert created.job_id is not None
    job = await jobs.get_job(db_session, job_id=created.job_id)
    await run_import_job(job, transport=transport)
    return await imports_service.get_import(db_session, import_id=created.id)


async def import_csv(
    db_session: AsyncSession,
    *,
    header: list[str],
    rows: list[list[Any]],
    mapping: dict[str, Any],
    name: str = "검색 데이터",
    dataset_id: int | None = None,
) -> Import:
    """CSV를 올리고 끝까지 가져온다. dataset_id가 있으면 그 데이터셋에 더한다."""
    preview = await upload(db_session, "검색.csv", csv_bytes(header, rows))
    values: dict[str, Any] = {"source": "file", "upload_id": preview.upload_id, "mapping": mapping}
    if dataset_id is None:
        values["new_dataset_name"] = name
    else:
        values["dataset_id"] = dataset_id
    return await import_and_run(db_session, ImportCreate.model_validate(values))


async def connect_embedding(db_session: AsyncSession) -> None:
    """가짜 임베딩 서버를 연결한 것처럼 저장한다."""
    await connections_service.save_connection(
        db_session, role=ConnectionRole.EMBEDDING, base_url=EMBEDDING_URL, model=EMBEDDING_MODEL
    )


async def connect_jev(db_session: AsyncSession) -> None:
    """가짜 Jev 서버를 연결한 것처럼 저장한다."""
    await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
    )


async def build_analysis(
    db_session: AsyncSession,
    *,
    dataset_id: int,
    transport: httpx.AsyncBaseTransport,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> semantic.AnalysisState:
    """[뜻 분석 만들기]를 누르고 작업 실행기처럼 끝까지 돌린 뒤 상태를 돌려준다."""
    state = await semantic.start_analysis(db_session, dataset_id=dataset_id)
    assert state.run is not None and state.run.job_id is not None
    job = await jobs.get_job(db_session, job_id=state.run.job_id)
    await run_analysis_job(job, transport=transport, jev_transport=jev_transport)
    return await semantic.get_analysis_state(db_session, dataset_id=dataset_id)


def contains_jev(yes: float) -> httpx.MockTransport:
    """'답을 담나'에 늘 같은 예 확률을 주는 가짜 Jev. 받은 요청은 .requests에."""
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        answer = {
            "type": "choice",
            "choice": "yes" if yes >= 0.5 else "no",
            "probabilities": {"yes": yes, "no": 1 - yes},
            "confidence": 0.8,
        }
        return httpx.Response(
            200, json={"model": "laya", "answers": {name: answer for name in body["questions"]}}
        )

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport
