"""생성 검토 · 결정 테스트: 응답 없는 대화(응답 만들기) · 도구 호출 대화 · 선호 쌍 결정(뒤집기 · 빼기 · 그대로 두기)과
되돌리기 · 그대로 둔 것 다시 묻기 · 진단의 '그대로 둔 수'."""

import json

import httpx
from fastapi import status
from sqlalchemy import select

from dent.modules.generation import conversations
from dent.modules.generation.models import Pair, Response
from tests.modules.generation.helpers import (
    connect_models,
    conversation,
    import_file,
    jsonl_bytes,
    messages_rows,
)

API = "/api/v1/generation"

# 가짜 LLM이 쓰는 응답
WRITTEN = "서울은 대한민국의 수도이며 인구는 약 940만 명입니다."

# 응답이 없는 지시 둘
PROMPTS = [
    {"messages": [{"role": "user", "content": "서울을 소개해 줘"}]},
    {"messages": [{"role": "user", "content": "부산을 소개해 줘"}]},
]

# 선호 쌍 하나 (고른 응답이 더 짧다)
PREFERENCE = [
    {
        "chosen": [
            {"role": "user", "content": "요약해 줘"},
            {"role": "assistant", "content": "핵심만 세 줄입니다."},
        ],
        "rejected": [
            {"role": "user", "content": "요약해 줘"},
            {"role": "assistant", "content": "모르겠어요. 다른 걸 물어봐 주세요."},
        ],
    }
]


def fake_llm() -> httpx.MockTransport:
    """가짜 LLM (OpenAI 모양): 무엇을 물어도 WRITTEN을 답한다."""

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": WRITTEN}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            },
        )

    return httpx.MockTransport(reply)


async def _prompts_dataset(db_session) -> int:
    finished = await import_file(db_session, jsonl_bytes(PROMPTS), name="지시만")
    return finished.dataset_id


async def _pair_dataset(db_session) -> int:
    finished = await import_file(
        db_session,
        jsonl_bytes(PREFERENCE),
        name="선호",
        mapping={"shape": "preference", "chosen": "chosen", "rejected": "rejected"},
    )
    return finished.dataset_id


async def _first_id(client, dataset_id: int) -> int:
    page = (await client.get(f"{API}/datasets/{dataset_id}/conversations")).json()
    return page["items"][0]["id"]


# ---------- 응답 없는 대화 ----------


async def test_conversation_without_answer_shows_no_answer_problem(client, db_session):
    # 준비
    dataset_id = await _prompts_dataset(db_session)
    conversation_id = await _first_id(client, dataset_id)

    # 실행
    response = await client.get(f"{API}/conversations/{conversation_id}")

    # 확인: 맥락만 있고 응답 없음이 문제로 뜬다
    body = response.json()
    assert body["has_answer"] is False
    assert body["messages"] == [
        {"role": "user", "content": "서울을 소개해 줘", "tool_calls": None, "tool_call_id": None}
    ]
    assert [problem["key"] for problem in body["problems"]] == ["no_answer"]


async def test_list_filters_conversations_without_answer(client, db_session):
    # 준비
    dataset_id = await _prompts_dataset(db_session)

    # 실행
    found = await client.get(
        f"{API}/datasets/{dataset_id}/conversations", params={"problem": "no_answer"}
    )
    review = await client.get(f"{API}/datasets/{dataset_id}/review", params={"check": "no_answer"})

    # 확인: 응답 없음은 표시(marks)가 아니어도 거르기 · 검토 큐에 둘 다 든다
    assert found.json()["total"] == 2
    assert review.json()["total"] == 2


async def test_decide_answer_writes_synthetic_answer_and_undo_removes_it(
    client, db_session, monkeypatch
):
    # 준비
    dataset_id = await _prompts_dataset(db_session)
    await connect_models(db_session, jev=False, llm=True)
    monkeypatch.setattr(conversations, "REWRITE_TRANSPORT", fake_llm())
    conversation_id = await _first_id(client, dataset_id)

    # 실행
    decided = await client.post(
        f"{API}/conversations/{conversation_id}/decide",
        json={"check": "no_answer", "action": "answer"},
    )
    entry_after_answer = (await client.get(f"{API}/datasets/{dataset_id}")).json()["entry_stage"]
    undone = await client.post(f"{API}/changes/{decided.json()['change']['id']}/undo")

    # 확인: 만든 응답은 주 응답 · 출처 합성이고, 되돌리면 응답이 지워져 다시 응답 없음
    body = decided.json()["conversation"]
    assert decided.status_code == status.HTTP_200_OK
    assert body["has_answer"] is True
    assert body["messages"][-1]["content"] == WRITTEN
    assert body["source"] == "synthetic"
    assert body["problems"] == []
    assert entry_after_answer == 1
    assert undone.json()["has_answer"] is False
    assert await db_session.scalar(select(Response.id)) is None
    # 입구는 가져온 모양(지시만) 그대로다(응답을 만들어도 바뀌지 않는다).
    assert (await client.get(f"{API}/datasets/{dataset_id}")).json()["entry_stage"] == 1


async def test_decide_answer_returns_422_without_llm(client, db_session):
    # 준비
    dataset_id = await _prompts_dataset(db_session)
    conversation_id = await _first_id(client, dataset_id)

    # 실행
    response = await client.post(
        f"{API}/conversations/{conversation_id}/decide",
        json={"check": "no_answer", "action": "answer"},
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": conversations.LLM_NOT_CONNECTED_MESSAGE}


async def test_decide_drop_works_on_conversation_without_answer(client, db_session):
    # 준비
    dataset_id = await _prompts_dataset(db_session)
    conversation_id = await _first_id(client, dataset_id)

    # 실행
    response = await client.post(
        f"{API}/conversations/{conversation_id}/decide",
        json={"check": "no_answer", "action": "drop"},
    )
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인
    assert response.json()["conversation"]["trashed"] is True
    assert overview["unanswered"] == 1


# ---------- 도구 호출 ----------


async def test_tool_call_problem_names_what_is_wrong(client, db_session):
    # 준비: 정의에 없는 도구를 부른 대화
    tools = [{"type": "function", "function": {"name": "weather", "parameters": {}}}]
    messages = [
        {"role": "user", "content": "환율 알려줘"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "fx", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"usd": 1400}'},
        {"role": "assistant", "content": "1달러는 1,400원입니다."},
    ]
    finished = await import_file(
        db_session,
        jsonl_bytes([{"messages": messages, "tools": tools}]),
        mapping={"shape": "messages", "messages": "messages", "tools": "tools"},
    )
    conversation_id = await _first_id(client, finished.dataset_id)

    # 실행
    body = (await client.get(f"{API}/conversations/{conversation_id}")).json()

    # 확인: 메시지에 호출 · 결과 번호가 실리고, 문제 조각이 '도구 이름 · 무엇이 틀렸나'
    assert body["tools"] == tools
    assert body["messages"][1]["tool_calls"][0]["function"]["name"] == "fx"
    assert body["messages"][2]["tool_call_id"] == "c1"
    [problem] = body["problems"]
    assert (problem["key"], problem["turn"], problem["snippet"]) == (
        "tool_call",
        1,
        "fx · 정의에 없는 도구",
    )


# ---------- 선호 쌍 ----------


async def test_review_queue_of_pair_check_lists_pair_ids(client, db_session):
    # 준비: 같은 응답 쌍(같은 쌍 검사)
    same = [
        {
            "chosen": [
                {"role": "user", "content": "인사해 줘"},
                {"role": "assistant", "content": "안녕하세요!"},
            ],
            "rejected": [
                {"role": "user", "content": "인사해 줘"},
                {"role": "assistant", "content": "안녕하세요!"},
            ],
        }
    ]
    finished = await import_file(
        db_session,
        jsonl_bytes(same),
        mapping={"shape": "preference", "chosen": "chosen", "rejected": "rejected"},
    )
    pair_id = await db_session.scalar(select(Pair.id))

    # 실행
    queue = await client.get(
        f"{API}/datasets/{finished.dataset_id}/review", params={"check": "same_pair"}
    )
    pair = await client.get(f"{API}/pairs/{pair_id}")

    # 확인: 쌍 번호 · 단위 pair, 쌍이 든 대화에 쌍 문제가 실린다
    assert queue.json() == {"check": "same_pair", "unit": "pair", "ids": [pair_id], "total": 1}
    assert pair.json()["pairs"][0]["problems"] == ["same_pair"]


async def test_flip_pair_swaps_chosen_and_main_answer_and_undo_restores(client, db_session):
    # 준비: Jev가 버린 응답을 더 낫게 본 쌍(판정 반대)
    dataset_id = await _pair_dataset(db_session)
    pair = await db_session.scalar(select(Pair))
    pair.judge_chosen, pair.judge_rejected = 0.1, 0.9
    await db_session.commit()
    chosen_id, rejected_id = pair.chosen_id, pair.rejected_id

    # 실행
    flipped = await client.post(
        f"{API}/pairs/{pair.id}/decide", json={"check": "judge_reversed", "action": "flip"}
    )
    undone = await client.post(f"{API}/changes/{flipped.json()['change']['id']}/undo")

    # 확인: 뒤집으면 고른 응답 · 주 응답이 바뀌고 문제가 풀리며, 되돌리면 처음대로
    after = flipped.json()["conversation"]
    assert after["pairs"][0]["chosen_id"] == rejected_id
    assert after["pairs"][0]["problems"] == []
    assert after["answers"][0] == {**after["answers"][0], "id": rejected_id, "is_main": True}
    assert after["messages"][-1]["content"] == "모르겠어요. 다른 걸 물어봐 주세요."
    assert flipped.json()["change"]["pair_id"] == pair.id
    restored = undone.json()
    assert restored["pairs"][0]["chosen_id"] == chosen_id
    assert restored["pairs"][0]["problems"] == ["judge_reversed"]
    assert dataset_id


async def test_pair_decision_returns_422_for_conversation_check(client, db_session):
    # 준비
    await _pair_dataset(db_session)
    pair_id = await db_session.scalar(select(Pair.id))

    # 실행
    response = await client.post(
        f"{API}/pairs/{pair_id}/decide", json={"check": "pii", "action": "drop"}
    )

    # 확인
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {"detail": conversations.NOT_PAIR_CHECK_MESSAGE}


async def test_drop_pair_returns_409_when_problem_is_gone(client, db_session):
    # 준비: 판정이 없는 쌍(판정 반대가 아님)
    await _pair_dataset(db_session)
    pair_id = await db_session.scalar(select(Pair.id))

    # 실행
    response = await client.post(
        f"{API}/pairs/{pair_id}/decide", json={"check": "judge_reversed", "action": "drop"}
    )

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": conversations.PAIR_NO_PROBLEM_MESSAGE}


# ---------- 그대로 둔 것 다시 묻기 ----------


async def test_unkeep_asks_again_and_overview_counts_kept(client, db_session):
    # 준비: 짧은 응답을 그대로 둔다
    finished = await import_file(
        db_session, jsonl_bytes(messages_rows([conversation("1을 출력해", "1")]))
    )
    dataset_id = finished.dataset_id
    conversation_id = await _first_id(client, dataset_id)
    await client.post(
        f"{API}/conversations/{conversation_id}/decide", json={"check": "short", "action": "keep"}
    )
    kept_overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    kept_queue = await client.get(
        f"{API}/datasets/{dataset_id}/review", params={"check": "short", "kept": "true"}
    )

    # 실행
    response = await client.post(
        f"{API}/conversations/{conversation_id}/unkeep", json={"check": "short"}
    )
    again = await client.post(
        f"{API}/conversations/{conversation_id}/unkeep", json={"check": "short"}
    )
    changes = (await client.get(f"{API}/datasets/{dataset_id}/changes")).json()

    # 확인: 그대로 둔 수 · 목록에 보이고, 거두면 다시 문제로 뜨며 그 결정 기록은 되돌린 것이 된다
    short = next(check for check in kept_overview["checks"] if check["key"] == "short")
    assert (short["count"], short["kept"]) == (0, 1)
    assert kept_queue.json()["ids"] == [conversation_id]
    assert [problem["key"] for problem in response.json()["problems"]] == ["short"]
    assert again.status_code == status.HTTP_409_CONFLICT
    assert changes == []


async def test_export_dpo_and_prompts_files(client, db_session):
    # 준비: 선호 쌍 하나
    dataset_id = await _pair_dataset(db_session)

    # 실행
    preview = await client.post(
        f"{API}/datasets/{dataset_id}/exports/preview",
        json={"formats": ["dpo_jsonl", "prompts_jsonl"]},
    )

    # 확인: 첫 줄은 DPO 모양, 파일마다 단위와 수
    line = json.loads(preview.json()["line"])
    assert line == {
        "prompt": [{"role": "user", "content": "요약해 줘"}],
        "chosen": [{"role": "assistant", "content": "핵심만 세 줄입니다."}],
        "rejected": [{"role": "assistant", "content": "모르겠어요. 다른 걸 물어봐 주세요."}],
        "source": "original",
    }
    files = preview.json()["files"]
    assert [(f["format"], f["unit"], f["conversations"]) for f in files] == [
        ("dpo_jsonl", "pair", 1),
        ("prompts_jsonl", "conversation", 1),
    ]
