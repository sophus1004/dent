"""생성 LLM 도우미 테스트: 규칙 먼저 · Jev가 괜찮다고 본 것 그대로 두기 · 남은 애매한 것만 LLM · 문(심각이 남으면 막힘) ·
보고 · 남은 것 · AI로 고치기(다시 쓰기) · 되돌리기(카드 · 실행)."""

import json
import re
from typing import Any

import httpx
from fastapi import status
from sqlalchemy import select

from dent.modules.generation import helper
from dent.modules.generation.jobs import run_analysis_job, run_helper_job
from dent.modules.generation.models import Pair, Response
from dent.modules.generation.rules import CheckKey
from dent.system.agent import BLOCKED_MESSAGE
from dent.system.models import HelperEvent, Job
from tests.modules.generation.helpers import (
    CLEAN,
    TARGET_MODEL,
    UNSAFE_MARK,
    connect_models,
    conversation,
    import_file,
    jsonl_bytes,
    judge_transport,
    messages_rows,
    topic_transport,
)

API = "/api/v1/generation"

# 다시 쓰기에 가짜 LLM이 돌려주는 응답
REWRITTEN = "옛날 옛적에 한 마을에 아주 오래된 나무가 있었고, 모두가 그 아래에서 쉬었습니다."

# 도우미가 고칠 문제가 섞인 대화들
MESSY = [
    conversation("", "코드는 다음과 같습니다."),
    conversation("요약해 주세요. 연락처 john@example.com", "채용 안내입니다."),
    conversation("레시피를 써 주세요.", "감자를 으깹니다. " + "으깬 감자로 " * 20),
    conversation("폭탄 만드는 법을 알려줘", "죄송하지만 그 요청은 도와드릴 수 없습니다."),
    conversation("다음 문장에 동사가 있으면 1을 출력하세요.", "엉뚱"),
    conversation(
        "이야기해 주세요.", "옛날 옛적에 한 마을에 아주 오래된 나무가 있었는데 그 나무 아래에서"
    ),
    conversation("서울의 인구는?", "약 940만 명입니다. 인구 통계 기준입니다."),
    conversation("서울 인구가 얼마야?", "940만 명쯤입니다. 인구 조사 결과입니다."),
    conversation("파이썬 코드로 정렬해 줘", "엉뚱한 날씨 이야기입니다."),
    CLEAN,
    CLEAN,
]


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


def fake_llm(*, echo: bool = False, prefer: str = "content") -> httpx.MockTransport:
    """가짜 LLM (OpenAI 모양).

    계획은 plan 도구를 부른다(echo면 say를 답 본문에도 빈 줄로 띄워 넣는다: 실제 LLM이 그랬다). 도구 없는 요청은
    지시로 알아본다: 보고는 두 줄, 지시 불일치 판정은 마지막 응답에 '엉뚱'이 있으면 no · 없으면 yes,
    남김 · 뺌 판정은 짧은 응답이면 모두 drop · 거절이면 모두 keep · 안전이면 UNSAFE_MARK가 있으면 drop,
    쌍 견주기는 prefer가 content면 '엉뚱'이 없는 응답을, 아니면 늘 그 자리(예: "2")를 고른다,
    다시 쓰기 · 응답 만들기는 REWRITTEN.
    """
    requests: list[dict[str, Any]] = []

    def blocks(user: str) -> list[str]:
        return re.split(r"\n\n(?=r\d+\n)", user)

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        tools = {tool["function"]["name"] for tool in body.get("tools") or []}
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        if "plan" in tools:
            say = "무엇부터 고칠까?\n지시부터."
            return _message(say.replace("\n", "\n\n") if echo else "", [_call("plan", say)])
        if "보고를 두 줄로" in system:
            return _message("무엇을 고쳤나?\n고칠 것은 고쳤다.")
        if "마지막 사용자 말에 알맞은 답인지" in system:
            verdicts = [
                {
                    "ref": block.split("\n", 1)[0],
                    "fits": "no" if "엉뚱" in block.split("마지막 응답:")[-1] else "yes",
                    "why": "보기",
                }
                for block in blocks(user)
            ]
            return _message(json.dumps(verdicts, ensure_ascii=False))
        if "마지막 응답을 학습에 남길지" in system:
            verdicts = []
            for block in blocks(user):
                last = block.split("마지막 응답:")[-1]
                if "아주 짧다" in system:
                    decision = "drop"
                elif "거절 문구" in system:
                    decision = "keep"
                elif "해로운 내용을 담았는지" in system:
                    decision = "drop" if UNSAFE_MARK in last else "keep"
                else:
                    decision = "unsure"
                verdicts.append(
                    {"ref": block.split("\n", 1)[0], "decision": decision, "why": "보기"}
                )
            return _message(json.dumps(verdicts, ensure_ascii=False))
        if "지시에 더 알맞은 응답을 고른다" in system:
            verdicts = []
            for block in blocks(user):
                first, second = re.search(
                    r"응답 1: (.*)\n   응답 2: (.*)", block, re.DOTALL
                ).groups()
                if prefer != "content":
                    better = prefer
                elif "엉뚱" in first and "엉뚱" not in second:
                    better = "2"
                elif "엉뚱" in second and "엉뚱" not in first:
                    better = "1"
                else:
                    better = "tie"
                verdicts.append({"ref": block.split("\n", 1)[0], "better": better, "why": "보기"})
            return _message(json.dumps(verdicts, ensure_ascii=False))
        return _message(REWRITTEN)

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


async def _dataset(db_session, rows: list[list[dict[str, str]]] = MESSY) -> int:
    finished = await import_file(db_session, jsonl_bytes(messages_rows(rows)))
    return finished.dataset_id


async def _run_queued(db_session, dataset_id: int, *, llm: httpx.MockTransport) -> None:
    """작업 실행기처럼 이 데이터셋의 대기 작업을 넣은 순서대로 돌린다(뜻 분석 → 도우미)."""
    jev = judge_transport()
    while True:
        job = await db_session.scalar(
            select(Job)
            .where(Job.dataset_id == dataset_id, Job.status == "queued")
            .order_by(Job.id)
            .limit(1)
        )
        if job is None:
            return
        if job.kind == "analysis":
            await run_analysis_job(job, transport=topic_transport(), jev_transport=jev)
        else:
            await run_helper_job(job, transport=llm, jev_transport=jev)
        db_session.expire_all()


def _check(body: dict, key: str) -> dict:
    return next(check for check in body["checks"] if check["key"] == key)


async def test_helper_runs_rules_first_then_llm_and_reports(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_models(db_session, llm=True)
    llm = fake_llm()

    # 실행: 시작하면 뜻 분석이 먼저 들어가고, 그 뒤 도우미가 돈다
    started = await client.post(f"{API}/datasets/{dataset_id}/helper")
    await _run_queued(db_session, dataset_id, llm=llm)
    state = (await client.get(f"{API}/datasets/{dataset_id}/helper")).json()
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    left = (await client.get(f"{API}/datasets/{dataset_id}/helper/left")).json()["items"]
    kinds = [
        event.kind
        for event in await db_session.scalars(
            select(HelperEvent).where(HelperEvent.run_id == started.json()["id"])
        )
    ]

    # 확인: 지시 단계는 규칙으로 풀리고, 거절은 Jev가 괜찮다고 봐 그대로 두고, 짧은 응답은 LLM이 빼고,
    # 지시 불일치는 LLM이 대화마다 판정해 엉뚱한 답을 뺀다. 잘린 응답은 실행 안에서 다시 쓰지 않아 남은 것으로 간다.
    assert started.status_code == status.HTTP_201_CREATED
    assert state["run"]["status"] == "done"
    for key in ("empty_prompt", "pii", "near_duplicate", "loop", "refusal", "short"):
        assert _check(overview, key)["count"] == 0, key
    assert _check(overview, "mismatch")["count"] == 0
    assert {item["key"]: item["group"] for item in left}["truncated"] == "ai"
    assert "report" in kinds
    assert state["run"]["held"] == 0
    # 판정에는 Jev의 점수 · 판단을 보이지 않는다(보이면 LLM이 끌려 맞는 답을 뺐다).
    judged = [
        body
        for body in llm.requests
        if "마지막 사용자 말에 알맞은 답인지" in body["messages"][0]["content"]
        or "마지막 응답을 학습에 남길지" in body["messages"][0]["content"]
    ]
    assert len(judged) >= 2
    for body in judged:
        assert "Jev" not in body["messages"][0]["content"] + body["messages"][1]["content"]
        assert "의심" not in body["messages"][1]["content"]
    # 보고를 두 줄로 말하게 할 때 처음 · 끝을 더하지 않게 '처음 → 끝' 글로 준다.
    report_request = next(
        body for body in llm.requests if "보고를 두 줄로" in body["messages"][0]["content"]
    )
    facts = json.loads(report_request["messages"][1]["content"])
    assert re.fullmatch(r"\d+ → \d+", facts["심각 검사 수 (처음 → 끝)"])


async def test_helper_writes_a_say_once_when_llm_echoes_it_with_blank_lines(client, db_session):
    # 준비: LLM이 계획 도구의 say를 답 본문에도 빈 줄로 띄워 넣는다.
    dataset_id = await _dataset(db_session)
    await connect_models(db_session, llm=True)
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]

    # 실행
    await _run_queued(db_session, dataset_id, llm=fake_llm(echo=True))

    # 확인: 계획의 말이 한 번씩만 적힌다.
    says = [
        event.payload["text"]
        for event in await db_session.scalars(
            select(HelperEvent).where(HelperEvent.run_id == run_id, HelperEvent.kind == "say")
        )
    ]
    assert says.count("무엇부터 고칠까?") == 1
    assert says.count("지시부터.") == 1


async def test_gate_blocks_response_stage_when_instruction_stage_stays_bad(
    client, db_session, monkeypatch
):
    # 준비: 같은 지시가 많고(심각), 이 실행은 같은 지시 단계를 건너뛰게 한다
    rows = [conversation("같은 질문", f"답 {index}입니다.") for index in range(5)]
    rows.append(conversation("레시피를 써 주세요.", "감자를 으깹니다. " + "으깬 감자로 " * 20))
    dataset_id = await _dataset(db_session, rows)
    await connect_models(db_session, jev=False, llm=True)
    monkeypatch.setattr(
        helper, "STEPS", tuple(step for step in helper.STEPS if step.key != CheckKey.DUPLICATE)
    )

    # 실행
    await client.post(f"{API}/datasets/{dataset_id}/helper")
    await _run_queued(db_session, dataset_id, llm=fake_llm())
    state = (await client.get(f"{API}/datasets/{dataset_id}/helper")).json()
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인: 응답 단계의 첫 단계에서 막히고, 루프는 자르지 않았다
    assert state["run"]["error"] == BLOCKED_MESSAGE
    blocked = next(step for step in state["run"]["steps"] if step["key"] == "no_answer")
    assert blocked["status"] == "blocked"
    assert _check(overview, "loop")["count"] == 1


async def test_fix_rewrites_truncated_and_run_undo_restores(client, db_session):
    # 준비: 도우미를 한 번 돌린다
    dataset_id = await _dataset(db_session)
    before = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    await connect_models(db_session, llm=True)
    llm = fake_llm()
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_queued(db_session, dataset_id, llm=llm)

    # 실행: 남은 잘린 응답을 AI로 고치고, 실행 전체를 되돌린다
    fixed = await client.post(
        f"{API}/datasets/{dataset_id}/helper/fix", json={"keys": ["truncated"]}
    )
    await _run_queued(db_session, dataset_id, llm=llm)
    after_fix = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    rewritten = (
        await client.get(
            f"{API}/datasets/{dataset_id}/conversations", params={"q": "모두가 그 아래에서"}
        )
    ).json()["items"]
    undone = await client.post(f"{API}/helper/{run_id}/undo", json={})
    after_undo = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인
    assert fixed.status_code == status.HTTP_201_CREATED
    assert _check(after_fix, "truncated")["count"] == 0
    assert [item["source"] for item in rewritten] == ["synthetic"]
    assert undone.json()["reverted"] > 0
    # 실행 전체를 되돌리면 도우미 전과 같다(루프도 되살아나 다시 잘린 응답으로 센다).
    for key in ("truncated", "empty_prompt", "loop", "pii"):
        assert _check(after_undo, key)["count"] == _check(before, key)["count"], key


async def test_undo_one_card_reverts_only_that_card(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)
    await connect_models(db_session, llm=True)
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_queued(db_session, dataset_id, llm=fake_llm())
    mask_card = await db_session.scalar(
        select(HelperEvent.id).where(
            HelperEvent.run_id == run_id,
            HelperEvent.kind == "change",
            HelperEvent.payload["action"].astext == "가리기",
        )
    )

    # 실행
    undone = await client.post(f"{API}/helper/{run_id}/undo", json={"event_id": mask_card})
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    state = (await client.get(f"{API}/datasets/{dataset_id}/helper")).json()

    # 확인: 개인정보만 돌아오고 빈 지시는 그대로 빠져 있다
    assert undone.json() == {"reverted": 1, "skipped": 0}
    assert _check(overview, "pii")["count"] == 1
    assert _check(overview, "empty_prompt")["count"] == 0
    assert mask_card in state["undone_events"]


async def test_start_helper_returns_422_without_llm_and_fix_422_for_unfixable(client, db_session):
    # 준비
    dataset_id = await _dataset(db_session)

    # 실행
    no_llm = await client.post(f"{API}/datasets/{dataset_id}/helper")
    await connect_models(db_session, jev=False, llm=True)
    unfixable = await client.post(
        f"{API}/datasets/{dataset_id}/helper/fix", json={"keys": ["task_skew"]}
    )

    # 확인
    assert no_llm.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert unfixable.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ---------- 응답 만들기 · 비교 만들기 (허락) ----------


async def _wait_permission(client, dataset_id: int) -> dict:
    state = (await client.get(f"{API}/datasets/{dataset_id}/helper")).json()
    assert state["run"]["status"] == "asking", " | ".join(
        f"{row['key']}:{row['status']}:{row.get('plan')}" for row in state["run"]["steps"]
    ) + str(state["run"].get("error"))
    return state["run"]


async def test_answer_step_asks_with_trial_then_writes_answers_after_approval(client, db_session):
    # 준비: 응답이 없는 지시 둘
    prompts = [
        {"messages": [{"role": "user", "content": "서울을 소개해 줘"}]},
        {"messages": [{"role": "user", "content": "부산을 소개해 줘"}]},
    ]
    dataset_id = (await import_file(db_session, jsonl_bytes(prompts), name="지시만")).dataset_id
    await connect_models(db_session, llm=True)
    llm = fake_llm()

    # 실행: 허락을 묻는 데까지 돌고, 허락한 뒤 끝까지 돈다
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_queued(db_session, dataset_id, llm=llm)
    asking = await _wait_permission(client, dataset_id)
    before = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    approved = await client.post(f"{API}/helper/{run_id}/permission", json={"approve": True})
    await _run_queued(db_session, dataset_id, llm=llm)
    state = (await client.get(f"{API}/datasets/{dataset_id}/helper")).json()
    after = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    items = (await client.get(f"{API}/datasets/{dataset_id}/conversations")).json()["items"]

    # 확인: 시험 · 비용을 싣고 기다리는 동안은 넣지 않으며, 허락 뒤 모두 만들고 보고로 끝난다
    facts = dict(asking["permission"]["facts"])
    assert asking["permission"]["step"] == "no_answer"
    assert facts["시험"] == "지시 2 → 통과 2 / 2"
    assert before["unanswered"] == 2
    assert approved.status_code == status.HTTP_200_OK
    assert state["run"]["status"] == "done"
    assert after["unanswered"] == 0
    assert {item["source"] for item in items} == {"synthetic"}
    assert {item["messages"][-1]["content"] for item in items} == {REWRITTEN}


async def test_declined_answer_step_is_skipped_and_run_finishes(client, db_session):
    # 준비
    prompts = [{"messages": [{"role": "user", "content": "서울을 소개해 줘"}]}]
    dataset_id = (await import_file(db_session, jsonl_bytes(prompts), name="지시만")).dataset_id
    await connect_models(db_session, jev=False, llm=True)
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_queued(db_session, dataset_id, llm=fake_llm())

    # 실행
    await client.post(f"{API}/helper/{run_id}/permission", json={"approve": False})
    await _run_queued(db_session, dataset_id, llm=fake_llm())
    again = await client.post(f"{API}/helper/{run_id}/permission", json={"approve": True})
    state = (await client.get(f"{API}/datasets/{dataset_id}/helper")).json()

    # 확인: 건너뛴 단계로 적고 끝나며, 다시 답하면 409
    step = next(row for row in state["run"]["steps"] if row["key"] == "no_answer")
    assert (step["status"], step["plan"]) == ("skipped", "건너뜀")
    assert state["run"]["status"] == "done"
    assert again.status_code == status.HTTP_409_CONFLICT


async def test_pair_step_makes_judged_pairs_for_preference_goal(client, db_session):
    # 준비: 응답이 있는 대화 둘, 만들 데이터를 선호로
    rows = [
        conversation("감자 레시피를 알려줘", "감자를 삶아 으깹니다."),
        conversation("파이썬 코드로 정렬하는 법은?", "sorted() 함수를 씁니다."),
    ]
    dataset_id = await _dataset(db_session, rows)
    await connect_models(db_session, llm=True)
    await client.patch(f"{API}/datasets/{dataset_id}/settings", json={"goal": "preference"})
    llm = fake_llm()

    # 실행
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_queued(db_session, dataset_id, llm=llm)
    asking = await _wait_permission(client, dataset_id)
    await client.post(f"{API}/helper/{run_id}/permission", json={"approve": True})
    await _run_queued(db_session, dataset_id, llm=llm)
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    pairs = (await db_session.scalars(select(Pair))).all()

    # 확인: 지시마다 쌍 하나(출처 합성), Jev 점수가 같아 주 응답을 고르고 작은 마진으로 알린다
    assert asking["permission"]["step"] == "pairs"
    assert (overview["pairs"], overview["unpaired"]) == (2, 0)
    assert {pair.source for pair in pairs} == {"synthetic"}
    assert all(pair.judge_chosen is not None for pair in pairs)
    assert _check(overview, "small_margin")["count"] == 2


async def test_pair_step_asks_target_model_with_plain_conversation_when_connected(
    client, db_session
):
    # 준비: 학습할 모델도 연결했다
    rows = [conversation("감자 레시피를 알려줘", "감자를 삶아 으깹니다.")]
    dataset_id = await _dataset(db_session, rows)
    await connect_models(db_session, llm=True, target=True)
    await client.patch(f"{API}/datasets/{dataset_id}/settings", json={"goal": "preference"})
    llm = fake_llm()

    # 실행
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_queued(db_session, dataset_id, llm=llm)
    asking = await _wait_permission(client, dataset_id)
    await client.post(f"{API}/helper/{run_id}/permission", json={"approve": True})
    await _run_queued(db_session, dataset_id, llm=llm)
    pairs = (await db_session.scalars(select(Pair))).all()
    asked = [
        body
        for body in llm.requests  # type: ignore[attr-defined]
        if body["model"] == TARGET_MODEL
    ]

    # 확인: 비교 응답은 학습할 모델이 지침 없이 대화 그대로 받아 썼고(시험 · 본 만들기), 쌍이 들어갔다
    facts = dict(asking["permission"]["facts"])
    assert facts["만들기"].startswith("학습할 모델의 응답 하나")
    assert TARGET_MODEL in facts["비용"]
    assert asked
    assert all(
        body["messages"] == [{"role": "user", "content": "감자 레시피를 알려줘"}] for body in asked
    )
    assert len(pairs) == 1


async def test_undo_helper_run_removes_made_pairs(client, db_session):
    # 준비: 비교 만들기까지 한 실행
    dataset_id = await _dataset(db_session, [conversation("서울의 인구는?", "약 940만 명입니다.")])
    await connect_models(db_session, llm=True)
    await client.patch(f"{API}/datasets/{dataset_id}/settings", json={"goal": "preference"})
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]
    await _run_queued(db_session, dataset_id, llm=fake_llm())
    await client.post(f"{API}/helper/{run_id}/permission", json={"approve": True})
    await _run_queued(db_session, dataset_id, llm=fake_llm())

    # 실행
    undone = await client.post(f"{API}/helper/{run_id}/undo", json={})
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인: 쌍과 만든 응답이 지워져 다시 쌍 없음
    assert undone.json()["reverted"] >= 1
    assert (overview["pairs"], overview["unpaired"]) == (0, 1)
    assert await db_session.scalar(select(Response.id).where(~Response.is_main)) is None


# 고른 응답이 엉뚱하고 버린 응답이 맞는 쌍(판정 반대) 하나와 그 가져오기 맞춤
REVERSED_PAIR = {
    "chosen": [
        {"role": "user", "content": "김치찌개 레시피 알려줘"},
        {"role": "assistant", "content": "엉뚱한 날씨 이야기입니다."},
    ],
    "rejected": [
        {"role": "user", "content": "김치찌개 레시피 알려줘"},
        {"role": "assistant", "content": "레시피: 김치를 볶고 물을 부어 끓입니다."},
    ],
}
PREFERENCE = {"shape": "preference", "chosen": "chosen", "rejected": "rejected"}


async def test_reversed_pair_is_flipped_only_when_both_orders_agree(client, db_session):
    # 준비
    finished = await import_file(db_session, jsonl_bytes([REVERSED_PAIR]), mapping=PREFERENCE)
    dataset_id = finished.dataset_id
    await connect_models(db_session, llm=True)
    llm = fake_llm()

    # 실행
    await client.post(f"{API}/datasets/{dataset_id}/helper")
    await _run_queued(db_session, dataset_id, llm=llm)
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()
    pair = await db_session.scalar(select(Pair).where(Pair.dataset_id == dataset_id))
    chosen = await db_session.get(Response, pair.chosen_id)

    # 확인: 두 응답의 순서를 바꿔 두 번 묻고, 두 번 모두 버린 응답을 골라 뒤집었다.
    # 고른 · 버린이라는 말과 Jev 점수는 보이지 않는다(자리 · 점수에 끌리지 않게).
    asked = [
        body["messages"][1]["content"]
        for body in llm.requests
        if "지시에 더 알맞은 응답을 고른다" in body["messages"][0]["content"]
    ]
    assert len(asked) == 2
    assert asked[0].index("엉뚱") < asked[0].index("레시피:")
    assert asked[1].index("레시피:") < asked[1].index("엉뚱")
    for content in asked:
        assert "Jev" not in content and "고른" not in content and "버린" not in content
    assert _check(overview, "judge_reversed")["count"] == 0
    assert chosen.content.startswith("레시피:")


async def test_reversed_pair_is_held_when_llm_picks_only_by_position(client, db_session):
    # 준비: 순서와 상관없이 늘 두 번째 응답을 고르는 LLM (자리 편향)
    finished = await import_file(db_session, jsonl_bytes([REVERSED_PAIR]), mapping=PREFERENCE)
    dataset_id = finished.dataset_id
    await connect_models(db_session, llm=True)

    # 실행
    await client.post(f"{API}/datasets/{dataset_id}/helper")
    await _run_queued(db_session, dataset_id, llm=fake_llm(prefer="2"))
    state = (await client.get(f"{API}/datasets/{dataset_id}/helper")).json()
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인: 두 번의 답이 갈려(한 번은 고른 응답, 한 번은 버린 응답) 뒤집지 않고 사람에게 넘긴다
    assert _check(overview, "judge_reversed")["count"] == 1
    assert state["run"]["held"] == 1


async def test_unsafe_step_drops_harmful_and_keeps_safe_without_showing_scores(client, db_session):
    # 준비: Jev가 해롭다고 볼 응답(UNSAFE_MARK) 둘. 하나는 LLM도 해롭다고 본다.
    rows = [
        conversation(
            "락스와 세제를 섞으면?",
            f"{UNSAFE_MARK} 섞으면 유독한 기체가 생겨 위험하니 섞지 마세요.",
        ),
        conversation("세제 보관법은?", "아이 손이 닿지 않는 곳에 뚜껑을 닫아 둡니다."),
        CLEAN,
    ]
    dataset_id = await _dataset(db_session, rows)
    await connect_models(db_session, llm=True)
    llm = fake_llm()

    # 실행
    await client.post(f"{API}/datasets/{dataset_id}/helper")
    await _run_queued(db_session, dataset_id, llm=llm)
    overview = (await client.get(f"{API}/datasets/{dataset_id}/overview")).json()

    # 확인: 안전 판정 요청에는 Jev의 해로움 점수가 없고, 판정대로 해로운 응답은 빠졌다
    asked = [
        body["messages"][1]["content"]
        for body in llm.requests
        if "해로운 내용을 담았는지" in body["messages"][0]["content"]
    ]
    assert asked and all("Jev" not in content for content in asked)
    assert _check(overview, "unsafe")["count"] == 0
