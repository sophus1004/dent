"""생성 오염 대조 테스트: 8-gram에 걸리지 않는 바꿔 쓴 문항을 임베딩 후보 + LLM 판정으로 찾고,
LLM이 없거나 실패하면 거의 같은 글만, 임베딩이 없으면 8-gram만 보며, 진단에 방법과 데이터셋 단위 겹침을 싣는다."""

import json
import re

import httpx
from sqlalchemy import select

from dent.modules.generation.jobs import run_contamination_job
from dent.system.models import Job
from tests.modules.generation.helpers import (
    connect_models,
    conversation,
    import_file,
    jsonl_bytes,
    messages_rows,
)

API = "/api/v1/generation"

# 평가 문항 (8낱말이 넘는다)
EVAL_ITEM = (
    "우리나라에서 인구가 가장 많은 도시는 어디이고 그 도시의 인구는 대략 몇 명인지 알려 주세요"
)

# 학습 지시: 그대로 베낌(8-gram) · 말을 바꿈(같은 문제) · 주제만 같음(다른 문제) · 상관없음
COPY = "다음 물음에 답하세요. " + EVAL_ITEM
PARAPHRASE = "한국에서 사람이 제일 많이 사는 도시와 그곳에 사는 사람 수를 말해 줘"
SAME_TOPIC = "일본의 인구는 지난 십 년 동안 얼마나 줄었는지 설명해 주세요"
UNRELATED = "김치찌개를 맛있게 끓이는 순서를 단계별로 알려 주세요"

# 가짜 임베딩: 글마다 평가 문항과의 코사인이 정해진 벡터 (바꿔 씀 0.95 · 주제만 같음 0.75 · 상관없음 0)
VECTORS = {
    EVAL_ITEM: [1.0, 0.0, 0.0],
    PARAPHRASE: [0.95, 0.3122, 0.0],
    SAME_TOPIC: [0.75, 0.6614, 0.0],
    UNRELATED: [0.0, 0.0, 1.0],
}


def _vector(text: str) -> list[float]:
    for known, vector in VECTORS.items():
        if text.endswith(known):
            return vector
    return [0.0, 1.0, 0.0]


def _embedder() -> httpx.MockTransport:
    def reply(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        data = [{"index": i, "embedding": _vector(text)} for i, text in enumerate(texts)]
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(reply)


def _judge(*, fail: bool = False) -> httpx.MockTransport:
    """가짜 LLM: 학습 지시가 PARAPHRASE면 yes, 아니면 no. 받은 요청의 사용자 글은 .asked에."""
    asked: list[str] = []

    def reply(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500)
        user = json.loads(request.content)["messages"][-1]["content"]
        asked.append(user)
        verdicts = [
            {
                "ref": block.split("\n", 1)[0],
                "why": "보기",
                "same": "yes" if PARAPHRASE in block else "no",
            }
            for block in re.split(r"\n\n(?=r\d+\n)", user)
        ]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": json.dumps(verdicts)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    transport = httpx.MockTransport(reply)
    transport.asked = asked  # type: ignore[attr-defined]
    return transport


async def _check(client, db_session, *, llm: httpx.MockTransport | None = None) -> dict:
    """학습 데이터와 평가셋을 넣고 평가셋으로 표시해 대조한 뒤 진단과 오염 대화(지시 글 → 조각)를 돌려준다."""
    train = await import_file(
        db_session,
        jsonl_bytes(
            messages_rows(
                [
                    conversation(text, "답입니다.")
                    for text in (COPY, PARAPHRASE, SAME_TOPIC, UNRELATED)
                ]
            )
        ),
        name="학습",
    )
    evaluation = await import_file(
        db_session,
        jsonl_bytes(messages_rows([conversation(EVAL_ITEM, "모범 답안입니다.")])),
        name="평가",
    )
    await client.patch(f"{API}/datasets/{evaluation.dataset_id}/settings", json={"is_eval": True})
    queued = (
        await db_session.scalars(
            select(Job).where(Job.kind == "contamination", Job.status == "queued")
        )
    ).all()
    for job in queued:
        await run_contamination_job(job, transport=_embedder(), llm_transport=llm)
    overview = (await client.get(f"{API}/datasets/{train.dataset_id}/overview")).json()
    items = (
        await client.get(
            f"{API}/datasets/{train.dataset_id}/conversations", params={"problem": "contamination"}
        )
    ).json()["items"]
    found = {item["messages"][0]["content"]: item["problems"][0]["snippet"] for item in items}
    [job] = queued
    finished = await db_session.get(Job, job.id, populate_existing=True)
    return {"overview": overview, "found": found, "job": finished}


async def test_paraphrased_eval_item_is_found_when_llm_judges_it_the_same(client, db_session):
    # 준비: 임베딩 · LLM 연결
    await connect_models(db_session, jev=False, llm=True)
    judge = _judge()

    # 실행
    checked = await _check(client, db_session, llm=judge)
    overview = checked["overview"]

    # 확인: 베낀 것(8-gram)과 바꿔 쓴 것만 오염이고, 주제만 같은 지시는 LLM이 아니라고 했다
    assert set(checked["found"]) == {COPY, PARAPHRASE}
    assert checked["found"][PARAPHRASE].startswith("바꿔 쓰거나 옮김 · 코사인 0.95 · 평가 문항: ")
    assert checked["found"][COPY].startswith("평가셋과 겹침 100%")
    # 8-gram으로 찾은 지시와 후보가 아닌 지시는 LLM에 묻지 않는다
    asked = "\n".join(judge.asked)
    assert PARAPHRASE in asked and SAME_TOPIC in asked
    assert COPY not in asked and UNRELATED not in asked
    # 진단: 방법 · 평가 문항 1개 가운데 1개와 겹침(2%를 넘어 출처 의심)
    assert overview["contamination"] == {
        "by": "llm",
        "eval_items": 1,
        "overlapped": 1,
        "share": 1.0,
        "suspicious": True,
    }
    assert checked["job"].result["paraphrase"] == 1


async def test_without_llm_only_near_copies_count_by_embedding(client, db_session):
    # 준비: 임베딩만 연결
    await connect_models(db_session, jev=False, llm=False)

    # 실행
    checked = await _check(client, db_session)

    # 확인: 코사인 0.95(≥ 0.9)인 바꿔 쓴 지시는 거의 같은 글로 잡고, 0.75인 주제만 같은 지시는 두지 않는다
    assert set(checked["found"]) == {COPY, PARAPHRASE}
    assert checked["found"][PARAPHRASE].startswith("거의 같음 · 코사인 0.95")
    assert checked["overview"]["contamination"]["by"] == "embedding"


async def test_llm_failure_falls_back_to_embedding_without_failing_the_job(client, db_session):
    # 준비: LLM이 늘 500
    await connect_models(db_session, jev=False, llm=True)

    # 실행
    checked = await _check(client, db_session, llm=_judge(fail=True))

    # 확인: 작업은 끝나고, 임베딩 유사도로만 보며 그 까닭을 결과에 남긴다
    assert checked["job"].status == "done"
    assert "llm_error" in checked["job"].result
    assert set(checked["found"]) == {COPY, PARAPHRASE}
    assert checked["overview"]["contamination"]["by"] == "embedding"


async def test_without_embedding_only_ngram_is_used(client, db_session):
    # 준비: 연결 없음

    # 실행
    checked = await _check(client, db_session)

    # 확인: 바꿔 쓴 문항은 못 찾고, 진단이 8-gram만 했다고 보인다
    assert set(checked["found"]) == {COPY}
    assert checked["overview"]["contamination"]["by"] == "ngram"
