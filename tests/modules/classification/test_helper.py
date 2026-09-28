"""LLM 도우미 테스트: 한 번 실행(계획 → 단계마다 규칙 → 남은 것만 LLM → 결과 → 보고), 스스로 묻고 답한 말,
바꾼 카드 · 되돌리기, 보류, 멈추기 · 토큰 상한 · LLM 실패, 남은 것(지금 진단으로 셈) · AI로 고치기
(라벨 충돌 둘째 판정 · 새 문장: 그 라벨의 문장 · 앞서 더한 새 문장과 근접한 것 거르기 · 심각이 남으면 만들지 않음), API.

LLM은 시스템 말로 할 일을 알아보는 가짜다: '지금 단계: … (열쇠)'면 규칙이 남긴 판단만 하고,
라벨 판정 · 짧은 문장 판정 · 보고 두 줄 · 새 문장 요청(도구 없음)에는 정해 둔 JSON · 글을 준다.
Jev는 문장마다 정해 둔 라벨을 고르는 가짜다.
"""

import hashlib
import json
import re
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import status
from sqlalchemy import select

from dent.modules.classification import helper
from dent.modules.classification.jobs import run_helper_job
from dent.modules.classification.models import ExcludeReason, HelperChange, Record
from dent.system import connections as connections_service
from dent.system import helper as helper_service
from dent.system import jobs
from dent.system.models import ConnectionRole, HelperEvent, HelperRunStatus, JobStatus, LlmProvider
from dent.system.text import normalize_text
from tests.modules.classification.helpers import Row, build_map, connect_embedding
from tests.system.helpers import JEV_URL, LLM_KEY, LLM_URL

API = "/api/v1/classification"

KNOWN = "추신수 2타수 무안타…볼넷으로 2차례 출루"
CLEAR_CONFLICT = "알뜰폰에 SKT 저가 신규요금제 도매로 제공"
VAGUE_CONFLICT = "게시판 한국언론학회 봄 정기학술대회"
SHORT = "속보"
DUPLICATE = "KB손보 배구단 유소년배구클럽 2기 수료식"
# 가 · 나로 갈렸는데 Jev는 셋째 라벨 다를 확신하는 문장
THIRD_CONFLICT = "지역 축제 개막과 도심 교통 통제 안내"

ROWS = [
    Row(CLEAR_CONFLICT, "가"),
    Row(CLEAR_CONFLICT, "나"),
    Row(VAGUE_CONFLICT, "가"),
    Row(VAGUE_CONFLICT, "나"),
    Row(SHORT, "가"),
    Row(DUPLICATE, "나"),
    Row(DUPLICATE, "나"),
    *[Row(f"가 쪽 평범한 문장 {index}", "가") for index in range(6)],
    *[Row(f"나 쪽 평범한 문장 {index}", "나") for index in range(6)],
]

# 가짜 Jev: 문장 → (고른 라벨, 확률)
JEV_ANSWERS = {
    CLEAR_CONFLICT: ("나", 0.91),
    VAGUE_CONFLICT: ("나", 0.52),
    THIRD_CONFLICT: ("다", 0.95),
}

# 새 문장 가운데 보기 문장 KNOWN과 뜻이 같은 것 (가짜 임베딩이 같은 벡터를 준다)
NEW_NEAR_SAMPLE = "가 쪽에 새로 쓴 문장 하나"
# 새 문장 가운데 앞서 더한 새 문장 NEW_KEPT와 뜻이 같은 것
NEW_KEPT = "가 쪽에 새로 쓴 문장 둘"
NEW_NEAR_MADE = "가 쪽에 새로 쓴 문장 둘을 살짝 바꾼 것"


def fake_jev(*, sure_of_vague: bool = False) -> httpx.MockTransport:
    """문장마다 정해 둔 라벨을 고르는 가짜 Jev. 모르는 문장(새 문장)은 '가' 0.95, '엉뚱'이 든 문장은 '나'.

    sure_of_vague면 애매한 충돌도 '나' 0.9로 확신한다(충돌이 남지 않는다).
    """
    answers = {**JEV_ANSWERS, VAGUE_CONFLICT: ("나", 0.9)} if sure_of_vague else JEV_ANSWERS

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        text = body["state"]["text"]
        choice, prob = answers.get(text, ("나", 0.9) if "엉뚱" in text else ("가", 0.95))
        other = "가" if choice == "나" else "나"
        answer = {
            "type": "choice",
            "choice": choice,
            "probabilities": {choice: prob, other: 1 - prob},
            "confidence": 0.8,
        }
        return httpx.Response(
            200, json={"model": "laya", "answers": {name: answer for name in body["questions"]}}
        )

    return httpx.MockTransport(reply)


def _call(name: str, say: str, **arguments: Any) -> dict[str, Any]:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps({"say": say, **arguments}, ensure_ascii=False),
        },
    }


def fake_llm(
    *,
    fail: bool = False,
    lazy: bool = False,
    hold_short: bool = False,
    label: str = "나",
    echo: bool = False,
) -> httpx.MockTransport:
    """가짜 LLM (OpenAI 모양). 도우미 단계는 '지금 단계: … (열쇠)'로 알아보고 규칙이 남긴 판단만 한다:
    라벨 충돌은 보기의 첫 ref를 보류, 짧은 문장은 모두 뺀다(hold_short면 보류). 도구 결과를 받으면 finish_step.
    lazy면 어느 단계에서도 finish_step만 부른다(규칙이 LLM 없이 되는지 본다).
    echo면 계획의 말을 답 본문과 도구의 say에 똑같이 넣는다(실제 LLM이 자주 그런다).

    도구 없는 요청: 라벨 판정은 모든 문장에 label, 짧은 문장 판정은 모두 drop, 보고는 두 줄,
    그 밖(새 문장 요청)은 문장 셋을 JSON으로 준다.
    """
    requests: list[dict[str, Any]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if fail:
            return httpx.Response(
                429,
                json={"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}},
            )
        tools = {tool["function"]["name"] for tool in body.get("tools") or []}
        has_results = any(message["role"] == "tool" for message in body["messages"])
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        if not tools:
            ids = [int(found) for found in re.findall(r"^\[(\d+)\]", user, flags=re.MULTILINE)]
            if "라벨 판정자" in system:
                items = [{"id": item_id, "label": label} for item_id in ids]
                return _message(json.dumps({"items": items}, ensure_ascii=False))
            if "짧은 문장마다" in system:
                items = [{"id": item_id, "answer": "drop"} for item_id in ids]
                return _message(json.dumps({"items": items}, ensure_ascii=False))
            if "보고를 두 줄로" in system:
                return _message("무엇을 고쳤나?\n고칠 것은 고쳤다.")
            texts = [NEW_NEAR_SAMPLE, NEW_KEPT, NEW_NEAR_MADE, "엉뚱하게 나로 읽힐 문장"]
            return _message(json.dumps({"texts": texts}, ensure_ascii=False))
        if "plan" in tools:
            plan = "무엇부터 고칠까?\n라벨이 갈린 같은 문장부터."
            return _message(plan if echo else "", [_call("plan", plan)])
        if lazy or has_results:
            return _message("", [_call("finish_step", "다 했다.")])
        found = re.search(r"지금 단계: .+ \((\w+)\)", system)
        key = found.group(1) if found else ""
        refs = re.findall(r"^(r\d+) ", user, flags=re.MULTILINE)
        if key == "conflict" and refs:
            items = [{"ref": refs[0], "reason": "학회 공지"}]
            return _message("", [_call("hold", "근거가 없다.\n사람에게 넘긴다.", items=items)])
        if key == "short":
            if hold_short:
                items = [{"ref": ref, "reason": "뜻을 모름"} for ref in refs]
                return _message("", [_call("hold", "뺄까?\n사람이 본다.", items=items)])
            return _message("", [_call("exclude_all_short", "뜻이 없다.\n뺀다.")])
        return _message("", [_call("finish_step", "끝.")])

    transport = httpx.MockTransport(reply)
    transport.requests = requests  # type: ignore[attr-defined]
    return transport


def near_embedding() -> httpx.MockTransport:
    """가짜 임베딩 서버: 문장마다 해시로 정한 64차원 벡터(서로 멀다).

    NEW_NEAR_SAMPLE은 KNOWN과, NEW_NEAR_MADE는 NEW_KEPT와 같은 벡터를 준다.
    """
    twin = {
        normalize_text(NEW_NEAR_SAMPLE): normalize_text(KNOWN),
        normalize_text(NEW_NEAR_MADE): normalize_text(NEW_KEPT),
    }

    def vector(text: str) -> list[float]:
        seed = int(hashlib.sha256(twin.get(text, text).encode()).hexdigest()[:8], 16)
        return np.random.default_rng(seed).normal(size=64).tolist()

    def reply(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        data = [{"index": index, "embedding": vector(text)} for index, text in enumerate(texts)]
        return httpx.Response(200, json={"data": data})

    return httpx.MockTransport(reply)


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


async def _connect(db_session, *, with_jev: bool = True) -> None:
    await connections_service.save_connection(
        db_session,
        role=ConnectionRole.LLM,
        base_url=LLM_URL,
        model="test-chat",
        provider=LlmProvider.OPENAI,
        api_key=LLM_KEY,
    )
    if with_jev:
        await connections_service.save_connection(
            db_session, role=ConnectionRole.JEV, base_url=JEV_URL, model=None
        )


async def _run(db_session, run_id: int, **transports: Any) -> None:
    """작업 실행기처럼 실행의 작업을 처리 함수에 넘긴다."""
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.job_id is not None
    job = await jobs.get_job(db_session, job_id=run.job_id)
    await run_helper_job(job, **transports)


async def _started(
    client,
    db_session,
    make_dataset,
    *,
    rows: list[Row] = ROWS,
    sure_of_vague: bool = False,
    **llm_options: Any,
):
    """데이터셋을 만들고 연결한 뒤 도우미를 시작해 끝까지 돌린다. (만든 데이터셋, 실행 번호)."""
    made = await make_dataset("도우미", rows)
    await _connect(db_session)
    response = await client.post(f"{API}/datasets/{made.dataset.id}/helper")
    assert response.status_code == status.HTTP_201_CREATED, response.text
    run_id = response.json()["id"]
    await _run(
        db_session,
        run_id,
        transport=fake_llm(**llm_options),
        jev_transport=fake_jev(sure_of_vague=sure_of_vague),
    )
    return made, run_id


async def _records(db_session, dataset_id: int) -> dict[str, list[Record]]:
    rows = await db_session.scalars(
        select(Record)
        .where(Record.dataset_id == dataset_id)
        .order_by(Record.id)
        .execution_options(populate_existing=True)
    )
    by_text: dict[str, list[Record]] = {}
    for record in rows:
        by_text.setdefault(record.text, []).append(record)
    return by_text


async def _events(db_session, run_id: int) -> list[HelperEvent]:
    return list(
        await db_session.scalars(
            select(HelperEvent).where(HelperEvent.run_id == run_id).order_by(HelperEvent.id)
        )
    )


# ---------- 한 번 실행 ----------


async def test_helper_fixes_conflict_short_and_duplicate_and_finishes(
    client, db_session, make_dataset
):
    # 준비 · 실행
    made, run_id = await _started(client, db_session, make_dataset)

    # 확인: 데이터
    records = await _records(db_session, made.dataset.id)
    labels = {label.id: name for name, label in made.labels.items()}
    assert {labels[record.label_id] for record in records[CLEAR_CONFLICT]} == {"나"}
    assert [record.exclude_reason for record in records[CLEAR_CONFLICT]] == [
        None,
        ExcludeReason.HELPER,
    ]
    assert {labels[record.label_id] for record in records[VAGUE_CONFLICT]} == {
        "가",
        "나",
    }
    assert records[SHORT][0].exclude_reason == ExcludeReason.HELPER
    assert [record.exclude_reason for record in records[DUPLICATE]] == [
        None,
        ExcludeReason.HELPER,
    ]

    # 확인: 실행
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.DONE
    # 라벨 충돌을 통일하자 같은 문장 둘이 중복이 되어 중복 단계에서 하나 더 뺀다(바꿈 4).
    assert (run.changed, run.held, run.jev_calls) == (4, 1, 2)
    assert run.llm_tokens > 0
    steps = {step["key"]: step for step in run.steps}
    assert "leak" not in steps
    assert steps["conflict"]["delta"] == ["4건", "2건", "bad"]
    assert steps["suspect"]["status"] == "skipped"
    job = await jobs.get_job(db_session, job_id=run.job_id)
    assert job.status == JobStatus.DONE


async def test_helper_leaves_conflict_to_llm_when_jev_picks_third_label(
    client, db_session, make_dataset
):
    # 준비: 가 · 나로 갈린 문장을 Jev가 셋째 라벨 다로 확신한다.
    rows = [
        *ROWS,
        Row(THIRD_CONFLICT, "가"),
        Row(THIRD_CONFLICT, "나"),
        Row("다 쪽 평범한 문장", "다"),
    ]

    # 실행
    made, run_id = await _started(client, db_session, make_dataset, rows=rows)

    # 확인: 규칙은 무리의 라벨을 고른 확신만 통일하고, 셋째 라벨은 LLM에 넘긴다(가짜 LLM은 바꾸지 않는다).
    records = await _records(db_session, made.dataset.id)
    labels = {label.id: name for name, label in made.labels.items()}
    assert {labels[record.label_id] for record in records[THIRD_CONFLICT]} == {"가", "나"}
    assert {labels[record.label_id] for record in records[CLEAR_CONFLICT]} == {"나"}
    events = await _events(db_session, run_id)
    jev_rows = next(event.payload["rows"] for event in events if event.kind == "jev")
    decisions = {row["text"]: row["decision"] for row in jev_rows}
    assert decisions[THIRD_CONFLICT] == "llm"
    assert decisions[CLEAR_CONFLICT] == "apply"


async def test_helper_records_self_talk_tool_cards_and_results(client, db_session, make_dataset):
    # 준비 · 실행
    _made, run_id = await _started(client, db_session, make_dataset)

    # 확인
    events = await _events(db_session, run_id)
    says = [event.payload for event in events if event.kind == "say"]
    assert {"text": "무엇부터 고칠까?", "ask": True} in says
    assert {"text": "라벨이 갈린 같은 문장부터.", "ask": False} in says
    # 규칙으로 한 일도 스스로 묻고 답하는 말을 남긴다.
    assert {"text": "같은 문장 · 라벨이 여럿인가?", "ask": True} in says
    kinds = [event.kind for event in events]
    assert "jev" in kinds and "hold" in kinds
    changes = [event.payload for event in events if event.kind == "change"]
    assert [(change["action"], change["count"]) for change in changes] == [
        ("라벨 바꾸기", 1),
        ("학습에서 빼기", 1),
        ("학습에서 빼기", 2),
    ]
    results = [event.payload for event in events if event.kind == "result"]
    assert results[0] == {"check": "라벨 충돌", "from": "4건", "to": "2건", "grade": "bad"}
    hold = next(event.payload for event in events if event.kind == "hold")
    assert hold["items"][0]["text"] == VAGUE_CONFLICT


async def test_helper_writes_a_say_once_when_llm_repeats_it_in_content(
    client, db_session, make_dataset
):
    # 준비 · 실행: LLM이 계획의 말을 답 본문과 도구의 say에 똑같이 넣는다.
    _made, run_id = await _started(client, db_session, make_dataset, echo=True)

    # 확인
    events = await _events(db_session, run_id)
    says = [event.payload["text"] for event in events if event.kind == "say"]
    assert says.count("무엇부터 고칠까?") == 1
    assert says.count("라벨이 갈린 같은 문장부터.") == 1


async def test_helper_sends_only_judgement_tools_and_a_fresh_conversation_each_step(
    client, db_session, make_dataset
):
    # 준비
    made = await make_dataset("도우미", ROWS)
    await _connect(db_session)
    run_id = (await client.post(f"{API}/datasets/{made.dataset.id}/helper")).json()["id"]
    llm_transport = fake_llm()

    # 실행
    await _run(db_session, run_id, transport=llm_transport, jev_transport=fake_jev())

    # 확인: 단계마다 첫 요청은 system · user 두 말뿐이다(앞 단계 대화를 싣지 않는다).
    requests = llm_transport.requests  # type: ignore[attr-defined]
    firsts = [body for body in requests if len(body["messages"]) == 2]
    step_tools = {
        re.search(r"\((\w+)\)", body["messages"][0]["content"]).group(1): {
            tool["function"]["name"] for tool in body["tools"]
        }
        for body in firsts
        if "지금 단계" in body["messages"][0]["content"]
    }
    # 규칙 단계(중복 여분)는 LLM에 묻지 않고, 판단이 남은 단계에는 판단 도구만 준다.
    assert set(step_tools) == {"conflict", "short"}
    assert step_tools["conflict"] == {"hold", "relabel", "finish_step"}
    assert step_tools["short"] == {"exclude_all_short", "exclude", "hold", "relabel", "finish_step"}
    offered = {tool["function"]["name"] for body in requests for tool in body.get("tools") or []}
    assert offered.isdisjoint(
        {"jev_judge_conflicts", "apply_jev_labels", "exclude_duplicate_extras"}
    )


async def test_helper_applies_rules_even_when_llm_only_finishes(client, db_session, make_dataset):
    # 준비 · 실행: 어느 단계에서도 finish_step만 부르는 LLM
    made, _run_id = await _started(client, db_session, make_dataset, lazy=True)

    # 확인: 판단이 필요 없는 고치기는 LLM의 선택과 상관없이 됐다.
    records = await _records(db_session, made.dataset.id)
    labels = {label.id: name for name, label in made.labels.items()}
    assert {labels[record.label_id] for record in records[CLEAR_CONFLICT]} == {"나"}
    assert [record.exclude_reason for record in records[DUPLICATE]] == [
        None,
        ExcludeReason.HELPER,
    ]
    # 짧은 문장은 판단 몫이라 그대로 남는다.
    assert records[SHORT][0].exclude_reason is None


async def test_helper_runs_duplicate_step_for_duplicates_made_by_conflict_fix(
    client, db_session, make_dataset
):
    # 준비: 처음에는 중복 여분이 없다. 라벨 충돌을 통일하면 같은 문장 · 라벨 둘이 생긴다.
    rows = [row for row in ROWS if row.text not in (DUPLICATE, VAGUE_CONFLICT)]

    # 실행
    made, run_id = await _started(client, db_session, make_dataset, rows=rows)

    # 확인: 닿은 때 재므로 중복 여분 단계가 돌아 새로 생긴 여분을 뺀다('문제 없음'으로 건너뛰지 않는다).
    run = await helper_service.get_run(db_session, run_id=run_id)
    steps = {step["key"]: step for step in run.steps}
    assert steps["duplicate"]["status"] == "done"
    records = await _records(db_session, made.dataset.id)
    assert [record.exclude_reason for record in records[CLEAR_CONFLICT]] == [
        None,
        ExcludeReason.HELPER,
    ]


# ---------- 보고 · 남은 것 ----------


async def test_helper_ends_with_report(client, db_session, make_dataset):
    # 준비 · 실행
    _made, run_id = await _started(client, db_session, make_dataset)

    # 확인: 마지막 단계는 보고이고, 처음 → 끝 · 고친 검사 · 바꿈 · 비용과 LLM 두 줄을 싣는다.
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert (run.status, run.permission) == (HelperRunStatus.DONE, None)
    assert run.steps[-1]["key"] == "report"
    assert run.steps[-1]["status"] == "done"
    events = await _events(db_session, run_id)
    report = next(event.payload for event in events if event.kind == "report")
    assert report["title"] == "보고"
    assert report["overall"] == ["준비 안 됨", "준비 안 됨"]
    assert {"name": "라벨 충돌", "from": "4건", "to": "2건", "grade": "bad"} in report["rows"]
    assert "학습에서 빼기 3" in report["changed"]
    assert report["cost"]["jev"] == 2
    says = [event.payload["text"] for event in events if event.kind == "say"]
    assert says[-2:] == ["무엇을 고쳤나?", "고칠 것은 고쳤다."]


async def test_left_lists_remaining_checks_by_group(client, db_session, make_dataset):
    # 준비: 애매한 라벨 충돌을 보류해 라벨 충돌(심각)이 남는다.
    made, _run_id = await _started(client, db_session, make_dataset)

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/helper/left")

    # 확인
    assert response.status_code == status.HTTP_200_OK
    items = {item["key"]: item for item in response.json()["items"]}
    conflict = items["conflict"]
    assert (conflict["value"], conflict["unit"], conflict["grade"]) == ("2", "건", "bad")
    assert conflict["group"] == "ai"
    assert conflict["how"].startswith("Jev → LLM")
    assert conflict["tokens"] > 0 and conflict["jev"] == 2


async def test_left_shrinks_when_a_person_fixes_it(client, db_session, make_dataset):
    # 준비
    made, _run_id = await _started(client, db_session, make_dataset)
    records = await _records(db_session, made.dataset.id)

    # 실행: 사람이 애매한 충돌 문장을 학습에서 뺀다.
    await client.post(
        f"{API}/datasets/{made.dataset.id}/records/bulk",
        json={
            "record_ids": [record.id for record in records[VAGUE_CONFLICT]],
            "action": "exclude",
        },
    )
    response = await client.get(f"{API}/datasets/{made.dataset.id}/helper/left")

    # 확인: 남은 것은 저장하지 않고 지금 진단으로 센다.
    assert "conflict" not in {item["key"] for item in response.json()["items"]}


async def test_left_is_empty_before_first_run(client, make_dataset):
    # 준비
    made = await make_dataset("도우미", ROWS)

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/helper/left")

    # 확인
    assert response.json() == {"items": []}


# ---------- 되돌리기 ----------


async def test_undo_restores_everything_the_run_changed(client, db_session, make_dataset):
    # 준비
    made, run_id = await _started(client, db_session, make_dataset)

    # 실행
    response = await client.post(f"{API}/helper/{run_id}/undo")

    # 확인
    assert response.json() == {"reverted": 4, "skipped": 0}
    records = await _records(db_session, made.dataset.id)
    assert all(record.exclude_reason is None for group in records.values() for record in group)
    labels = {label.id: name for name, label in made.labels.items()}
    assert sorted(labels[record.label_id] for record in records[CLEAR_CONFLICT]) == [
        "가",
        "나",
    ]
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.changed == 0
    state = (await client.get(f"{API}/datasets/{made.dataset.id}/helper")).json()
    assert len(state["undone_events"]) == 3


async def test_undo_one_card_leaves_other_changes(client, db_session, make_dataset):
    # 준비
    made, run_id = await _started(client, db_session, make_dataset)
    relabel_card = next(
        event
        for event in await _events(db_session, run_id)
        if event.payload.get("action") == "라벨 바꾸기"
    )

    # 실행
    response = await client.post(f"{API}/helper/{run_id}/undo", json={"event_id": relabel_card.id})

    # 확인
    assert response.json() == {"reverted": 1, "skipped": 0}
    records = await _records(db_session, made.dataset.id)
    assert records[SHORT][0].exclude_reason == ExcludeReason.HELPER


async def test_undo_skips_records_changed_after_the_helper(client, db_session, make_dataset):
    # 준비: 도우미가 뺀 짧은 문장을 사람이 다시 넣었다.
    made, run_id = await _started(client, db_session, make_dataset)
    records = await _records(db_session, made.dataset.id)
    short = records[SHORT][0]
    await client.post(
        f"{API}/datasets/{made.dataset.id}/records/bulk",
        json={"record_ids": [short.id], "action": "include"},
    )

    # 실행
    response = await client.post(f"{API}/helper/{run_id}/undo")

    # 확인
    assert response.json() == {"reverted": 3, "skipped": 1}


async def test_undo_returns_409_while_helper_is_running(client, db_session, make_dataset):
    # 준비
    made = await make_dataset("도우미", ROWS)
    await _connect(db_session)
    run_id = (await client.post(f"{API}/datasets/{made.dataset.id}/helper")).json()["id"]

    # 실행
    response = await client.post(f"{API}/helper/{run_id}/undo")

    # 확인
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {
        "detail": "도우미가 일하는 중에는 되돌릴 수 없습니다. 멈춘 뒤 되돌려 주세요."
    }


# ---------- 시작 규칙 · 멈춤 · 실패 ----------


async def test_start_helper_returns_422_without_llm_and_409_when_running(
    client, db_session, make_dataset
):
    # 준비
    made = await make_dataset("도우미", ROWS)
    url = f"{API}/datasets/{made.dataset.id}/helper"

    # 실행
    without_llm = await client.post(url)
    await _connect(db_session)
    first = await client.post(url)
    second = await client.post(url)

    # 확인
    assert without_llm.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert without_llm.json() == {"detail": helper.LLM_NOT_CONNECTED_MESSAGE}
    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_409_CONFLICT
    assert second.json() == {"detail": "도우미가 이미 일하는 중입니다."}


async def test_stop_before_work_ends_run_as_stopped_and_cancels_job(
    client, db_session, make_dataset
):
    # 준비
    made = await make_dataset("도우미", ROWS)
    await _connect(db_session)
    run_id = (await client.post(f"{API}/datasets/{made.dataset.id}/helper")).json()["id"]
    stopped = await client.post(f"/api/v1/helper/runs/{run_id}/stop")

    # 실행
    await _run(db_session, run_id, transport=fake_llm(), jev_transport=fake_jev())

    # 확인
    assert stopped.json()["stop_requested"] is True
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert (run.status, run.error) == (HelperRunStatus.STOPPED, "멈춤")
    assert run.changed == 0
    job = await jobs.get_job(db_session, job_id=run.job_id)
    assert job.status == JobStatus.CANCELED


async def test_helper_fails_with_no_credit_message_when_llm_refuses(
    client, db_session, make_dataset
):
    # 준비 · 실행
    _made, run_id = await _started(client, db_session, make_dataset, fail=True)

    # 확인
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert (run.status, run.error) == (
        HelperRunStatus.FAILED,
        "LLM 답을 받지 못했습니다 (크레딧 없음).",
    )
    job = await jobs.get_job(db_session, job_id=run.job_id)
    assert (job.status, job.error) == (JobStatus.FAILED, "LLM 답을 받지 못했습니다 (크레딧 없음).")
    notice = (await _events(db_session, run_id))[-1]
    assert (notice.kind, notice.payload["tone"]) == ("notice", "bad")


async def test_helper_stops_at_token_limit(client, db_session, make_dataset, monkeypatch):
    # 준비: 첫 답(120토큰) 뒤로는 상한에 닿는다.
    monkeypatch.setattr(helper, "MAX_RUN_TOKENS", 100)

    # 실행
    _made, run_id = await _started(client, db_session, make_dataset)

    # 확인
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.STOPPED
    assert run.error == "LLM 토큰 상한 100에 닿아 멈춤"


# ---------- AI로 고치기 ----------

# 가 쪽이 적은 데이터셋 (가 4 · 나 8 → 2.0배 주의). 가의 보기 KNOWN은 새 문장 근접 거르기에 쓴다.
UNEVEN = [
    Row(KNOWN, "가"),
    *[Row(f"가 쪽 평범한 문장 {index}", "가") for index in range(3)],
    *[Row(f"나 쪽 평범한 문장 {index}", "나") for index in range(8)],
]


async def _fix(client, db_session, dataset_id: int, keys: list[str], **transports: Any):
    """AI로 고치기를 누르고 작업을 끝까지 돌린다. (응답, 실행 번호)."""
    response = await client.post(f"{API}/datasets/{dataset_id}/helper/fix", json={"keys": keys})
    if response.status_code != status.HTTP_201_CREATED:
        return response, None
    run_id = response.json()["id"]
    await _run(db_session, run_id, **transports)
    return response, run_id


async def _created(db_session, dataset_id: int) -> list[Record]:
    return list(
        await db_session.scalars(
            select(Record)
            .where(Record.dataset_id == dataset_id, Record.extra["도우미"].astext == "새 문장")
            .order_by(Record.id)
            .execution_options(populate_existing=True)
        )
    )


async def test_fix_conflict_lets_llm_pick_label_and_appends_steps_and_report(
    client, db_session, make_dataset
):
    # 준비: 애매한 충돌(Jev 0.52)이 보류로 남았다.
    made, run_id = await _started(client, db_session, make_dataset)

    # 실행
    response, fixed_run = await _fix(
        client,
        db_session,
        made.dataset.id,
        ["conflict"],
        transport=fake_llm(label="가"),
        jev_transport=fake_jev(),
    )

    # 확인: 같은 실행에 'AI로 고치기' 단계 · 보고가 붙고, LLM이 고른 라벨로 통일했다.
    assert response.status_code == status.HTTP_201_CREATED
    assert fixed_run == run_id
    records = await _records(db_session, made.dataset.id)
    labels = {label.id: name for name, label in made.labels.items()}
    assert {labels[record.label_id] for record in records[VAGUE_CONFLICT]} == {"가"}
    run = await helper_service.get_run(db_session, run_id=run_id)
    assert run.status == HelperRunStatus.DONE
    fix_rows = [row for row in run.steps if row.get("fix_round") == 1]
    assert [row["key"] for row in fix_rows] == ["fix:1:conflict", "report:1"]
    assert fix_rows[0]["delta"] == ["2건", "0건", "good"]
    reports = [
        event.payload for event in await _events(db_session, run_id) if event.kind == "report"
    ]
    assert [report["title"] for report in reports] == ["보고", "보고 · AI로 고치기"]


async def test_fix_balance_adds_sentences_and_drops_ones_near_label_or_made(
    client, db_session, make_dataset
):
    # 준비: 가 쪽이 모자란 데이터셋을 도우미가 한 번 돌았다. 임베딩은 그 뒤에 연결한다.
    made, _run_id = await _started(client, db_session, make_dataset, rows=UNEVEN)
    left = (await client.get(f"{API}/datasets/{made.dataset.id}/helper/left")).json()["items"]
    balance = next(item for item in left if item["key"] == "balance")
    await connect_embedding(db_session)
    llm_transport = fake_llm()

    # 실행
    await _fix(
        client,
        db_session,
        made.dataset.id,
        ["balance"],
        transport=llm_transport,
        jev_transport=fake_jev(),
        embedding_transport=near_embedding(),
    )

    # 확인: 계획은 가 +2(가장 많은 8의 1/1.5 = 6까지)
    assert balance["how"].startswith("새 문장 가 +2")
    assert balance["jev"] == 2
    # 가의 문장을 보기로 싣는다.
    prompts = [
        body["messages"][-1]["content"]
        for body in llm_transport.requests  # type: ignore[attr-defined]
        if not body.get("tools") and "라벨:" in body["messages"][-1]["content"]
    ]
    assert prompts and all(KNOWN in prompt for prompt in prompts)
    # 모자라 다시 물을 때는 더한 문장을 '이미 만든 문장', Jev가 버린 문장을 '피할 것'으로 싣는다.
    assert len(prompts) > 1
    assert f"- {NEW_KEPT}" in prompts[1].split("이미 만든 문장")[1]
    assert "- (나) 엉뚱하게 나로 읽힐 문장" in prompts[1]
    # 가의 문장 KNOWN · 앞서 더한 NEW_KEPT와 뜻이 같은 새 문장은 버리고, Jev가 '나'로 본 문장도 버려
    # 하나만 더했다.
    created = await _created(db_session, made.dataset.id)
    assert [record.text for record in created] == [NEW_KEPT]
    notices = [
        event.payload["text"]
        for event in await _events(db_session, _run_id)
        if event.kind == "notice"
    ]
    assert "근접 2 버림" in notices


async def test_left_balance_follows_target_setting_and_lists_editable_adds(
    client, db_session, make_dataset
):
    # 준비: 가 4 · 나 8. 목표를 1.2배로 고친다.
    made, _run_id = await _started(client, db_session, make_dataset, rows=UNEVEN)
    url = f"{API}/datasets/{made.dataset.id}"
    saved = await client.patch(f"{url}/settings", json={"balance_target": 1.2})

    # 실행
    left = (await client.get(f"{url}/helper/left")).json()["items"]

    # 확인: 가장 많은 8 ÷ 1.2 = 7까지 가 +3. 라벨마다 고칠 수 있는 칸을 싣는다.
    balance = next(item for item in left if item["key"] == "balance")
    assert saved.json() == {"balance_target": 1.2}
    assert balance["how"].startswith("새 문장 가 +3")
    assert [(row["name"], row["now"], row["add"]) for row in balance["adds"]] == [
        ("가", 4, 3),
        ("나", 8, 0),
    ]
    assert balance["adds"][0]["key"] == str(made.labels["가"].id)
    assert balance["adds_note"] == "목표 1.2배 · 라벨당 최대 100"


async def test_fix_balance_uses_counts_edited_by_person(client, db_session, make_dataset):
    # 준비: 계획은 가 +2지만 사람이 1로 고친다.
    made, _run_id = await _started(client, db_session, make_dataset, rows=UNEVEN)
    label_id = str(made.labels["가"].id)

    # 실행
    response = await client.post(
        f"{API}/datasets/{made.dataset.id}/helper/fix",
        json={"keys": ["balance"], "adds": {"balance": {label_id: 1}}},
    )
    run_id = response.json()["id"]
    await _run(db_session, run_id, transport=fake_llm(), jev_transport=fake_jev())

    # 확인: 가에 하나만 더한다.
    assert response.status_code == status.HTTP_201_CREATED
    created = await _created(db_session, made.dataset.id)
    assert len(created) == 1
    assert created[0].label_id == made.labels["가"].id


async def test_fix_balance_makes_nothing_when_bad_check_remains(client, db_session, make_dataset):
    # 준비: 모자란 라벨과 애매한 라벨 충돌(심각)이 함께 남았다.
    rows = [
        *UNEVEN,
        *[Row(f"나 쪽 더한 문장 {index}", "나") for index in range(4)],
        Row(VAGUE_CONFLICT, "가"),
        Row(VAGUE_CONFLICT, "나"),
    ]
    made, run_id = await _started(client, db_session, make_dataset, rows=rows)

    # 실행
    response, _ = await _fix(
        client,
        db_session,
        made.dataset.id,
        ["balance"],
        transport=fake_llm(),
        jev_transport=fake_jev(),
    )

    # 확인: 고를 수는 있지만 문에 걸려 만들지 않는다.
    assert response.status_code == status.HTTP_201_CREATED
    assert await _created(db_session, made.dataset.id) == []
    notices = [
        event.payload for event in await _events(db_session, run_id) if event.kind == "notice"
    ]
    assert {"text": helper.BALANCE_BLOCKED_MESSAGE, "tone": "bad"} in notices


async def test_undo_sends_generated_sentences_to_trash(client, db_session, make_dataset):
    # 준비
    made, run_id = await _started(client, db_session, make_dataset, rows=UNEVEN)
    await _fix(
        client,
        db_session,
        made.dataset.id,
        ["balance"],
        transport=fake_llm(),
        jev_transport=fake_jev(),
    )

    # 실행
    await client.post(f"{API}/helper/{run_id}/undo")

    # 확인
    created = await _created(db_session, made.dataset.id)
    assert created and all(record.trashed_at is not None for record in created)
    changes = list(
        await db_session.scalars(select(HelperChange).where(HelperChange.field == "created"))
    )
    assert changes


async def test_fix_returns_422_for_unfixable_key_and_409_while_running(
    client, db_session, make_dataset
):
    # 준비
    made, _run_id = await _started(client, db_session, make_dataset)
    url = f"{API}/datasets/{made.dataset.id}/helper/fix"

    # 실행
    unknown = await client.post(url, json={"keys": ["leak"]})
    first = await client.post(url, json={"keys": ["conflict"]})
    second = await client.post(url, json={"keys": ["conflict"]})

    # 확인: 없는 검사(분할 간 중복)는 고칠 수 없다. 도는 중에는 다시 고칠 수 없다.
    assert unknown.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert unknown.json() == {"detail": helper.NOT_FIXABLE_MESSAGE}
    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_409_CONFLICT


# ---------- 읽기 API ----------


async def test_helper_api_returns_state_and_events_after_id(client, db_session, make_dataset):
    # 준비
    made, run_id = await _started(client, db_session, make_dataset)

    # 실행
    state = (await client.get(f"{API}/datasets/{made.dataset.id}/helper")).json()
    events = (await client.get(f"/api/v1/helper/runs/{run_id}/events")).json()
    later = (
        await client.get(f"/api/v1/helper/runs/{run_id}/events", params={"after": events[-2]["id"]})
    ).json()

    # 확인
    assert state["run"]["id"] == run_id
    assert state["run"]["status"] == "done"
    assert state["undone_events"] == []
    assert events[0]["kind"] == "say"
    assert [event["id"] for event in later] == [events[-1]["id"]]


async def test_helper_state_is_empty_before_first_run(client, make_dataset):
    # 준비
    made = await make_dataset("도우미", ROWS)

    # 실행
    response = await client.get(f"{API}/datasets/{made.dataset.id}/helper")

    # 확인
    assert response.json() == {"run": None, "undone_events": []}


@pytest.mark.parametrize(
    "path", ["/api/v1/helper/runs/999999", "/api/v1/helper/runs/999999/events"]
)
async def test_helper_run_api_returns_404_for_unknown_run(client, path):
    # 실행
    response = await client.get(path)

    # 확인
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "도우미 실행을 찾을 수 없습니다."}


# ---------- 뜻 검사 단계 (오라벨 의심 · 근접 중복) ----------


async def test_helper_accepts_agreeing_suspect_and_resolves_same_label_near_duplicate(
    client, db_session, make_dataset
):
    # 준비: 뜻 분석을 끝낸 데이터셋 (오라벨 의심 1 · 라벨이 같은 근접 중복 1쌍)
    from tests.modules.classification.test_semantic_checks import (
        MISLABELED,
        NEAR_TWIN,
        _confirming_jev,
        _embedding_transport,
        _rows_and_vectors,
    )

    rows, _vectors = _rows_and_vectors()
    made = await make_dataset("뜻 분석 도우미", rows)
    await connect_embedding(db_session)
    await _connect(db_session)
    await build_map(
        db_session,
        dataset_id=made.dataset.id,
        transport=_embedding_transport(),
        jev_transport=_confirming_jev(),
    )
    run_id = (await client.post(f"{API}/datasets/{made.dataset.id}/helper")).json()["id"]

    # 실행
    await _run(db_session, run_id, transport=fake_llm(), jev_transport=fake_jev())

    # 확인
    records = await _records(db_session, made.dataset.id)
    assert records[MISLABELED][0].label_id == made.labels["나"].id
    # 라벨이 같은 쌍은 번호가 큰 쪽(나중에 들어온 NEAR_TWIN)을 규칙으로 뺀다.
    assert records["가 문장 0"][0].exclude_reason is None
    assert records[NEAR_TWIN][0].exclude_reason == ExcludeReason.HELPER
    run = await helper_service.get_run(db_session, run_id=run_id)
    steps = {step["key"]: step for step in run.steps}
    assert steps["suspect"]["delta"] == ["1건", "0건", "good"]
    assert steps["near_duplicate"]["delta"] == ["1쌍", "0쌍", "good"]
    jev_card = next(
        event.payload for event in await _events(db_session, run_id) if event.kind == "jev"
    )
    assert jev_card["meta"] == "뜻 분석 때 판정 · 다시 묻지 않음"
