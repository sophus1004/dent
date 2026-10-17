"""응답 만들기: 응답이 없는 지시(지시만 데이터)에 LLM이 응답을 쓴다. 비교 만들기의 견줄 응답도 여기서 쓴다.
학습할 모델이 연결돼 있으면 비교 만들기의 견줄 응답은 학습할 모델이 대화에 그대로 답한 것이다(policy_messages:
지침을 붙이지 않아야 그 모델이 평소에 내는 응답이 된다, on-policy).

만든 응답은 출처가 synthetic이다(내보내는 파일의 출처 칸으로 가를 수 있게). 학습에 바로 쓸 수 없는 모양
(빈 · 잘린 응답 · 루프 · 언어 섞임 · 번역된 코드)이면 한 번 더 쓰게 하고, 그래도 걸리면 넣지 않는다(모자람으로 센다).
도구 대화는 만들지 않는다(호출을 지어내지 않게).
"""

from typing import Any

import httpx

from dent.modules.generation import importing, rules
from dent.modules.generation.rewriting import render
from dent.modules.generation.rules import CheckKey
from dent.system import llm
from dent.system.exceptions import ExternalServiceError
from dent.system.llm import ChatMessage
from dent.system.models import Connection

# 만든 응답의 최대 토큰 (긴 답도 끝까지 쓰게 넉넉히)
ANSWER_MAX_TOKENS = 4_000

# 한 지시에 쓰는 최대 횟수 (처음 + 다시 한 번). 두 번 다 걸리면 넣지 않는다.
MAX_ATTEMPTS = 2

# 만든 응답을 다시 쓰게 하는 규칙 검사 (학습에 바로 쓸 수 없는 모양)
RETRY_CHECKS = (
    CheckKey.EMPTY_ANSWER,
    CheckKey.TRUNCATED,
    CheckKey.LOOP,
    CheckKey.LANG_MIX,
    CheckKey.CODE_TRANSLATED,
)

SYSTEM_PROMPT = (
    "너는 LLM 학습 데이터의 응답을 쓰는 사람이다. 대화의 마지막 사용자 지시에 대한 응답 글만 출력한다. "
    "머리말 · 설명 · 따옴표를 붙이지 않고, 응답 전체를 코드 울타리로 감싸지 않는다. "
    "지시가 바라는 언어 · 형식 · 길이를 따른다(따로 말이 없으면 사용자의 말과 같은 언어로). "
    "해롭거나 할 수 없는 요청이면 짧게 거절하고 까닭을 말한다."
)

# 비교 만들기: 이미 있는 응답과 다른 응답을 쓰게 하는 말. 일부러 나쁘게 쓰게 하지 않는다(Jev가 고른다).
OTHER_TASK = (
    "위 응답과 다른 구성 · 표현으로, 지시에 맞는 응답을 새로 써라. 일부러 나쁘게 쓰지 않는다."
)

EMPTY_REPLY_MESSAGE = "LLM이 빈 글을 돌려주었습니다. 다시 해 주세요."

# 학습할 모델에 맥락 그대로 보내는 말의 역할 (도구 대화는 비교 만들기에서 빠진다)
POLICY_ROLES = ("system", "user", "assistant")


def answer_messages(
    messages: list[dict[str, Any]], *, other: str | None = None
) -> list[ChatMessage]:
    """응답 만들기를 LLM에 묻는 말들. other를 주면(비교 만들기) 그 응답과 다른 응답을 쓰게 한다."""
    prompt = (
        f"아래는 학습 데이터의 대화다. 마지막 사용자 지시에 대한 응답을 써라.\n\n{render(messages)}"
    )
    if other is not None:
        prompt += f"\n\n[이미 있는 응답]\n{other}\n\n[할 일]\n{OTHER_TASK}"
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    ]


def policy_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
    """학습할 모델에 보내는 말들: 대화의 맥락 그대로(system · user · assistant). 지침을 더하지 않는다."""
    return [
        ChatMessage(role=message["role"], content=message["content"])
        for message in messages
        if message["role"] in POLICY_ROLES
    ]


def answer_problems(messages: list[dict[str, Any]], content: str) -> list[CheckKey]:
    """만든 응답이 걸린 규칙 검사들 (RETRY_CHECKS 가운데, 그 응답 자리의 것만)."""
    marks = rules.measure(importing.conversation(messages, content))
    turn = len(messages)
    return [
        check
        for check in RETRY_CHECKS
        if any(mark.get("turn") in (None, turn) for mark in marks.get(check.value, []))
    ]


def read_answer(reply: str) -> str:
    """LLM 답에서 응답 글. 비었으면 ExternalServiceError."""
    text = reply.strip()
    if not text:
        raise ExternalServiceError(EMPTY_REPLY_MESSAGE)
    return text


async def write_answer(
    connection: Connection,
    *,
    messages: list[dict[str, Any]],
    other: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """검토의 응답 만들기 한 건: LLM이 쓴 응답 글. 모양이 걸리면 한 번 더 쓰고, 그래도 걸리면 마지막 것을 돌려준다
    (검토에서는 사람이 보고 고친다). LLM이 답하지 않거나 빈 글이면 ExternalServiceError."""
    text = ""
    for _attempt in range(MAX_ATTEMPTS):
        reply = await llm.complete(
            connection,
            messages=answer_messages(messages, other=other),
            max_tokens=ANSWER_MAX_TOKENS,
            transport=transport,
        )
        text = read_answer(reply)
        if not answer_problems(messages, text):
            break
    return text
