"""분류 모듈 테스트 도우미: 테스트용 줄 모양, 가져오기 실행, 의미 지도 만들기.

파일 올리기와 가짜 허깅페이스 · 임베딩 서버는 시스템 도우미(tests.system.helpers)를 쓴다.
"""

from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import semantic_map
from dent.modules.classification import service as classification_service
from dent.modules.classification.jobs import run_import_job, run_map_job
from dent.modules.classification.models import Label, Record
from dent.modules.classification.schemas import ImportCreate
from dent.system import connections as connections_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.models import ConnectionRole, Dataset, Import
from tests.system.helpers import EMBEDDING_MODEL, EMBEDDING_URL


@dataclass
class Row:
    """테스트용 문장 한 줄."""

    # 문장
    text: str

    # 라벨 이름
    label: str

    # 학습에서 뺀 이유
    exclude_reason: str | None = None

    # 휴지통에 넣었는지
    trashed: bool = False


@dataclass
class MadeDataset:
    """테스트용으로 만든 데이터셋."""

    # 데이터셋 (시스템 목록의 한 줄)
    dataset: Dataset

    # {라벨 이름: 라벨}
    labels: dict[str, Label] = field(default_factory=dict)

    # 넣은 순서대로의 문장들
    records: list[Record] = field(default_factory=list)


async def import_and_run(
    db_session: AsyncSession,
    data: ImportCreate,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Import:
    """가져오기를 대기열에 넣고, 작업 실행기처럼 처리 함수를 불러 끝까지 돌린다."""
    created = await classification_service.create_import(db_session, data=data)
    assert created.job_id is not None
    job = await jobs.get_job(db_session, job_id=created.job_id)
    await run_import_job(job, transport=transport)
    return await imports_service.get_import(db_session, import_id=created.id)


async def connect_embedding(db_session: AsyncSession) -> None:
    """가짜 임베딩 서버를 연결한 것처럼 저장한다."""
    await connections_service.save_connection(
        db_session, role=ConnectionRole.EMBEDDING, base_url=EMBEDDING_URL, model=EMBEDDING_MODEL
    )


async def run_map(
    db_session: AsyncSession,
    *,
    dataset_id: int,
    transport: httpx.AsyncBaseTransport,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """데이터셋의 대기 중인 뜻 분석 작업을 작업 실행기처럼 처리 함수에 넘겨 끝까지 돌린다."""
    state = await semantic_map.get_map_state(db_session, dataset_id=dataset_id)
    assert state.run is not None and state.run.job_id is not None
    job = await jobs.get_job(db_session, job_id=state.run.job_id)
    await run_map_job(job, transport=transport, jev_transport=jev_transport)


async def build_map(
    db_session: AsyncSession,
    *,
    dataset_id: int,
    transport: httpx.AsyncBaseTransport,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> semantic_map.MapState:
    """뜻 분석 만들기를 누르고 끝까지 돌린 뒤 상태를 돌려준다."""
    await semantic_map.start_map(db_session, dataset_id=dataset_id)
    await run_map(
        db_session, dataset_id=dataset_id, transport=transport, jev_transport=jev_transport
    )
    return await semantic_map.get_map_state(db_session, dataset_id=dataset_id)
