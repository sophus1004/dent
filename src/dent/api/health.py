"""상태 확인 주소. /healthz는 살아 있는지만, /readyz는 쓸 준비가 됐는지 알려 준다."""

from dataclasses import asdict
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from dent.system import status as status_service
from dent.system.config import APP_VERSION, get_settings

router = APIRouter(tags=["상태"])

# /healthz가 돌려주는 앱 이름. 런처는 이 값으로 8000 포트의 주인이 DENT인지 안다.
APP_NAME = "dent"


class HealthRead(BaseModel):
    """살아 있는지에 대한 답."""

    # 앱 이름. 늘 "dent"
    app: str

    # 앱 버전
    version: str


class CheckRead(BaseModel):
    """확인 하나의 결과."""

    # 되는지
    ok: bool

    # 사람이 읽을 한 줄
    detail: str


class ModelInfoRead(BaseModel):
    """홈의 모델 칸 한 줄: 역할 하나의 모델과 상태."""

    # 역할: embedding · jev · llm
    role: str

    # 내장 모델 이름(bge-m3 · laya). 내장이 아니면 null
    embedded: str | None

    # 연결 주소. 미연결이면 null
    base_url: str | None

    # 모델 이름. 비어 있으면 null(Jev 자동 고르기 · 미연결)
    model: str | None

    # LLM 공급자. 없으면 null
    provider: str | None

    # 상태: ok · downloading · loading · failed · off(미연결)
    state: str

    # 값이나 까닭. 예: '1024차원 · 47ms', '연결 거부'. 없으면 ''
    detail: str

    # 내려받기: 받은 · 전체 바이트와 남은 초. 내려받는 중이 아니면 null
    done_bytes: int | None
    total_bytes: int | None
    eta_s: int | None

    # 쓰는 장치 (mps · cuda · cpu). 모르면 null
    device: str | None


class ReadinessRead(BaseModel):
    """쓸 준비가 됐는지에 대한 답."""

    # DB·테이블·storage가 모두 되면 True
    ready: bool

    # 준비의 조건이 되는 확인들: storage, db, schema
    checks: dict[str, CheckRead]

    # 참고로만 알려 주는 상태: worker, embedding, jev, llm.
    # 연결 상태는 '연결됨 · 1024차원 · 42ms' · '실패 · 연결 거부' · '미연결'처럼 낱말로 시작한다.
    # 내장 모델이 준비되기 전이면 '내려받는 중 · 43% · 0.9 / 2.2GB' · '불러오는 중'.
    info: dict[str, CheckRead]

    # 홈의 모델 칸: 임베딩 · Jev · LLM 순서로 한 줄씩
    models: list[ModelInfoRead]


def get_http_transport() -> httpx.AsyncBaseTransport | None:
    """외부 서버(임베딩 · Jev)를 확인할 때 쓸 전송 계층. 평소에는 None이고 테스트가 가짜로 바꾼다."""
    return None


@router.get("/healthz")
async def healthz() -> HealthRead:
    """API 서버가 살아 있으면 답한다. 런처가 '이미 실행 중인지' 볼 때 쓴다."""
    return HealthRead(app=APP_NAME, version=APP_VERSION)


@router.get("/readyz")
async def readyz(
    response: Response,
    transport: Annotated[httpx.AsyncBaseTransport | None, Depends(get_http_transport)],
) -> ReadinessRead:
    """DB 연결, 테이블 버전, storage가 모두 되면 200, 아니면 503.

    작업 실행기와 외부 모델 연결(임베딩 · Jev · LLM)은 참고로만 알려 준다.
    """
    readiness = await status_service.collect_readiness(get_settings(), transport=transport)
    if not readiness.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessRead.model_validate(asdict(readiness))
