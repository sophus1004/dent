"""가져오기 실행. 작업 실행기가 부른다.

원본은 시스템이 줄로 읽어 준다(dent.system.sources). 이 파일은 그 줄을 모양(필드 맞춤)대로 대화로 맞춰
지시(마지막 응답 앞까지) · 응답(마지막 응답)으로 넣고, 넣을 때 규칙 검사(rules.measure)를 한 번 잰다.
다시 돌려도 결과가 같다: 시작할 때 이 가져오기가 전에 넣은 대화를 먼저 지운다.

한 줄의 규칙
- 칸 안의 대화 목록은 JSON 글이다(시스템이 목록 · 사전을 JSON 글로 담는다). 역할 이름은 맞춘다(human → user, gpt → assistant …).
- 마지막이 응답이면 그것이 주 응답이고, 응답이 없거나 마지막이 지시 · 도구 결과면 지시만으로 넣는다(응답 만들기로 채운다).
- 도구 대화: OpenAI 모양(assistant의 tool_calls · tool 역할의 tool_call_id)과 ShareGPT 모양(function_call · observation)을
  같은 모양(tool_calls · tool)으로 맞춘다. 도구 정의 칸이 있으면 지시 줄의 tools에 둔다.
- 선호(prompt · chosen · rejected)와 짝 점수(지시 · response1 · response2 · 점수)는 지시 하나와 응답 둘(고른 쪽이 주 응답) ·
  선호 쌍 하나로 넣는다. 응답이 대화 목록이면 마지막 응답을 쓰고, 지시 칸이 없으면 고른 응답의 앞 메시지가 지시다.
- 비었거나(메시지 없음) 읽을 수 없는 줄은 건너뛰고 줄 번호와 이유를 남긴다.
- 빈 지시 · 잘린 응답처럼 문제가 있어도 건너뛰지 않는다. 진단이 찾아 보여 주고 검토에서 고른다.
- train · valid · test를 나누지 않는다. 원본의 분할 열이나 허깅페이스의 여러 분할도 한 덩어리로 넣는다.
- 맞춤에 쓰지 않은 열은 extra에 담는다(원본의 source · id 등).
"""

import itertools
import json
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import delete, insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import rules
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import (
    CONVERSATION_MAX_LENGTH,
    CONVERSATION_MAX_MESSAGES,
    Pair,
    Prompt,
    Response,
    Role,
    Shape,
)
from dent.modules.generation.schemas import FieldMappingCreate
from dent.system import imports as imports_service
from dent.system import jobs, sources, tables
from dent.system.db import get_engine
from dent.system.exceptions import InvalidInputError
from dent.system.models import Import
from dent.system.schemas import CellValue
from dent.system.text import make_text_hash

# 한 번에 넣는 줄 수. 대화는 글이 길어 분류(1만)보다 작게 묶는다. 묶음마다 커밋하고 진행률을 적는다.
BATCH_ROWS = 2_000

# 원본의 역할 이름 → 역할 (TRL · ShareGPT · LLaMA-Factory · Gemini가 쓰는 이름들)
ROLE_ALIASES = {
    "system": Role.SYSTEM,
    "user": Role.USER,
    "human": Role.USER,
    "prompter": Role.USER,
    "assistant": Role.ASSISTANT,
    "gpt": Role.ASSISTANT,
    "bot": Role.ASSISTANT,
    "model": Role.ASSISTANT,
    "chatgpt": Role.ASSISTANT,
}

# 도구 결과의 역할 이름 (OpenAI tool · 옛 function · LLaMA-Factory observation · Llama ipython)
TOOL_RESULT_ROLES = {"tool", "function", "observation", "ipython"}

# 도구 호출을 따로 된 메시지로 두는 역할 이름 (ShareGPT · LLaMA-Factory). 응답의 tool_calls로 맞춘다.
TOOL_CALL_ROLES = {"function_call", "tool_call"}

# 건너뛰는 이유
SKIP_EMPTY = "빈 대화"
SKIP_NOT_LIST = "대화 목록이 아님"
SKIP_UNKNOWN_ROLE = "알 수 없는 역할"
SKIP_TOO_LONG = "너무 긴 대화"
SKIP_TOO_MANY = "메시지가 너무 많음"
SKIP_BAD_TOOLS = "도구 정의를 읽을 수 없음"
SKIP_NO_PAIR = "고른 응답 · 버린 응답이 없음"
SKIP_BAD_PREFERENCE = "선호 점수를 읽을 수 없음"

# 도구 호출 번호를 원본이 주지 않았을 때(ShareGPT) 붙이는 앞머리
CALL_ID_PREFIX = "call_"

# 덧붙인 입력(alpaca의 input)을 지시 뒤에 붙일 때 사이에 두는 것
INPUT_JOIN = "\n\n"


@dataclass(frozen=True)
class ParsedAnswer:
    """넣을 응답 하나."""

    # 응답 글
    content: str

    # 응답이 부른 도구들 (부르지 않았으면 None)
    tool_calls: list[dict[str, Any]] | None = None

    # 원본 점수 (선호 데이터, 없으면 None)
    score: float | None = None


@dataclass(frozen=True)
class ParsedRow:
    """대화로 넣을 수 있는 한 줄."""

    # 맥락 메시지들 (마지막 응답 앞까지)
    context: list[dict[str, Any]]

    # 응답들. 앞의 것이 주 응답이다. 비어 있으면 지시만.
    answers: list[ParsedAnswer]

    # 선호 쌍인지 (answers[0]이 고른 응답, answers[1]이 버린 응답)
    is_pair: bool

    # 도구 정의들 (도구 대화가 아니면 None)
    tools: list[dict[str, Any]] | None

    # 남는 열들
    extra: dict[str, CellValue]


async def run_import(
    db: AsyncSession,
    *,
    import_id: int,
    job_id: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Import:
    """가져오기 하나를 처음부터 끝까지 한다. 원본을 읽을 수 없으면 도메인 예외를 낸다."""
    import_row = await imports_service.get_import(db, import_id=import_id)
    await delete_inserted(db, dataset_id=import_row.dataset_id, import_id=import_id)
    imports_service.start_import(import_row)
    await db.commit()

    mapping = FieldMappingCreate.model_validate(import_row.options["mapping"])
    async with sources.open_source(db, import_row, transport=transport) as source:
        _check_mapping(mapping, source.columns)
        import_row.rows_total = source.total_rows
        done = 0
        for batch in itertools.batched(source.rows, BATCH_ROWS):
            await _insert_batch(db, import_row=import_row, batch=batch, mapping=mapping)
            done += len(batch)
            await jobs.set_progress(db, job_id=job_id, done=done, total=source.total_rows)
            await db.commit()

    await imports_service.finish_import(db, import_row, job_id=job_id)
    await db.commit()
    return import_row


async def fail_import(db: AsyncSession, *, import_id: int, job_id: int, message: str) -> None:
    """가져오기와 작업을 실패로 끝낸다. 반쯤 들어간 대화는 지워 데이터셋을 가져오기 전으로 둔다."""
    await db.rollback()
    import_row = await db.get(Import, import_id)
    if import_row is not None:
        await delete_inserted(db, dataset_id=import_row.dataset_id, import_id=import_id)
    await imports_service.fail_import(db, import_id=import_id, job_id=job_id, message=message)


async def vacuum_tables() -> None:
    """지시 · 응답 표를 VACUUM ANALYZE 한다. 가져오기를 마친 뒤 작업 실행기가 부른다.

    막 넣은 줄은 DB가 청소하기 전까지 인덱스만 훑어 셀 수 없어 진단이 몇 배 느리다. 자동 청소를 기다리지 않고 바로 한다.
    """
    engine = get_engine()
    # VACUUM은 트랜잭션 안에서 돌 수 없어서, 문장마다 바로 확정하는 연결을 쓴다.
    async with engine.connect() as conn:
        autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for table in (Prompt.__tablename__, Response.__tablename__, Pair.__tablename__):
            await autocommit.execute(text(f"VACUUM (ANALYZE) {table}"))


async def delete_inserted(db: AsyncSession, *, dataset_id: int, import_id: int) -> None:
    """이 가져오기가 넣은 대화를 지운다(응답 · 결정 기록은 CASCADE). 커밋은 부른 쪽이 한다."""
    await db.execute(
        delete(Prompt)
        .where(Prompt.dataset_id == dataset_id, Prompt.import_id == import_id)
        .execution_options(synchronize_session=False)
    )


def parse_row(row: sources.SourceRow, *, mapping: FieldMappingCreate) -> ParsedRow | str:
    """원본 한 줄을 대화로 바꾼다. 넣을 수 없는 줄이면 건너뛰는 이유(문자열)를 돌려준다."""
    used = mapping.used_columns()
    extra = {name: value for name, value in row.values.items() if name not in used}
    if mapping.shape in (Shape.PREFERENCE, Shape.PAIRWISE):
        return _parse_pair(row.values, mapping, extra=extra)
    messages = _messages_of(row.values, mapping)
    if isinstance(messages, str):
        return messages
    if not messages:
        return SKIP_EMPTY
    if too_big := _too_big(messages):
        return too_big
    tools = _tools_of(row.values, mapping)
    if isinstance(tools, str):
        return tools
    last = messages[-1]
    if last["role"] == Role.ASSISTANT:
        answer = ParsedAnswer(content=last["content"], tool_calls=last.get("tool_calls") or None)
        return ParsedRow(
            context=messages[:-1], answers=[answer], is_pair=False, tools=tools, extra=extra
        )
    # 마지막이 지시 · 도구 결과면 응답이 없는 지시다(응답 만들기로 채운다).
    return ParsedRow(context=messages, answers=[], is_pair=False, tools=tools, extra=extra)


def conversation(
    context: list[dict[str, Any]],
    answer: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """맥락과 마지막 응답을 이은 대화 전체. 응답이 없으면(None) 맥락 그대로."""
    if answer is None and not tool_calls:
        return list(context)
    final: dict[str, Any] = {"role": Role.ASSISTANT.value, "content": answer or ""}
    if tool_calls:
        final["tool_calls"] = tool_calls
    return [*context, final]


def context_hash(context: list[dict[str, Any]]) -> str:
    """같은 지시를 찾는 열쇠: 맥락 메시지를 '역할: 글'로 이은 것의 해시 (도구 호출이 있으면 그 JSON도)."""
    return make_text_hash(
        "\n".join(
            f"{m['role']}: {m['content']}"
            + (f" {json.dumps(m['tool_calls'], ensure_ascii=False)}" if m.get("tool_calls") else "")
            for m in context
        )
    )


# ---------- 모양마다 대화 읽기 ----------


def _too_big(messages: list[dict[str, Any]]) -> str | None:
    if len(messages) > CONVERSATION_MAX_MESSAGES:
        return SKIP_TOO_MANY
    if sum(len(message["content"]) for message in messages) > CONVERSATION_MAX_LENGTH:
        return SKIP_TOO_LONG
    return None


def _messages_of(
    values: dict[str, CellValue], mapping: FieldMappingCreate
) -> list[dict[str, Any]] | str:
    """한 줄의 칸들을 [{"role", "content"}]로 맞춘다. 맞출 수 없으면 건너뛰는 이유."""
    if mapping.shape in (Shape.MESSAGES, Shape.SHAREGPT):
        return _parse_list(values.get(str(mapping.messages)))
    system = tables.to_text(values.get(mapping.system)) if mapping.system else ""
    head = [{"role": Role.SYSTEM.value, "content": system}] if system else []
    if mapping.shape == Shape.ALPACA:
        instruction = tables.to_text(values.get(mapping.instruction))
        extra_input = tables.to_text(values.get(mapping.input)) if mapping.input else ""
        user = instruction + (INPUT_JOIN + extra_input if extra_input else "")
        answer = tables.to_text(values.get(mapping.output)) if mapping.output else ""
        if not user and not answer:
            return []
        # 응답 칸이 없거나 비면 지시만이다.
        tail = [_message(Role.ASSISTANT, answer)] if answer else []
        return [*head, _message(Role.USER, user), *tail]
    turns: list[dict[str, Any]] = []
    for pair in mapping.turns:
        user = tables.to_text(values.get(pair.user))
        answer = tables.to_text(values.get(pair.assistant))
        if not user and not answer:
            # 턴 칸이 비면 대화가 거기서 끝났다(턴 수가 줄마다 다르다).
            break
        turns.append(_message(Role.USER, user))
        if answer:
            turns.append(_message(Role.ASSISTANT, answer))
        else:
            # 응답이 빈 턴이 마지막이면 지시만이다(그 뒤 턴은 읽지 않는다).
            break
    return [*head, *turns] if turns else []


def _parse_list(value: CellValue) -> list[dict[str, Any]] | str:
    """대화 목록 칸(JSON 글)을 읽는다. role/content · from/value 둘 다, 도구 호출 · 결과도 받는다."""
    items = _json_value(value)
    if items is None:
        return []
    if not isinstance(items, list):
        return SKIP_NOT_LIST
    messages: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return SKIP_NOT_LIST
        message = _parse_message(item, messages)
        if isinstance(message, str):
            return message
        messages.append(message)
    return messages


def _parse_message(item: dict[str, Any], before: list[dict[str, Any]]) -> dict[str, Any] | str:
    """메시지 하나. 도구 호출은 응답의 tool_calls로, 도구 결과는 tool 역할로 맞춘다."""
    raw_role = str(item.get("role", item.get("from", ""))).strip().lower()
    content = _content_text(item.get("content", item.get("value")))
    if raw_role in TOOL_CALL_ROLES:
        # ShareGPT의 function_call: 값이 {"name", "arguments"} JSON 글이다.
        call = _json_value(item.get("value", item.get("content")))
        calls = call if isinstance(call, list) else [call]
        return {
            "role": Role.ASSISTANT.value,
            "content": "",
            "tool_calls": [_tool_call(entry, before) for entry in calls if isinstance(entry, dict)],
        }
    if raw_role in TOOL_RESULT_ROLES:
        message: dict[str, Any] = {"role": Role.TOOL.value, "content": content}
        call_id = item.get("tool_call_id") or _last_call_id(before)
        if call_id is not None:
            message["tool_call_id"] = str(call_id)
        if item.get("name"):
            message["name"] = str(item["name"])
        return message
    role = ROLE_ALIASES.get(raw_role)
    if role is None:
        return SKIP_UNKNOWN_ROLE
    message = _message(role, content)
    raw_calls = item.get("tool_calls") or (
        [{"function": item["function_call"]}] if item.get("function_call") else None
    )
    if role == Role.ASSISTANT and raw_calls:
        message["tool_calls"] = [_tool_call(entry, before) for entry in raw_calls]
    return message


def _tool_call(entry: dict[str, Any], before: list[dict[str, Any]]) -> dict[str, Any]:
    """도구 호출 하나를 {"id", "type": "function", "function": {"name", "arguments"}}로. 번호가 없으면 붙인다."""
    function = entry.get("function") if isinstance(entry.get("function"), dict) else entry
    count = sum(len(m.get("tool_calls") or []) for m in before)
    return {
        "id": str(entry.get("id") or f"{CALL_ID_PREFIX}{count + 1}"),
        "type": "function",
        "function": {
            "name": str(function.get("name") or ""),
            "arguments": function.get("arguments", function.get("parameters", {})),
        },
    }


def _last_call_id(before: list[dict[str, Any]]) -> str | None:
    """앞 메시지들 가운데 마지막 도구 호출의 번호 (결과가 번호를 주지 않을 때 잇는다)."""
    for message in reversed(before):
        calls = message.get("tool_calls") or []
        if calls:
            answered = {m.get("tool_call_id") for m in before if m["role"] == Role.TOOL}
            for call in calls:
                if call["id"] not in answered:
                    return str(call["id"])
            return str(calls[-1]["id"])
    return None


def _tools_of(
    values: dict[str, CellValue], mapping: FieldMappingCreate
) -> list[dict[str, Any]] | None | str:
    """도구 정의 칸. 칸이 없거나 비면 None, 읽을 수 없으면 건너뛰는 이유."""
    if not mapping.tools:
        return None
    raw = values.get(mapping.tools)
    if raw is None or raw == "":
        return None
    tools = _json_value(raw)
    if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
        return SKIP_BAD_TOOLS
    return tools or None


def _parse_pair(
    values: dict[str, CellValue], mapping: FieldMappingCreate, *, extra: dict[str, CellValue]
) -> ParsedRow | str:
    """선호 · 짝 점수 한 줄: 지시 · 고른 응답(주 응답) · 버린 응답."""
    if mapping.shape == Shape.PREFERENCE:
        chosen_messages = _answer_messages(values.get(str(mapping.chosen)))
        rejected_messages = _answer_messages(values.get(str(mapping.rejected)))
        if isinstance(chosen_messages, str):
            return chosen_messages
        if isinstance(rejected_messages, str):
            return rejected_messages
        context = (
            _prompt_messages(values.get(mapping.prompt)) if mapping.prompt else chosen_messages[:-1]
        )
        chosen = _last_answer(chosen_messages)
        rejected = _last_answer(rejected_messages)
        scores = (
            _number(values.get(mapping.chosen_score)) if mapping.chosen_score else None,
            _number(values.get(mapping.rejected_score)) if mapping.rejected_score else None,
        )
    else:
        context = _prompt_messages(values.get(mapping.prompt))
        first = _last_answer(_answer_messages(values.get(str(mapping.response1))))
        second = _last_answer(_answer_messages(values.get(str(mapping.response2))))
        preference = _number(values.get(mapping.preference))
        if preference is None:
            return SKIP_BAD_PREFERENCE
        # 음수면 response1이, 양수면 response2가 낫다. 0이면 동점(같은 점수로 표시한다).
        chosen, rejected = (second, first) if preference > 0 else (first, second)
        scores = (0.0, 0.0) if preference == 0 else (None, None)
    if isinstance(context, str):
        return context
    if chosen is None or rejected is None:
        return SKIP_NO_PAIR
    if not context:
        return SKIP_EMPTY
    if too_big := _too_big([*context, {"content": chosen}, {"content": rejected}]):
        return too_big
    return ParsedRow(
        context=context,
        answers=[
            ParsedAnswer(content=chosen, score=scores[0]),
            ParsedAnswer(content=rejected, score=scores[1]),
        ],
        is_pair=True,
        tools=None,
        extra=extra,
    )


def _prompt_messages(value: CellValue) -> list[dict[str, Any]] | str:
    """지시 칸: 대화 목록이면 그대로, 글이면 사용자 메시지 하나."""
    parsed = _json_value(value)
    if isinstance(parsed, list):
        return _parse_list(parsed)
    text_value = tables.to_text(value)
    return [_message(Role.USER, text_value)] if text_value else []


def _answer_messages(value: CellValue) -> list[dict[str, Any]] | str:
    """응답 칸: 대화 목록이면 그대로(마지막이 응답), 글이면 응답 메시지 하나."""
    parsed = _json_value(value)
    if isinstance(parsed, list):
        return _parse_list(parsed)
    text_value = tables.to_text(value)
    return [_message(Role.ASSISTANT, text_value)] if text_value else []


def _last_answer(messages: list[dict[str, Any]] | str) -> str | None:
    if isinstance(messages, str) or not messages or messages[-1]["role"] != Role.ASSISTANT:
        return None
    return str(messages[-1]["content"])


def _number(value: CellValue) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _json_value(value: Any) -> Any:
    """칸 값이 JSON 글이면 읽은 값, 이미 목록 · 사전이면 그대로. 비었거나 읽을 수 없으면 None(글이면 그 글)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _content_text(content: Any) -> str:
    """메시지 글. 여러 조각([{"type": "text", "text": …}])이면 글 조각만 이어 붙인다."""
    if content is None:
        return ""
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "\n".join(part for part in parts if part).replace(tables.NUL, "")
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content).replace(tables.NUL, "")


def _message(role: Role, content: str) -> dict[str, Any]:
    return {"role": role.value, "content": content}


# ---------- 안에서만 쓰는 함수 ----------


def _check_mapping(mapping: FieldMappingCreate, columns: list[str]) -> None:
    """필드 맞춤의 열이 원본에 있는지 본다. 없으면 InvalidInputError."""
    for name in sorted(mapping.used_columns()):
        if name not in columns:
            raise InvalidInputError(
                f"'{name}' 열이 원본에 없습니다. 필드 맞추기를 다시 확인하세요."
            )


async def _insert_batch(
    db: AsyncSession,
    *,
    import_row: Import,
    batch: tuple[sources.SourceRow, ...],
    mapping: FieldMappingCreate,
) -> None:
    """한 묶음을 대화로 넣고 가져오기 기록의 수를 늘린다. 커밋은 부른 쪽이 한다."""
    parsed_rows: list[ParsedRow] = []
    skipped: list[dict[str, Any]] = []
    for row in batch:
        parsed = parse_row(row, mapping=mapping)
        if isinstance(parsed, str):
            skipped.append({"line": row.line, "reason": parsed})
        else:
            parsed_rows.append(parsed)

    if parsed_rows:
        prompt_rows = []
        for parsed in parsed_rows:
            main = parsed.answers[0] if parsed.answers else None
            marks = rules.measure(
                conversation(
                    parsed.context,
                    main.content if main else None,
                    main.tool_calls if main else None,
                ),
                tools=parsed.tools,
            )
            prompt_rows.append(
                {
                    "dataset_id": import_row.dataset_id,
                    "import_id": import_row.id,
                    "messages": parsed.context,
                    "tools": parsed.tools,
                    "text_hash": context_hash(parsed.context),
                    "marks": marks,
                    "open_checks": generation_service.open_checks(marks, []),
                    "extra": parsed.extra,
                }
            )
        # 응답이 지시 번호를 가리키므로 넣은 순서대로 번호를 받는다.
        prompt_ids = (
            await db.scalars(
                insert(Prompt).returning(Prompt.id, sort_by_parameter_order=True), prompt_rows
            )
        ).all()
        answer_rows = [
            {
                "dataset_id": import_row.dataset_id,
                "prompt_id": prompt_id,
                "content": answer.content,
                "tool_calls": answer.tool_calls,
                "score": answer.score,
                "is_main": index == 0,
            }
            for prompt_id, parsed in zip(prompt_ids, parsed_rows, strict=True)
            for index, answer in enumerate(parsed.answers)
        ]
        answer_ids = (
            (
                await db.scalars(
                    insert(Response).returning(Response.id, sort_by_parameter_order=True),
                    answer_rows,
                )
            ).all()
            if answer_rows
            else []
        )
        pair_rows = []
        position = 0
        for prompt_id, parsed in zip(prompt_ids, parsed_rows, strict=True):
            if parsed.is_pair:
                chosen, rejected = parsed.answers[0], parsed.answers[1]
                pair_rows.append(
                    {
                        "dataset_id": import_row.dataset_id,
                        "prompt_id": prompt_id,
                        "chosen_id": answer_ids[position],
                        "rejected_id": answer_ids[position + 1],
                        "marks": rules.measure_pair(
                            chosen.content,
                            rejected.content,
                            chosen_score=chosen.score,
                            rejected_score=rejected.score,
                        ),
                    }
                )
            position += len(parsed.answers)
        if pair_rows:
            await db.execute(insert(Pair), pair_rows)

    imports_service.add_batch(import_row, added=len(parsed_rows), skipped=skipped)
