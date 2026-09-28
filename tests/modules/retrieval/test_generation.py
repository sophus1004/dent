"""질의 만들기 테스트: 질문형 몫에 맞춘 후보 설정 · 청크마다 N개 · 모자란 청크만 한 번 더 만들기 ·
질의를 만들 문서(입구와 상관없이 더한 문서 · 나눈 지문의 답 없는 청크, 원본 오답은 빼기) · 못 만든 청크는 질의 안 만듦.

LLM은 청크마다 '만들 것 k개'를 읽어 답하는 가짜다. 첫 판(k = 2N)에는 살아남는 질의를 good개만 주고
나머지는 모양 거르기에 걸리는 짧은 글을 준다. 한 번 더(k < 2N)에는 retry_good개를 준다.
임베딩 · Jev는 연결하지 않는다(거르기는 규칙만).
"""

import json
import re
from typing import Any

import httpx
from fastapi import status
from sqlalchemy import select

from dent.modules.retrieval import edits, generation
from dent.modules.retrieval import overview as overview_service
from dent.modules.retrieval.models import Document, Query, QuerySource
from dent.system import connections as connections_service
from dent.system.models import ConnectionRole, LlmProvider
from dent.system.text import make_text_hash
from tests.modules.retrieval.helpers import import_csv
from tests.system.helpers import LLM_KEY, LLM_URL

API = "/api/v1/retrieval"

# 서로 다른 짧은 문서 셋 (나누지 않아도 되는 길이)
DOCS = [
    "복리후생 규정: 직원은 매년 건강검진을 받을 수 있고 비용은 회사가 부담한다.",
    "보안 규정: 사내 문서는 허가 없이 외부로 보낼 수 없으며 위반하면 징계한다.",
    "출장 규정: 국내 출장 숙박비는 하루 10만 원까지 실비로 정산한다.",
]


def question_llm(*, good: int, retry_good: int) -> httpx.MockTransport:
    """청크마다 '만들 것 k개'를 읽는 가짜 LLM. 첫 판은 good개만 살아남고, 한 번 더에는 retry_good개."""
    asked: list[dict[int, int]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][-1]["content"]
        wanted = {
            int(found[0]): int(found[1]) for found in re.findall(r"\[(\d+)\] 만들 것 (\d+)개", user)
        }
        asked.append(wanted)
        is_retry = len(asked) > 1
        items = []
        for document_id, count in wanted.items():
            keep = retry_good if is_retry else good
            queries = [
                f"사내 규정 {document_id}번 {'다시' if is_retry else '처음'} 질문 {index}는 무엇인가"
                for index in range(min(keep, count))
            ]
            queries += ["짧"] * (count - len(queries))
            items.append({"id": document_id, "queries": queries})
        message = {"role": "assistant", "content": json.dumps({"items": items}, ensure_ascii=False)}
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": usage})

    transport = httpx.MockTransport(reply)
    transport.asked = asked  # type: ignore[attr-defined]
    return transport


async def _documents_dataset(db_session) -> int:
    finished = await import_csv(
        db_session,
        header=["text"],
        rows=[[text] for text in DOCS],
        mapping={"shape": "documents", "text": "text"},
    )
    return finished.dataset_id


async def _llm(db_session) -> Any:
    await connections_service.save_connection(
        db_session,
        role=ConnectionRole.LLM,
        base_url=LLM_URL,
        model="test-chat",
        provider=LlmProvider.OPENAI,
        api_key=LLM_KEY,
    )
    return await connections_service.get_connection(db_session, role=ConnectionRole.LLM)


async def _generate(db_session, dataset_id: int, transport: httpx.MockTransport):
    documents = await generation.target_documents(db_session, dataset_id=dataset_id)
    return await generation.generate(
        db_session,
        dataset_id=dataset_id,
        documents=documents,
        llm_connection=await _llm(db_session),
        dry_run=True,
        transport=transport,
    )


# ---------- 후보 설정 ----------


def _fake_documents(count: int) -> list[Document]:
    return [Document(text_hash=make_text_hash(f"청크 {index}")) for index in range(count)]


def _keyword_share(question_share: int) -> float:
    specs = [
        spec
        for document in _fake_documents(200)
        for spec in generation._specs(document, count=4, offset=0, question_share=question_share)
    ]
    return sum(spec.kind == generation.KEYWORD_TYPE for spec in specs) / len(specs)


def test_specs_follow_question_share():
    # 실행
    shares = {share: _keyword_share(share) for share in (0, 60, 100)}

    # 확인: 질문형 60%면 검색어형이 40% 안팎, 100%면 없고, 0%면 모두 검색어형이다.
    assert shares[100] == 0.0
    assert shares[0] == 1.0
    assert abs(shares[60] - 0.4) < 0.05


def test_specs_use_different_slots_for_retry():
    # 준비
    [document] = _fake_documents(1)

    # 실행
    first = generation._specs(document, count=4, offset=0, question_share=60)
    retry = generation._specs(document, count=4, offset=4, question_share=60)
    again = generation._specs(document, count=4, offset=0, question_share=60)

    # 확인: 같은 자리는 늘 같고, 한 번 더 만들 때는 다른 자리를 받는다.
    assert first == again
    assert first != retry


# ---------- 청크마다 N개 ----------


async def test_generate_asks_twice_as_many_candidates_as_queries_per_chunk(client, db_session):
    # 준비
    dataset_id = await _documents_dataset(db_session)
    await client.patch(f"{API}/datasets/{dataset_id}/settings", json={"queries_per_chunk": 3})
    transport = question_llm(good=6, retry_good=0)

    # 실행
    outcome = await _generate(db_session, dataset_id, transport)

    # 확인: 청크마다 후보 6개를 묻고 3개를 남긴다(나머지 3은 여분). 모두 채워 한 번 더 묻지 않는다.
    asked = transport.asked  # type: ignore[attr-defined]
    assert len(asked) == 1
    assert set(asked[0].values()) == {6}
    assert outcome.added == 3 * len(DOCS)
    assert outcome.rejected["여분"] == 3 * len(DOCS)
    assert (outcome.retried, outcome.short) == (0, 0)


async def test_generate_retries_only_short_chunks_for_the_missing_count(client, db_session):
    # 준비: 첫 판에는 청크마다 둘만 살아남는다(청크마다 3개를 남기고 싶다).
    dataset_id = await _documents_dataset(db_session)
    await client.patch(f"{API}/datasets/{dataset_id}/settings", json={"queries_per_chunk": 3})
    transport = question_llm(good=2, retry_good=2)

    # 실행
    outcome = await _generate(db_session, dataset_id, transport)

    # 확인: 모자란 1개의 두 배(후보 2)만 한 번 더 묻고, 모두 채운다.
    asked = transport.asked  # type: ignore[attr-defined]
    assert len(asked) == 2
    assert set(asked[1].values()) == {2}
    assert outcome.added == 3 * len(DOCS)
    assert (outcome.retried, outcome.short) == (len(DOCS), 0)


async def test_generate_counts_chunks_still_short_after_retry(client, db_session):
    # 준비: 한 번 더 만들어도 살아남는 것이 없다.
    dataset_id = await _documents_dataset(db_session)
    transport = question_llm(good=1, retry_good=0)

    # 실행
    outcome = await _generate(db_session, dataset_id, transport)

    # 확인: 기본은 청크마다 2개. 하나씩만 남아 모두 모자람이다.
    assert outcome.added == len(DOCS)
    assert (outcome.retried, outcome.short) == (len(DOCS), len(DOCS))


# ---------- 질의를 만들 문서 ----------

# 표만 있는 문서 (고르기 검사에 걸린다)
TABLE_DOC = "| 항목 | 금액 |\n| 숙박 | 10만 원 |\n| 식비 | 3만 원 |"


async def test_added_documents_need_queries_but_original_negatives_do_not(
    client, db_session, import_triplets
):
    # 준비: 세 쌍(입구 3) 데이터셋에 문서만 파일을 더한다.
    first = await import_triplets()
    await import_csv(
        db_session,
        header=["text"],
        rows=[[text] for text in [*DOCS, TABLE_DOC]],
        mapping={"shape": "documents", "text": "text"},
        dataset_id=first.dataset_id,
    )

    # 실행
    targets = await generation.target_documents(db_session, dataset_id=first.dataset_id)
    overview = await overview_service.get_overview(db_session, dataset_id=first.dataset_id)

    # 확인: 더한 문서만 질의를 만들 문서다(원본 오답으로만 쓰인 문서는 빠진다). 흐름은 1단계(고르기)로 돌아온다.
    assert [document.text for document in targets] == [*DOCS, TABLE_DOC]
    checks = {check.key: check for check in overview.checks}
    assert checks["pick"].view_count == 1
    assert overview.flow.current == 1
    generate = next(move for move in overview.flow.moves if move.key == "generate")
    assert (generate.state, generate.text) == ("todo", f"질의 없는 문서 {len(DOCS) + 1} · 허락")


async def test_unanswered_chunks_need_queries_after_splitting_passage(db_session):
    # 준비: 긴 지문 하나에 질문 하나(MRC, 입구 2)
    passage = " ".join(
        f"문장 {index:03d}번은 출장 규정의 세부 내용을 적은 글입니다." for index in range(200)
    )
    finished = await import_csv(
        db_session,
        header=["q", "p", "a"],
        rows=[["출장 규정의 첫 문장은?", passage, "문장 000번"]],
        mapping={"shape": "mrc", "query": "q", "positive": "p", "answer": "a"},
    )

    # 실행
    _split, pieces = await edits.split_long_documents(
        db_session, dataset_id=finished.dataset_id, log=None
    )
    targets = await generation.target_documents(db_session, dataset_id=finished.dataset_id)

    # 확인: 답이 든 청크(정답)를 뺀 나머지 청크에 질의를 만든다.
    assert len(targets) == pieces - 1
    assert all("문장 000번" not in document.text for document in targets)


async def test_generate_marks_chunks_without_any_query_as_skip(client, db_session):
    # 준비: 두 판 모두 살아남는 질의가 없다.
    dataset_id = await _documents_dataset(db_session)
    documents = await generation.target_documents(db_session, dataset_id=dataset_id)

    # 실행
    outcome = await generation.generate(
        db_session,
        dataset_id=dataset_id,
        documents=documents,
        llm_connection=await _llm(db_session),
        transport=question_llm(good=0, retry_good=0),
    )

    # 확인: '질의 안 만듦'으로 두어 다시 질의를 만들 문서로 남지 않는다(흐름이 질의 만들기에 머물지 않게).
    assert (outcome.added, outcome.gave_up) == (0, len(DOCS))
    assert await generation.target_documents(db_session, dataset_id=dataset_id) == []


# ---------- 설정 API ----------


async def test_update_settings_saves_helper_run_settings_without_touching_dataset(
    client, db_session
):
    # 준비
    dataset_id = await _documents_dataset(db_session)
    before = (await client.get(f"{API}/datasets/{dataset_id}")).json()

    # 실행
    response = await client.patch(
        f"{API}/datasets/{dataset_id}/settings",
        json={"chunk_overlap": 64, "queries_per_chunk": 5, "question_share": 70},
    )
    after = (await client.get(f"{API}/datasets/{dataset_id}")).json()

    # 확인: 실행 설정만 바꾸면 데이터셋 수정 시각을 건드리지 않는다(뜻 분석이 '분석 이후 변경'이 되지 않게).
    assert response.status_code == status.HTTP_200_OK
    settings = after["settings"]
    assert (settings["chunk_overlap"], settings["queries_per_chunk"]) == (64, 5)
    assert settings["question_share"] == 70
    assert after["updated_at"] == before["updated_at"]


async def test_update_settings_returns_422_when_queries_per_chunk_is_out_of_range(
    client, db_session
):
    # 준비
    dataset_id = await _documents_dataset(db_session)

    # 실행
    response = await client.patch(
        f"{API}/datasets/{dataset_id}/settings", json={"queries_per_chunk": 9}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_estimate_counts_chunks_and_queries_for_chosen_settings(client, db_session):
    # 준비: 짧은 문서 셋 · 청크 크기를 아주 작게 골라 모두 긴 문서로 만든다.
    dataset_id = await _documents_dataset(db_session)

    # 실행
    small = await client.get(
        f"{API}/datasets/{dataset_id}/helper/estimate",
        params={"chunk_tokens": 16, "overlap": 0, "per_chunk": 3},
    )
    large = await client.get(
        f"{API}/datasets/{dataset_id}/helper/estimate", params={"chunk_tokens": 512}
    )

    # 확인: 작게 나누면 긴 문서 셋이 청크 여럿으로, 질의는 청크마다 3개씩 어림한다.
    body = small.json()
    assert small.status_code == status.HTTP_200_OK
    assert body["long_documents"] == len(DOCS)
    assert body["chunks"] == body["long_chunks"] > len(DOCS)
    assert body["queries"] == body["targets"] * 3
    assert large.json()["chunks"] == len(DOCS)
    assert large.json()["queries"] == len(DOCS) * 2


async def test_estimate_returns_422_when_overlap_is_out_of_range(client, db_session):
    # 준비
    dataset_id = await _documents_dataset(db_session)

    # 실행
    response = await client.get(
        f"{API}/datasets/{dataset_id}/helper/estimate",
        params={"chunk_tokens": 512, "overlap": 999},
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_generation_targets_and_overview_count_only_group_representatives(db_session):
    # 준비: 문서 가운데 둘을 같은 본문(같은 해시)으로 만들고, 묶음의 대표들에만 만든 질의를 둔다.
    dataset_id = await _documents_dataset(db_session)
    documents = list(
        await db_session.scalars(
            select(Document).where(Document.dataset_id == dataset_id).order_by(Document.id)
        )
    )
    documents[1].text_hash = documents[0].text_hash
    await db_session.commit()
    targets = await generation.target_documents(db_session, dataset_id=dataset_id)
    for document in targets:
        text = f"{document.id}번 문서의 질의"
        db_session.add(
            Query(
                dataset_id=dataset_id,
                text=text,
                text_hash=make_text_hash(text),
                source=QuerySource.SYNTHETIC.value,
                source_document_id=document.id,
            )
        )
    await db_session.commit()

    # 실행
    left = await generation.target_documents(db_session, dataset_id=dataset_id)
    overview = await overview_service.get_overview(db_session, dataset_id=dataset_id)

    # 확인: 번호가 큰 쪽은 대표가 아니라 질의를 만들지 않고, 진단도 그 문서를 할 일로 세지 않는다
    # (흐름이 질의 만들기에 머물지 않는다).
    assert [document.id for document in targets] == [
        documents[0].id,
        *[document.id for document in documents[2:]],
    ]
    assert left == []
    generate = next(move for move in overview.flow.moves if move.key == "generate")
    assert generate.state == "skip"
