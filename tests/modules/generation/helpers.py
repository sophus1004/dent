"""생성 모듈 테스트 도우미: 대화 jsonl 만들기, 올려서 가져오기를 끝까지 돌리기, 뜻 분석용 가짜 서버.

파일 올리기는 시스템 도우미(tests.system.helpers)를 쓴다.
"""

import hashlib
import json
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import semantic
from dent.modules.generation import service as generation_service
from dent.modules.generation.jobs import run_analysis_job, run_import_job
from dent.modules.generation.schemas import ImportCreate
from dent.system import connections as connections_service
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.models import ConnectionRole, Import, LlmProvider
from tests.system.helpers import (
    EMBEDDING_MODEL,
    EMBEDDING_URL,
    JEV_URL,
    LLM_KEY,
    LLM_URL,
    upload,
)

# 문제 없는 대화 두 턴 (시스템 지시 포함)
CLEAN = [
    {"role": "system", "content": "친절하게 답합니다."},
    {"role": "user", "content": "서울의 인구는 얼마인가요?"},
    {"role": "assistant", "content": "약 940만 명입니다."},
    {"role": "user", "content": "부산은요?"},
    {"role": "assistant", "content": "약 330만 명입니다."},
]


def conversation(user: str, answer: str) -> list[dict[str, str]]:
    """한 턴 대화."""
    return [{"role": "user", "content": user}, {"role": "assistant", "content": answer}]


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    """줄마다 JSON 객체 하나인 파일 내용."""
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode()


def messages_rows(conversations: list[list[dict[str, str]]]) -> list[dict[str, Any]]:
    """messages 모양의 줄들 (원본의 source 칸을 곁들인다)."""
    return [{"messages": messages, "source": "시험"} for messages in conversations]


async def import_file(
    db_session: AsyncSession,
    content: bytes,
    *,
    mapping: dict[str, Any] | None = None,
    file_name: str = "sft.jsonl",
    name: str = "SFT 시험",
    transport: httpx.AsyncBaseTransport | None = None,
) -> Import:
    """파일을 올리고 새 데이터셋으로 가져오기를 끝까지 돌린다(작업 실행기처럼 처리 함수를 부른다)."""
    preview = await upload(db_session, file_name, content)
    data = ImportCreate.model_validate(
        {
            "new_dataset_name": name,
            "source": "file",
            "upload_id": preview.upload_id,
            "mapping": mapping or {"shape": "messages", "messages": "messages"},
        }
    )
    created = await generation_service.create_import(db_session, data=data)
    assert created.job_id is not None
    job = await jobs.get_job(db_session, job_id=created.job_id)
    await run_import_job(job, transport=transport)
    return await imports_service.get_import(db_session, import_id=created.id)


# ---------- 뜻 분석용 가짜 서버 ----------

# 뜻 무리마다의 기준 벡터 (가짜 임베딩이 글에 든 낱말로 고른다). 서로 거의 직교한다.
TOPIC_VECTORS = {
    "인구": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "레시피": [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "코드": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "날씨": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
}

# 무리의 기준 벡터에 섞는 글마다의 작은 흔들림 (같은 무리의 글끼리 코사인 ≥ 0.99)
TOPIC_JITTER = 0.05


def topic_vector(text: str) -> list[float]:
    """글에 든 첫 낱말의 무리 벡터 + 글마다 작은 흔들림. 낱말이 없으면 글로 정해지는 벡터."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    noise = [digest[index] / 255 - 0.5 for index in range(8)]
    for word, base in TOPIC_VECTORS.items():
        if word in text:
            return [value + TOPIC_JITTER * shake for value, shake in zip(base, noise, strict=True)]
    return [shake * 2 for shake in noise]


def topic_transport() -> httpx.MockTransport:
    """가짜 임베딩 서버: 보낸 글마다 topic_vector를 돌려준다(같은 무리의 글은 뜻이 가깝다)."""

    def reply(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        data = [{"index": i, "embedding": topic_vector(text)} for i, text in enumerate(texts)]
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(reply)


# 가짜 Jev가 '해롭다'고 보는 응답의 표시어 (안전 질문)
UNSAFE_MARK = "[해로움]"


def judge_transport(*, off_topic: str = "엉뚱") -> httpx.MockTransport:
    """가짜 Jev: 응답에 off_topic 낱말이 있으면 '따르지 않음'(예 0.05), 아니면 '따름'(예 0.95).

    거절이 마땅한지는 늘 '예' 0.9, 해로운지는 응답에 UNSAFE_MARK가 있으면 0.95 · 없으면 0.05로 답한다.
    받은 요청은 .requests에.
    """
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        response = body["state"].get("response", "")
        follows = 0.05 if off_topic in response else 0.95
        unsafe = 0.95 if UNSAFE_MARK in response else 0.05
        answers = {}
        for name in body["questions"]:
            yes = {"follows": follows, "unsafe": unsafe}.get(name, 0.9)
            answers[name] = {
                "type": "choice",
                "choice": "yes" if yes >= 0.5 else "no",
                "probabilities": {"yes": yes, "no": 1 - yes},
            }
        return httpx.Response(200, json={"model": "laya", "answers": answers})

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


# 학습할 모델로 연결하는 가짜 모델 이름 (LLM과 같은 가짜 서버)
TARGET_MODEL = "test-policy"


async def connect_models(
    db_session: AsyncSession, *, jev: bool = True, llm: bool = False, target: bool = False
) -> None:
    """가짜 임베딩(· Jev · LLM · 학습할 모델) 서버를 연결한 것처럼 저장한다."""
    await connections_service.save_connection(
        db_session, role=ConnectionRole.EMBEDDING, base_url=EMBEDDING_URL, model=EMBEDDING_MODEL
    )
    if jev:
        await connections_service.save_connection(
            db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
        )
    if llm:
        await connections_service.save_connection(
            db_session,
            role=ConnectionRole.LLM,
            base_url=LLM_URL,
            model="test-chat",
            provider=LlmProvider.OPENAI,
            api_key=LLM_KEY,
        )
    if target:
        await connections_service.save_connection(
            db_session,
            role=ConnectionRole.TARGET,
            base_url=LLM_URL,
            model=TARGET_MODEL,
            provider=LlmProvider.VLLM,
        )


async def build_analysis(
    db_session: AsyncSession, *, dataset_id: int, jev_transport: httpx.AsyncBaseTransport | None
) -> semantic.AnalysisState:
    """[뜻 분석 만들기]를 누르고 작업 실행기처럼 끝까지 돌린 뒤 상태를 돌려준다."""
    state = await semantic.start_analysis(db_session, dataset_id=dataset_id)
    assert state.run is not None and state.run.job_id is not None
    job = await jobs.get_job(db_session, job_id=state.run.job_id)
    await run_analysis_job(job, transport=topic_transport(), jev_transport=jev_transport)
    return await semantic.get_analysis_state(db_session, dataset_id=dataset_id)
