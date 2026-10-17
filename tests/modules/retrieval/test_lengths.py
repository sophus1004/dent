"""검색 토큰 세기 테스트: 학습할 모델을 바꾸면 그 토크나이저를 받아 문서 토큰을 다시 재고(긴 문서 · 청크 크기),
받을 수 없는 이름이면 글자 수 어림으로 남고, 문서 나누기는 주어진 세기로 청크 크기를 맞춘다."""

import httpx
from sqlalchemy import select
from tokenizers import Tokenizer, models, pre_tokenizers

from dent.modules.retrieval.jobs import run_length_job
from dent.modules.retrieval.models import DatasetSettings, Document
from dent.modules.retrieval.text_rules import chunk_text, estimate_tokens
from dent.system.models import Job
from tests.modules.retrieval.helpers import import_csv

API = "/api/v1/retrieval"

# 한글 300자 문서: 글자 수 어림(약 215토큰)으로는 256을 넘지 않지만 글자마다 한 토큰이면 넘는다
LONG_KOREAN = "가" * 300


def _hub() -> httpx.MockTransport:
    """가짜 허깅페이스: acme/chars의 tokenizer.json (글자 하나가 토큰 하나). 다른 모델은 없다(404)."""
    tokenizer = Tokenizer(models.WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Split("", behavior="isolated")

    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/acme/chars/resolve/main/tokenizer.json":
            return httpx.Response(200, content=tokenizer.to_str().encode("utf-8"))
        return httpx.Response(404)

    return httpx.MockTransport(reply)


async def _dataset(db_session) -> int:
    finished = await import_csv(
        db_session,
        header=["text"],
        rows=[[LONG_KOREAN], ["짧은 문서입니다."]],
        mapping={"shape": "documents", "text": "text"},
    )
    return finished.dataset_id


async def _length_jobs(db_session) -> list[Job]:
    return list(
        (
            await db_session.scalars(
                select(Job).where(Job.kind == "length", Job.status == "queued").order_by(Job.id)
            )
        ).all()
    )


def _long(body: dict) -> dict:
    return next(check for check in body["checks"] if check["key"] == "long")


async def test_target_model_recounts_documents_with_its_tokenizer(client, db_session):
    # 준비: 문서 최대 256토큰. 글자 수 어림으로는 긴 문서가 없다.
    dataset_id = await _dataset(db_session)
    await client.patch(f"{API}/datasets/{dataset_id}/settings", json={"doc_max_tokens": 256})
    before = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 실행: 학습할 모델을 정하면 토큰 다시 세기 작업이 들어가 그 토크나이저를 받고 다시 잰다
    await client.patch(f"{API}/datasets/{dataset_id}/settings", json={"target_model": "acme/chars"})
    [job] = await _length_jobs(db_session)
    await run_length_job(job, transport=_hub())
    after = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    settings = await db_session.get(DatasetSettings, dataset_id, populate_existing=True)
    counted = await db_session.scalar(
        select(Document.token_count)
        .where(Document.dataset_id == dataset_id)
        .order_by(Document.token_count.desc())
        .limit(1)
    )

    # 확인: 300자 문서가 300토큰으로 잡혀 긴 문서가 되고, 진단에 센 기준이 보인다
    assert _long(before)["view_count"] == 0
    assert "글자 수 어림" in _long(before)["sub"]
    assert counted == len(LONG_KOREAN)
    assert _long(after)["view_count"] == 1
    assert "chars 토크나이저" in _long(after)["sub"]
    assert settings.measured_model == "acme/chars"


async def test_unknown_target_model_keeps_estimate_without_failing(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행: 허깅페이스에 없는 이름(임베딩 서버의 별칭일 수 있다)
    await client.patch(
        f"{API}/datasets/{dataset_id}/settings", json={"target_model": "acme/missing"}
    )
    [job] = await _length_jobs(db_session)
    await run_length_job(job, transport=_hub())
    finished = await db_session.get(Job, job.id, populate_existing=True)
    settings = await db_session.get(DatasetSettings, dataset_id, populate_existing=True)

    # 확인: 작업은 끝나고 글자 수 어림으로 남는다
    assert finished.status == "done"
    assert finished.result["model"] == "글자 수 어림"
    assert settings.measured_model is None


def test_chunk_text_fits_chunks_to_the_given_counter():
    # 준비: 문장 여럿. 글자마다 한 토큰으로 세면 어림보다 크다.
    text = " ".join(f"{index}번째 문장은 규정의 한 조항을 길게 설명합니다." for index in range(40))

    # 실행
    chunks = chunk_text(text, max_tokens=120, count=len)

    # 확인: 주어진 세기로 최대 길이를 넘지 않게 나누고, 어림으로 나눌 때보다 잘게 나눈다
    assert all(len(chunk.text) <= 120 for chunk in chunks)
    assert len(chunks) > len(chunk_text(text, max_tokens=120, count=estimate_tokens))
