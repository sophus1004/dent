"""필드 맞춤 짐작: 원본의 열 이름을 보고 모양(messages · ShareGPT · Alpaca · 턴마다 칸 · 선호 · 짝 점수)과 칸을 고른다.

시스템의 미리 보기는 열 이름과 앞쪽 줄까지만 준다. 그 열을 대화로 어떻게 맞출지는 이 모듈의 규칙이다.
턴마다 칸(input1 · output1 …)이 있으면 먼저 고른다. 번역 데이터셋은 영어 원본 대화(conversations)와
번역한 턴 칸을 함께 두는 일이 흔해서, 번역된 쪽을 고르려는 것이다(사람이 바꿀 수 있다).
"""

import re

from dent.modules.generation.models import Shape
from dent.modules.generation.schemas import FieldMappingRead, TurnColumns

# 칸마다 찾는 열 이름. 앞에 있는 이름일수록 먼저 고른다.
MESSAGES_NAMES = ("messages", "chat", "dialog", "dialogue", "대화")
SHAREGPT_NAMES = ("conversations", "conversation", "items")
INSTRUCTION_NAMES = ("instruction", "prompt", "question", "query", "지시", "질문")
INPUT_NAMES = ("input", "context", "입력")
OUTPUT_NAMES = ("output", "response", "answer", "completion", "응답", "답변")
SYSTEM_NAMES = ("system", "system_prompt", "system_message", "시스템")
TOOLS_NAMES = ("tools", "functions", "tool_definitions", "도구")
PROMPT_NAMES = ("prompt", "context", "instruction", "question", "지시")
CHOSEN_NAMES = ("chosen", "chosen_response", "preferred", "고른 응답")
REJECTED_NAMES = ("rejected", "rejected_response", "dispreferred", "버린 응답")
CHOSEN_SCORE_NAMES = ("score_chosen", "chosen_score", "chosen_rating")
REJECTED_SCORE_NAMES = ("score_rejected", "rejected_score", "rejected_rating")
RESPONSE1_NAMES = ("response1", "response_1", "response_a", "answer1")
RESPONSE2_NAMES = ("response2", "response_2", "response_b", "answer2")
PREFERENCE_NAMES = ("overall_preference", "preference", "winner", "선호")

# 턴마다 칸의 이름: input1 · output1, user_2 · assistant_2 …
TURN_USER = re.compile(r"^(input|instruction|question|user|prompt|query)[_ ]?(\d+)$", re.IGNORECASE)
TURN_ASSISTANT = re.compile(r"^(output|response|answer|assistant)[_ ]?(\d+)$", re.IGNORECASE)


def suggest_mapping(columns: list[str]) -> FieldMappingRead:
    """열 이름으로 모양과 칸을 짐작한다. 못 찾은 칸은 None. 분할 열은 쓰지 않는다(한 덩어리로 가져온다)."""
    turns = _turn_columns(columns)
    system = _find(columns, SYSTEM_NAMES)
    tools = _find(columns, TOOLS_NAMES)
    chosen, rejected = _find(columns, CHOSEN_NAMES), _find(columns, REJECTED_NAMES)
    if chosen and rejected:
        return _read(
            Shape.PREFERENCE,
            prompt=_find(columns, PROMPT_NAMES, skip={chosen, rejected}),
            chosen=chosen,
            rejected=rejected,
            chosen_score=_find(columns, CHOSEN_SCORE_NAMES),
            rejected_score=_find(columns, REJECTED_SCORE_NAMES),
        )
    response1, response2 = _find(columns, RESPONSE1_NAMES), _find(columns, RESPONSE2_NAMES)
    if response1 and response2:
        return _read(
            Shape.PAIRWISE,
            prompt=_find(columns, PROMPT_NAMES, skip={response1, response2}),
            response1=response1,
            response2=response2,
            preference=_find(columns, PREFERENCE_NAMES),
        )
    if turns:
        return _read(Shape.TURNS, turns=turns, system=system)
    if messages := _find(columns, MESSAGES_NAMES):
        return _read(Shape.MESSAGES, messages=messages, tools=tools)
    if conversations := _find(columns, SHAREGPT_NAMES):
        return _read(Shape.SHAREGPT, messages=conversations, tools=tools)
    instruction = _find(columns, INSTRUCTION_NAMES)
    output = _find(columns, OUTPUT_NAMES, skip={instruction})
    return _read(
        Shape.ALPACA,
        instruction=instruction,
        input_column=_find(columns, INPUT_NAMES, skip={instruction, output}),
        output=output,
        system=system,
    )


def _read(
    shape: Shape,
    *,
    messages: str | None = None,
    instruction: str | None = None,
    input_column: str | None = None,
    output: str | None = None,
    system: str | None = None,
    turns: list[TurnColumns] | None = None,
    **columns: str | None,
) -> FieldMappingRead:
    return FieldMappingRead(
        shape=shape,
        messages=messages,
        instruction=instruction,
        input=input_column,
        output=output,
        system=system,
        turns=turns or [],
        **columns,
    )


def _turn_columns(columns: list[str]) -> list[TurnColumns]:
    """번호가 같은 사용자 칸 · 응답 칸의 짝을 번호 순으로. 짝이 없는 번호는 뺀다."""
    users: dict[int, str] = {}
    assistants: dict[int, str] = {}
    for column in columns:
        if found := TURN_USER.match(column.strip()):
            users.setdefault(int(found.group(2)), column)
        elif found := TURN_ASSISTANT.match(column.strip()):
            assistants.setdefault(int(found.group(2)), column)
    numbers = sorted(set(users) & set(assistants))
    return [TurnColumns(user=users[number], assistant=assistants[number]) for number in numbers]


def _find(
    columns: list[str], wanted_names: tuple[str, ...], *, skip: set[str | None] | None = None
) -> str | None:
    """wanted_names 순서대로 찾아 처음 맞는 열 이름. 대소문자는 가리지 않고, skip의 열은 고르지 않는다."""
    skipped = skip or set()
    # 대소문자만 다른 열이 여럿이면 앞의 열이 남도록 뒤에서부터 채운다.
    by_lower_name = {
        column.strip().lower(): column for column in reversed(columns) if column not in skipped
    }
    for wanted in wanted_names:
        if wanted in by_lower_name:
            return by_lower_name[wanted]
    return None
