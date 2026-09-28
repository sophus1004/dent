"""검색 도우미 테스트: 계획 → 문서 나누기 허락 → 청크 고르기 → 질의 만들기 허락(시험 포함) → … → 끝,
허락 거절(건너뛰기), 되돌리기(만든 질의 · 나눈 문서), API(LLM 없음 422 · 도는 중 409).

LLM은 받은 도구를 모두 한 번 부르고 다음 차례에 finish_step을 부르는 가짜, 질의 만들기에는 JSON으로 질의를 준다.
임베딩 · Jev는 연결하지 않는다(거르기는 규칙만, 오답 찾기는 '임베딩 미연결' 알림).
"""

import json
import re
from typing import Any

import httpx
from fastapi import status
from sqlalchemy import func, select, update

from dent.modules.retrieval import edits
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.jobs import run_analysis_job, run_helper_job
from dent.modules.retrieval.models import (
    Document,
    HelperChange,
    Judgment,
    MapPoint,
    Query,
    QuerySource,
)
from dent.modules.retrieval.service import QUERY_INCLUDED
from dent.system import connections as connections_service
from dent.system import helper as helper_service
from dent.system import jobs
from dent.system.agent import BLOCKED_MESSAGE
from dent.system.models import ConnectionRole, HelperEvent, HelperRunStatus, LlmProvider
from tests.modules.retrieval.helpers import connect_embedding, import_csv
from tests.system.helpers import JEV_URL, LLM_KEY, LLM_URL, vector_transport

API = "/api/v1/retrieval"

# 512토큰을 넘는 긴 문서 하나와 짧은 문서 둘
LONG = "\n\n".join(
    f"제{index}조 {name}. " + f"{name}에 관한 사항은 회사 규정 {index}에 따른다. " * 45
    for index, name in enumerate(["연차휴가", "재택근무", "출장비"], start=1)
)
SHORT_DOCS = [
    "복리후생 규정: 직원은 매년 건강검진을 받을 수 있고 비용은 회사가 부담한다.",
    "보안 규정: 사내 문서는 허가 없이 외부로 보낼 수 없으며 위반하면 징계한다.",
]


def fake_llm(*, judge_answer: str = "yes") -> httpx.MockTransport:
    """가짜 LLM (OpenAI 모양). 도구가 있으면 모든 도구를 한 번 부른 뒤 finish_step.

    도구 없는 요청: 보고는 두 줄, 판정 · 검수는 모든 항목에 judge_answer, 그 밖(질의 만들기)은 청크마다 질의 둘.
    """
    requests: list[dict[str, Any]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        tools = [tool["function"]["name"] for tool in body.get("tools") or []]
        system = body["messages"][0]["content"]
        if not tools and "보고를 두 줄로" in system:
            return _message("무엇을 고쳤나?\n고칠 것은 고쳤다.")
        if not tools and ("판정자" in system or "검수자" in system):
            user = body["messages"][-1]["content"]
            ids = [int(found) for found in re.findall(r"^\[(\d+)\]", user, flags=re.MULTILINE)]
            items = [{"id": item_id, "answer": judge_answer} for item_id in ids]
            return _message(json.dumps({"items": items}, ensure_ascii=False))
        if not tools:
            user = body["messages"][-1]["content"]
            ids = [int(found) for found in re.findall(r"^\[(\d+)\]", user, flags=re.MULTILINE)]
            items = [
                {
                    "id": doc_id,
                    "queries": [
                        f"사내 규정 {doc_id}번 항목의 내용은 무엇인가",
                        f"규정 {doc_id} 항목 요약",
                    ],
                }
                for doc_id in ids
            ]
            return _message(json.dumps({"items": items}, ensure_ascii=False))
        if "plan" in tools:
            return _message("", [_call("plan", "무엇부터 할까?\n문서를 먼저 다듬는다.")])
        has_results = any(message["role"] == "tool" for message in body["messages"])
        if has_results:
            return _message("", [_call("finish_step", "다 했나?\n그렇다.")])
        calls = [
            _call(
                name, f"{name}를 할까?\n한다.", **({"why": "근거 없음"} if name == "hold" else {})
            )
            for name in tools
            if name not in ("finish_step", "hold", "rewrite", "accept_suggestions")
        ]
        return _message("", calls or [_call("finish_step", "할 것이 없나?\n없다.")])

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


def _call(name: str, say: str, **arguments: Any) -> dict[str, Any]:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps({"say": say, **arguments}, ensure_ascii=False),
        },
    }


def _message(content: str, tool_calls: list[dict[str, Any]] | None = None) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
    )


async def _connect_llm(db_session) -> None:
    await connections_service.save_connection(
        db_session,
        role=ConnectionRole.LLM,
        base_url=LLM_URL,
        model="test-chat",
        provider=LlmProvider.OPENAI,
        api_key=LLM_KEY,
    )


async def _dataset(db_session, *, footer: str = "") -> int:
    """긴 문서 하나와 짧은 문서 둘. footer를 주면 세 문서 끝에 같은 줄을 붙인다(반복 구간)."""
    texts = [LONG, *SHORT_DOCS]
    finished = await import_csv(
        db_session,
        header=["title", "text"],
        rows=[
            [title, f"{text}\n{footer}" if footer else text]
            for title, text in zip(["취업규칙", "복리후생", "보안"], texts, strict=True)
        ],
        mapping={"shape": "documents", "text": "text", "title": "title"},
    )
    return finished.dataset_id


async def _run_latest_job(db_session, run_id: int, transport: httpx.MockTransport) -> None:
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.job_id is not None
    job = await jobs.get_job(db_session, job_id=run.job_id)
    await run_helper_job(job, transport=transport)


async def _answer(client, db_session, run_id: int, transport, *, approve: bool) -> None:
    response = await client.post(f"{API}/helper/{run_id}/permission", json={"approve": approve})
    assert response.status_code == status.HTTP_200_OK, response.text
    await _run_latest_job(db_session, run_id, transport)


async def _steps(db_session, run_id: int) -> dict[str, str]:
    run = await helper_service.get_run(db_session, run_id=run_id)
    return {step["key"]: step["status"] for step in run.steps}


async def test_helper_asks_to_chunk_then_generate_and_finishes(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    response = await client.post(f"{API}/datasets/{dataset_id}/helper")
    assert response.status_code == status.HTTP_201_CREATED, response.text
    run_id = response.json()["id"]

    # 실행 1: 계획 → 문서 정리(문제 없음) → 문서 나누기 허락을 묻는다.
    await _run_latest_job(db_session, run_id, transport)
    first = await helper_service.get_run(db_session, run_id=run_id)
    first_status, first_permission = first.status, first.permission

    # 실행 2: 허락 → 나누기 → 청크 고르기 → 질의 만들기 허락을 묻는다(시험 포함).
    await _answer(client, db_session, run_id, transport, approve=True)
    second = await helper_service.get_run(db_session, run_id=run_id)
    second_status, second_permission = second.status, dict(second.permission or {})

    # 실행 3: 허락 → 질의 만들기 → 질의 정리 · 정답 확인 · 오답 찾기 · 거짓 오답 확인 → 끝
    await _answer(client, db_session, run_id, transport, approve=True)

    # 확인
    assert first_status == HelperRunStatus.ASKING
    assert first_permission["step"] == "chunk"
    assert second_status == HelperRunStatus.ASKING
    assert second_permission["step"] == "generate"
    assert any(name == "시험" for name, _value in second_permission["facts"])
    final = await helper_service.get_run(db_session, run_id=run_id)
    assert final.status == HelperRunStatus.DONE
    steps = await _steps(db_session, run_id)
    assert steps["chunk"] == "done"
    assert steps["generate"] == "done"
    original = await db_session.scalar(
        select(Document).where(Document.title == "취업규칙", Document.source_document_id.is_(None))
    )
    await db_session.refresh(original)
    assert original.replaced_at is not None
    synthetic = await db_session.scalar(
        select(func.count()).select_from(Query).where(Query.source == QuerySource.SYNTHETIC.value)
    )
    assert synthetic > 0
    kinds = {
        event.kind
        for event in await db_session.scalars(
            select(HelperEvent).where(HelperEvent.run_id == run_id)
        )
    }
    assert {"say", "permission", "change", "result", "llm"} <= kinds


async def test_helper_asks_to_generate_for_documents_added_to_triplet_dataset(
    client, db_session, import_triplets
):
    # 준비: 세 쌍(입구 3) 데이터셋에 문서만 파일을 더했다(데이터 추가).
    first = await import_triplets()
    await import_csv(
        db_session,
        header=["text"],
        rows=[[text] for text in SHORT_DOCS],
        mapping={"shape": "documents", "text": "text"},
        dataset_id=first.dataset_id,
    )
    await _connect_llm(db_session)
    response = await client.post(f"{API}/datasets/{first.dataset_id}/helper")
    run_id = response.json()["id"]

    # 실행
    await _run_latest_job(db_session, run_id, fake_llm())

    # 확인: 입구가 3이어도 흐름을 처음부터 돌아 더한 문서의 질의 만들기 허락을 묻는다(원본 오답 문서는 빼고).
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.ASKING
    assert run.permission["step"] == "generate"
    steps = {step["key"]: step for step in run.steps}
    assert steps["generate"]["plan"] == f"청크 {len(SHORT_DOCS)}"


async def test_helper_skips_chunk_when_declined(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)

    # 실행
    await _answer(client, db_session, run_id, transport, approve=False)

    # 확인
    steps = await _steps(db_session, run_id)
    assert steps["chunk"] == "skipped"
    replaced = await db_session.scalar(
        select(func.count()).select_from(Document).where(Document.replaced_at.is_not(None))
    )
    assert replaced == 0


async def test_chunk_permission_counts_chunks_by_actual_split(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)
    chunk = dict((await helper_service.get_run(db_session, run_id=run_id)).permission or {})

    # 실행
    await _answer(client, db_session, run_id, transport, approve=True)

    # 확인: 카드의 청크 수는 실제로 나뉜 수와 같다(길이로 세지 않고 같은 규칙으로 나눠 본다).
    made = await db_session.scalar(
        select(func.count()).select_from(Document).where(Document.source_document_id.is_not(None))
    )
    assert made
    assert dict(chunk["facts"])["결과"] == f"청크 약 {made:,}"


async def test_permission_cards_carry_run_settings_summary(client, db_session):
    # 준비: 실행 설정을 고친 뒤 시작한다.
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    await client.patch(
        f"{API}/datasets/{dataset_id}/settings",
        json={"chunk_overlap": 64, "queries_per_chunk": 3, "question_share": 70},
    )
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]

    # 실행
    await _run_latest_job(db_session, run_id, transport)
    chunk = dict((await helper_service.get_run(db_session, run_id=run_id)).permission or {})
    await _answer(client, db_session, run_id, transport, approve=True)
    generate = dict((await helper_service.get_run(db_session, run_id=run_id)).permission or {})

    # 확인: 허락 카드는 실행 설정의 요약을 싣는다(화면은 읽기만 하고 [설정]으로 설정 카드를 연다).
    assert chunk["settings"] == "512토큰 · 오버랩 64토큰 · 머리말 켬"
    assert generate["settings"] == "청크마다 3 · 질문형 70%"
    facts = dict(generate["facts"])
    assert facts["만들기"] == "청크마다 3 · 후보 6 · 질문형 70% · 검색어형 30%"
    assert "alert" not in generate


async def test_generate_permission_offers_chunk_first_when_chunking_was_skipped(client, db_session):
    # 준비: 문서 나누기를 건너뛰어 긴 문서가 남은 채 질의 만들기 허락을 묻는다.
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)
    await _answer(client, db_session, run_id, transport, approve=False)
    skipped = dict((await helper_service.get_run(db_session, run_id=run_id)).permission or {})

    # 실행: [먼저 나누기]
    response = await client.post(
        f"{API}/helper/{run_id}/permission", json={"approve": True, "action": "chunk_first"}
    )
    await _run_latest_job(db_session, run_id, transport)

    # 확인: 건너뛴 문서 나누기를 하고, 질의 만들기 허락을 다시 묻는다(이제 긴 문서가 없다).
    assert skipped["step"] == "generate"
    assert skipped["alert"]["text"] == "긴 문서 1 · 나누지 않음"
    assert skipped["alert"]["action"] == "chunk_first"
    assert response.status_code == status.HTTP_200_OK
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert (run.status, (run.permission or {}).get("step")) == (HelperRunStatus.ASKING, "generate")
    assert "alert" not in (run.permission or {})
    steps = await _steps(db_session, run_id)
    assert steps["chunk"] == "done"
    replaced = await db_session.scalar(
        select(func.count()).select_from(Document).where(Document.replaced_at.is_not(None))
    )
    assert replaced == 1


async def test_chunk_first_returns_422_when_card_has_no_long_documents(client, db_session):
    # 준비: 문서 나누기를 허락해 긴 문서가 없는 질의 만들기 허락 카드
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)
    await _answer(client, db_session, run_id, transport, approve=True)

    # 실행
    response = await client.post(
        f"{API}/helper/{run_id}/permission", json={"approve": True, "action": "chunk_first"}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": "먼저 나눌 긴 문서가 없는 허락입니다."}


async def test_undo_helper_run_trashes_queries_and_restores_documents(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)
    await _answer(client, db_session, run_id, transport, approve=True)
    await _answer(client, db_session, run_id, transport, approve=True)

    # 실행
    response = await client.post(f"{API}/helper/{run_id}/undo", json={"event_id": None})

    # 확인
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["reverted"] > 0
    active_synthetic = await db_session.scalar(
        select(func.count())
        .select_from(Query)
        .where(Query.source == QuerySource.SYNTHETIC.value, Query.trashed_at.is_(None))
    )
    assert active_synthetic == 0
    replaced = await db_session.scalar(
        select(func.count()).select_from(Document).where(Document.replaced_at.is_not(None))
    )
    assert replaced == 0
    undone = await db_session.scalar(
        select(func.count()).select_from(HelperChange).where(HelperChange.undone_at.is_not(None))
    )
    assert undone > 0
    state = await client.get(f"{API}/datasets/{dataset_id}/helper")
    assert state.json()["undone_events"]


async def test_start_helper_returns_422_without_llm(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/helper")

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_start_helper_returns_409_while_asking(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, fake_llm())

    # 실행
    response = await client.post(f"{API}/datasets/{dataset_id}/helper")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT


async def test_helper_marks_chunks_by_rule_without_llm_tools(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)

    # 실행: 나누기 허락 → 청크 고르기 → 질의 만들기 허락을 묻는다.
    await _answer(client, db_session, run_id, transport, approve=True)

    # 확인: 청크 고르기는 규칙으로 한다(LLM에 고르기 · 합치기 도구를 주지 않는다).
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.ASKING
    offered = {
        tool["function"]["name"]
        for body in transport.requests  # type: ignore[attr-defined]
        for tool in body.get("tools") or []
    }
    assert offered.isdisjoint({"mark_skip_generation", "merge_duplicates"})


async def test_helper_blocks_generation_when_long_documents_remain(client, db_session, monkeypatch):
    # 준비: 허락했지만 문서를 나누지 못한 경우(긴 문서 심각이 남는다).
    async def no_chunk(*_args, **_kwargs) -> tuple[int, int]:
        return 0, 0

    monkeypatch.setattr(edits, "split_long_documents", no_chunk)
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)

    # 실행
    await _answer(client, db_session, run_id, transport, approve=True)

    # 확인: 질의 만들기 허락을 묻지 않고 막는다(뒤가 잘린 문서로 질의를 만들지 않게).
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert (run.status, run.error) == (HelperRunStatus.DONE, BLOCKED_MESSAGE)
    steps = await _steps(db_session, run_id)
    assert steps["generate"] == "blocked"
    notices = [
        event.payload
        for event in await db_session.scalars(
            select(HelperEvent).where(HelperEvent.run_id == run_id, HelperEvent.kind == "notice")
        )
    ]
    blocked = next(notice for notice in notices if "checks" in notice)
    assert [(check["name"], check["grade"]) for check in blocked["checks"]] == [("긴 문서", "bad")]
    steps_now = await _steps(db_session, run_id)
    assert steps_now["report"] == "done"
    synthetic = await db_session.scalar(select(func.count()).select_from(Query))
    assert synthetic == 0


async def test_helper_permission_card_lists_undecided_repeats(client, db_session):
    # 준비: 세 문서 끝에 같은 연락처 줄(반복 구간, 사람이 고를 것)
    dataset_id = await _dataset(db_session, footer="문의: help@example.com")
    await _connect_llm(db_session)

    # 실행: 문서 정리 → 문서 나누기 허락을 묻는다.
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, fake_llm())

    # 확인: 반복 구간(주의)은 막지 않고 허락 카드에 싣는다.
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.ASKING
    assert [check["name"] for check in run.permission["checks"]] == ["반복 구간"]
    assert run.permission["checks"][0]["grade"] == "warn"


async def test_helper_blocks_approved_generation_when_long_document_appears(client, db_session):
    # 준비: 질의 만들기 허락을 기다리는 동안 긴 문서가 생겼다(심각).
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)
    await _answer(client, db_session, run_id, transport, approve=True)
    await db_session.execute(
        update(Document)
        .where(Document.dataset_id == dataset_id, Document.replaced_at.is_(None))
        .values(token_count=5_000)
    )
    await db_session.commit()

    # 실행
    await _answer(client, db_session, run_id, transport, approve=True)

    # 확인: 허락을 받았어도 문에서 막고 질의를 만들지 않는다.
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert (run.status, run.error) == (HelperRunStatus.DONE, BLOCKED_MESSAGE)
    assert await db_session.scalar(select(func.count()).select_from(Query)) == 0


async def _drain(
    db_session, *, llm: httpx.MockTransport, embedding: httpx.MockTransport
) -> list[str]:
    """작업 실행기처럼 대기 작업을 넣은 순서대로 하나씩 처리한다. 처리한 작업 종류들."""
    kinds = []
    while (job := await jobs.claim_next_job(db_session)) is not None:
        kinds.append(job.kind)
        if job.kind == "analysis":
            await run_analysis_job(job, transport=embedding)
        elif job.kind == "helper":
            await run_helper_job(job, transport=llm, embedding_transport=embedding)
        else:
            raise AssertionError(f"모르는 작업 {job.kind}")
    return kinds


async def test_helper_rebuilds_analysis_after_chunking_and_generating(client, db_session):
    # 준비: 임베딩이 연결돼 있다.
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    await connect_embedding(db_session)
    llm = fake_llm()
    embedding = vector_transport(dim=16)
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]

    # 실행: 시작 → 나누기 허락 → 질의 만들기 허락
    first = await _drain(db_session, llm=llm, embedding=embedding)
    await client.post(f"{API}/helper/{run_id}/permission", json={"approve": True})
    second = await _drain(db_session, llm=llm, embedding=embedding)
    await client.post(f"{API}/helper/{run_id}/permission", json={"approve": True})
    third = await _drain(db_session, llm=llm, embedding=embedding)

    # 확인: 나눈 청크 · 만든 질의가 분석에 없으면 분석을 다시 만든 뒤 그 단계부터 이어 돈다.
    assert first == ["analysis", "helper"]
    assert second == ["helper", "analysis", "helper"]
    assert third == ["helper", "analysis", "helper"]
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.DONE
    analysis = await retrieval_service.latest_done_analysis(db_session, dataset_id=dataset_id)
    in_analysis = select(MapPoint.query_id).where(MapPoint.analysis_id == analysis.id)
    not_analyzed = await db_session.scalar(
        select(func.count())
        .select_from(Query)
        .where(Query.dataset_id == dataset_id, QUERY_INCLUDED, Query.id.not_in(in_analysis))
    )
    assert not_analyzed == 0
    says = [
        event.payload["text"]
        for event in await db_session.scalars(
            select(HelperEvent).where(HelperEvent.run_id == run_id, HelperEvent.kind == "say")
        )
    ]
    assert any(text.endswith("뜻 분석에 없나?") for text in says)
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    labels = {item["label"]: item["count"] for item in overview["first_rank"]}
    assert labels["정답 없음"] == 0
    assert "분석에 없음" not in labels


async def _finished_run(client, db_session) -> tuple[int, int]:
    """문서만 데이터셋을 도우미가 끝까지 돈다(나누기 · 질의 만들기 허락). (데이터셋, 실행 번호)."""
    dataset_id = await _dataset(db_session)
    await _connect_llm(db_session)
    transport = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_latest_job(db_session, run_id, transport)
    await _answer(client, db_session, run_id, transport, approve=True)
    await _answer(client, db_session, run_id, transport, approve=True)
    return dataset_id, run_id


async def test_helper_ends_with_report_and_lists_left(client, db_session):
    # 준비 · 실행
    dataset_id, run_id = await _finished_run(client, db_session)
    left = (await client.get(f"{API}/datasets/{dataset_id}/helper/left")).json()["items"]

    # 확인: 보고로 끝나고, AI로 못 고치는 것은 할 일만 남는다.
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.DONE
    report = await db_session.scalar(
        select(HelperEvent).where(HelperEvent.run_id == run_id, HelperEvent.kind == "report")
    )
    assert report.payload["title"] == "보고"
    assert report.payload["overall"][0] == "준비 안 됨"
    items = {item["key"]: item for item in left}
    assert items["no_negative"]["group"] == "blocked"
    assert items["no_negative"]["action"].startswith("문서 더하기")


async def test_fix_short_query_excludes_by_rule_and_undo_restores(client, db_session):
    # 준비: 도우미가 끝난 뒤 짧은 질의 하나를 더 가져온다.
    dataset_id, run_id = await _finished_run(client, db_session)
    await import_csv(
        db_session,
        header=["query", "positive"],
        rows=[["휴가", SHORT_DOCS[0]]],
        mapping={"shape": "pair", "query": "query", "positive": "positive"},
        dataset_id=dataset_id,
    )
    left = (await client.get(f"{API}/datasets/{dataset_id}/helper/left")).json()["items"]

    # 실행
    response = await client.post(
        f"{API}/datasets/{dataset_id}/helper/fix", json={"keys": ["short_long"]}
    )
    await _run_latest_job(db_session, run_id, fake_llm())
    short = await db_session.scalar(select(Query).where(Query.text == "휴가"))
    excluded = short.exclude_reason
    undo = await client.post(f"{API}/helper/{run_id}/undo", json={"event_id": None})
    await db_session.refresh(short)

    # 확인: 같은 실행에 'AI로 고치기' 단계 · 보고가 붙고, 되돌리면 다시 학습에 쓴다.
    assert {item["key"]: item["group"] for item in left}["short_long"] == "ai"
    assert response.status_code == status.HTTP_201_CREATED
    assert excluded is not None
    assert undo.status_code == status.HTTP_200_OK
    assert short.exclude_reason is None
    steps = await _steps(db_session, run_id)
    assert (steps["fix:1:short_long"], steps["report:1"]) == ("done", "done")


def _jev(yes: float) -> httpx.MockTransport:
    """가짜 Jev: 모든 (질의, 문서)에 '예' 확률 yes."""

    def reply(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        answer = {
            "type": "choice",
            "choice": "yes" if yes >= 0.5 else "no",
            "probabilities": {"yes": yes, "no": 1 - yes},
            "confidence": 0.7,
        }
        body = {"model": "laya", "answers": {name: answer for name in questions}}
        return httpx.Response(200, json=body)

    return httpx.MockTransport(reply)


async def _run_with_jev(db_session, run_id: int, transport, jev) -> None:
    run = await helper_service.get_run(db_session, run_id=run_id)
    job = await jobs.get_job(db_session, job_id=run.job_id)
    await run_helper_job(job, transport=transport, jev_transport=jev)


async def test_conflict_changes_judgment_only_when_jev_and_llm_agree(client, db_session):
    # 준비: 원본에서 정답이자 오답인 쌍. Jev는 아니오(예 0.1)를 확신하고 LLM은 예라고 한다.
    finished = await import_csv(
        db_session,
        header=["query", "doc", "score"],
        rows=[
            ["환불 기간", "환불은 영업일 3일 안에 처리된다.", "1"],
            ["환불 기간", "환불은 영업일 3일 안에 처리된다.", "0"],
            ["배송비", "배송비는 3만 원 이상이면 무료다.", "1"],
        ],
        mapping={"shape": "scored", "query": "query", "document": "doc", "score": "score"},
    )
    await _connect_llm(db_session)
    await connections_service.save_connection(
        db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
    )
    transport, jev = fake_llm(judge_answer="yes"), _jev(0.1)
    run_id = (await client.post(f"{API}/datasets/{finished.dataset_id}/helper")).json()["id"]

    # 실행: 도우미(규칙) → 남은 것 AI로 고치기
    await _run_with_jev(db_session, run_id, transport, jev)
    held = await db_session.scalar(select(Judgment).where(Judgment.conflict.is_(True)))
    fixed = await client.post(
        f"{API}/datasets/{finished.dataset_id}/helper/fix", json={"keys": ["conflict"]}
    )
    await _run_with_jev(db_session, run_id, transport, jev)

    left = await db_session.scalar(
        select(func.count())
        .select_from(Judgment)
        .join(Query, Query.id == Judgment.query_id)
        .where(Query.text == "환불 기간")
    )
    undo = await client.post(f"{API}/helper/{run_id}/undo", json={"event_id": None})
    restored = await db_session.scalar(
        select(Judgment)
        .join(Query, Query.id == Judgment.query_id)
        .where(Query.text == "환불 기간")
        .execution_options(populate_existing=True)
    )

    # 확인: Jev 하나로 참 정답을 오답으로 바꾸지 않는다. 도우미는 보류, AI로 고치기는 판정을 뗀다(모름).
    assert held is not None
    assert fixed.status_code == status.HTTP_201_CREATED
    assert left == 0
    # 되돌리면 충돌 · 출처까지 원래대로 돌아온다.
    assert undo.status_code == status.HTTP_200_OK
    assert (restored.conflict, restored.source) == (True, "original")


async def test_fix_returns_422_for_blocked_left(client, db_session):
    # 준비
    dataset_id, _run_id = await _finished_run(client, db_session)

    # 실행: 오답 수는 AI로 못 고친다(문서 더하기).
    response = await client.post(
        f"{API}/datasets/{dataset_id}/helper/fix", json={"keys": ["no_negative"]}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
