"""생성 데이터셋 설정 테스트: 평가셋 표시 → 다른 데이터셋의 오염 대조, 학습할 모델 → 토크나이저로 긴 대화 다시 재기."""

import httpx
from fastapi import status
from sqlalchemy import select
from tokenizers import Tokenizer, models, pre_tokenizers

from dent.modules.generation.jobs import run_contamination_job, run_length_job
from dent.system.models import Job
from tests.modules.generation.helpers import conversation, import_file, jsonl_bytes, messages_rows

API = "/api/v1/generation"

# 평가셋 문항이자 학습 데이터에 섞인 지시 (8낱말 넘게 같은 글)
SHARED = "다음 글을 읽고 글쓴이가 가장 강조하는 주장과 그 근거를 두 문장으로 정리해 주세요"


def _hub() -> httpx.MockTransport:
    """가짜 허깅페이스: acme/chars의 tokenizer.json (글자 하나가 토큰 하나)."""
    tokenizer = Tokenizer(models.WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Split("", behavior="isolated")

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/acme/chars/resolve/main/tokenizer.json":
            return httpx.Response(200, content=tokenizer.to_str().encode("utf-8"))
        return httpx.Response(404)

    return httpx.MockTransport(reply)


async def _queued(db_session, kind: str) -> list[Job]:
    return list(
        (
            await db_session.scalars(
                select(Job).where(Job.kind == kind, Job.status == "queued").order_by(Job.id)
            )
        ).all()
    )


def _check(body: dict, key: str) -> dict | None:
    return next((check for check in body["checks"] if check["key"] == key), None)


async def test_marking_eval_set_checks_other_datasets_for_contamination(client, db_session):
    # 준비: 학습 데이터(겹친 지시 하나 + 다른 지시)와 평가셋
    train = await import_file(
        db_session,
        jsonl_bytes(
            messages_rows(
                [
                    conversation(SHARED, "주장은 A이고 근거는 B입니다."),
                    conversation("안녕?", "안녕하세요."),
                ]
            )
        ),
        name="학습",
    )
    evaluation = await import_file(
        db_session,
        jsonl_bytes(messages_rows([conversation(SHARED, "모범 답안입니다.")])),
        name="평가",
    )
    waiting = (await client.get(f"{API}/datasets/{train.dataset_id}/overview")).json()

    # 실행: 평가셋으로 표시하면 다른 데이터셋의 대조 작업이 들어간다
    marked = await client.patch(
        f"{API}/datasets/{evaluation.dataset_id}/settings", json={"is_eval": True}
    )
    for job in await _queued(db_session, "contamination"):
        await run_contamination_job(job)
    overview = (await client.get(f"{API}/datasets/{train.dataset_id}/overview")).json()
    found = (
        await client.get(
            f"{API}/datasets/{train.dataset_id}/conversations", params={"problem": "contamination"}
        )
    ).json()["items"]

    # 확인: 표시 전에는 '평가셋 없음'으로 기다리고, 대조 뒤 겹친 지시 하나가 심각
    assert {item["key"]: item["reason"] for item in waiting["waiting"]}["contamination"] == (
        "평가셋 없음"
    )
    assert marked.status_code == status.HTTP_200_OK
    assert _check(overview, "contamination") == {
        **_check(overview, "contamination"),
        "count": 1,
        "grade": "bad",
    }
    [item] = found
    assert [problem["snippet"] for problem in item["problems"]] == [
        f"평가셋과 겹침 100% · 평가 문항: {SHARED}"
    ]


async def test_tokenizer_model_remeasures_long_conversations_by_tokens(client, db_session):
    # 준비: 응답 300글자(글자 수 어림으로는 길지 않다)
    finished = await import_file(
        db_session, jsonl_bytes(messages_rows([conversation("길게 써 줘", "가" * 300 + ".")]))
    )
    dataset_id = finished.dataset_id
    before = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 실행: 학습할 모델 · 최대 길이를 정하면 길이 작업이 들어가 토크나이저로 다시 잰다
    saved = await client.patch(
        f"{API}/datasets/{dataset_id}/settings",
        json={"tokenizer_model": "acme/chars", "max_tokens": 256},
    )
    [job] = await _queued(db_session, "length")
    await run_length_job(job, transport=_hub())
    after = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    dataset = (await client.get(f"{API}/datasets/{dataset_id}")).json()

    # 확인: 토큰으로 재면 최대 길이(256)를 넘어 긴 대화
    assert saved.json()["tokenizer_model"] == "acme/chars"
    assert (_check(before, "long") or {}).get("count", 0) == 0
    assert after["length_by"] == "tokens"
    assert _check(after, "long")["count"] == 1
    assert dataset["settings"]["measured_model"] == "acme/chars"


async def test_settings_rejects_bad_model_name(client, db_session):
    # 준비
    finished = await import_file(
        db_session, jsonl_bytes(messages_rows([conversation("안녕?", "안녕하세요.")]))
    )

    # 실행
    response = await client.patch(
        f"{API}/datasets/{finished.dataset_id}/settings", json={"tokenizer_model": "no-owner"}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
