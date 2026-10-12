"""가져오기 테스트: 모양마다(messages · ShareGPT · Alpaca · 턴마다 칸) 대화로 맞추기, 건너뛰는 이유, 규칙 검사, 다시 돌리기.

원본 읽기(jsonl · csv · 허깅페이스 미리 보기)는 시스템 테스트(tests/system)가 본다.
"""

import json

import pytest
from sqlalchemy import func, select

from dent.modules.generation import importing
from dent.modules.generation import service as generation_service
from dent.modules.generation.jobs import run_import_job
from dent.modules.generation.models import Pair, Prompt, Response
from dent.modules.generation.rules import CheckKey
from dent.modules.generation.schemas import ImportCreate
from dent.system import imports as imports_service
from dent.system import jobs
from dent.system.models import ImportStatus
from tests.modules.generation.helpers import (
    CLEAN,
    conversation,
    import_file,
    jsonl_bytes,
    messages_rows,
)
from tests.system.helpers import upload


async def _conversations(db_session) -> list[tuple[Prompt, Response]]:
    rows = await db_session.execute(
        select(Prompt, Response).join(Response, Response.prompt_id == Prompt.id).order_by(Prompt.id)
    )
    return list(rows.tuples().all())


async def test_import_messages_splits_context_and_last_answer(db_session):
    # 준비
    content = jsonl_bytes(messages_rows([CLEAN]))

    # 실행
    finished = await import_file(db_session, content)

    # 확인: 마지막 응답 앞까지가 지시(맥락), 남는 칸은 extra
    assert finished.status == ImportStatus.DONE
    [(prompt, answer)] = await _conversations(db_session)
    assert prompt.messages == CLEAN[:-1]
    assert answer.content == "약 330만 명입니다."
    assert prompt.extra == {"source": "시험"}
    assert prompt.marks == {}
    assert prompt.open_checks == []


async def test_import_skips_rows_it_cannot_take_with_reasons(db_session):
    # 준비
    rows = [
        {"messages": [{"from": "human", "value": "안녕"}, {"from": "gpt", "value": "안녕하세요."}]},
        {"messages": [{"role": "narrator", "content": "…"}]},
        {"messages": "대화가 아님"},
        {"messages": []},
    ]

    # 실행
    finished = await import_file(db_session, jsonl_bytes(rows))

    # 확인: 역할 이름(human · gpt)은 맞추고, 넣을 수 없는 줄은 이유와 함께 건너뛴다
    assert finished.rows_added == 1
    assert finished.skipped_lines == [
        {"line": 2, "reason": "알 수 없는 역할"},
        {"line": 3, "reason": "대화 목록이 아님"},
        {"line": 4, "reason": "빈 대화"},
    ]
    [(prompt, _answer)] = await _conversations(db_session)
    assert prompt.messages == [{"role": "user", "content": "안녕"}]


async def test_import_takes_prompts_without_answer(db_session):
    # 준비: 지시만 · 앞 턴 뒤 지시로 끝남 · 응답 칸이 빈 Alpaca
    rows = [
        {"messages": [{"role": "user", "content": "지시만 있어요"}]},
        {"messages": [*conversation("질문", "답입니다."), {"role": "user", "content": "또 질문"}]},
    ]

    # 실행
    finished = await import_file(db_session, jsonl_bytes(rows))
    prompts = (await db_session.scalars(select(Prompt).order_by(Prompt.id))).all()
    answers = await db_session.scalar(select(func.count()).select_from(Response))

    # 확인: 건너뛰지 않고 응답 없는 지시로 넣는다(구조 문제도 아니다)
    assert finished.rows_added == 2
    assert answers == 0
    assert prompts[1].messages[-1] == {"role": "user", "content": "또 질문"}
    assert all(CheckKey.STRUCTURE.value not in prompt.marks for prompt in prompts)


async def test_import_reads_tool_calls_in_openai_and_sharegpt_shapes(db_session):
    # 준비: OpenAI 모양(tool_calls · tool_call_id)과 LLaMA-Factory 모양(function_call · observation)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    openai = [
        {"role": "user", "content": "서울 날씨?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city": "서울"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"temp": 21}'},
        {"role": "assistant", "content": "서울은 21도입니다."},
    ]
    sharegpt = [
        {"from": "human", "value": "부산 날씨?"},
        {"from": "function_call", "value": '{"name": "weather", "arguments": {"town": "부산"}}'},
        {"from": "observation", "value": '{"temp": 24}'},
        {"from": "gpt", "value": "부산은 24도입니다."},
    ]
    rows = [{"messages": openai, "tools": tools}, {"messages": sharegpt, "tools": tools}]

    # 실행
    finished = await import_file(
        db_session,
        jsonl_bytes(rows),
        mapping={"shape": "messages", "messages": "messages", "tools": "tools"},
    )
    loaded = await _conversations(db_session)

    # 확인: 둘 다 같은 모양(tool_calls · tool)으로 맞추고, 인자 스키마가 틀린 둘째만 도구 호출 형식 문제다
    assert finished.rows_added == 2
    (first, _), (second, _) = loaded
    assert first.tools == tools
    assert second.messages[1]["tool_calls"][0]["function"]["name"] == "weather"
    assert second.messages[2] == {
        "role": "tool",
        "content": '{"temp": 24}',
        "tool_call_id": "call_1",
    }
    assert CheckKey.TOOL_CALL.value not in first.marks
    assert CheckKey.TOOL_CALL.value in second.marks


async def test_import_preference_and_pairwise_make_pairs(db_session):
    # 준비: TRL 선호(지시 칸 없이 chosen의 앞 메시지가 지시) · HelpSteer3 모양(점수 양수면 2가 낫다)
    preference = [
        {
            "chosen": [
                {"role": "user", "content": "요약해 줘"},
                {"role": "assistant", "content": "핵심만 세 줄입니다."},
            ],
            "rejected": [
                {"role": "user", "content": "요약해 줘"},
                {"role": "assistant", "content": "모르겠어요."},
            ],
        }
    ]
    pairwise = [
        {
            "context": "인사해 줘",
            "response1": "음.",
            "response2": "안녕하세요!",
            "overall_preference": 2,
        }
    ]

    # 실행
    await import_file(
        db_session,
        jsonl_bytes(preference),
        mapping={"shape": "preference", "chosen": "chosen", "rejected": "rejected"},
    )
    await import_file(
        db_session,
        jsonl_bytes(pairwise),
        name="짝 점수",
        mapping={
            "shape": "pairwise",
            "prompt": "context",
            "response1": "response1",
            "response2": "response2",
            "preference": "overall_preference",
        },
    )
    pairs = (await db_session.scalars(select(Pair).order_by(Pair.id))).all()
    responses = {r.id: r for r in (await db_session.scalars(select(Response))).all()}

    # 확인: 쌍마다 응답 둘(고른 쪽이 주 응답)
    assert [responses[p.chosen_id].content for p in pairs] == ["핵심만 세 줄입니다.", "안녕하세요!"]
    assert [responses[p.rejected_id].content for p in pairs] == ["모르겠어요.", "음."]
    assert all(
        responses[p.chosen_id].is_main and not responses[p.rejected_id].is_main for p in pairs
    )


async def test_import_alpaca_joins_instruction_and_input_with_system(db_session):
    # 준비
    rows = [
        {
            "system": "번역가입니다.",
            "instruction": "영어로 옮기세요.",
            "input": "고양이",
            "output": "cat",
        },
        {"system": "", "instruction": "인사해 주세요.", "input": "", "output": "안녕하세요."},
    ]
    mapping = {
        "shape": "alpaca",
        "instruction": "instruction",
        "input": "input",
        "output": "output",
        "system": "system",
    }

    # 실행
    await import_file(db_session, jsonl_bytes(rows), mapping=mapping)

    # 확인
    [(first, first_answer), (second, _)] = await _conversations(db_session)
    assert first.messages == [
        {"role": "system", "content": "번역가입니다."},
        {"role": "user", "content": "영어로 옮기세요.\n\n고양이"},
    ]
    assert first_answer.content == "cat"
    assert second.messages == [{"role": "user", "content": "인사해 주세요."}]


async def test_import_turns_stops_at_empty_turn(db_session):
    # 준비: 둘째 줄은 한 턴만 있다
    rows = [
        {
            "input1": "안녕",
            "output1": "안녕하세요.",
            "input2": "잘 가",
            "output2": "안녕히 가세요.",
        },
        {"input1": "고마워", "output1": "천만에요.", "input2": None, "output2": None},
    ]
    mapping = {
        "shape": "turns",
        "turns": [
            {"user": "input1", "assistant": "output1"},
            {"user": "input2", "assistant": "output2"},
        ],
    }

    # 실행
    await import_file(db_session, jsonl_bytes(rows), mapping=mapping)

    # 확인
    [(first, first_answer), (second, second_answer)] = await _conversations(db_session)
    assert [m["content"] for m in first.messages] == ["안녕", "안녕하세요.", "잘 가"]
    assert first_answer.content == "안녕히 가세요."
    assert second.messages == [{"role": "user", "content": "고마워"}]
    assert second_answer.content == "천만에요."


async def test_import_records_rule_marks_and_open_checks(db_session):
    # 준비
    rows = messages_rows(
        [
            conversation("", "코드는 다음과 같습니다."),
            conversation("다음 문장에 동사가 있으면 1을 출력하세요.", "0."),
        ]
    )

    # 실행
    await import_file(db_session, jsonl_bytes(rows))

    # 확인: 규칙 검사를 넣을 때 한 번 재 둔다
    [(empty, _), (short, _)] = await _conversations(db_session)
    assert empty.open_checks == [CheckKey.EMPTY_PROMPT]
    assert short.marks[CheckKey.SHORT] == [{"turn": 1, "start": 0, "end": 2}]


class WorkerStopped(Exception):
    """작업 실행기가 도중에 멈춘 것을 흉내 내는 예외."""


async def test_import_rerun_after_interruption_gives_same_rows(db_session, monkeypatch):
    # 준비: 대화를 다 넣고 끝내기 직전에 작업 실행기가 멈춘 경우를 꾸민다
    preview = await upload(db_session, "sft.jsonl", jsonl_bytes(messages_rows([CLEAN, CLEAN])))
    data = ImportCreate.model_validate(
        {
            "new_dataset_name": "SFT 시험",
            "source": "file",
            "upload_id": preview.upload_id,
            "mapping": {"shape": "messages", "messages": "messages"},
        }
    )
    created = await generation_service.create_import(db_session, data=data)

    async def stop_before_finish(*_args, **_kwargs):
        raise WorkerStopped

    monkeypatch.setattr(imports_service, "finish_import", stop_before_finish)
    with pytest.raises(WorkerStopped):
        await importing.run_import(db_session, import_id=created.id, job_id=created.job_id)
    monkeypatch.undo()
    job = await jobs.get_job(db_session, job_id=created.job_id)

    # 실행: 작업 실행기가 다시 실행돼 같은 작업을 처음부터 다시 하는 경우
    await run_import_job(job)

    # 확인: 두 번 들어가지 않는다
    count = await db_session.scalar(select(func.count()).select_from(Prompt))
    rerun = await imports_service.get_import(db_session, import_id=created.id)
    assert rerun.status == ImportStatus.DONE
    assert count == 2


async def test_import_fails_with_message_when_mapped_column_is_missing(db_session):
    # 준비
    content = jsonl_bytes([{"conversation": json.dumps(CLEAN, ensure_ascii=False)}])

    # 실행
    finished = await import_file(db_session, content)

    # 확인: 필드 맞춤의 열이 없으면 사용자 문장으로 실패
    assert finished.status == ImportStatus.FAILED
    assert finished.error == "'messages' 열이 원본에 없습니다. 필드 맞추기를 다시 확인하세요."
