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
from dent.modules.generation.rules import CheckKey
from dent.system.agent import BLOCKED_MESSAGE
from dent.system.models import HelperEvent, Job
from tests.modules.generation.helpers import (
    CLEAN,
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


def fake_llm(*, echo: bool = False) -> httpx.MockTransport:
    """가짜 LLM (OpenAI 모양). 단계는 '지금 단계: … (열쇠)'로 알아본다:
    짧은 응답은 보기를 모두 빼고, 지시 불일치는 보류하고, 거절은 그대로 둔다. 도구 결과를 받으면 finish_step.
    도구 없는 요청: 보고는 두 줄, 다시 쓰기는 REWRITTEN.
    echo면 판단 도구의 say를 답 본문에도 빈 줄로 띄워 넣는다(실제 LLM이 그랬다).
    """
    requests: list[dict[str, Any]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        tools = {tool["function"]["name"] for tool in body.get("tools") or []}
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        if not tools:
            if "보고를 두 줄로" in system:
                return _message("무엇을 고쳤나?\n고칠 것은 고쳤다.")
            return _message(REWRITTEN)
        if "plan" in tools:
            return _message("", [_call("plan", "무엇부터 고칠까?\n지시부터.")])
        if any(message["role"] == "tool" for message in body["messages"]):
            return _message("", [_call("finish_step", "다 했다.")])
        found = re.search(r"지금 단계: .+ \((\w+)\)", system)
        key = found.group(1) if found else ""
        refs = re.findall(r"^(r\d+) ", user, flags=re.MULTILINE)
        if key == "short":
            return _message("", [_call("drop", "답이 엉뚱한가?\n뺀다.", refs=refs)])
        if key == "mismatch":
            say = "갈리나?\n사람이 본다."
            content = say.replace("\n", "\n\n") if echo else ""
            return _message(content, [_call("hold", say, refs=refs)])
        if key == "refusal":
            return _message("", [_call("keep", "마땅한가?\n둔다.", refs=refs)])
        return _message("", [_call("finish_step", "끝.")])

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
    # 지시 불일치는 LLM이 보류해 남는다. 잘린 응답은 실행 안에서 다시 쓰지 않아 남은 것으로 간다.
    assert started.status_code == status.HTTP_201_CREATED
    assert state["run"]["status"] == "done"
    for key in ("empty_prompt", "pii", "near_duplicate", "loop", "refusal", "short"):
        assert _check(overview, key)["count"] == 0, key
    assert _check(overview, "mismatch")["count"] == 1
    assert {item["key"]: item["group"] for item in left}["truncated"] == "ai"
    assert "report" in kinds
    assert state["run"]["held"] == 1
    # 보고를 두 줄로 말하게 할 때 처음 · 끝을 더하지 않게 '처음 → 끝' 글로 준다.
    report_request = next(
        body for body in llm.requests if "보고를 두 줄로" in body["messages"][0]["content"]
    )
    facts = json.loads(report_request["messages"][1]["content"])
    assert re.fullmatch(r"\d+ → \d+", facts["심각 검사 수 (처음 → 끝)"])


async def test_helper_writes_a_say_once_when_llm_echoes_it_with_blank_lines(client, db_session):
    # 준비: LLM이 판단 도구의 say를 답 본문에도 빈 줄로 띄워 넣는다.
    dataset_id = await _dataset(db_session)
    await connect_models(db_session, llm=True)
    run_id = (await client.post(f"{API}/datasets/{dataset_id}/helper")).json()["id"]

    # 실행
    await _run_queued(db_session, dataset_id, llm=fake_llm(echo=True))

    # 확인: 지시 불일치 단계의 말이 한 번씩만 적힌다.
    says = [
        event.payload["text"]
        for event in await db_session.scalars(
            select(HelperEvent).where(HelperEvent.run_id == run_id, HelperEvent.kind == "say")
        )
    ]
    assert says.count("갈리나?") == 1
    assert says.count("사람이 본다.") == 1


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
    blocked = next(step for step in state["run"]["steps"] if step["key"] == "empty_answer")
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
