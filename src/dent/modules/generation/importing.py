"""가져오기 실행. 작업 실행기가 부른다.

원본은 시스템이 줄로 읽어 준다(dent.system.sources). 이 파일은 그 줄을 모양(필드 맞춤)대로 대화로 맞춰
지시(마지막 응답 앞까지) · 응답(마지막 응답)으로 넣고, 넣을 때 규칙 검사(rules.measure)를 한 번 잰다.
다시 돌려도 결과가 같다: 시작할 때 이 가져오기가 전에 넣은 대화를 먼저 지운다.

한 줄의 규칙
- 칸 안의 대화 목록은 JSON 글이다(시스템이 목록 · 사전을 JSON 글로 담는다). 역할 이름은 맞춘다(human → user, gpt → assistant …).
- 비었거나(메시지 없음) 응답이 없거나(지시만, 다음 판) 마지막이 응답이 아니면 건너뛴다. 도구 호출 대화도 다음 판이라 건너뛴다.
  건너뛴 줄은 줄 번호와 이유를 남긴다.
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

# 도구 호출 대화의 역할 이름 (다음 판에서 받는다)
TOOL_ROLES = {"tool", "function", "function_call", "observation", "ipython"}

# 건너뛰는 이유
SKIP_EMPTY = "빈 대화"
SKIP_NOT_LIST = "대화 목록이 아님"
SKIP_UNKNOWN_ROLE = "알 수 없는 역할"
SKIP_TOOL = "도구 호출 대화(다음 판)"
SKIP_NO_ANSWER = "응답 없음(지시만은 다음 판)"
SKIP_LAST_NOT_ANSWER = "마지막이 응답이 아님"
SKIP_TOO_LONG = "너무 긴 대화"
SKIP_TOO_MANY = "메시지가 너무 많음"

# 덧붙인 입력(alpaca의 input)을 지시 뒤에 붙일 때 사이에 두는 것
INPUT_JOIN = "\n\n"


@dataclass(frozen=True)
class ParsedRow:
    """대화로 넣을 수 있는 한 줄."""

    # 맥락 메시지들 (마지막 응답 앞까지)
    context: list[dict[str, str]]

    # 마지막 응답 글
    answer: str

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
        for table in (Prompt.__tablename__, Response.__tablename__):
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
    messages = _messages_of(row.values, mapping)
    if isinstance(messages, str):
        return messages
    if not messages:
        return SKIP_EMPTY
    if len(messages) > CONVERSATION_MAX_MESSAGES:
        return SKIP_TOO_MANY
    if sum(len(message["content"]) for message in messages) > CONVERSATION_MAX_LENGTH:
        return SKIP_TOO_LONG
    last_answer = max(
        (index for index, message in enumerate(messages) if message["role"] == Role.ASSISTANT),
        default=None,
    )
    if last_answer is None:
        return SKIP_NO_ANSWER
    if last_answer != len(messages) - 1:
        return SKIP_LAST_NOT_ANSWER
    used = mapping.used_columns()
    return ParsedRow(
        context=messages[:last_answer],
        answer=messages[last_answer]["content"],
        extra={name: value for name, value in row.values.items() if name not in used},
    )


def conversation(context: list[dict[str, Any]], answer: str) -> list[dict[str, Any]]:
    """맥락과 마지막 응답을 이은 대화 전체."""
    return [*context, {"role": Role.ASSISTANT.value, "content": answer}]


def context_hash(context: list[dict[str, Any]]) -> str:
    """같은 지시를 찾는 열쇠: 맥락 메시지를 '역할: 글'로 이은 것의 해시."""
    return make_text_hash("\n".join(f"{m['role']}: {m['content']}" for m in context))


# ---------- 모양마다 대화 읽기 ----------


def _messages_of(
    values: dict[str, CellValue], mapping: FieldMappingCreate
) -> list[dict[str, str]] | str:
    """한 줄의 칸들을 [{"role", "content"}]로 맞춘다. 맞출 수 없으면 건너뛰는 이유."""
    if mapping.shape in (Shape.MESSAGES, Shape.SHAREGPT):
        return _parse_list(values.get(str(mapping.messages)))
    system = tables.to_text(values.get(mapping.system)) if mapping.system else ""
    head = [{"role": Role.SYSTEM.value, "content": system}] if system else []
    if mapping.shape == Shape.ALPACA:
        instruction = tables.to_text(values.get(mapping.instruction))
        extra_input = tables.to_text(values.get(mapping.input)) if mapping.input else ""
        user = instruction + (INPUT_JOIN + extra_input if extra_input else "")
        answer = tables.to_text(values.get(mapping.output))
        if not user and not answer:
            return []
        return [*head, _message(Role.USER, user), _message(Role.ASSISTANT, answer)]
    turns: list[dict[str, str]] = []
    for pair in mapping.turns:
        user = tables.to_text(values.get(pair.user))
        answer = tables.to_text(values.get(pair.assistant))
        if not user and not answer:
            # 턴 칸이 비면 대화가 거기서 끝났다(턴 수가 줄마다 다르다).
            break
        turns += [_message(Role.USER, user), _message(Role.ASSISTANT, answer)]
    return [*head, *turns] if turns else []


def _parse_list(value: CellValue) -> list[dict[str, str]] | str:
    """대화 목록 칸(JSON 글)을 읽는다. role/content · from/value 둘 다 받는다."""
    if value is None:
        return []
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return SKIP_NOT_LIST
    if not isinstance(items, list):
        return SKIP_NOT_LIST
    messages = []
    for item in items:
        if not isinstance(item, dict):
            return SKIP_NOT_LIST
        raw_role = str(item.get("role", item.get("from", ""))).strip().lower()
        if raw_role in TOOL_ROLES or item.get("tool_calls") or item.get("function_call"):
            return SKIP_TOOL
        role = ROLE_ALIASES.get(raw_role)
        if role is None:
            return SKIP_UNKNOWN_ROLE
        messages.append(_message(role, _content_text(item.get("content", item.get("value")))))
    return messages


def _content_text(content: Any) -> str:
    """메시지 글. 여러 조각([{"type": "text", "text": …}])이면 글 조각만 이어 붙인다."""
    if content is None:
        return ""
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "\n".join(part for part in parts if part).replace(tables.NUL, "")
    return str(content).replace(tables.NUL, "")


def _message(role: Role, content: str) -> dict[str, str]:
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
            marks = rules.measure(conversation(parsed.context, parsed.answer))
            prompt_rows.append(
                {
                    "dataset_id": import_row.dataset_id,
                    "import_id": import_row.id,
                    "messages": parsed.context,
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
        await db.execute(
            insert(Response),
            [
                {
                    "dataset_id": import_row.dataset_id,
                    "prompt_id": prompt_id,
                    "content": parsed.answer,
                }
                for prompt_id, parsed in zip(prompt_ids, parsed_rows, strict=True)
            ],
        )

    imports_service.add_batch(import_row, added=len(parsed_rows), skipped=skipped)
