"""LLM 다시 쓰기: 검토에서 한 대화의 마지막 응답을 LLM이 검사마다 정한 방향으로 다시 쓴다.

빈 · 잘린 응답은 끝까지 완성하고, 번역된 코드는 코드 안만 원래 언어로 되돌리고, 거절 · 상투구는 빼고 답하고,
언어 섞임은 한국어로, 지시 불일치 · 짧은 응답은 지시에 맞게 다시 쓴다. 다시 쓴 응답은 출처가 synthetic이다
(학습 파일의 출처 칸으로 가를 수 있게). 결정 기록에 전 · 후가 남아 되돌릴 수 있다.
"""

from typing import Any

import httpx

from dent.modules.generation.models import Role
from dent.modules.generation.rules import CheckKey
from dent.system import llm
from dent.system.exceptions import ExternalServiceError
from dent.system.llm import ChatMessage
from dent.system.models import Connection

# 다시 쓴 응답의 최대 토큰 (긴 응답도 끝까지 쓰게 넉넉히)
REWRITE_MAX_TOKENS = 4_000

# LLM에 보일 대화의 최대 글자 수. 길면 앞을 잘라 마지막 지시를 남긴다.
CONTEXT_MAX_CHARS = 12_000

# 대화를 한 글로 펼칠 때의 역할 이름
ROLE_NAMES = {Role.SYSTEM: "시스템", Role.USER: "사용자", Role.ASSISTANT: "응답"}

SYSTEM_PROMPT = (
    "너는 LLM 학습 데이터를 고치는 편집자다. 고친 응답 글만 출력한다. "
    "머리말 · 설명 · 따옴표를 붙이지 않고, 응답 전체를 코드 울타리로 감싸지 않는다."
)

# 검사마다 할 일 (다시 쓰기를 고를 수 있는 검사만)
INSTRUCTIONS: dict[CheckKey, str] = {
    CheckKey.EMPTY_ANSWER: "응답이 비어 있다. 사용자의 마지막 지시에 맞는 응답을 새로 써라.",
    CheckKey.TRUNCATED: (
        "응답이 중간에 끊겼다. 앞부분의 내용과 말투를 살려 끝까지 완성한 응답 전체를 써라."
    ),
    CheckKey.CODE_TRANSLATED: (
        "응답의 코드 안에서 변수 · 함수 이름이나 문법이 한국어로 바뀌었다. 코드 안을 원래 프로그래밍 언어대로"
        " 되돌려라(영어 식별자). 설명 글과 코드 안의 문자열 · 주석은 그대로 두고 응답 전체를 써라."
    ),
    CheckKey.REFUSAL: (
        "응답이 불필요하게 거절하거나 'AI 언어 모델로서' 같은 상투구 · 다른 모델 이름을 쓴다."
        " 상투구를 빼고 지시에 바로 답하는 응답을 써라."
    ),
    CheckKey.LANG_MIX: (
        "응답이 한국어가 아닌 언어로 쓰였거나 섞였다. 같은 내용을 자연스러운 한국어로 쓴 응답 전체를 써라"
        "(코드는 그대로)."
    ),
    CheckKey.MISMATCH: "응답이 사용자의 마지막 지시를 따르지 않는다. 지시에 맞게 답하는 응답을 새로 써라.",
    CheckKey.SHORT: (
        "응답이 너무 짧다. 지시에 맞게 답하되, 답이 한두 단어면 그 답과 짧은 근거를 함께 써라."
    ),
}

EMPTY_REPLY_MESSAGE = "LLM이 빈 글을 돌려주었습니다. 다시 해 주세요."


def render(messages: list[dict[str, Any]]) -> str:
    """맥락 메시지를 '사용자: …' 줄들로 펼친다. 길면 앞을 잘라 마지막 지시를 남긴다."""
    text = "\n\n".join(
        f"[{ROLE_NAMES.get(Role(m['role']), m['role'])}]\n{m['content']}" for m in messages
    )
    return text if len(text) <= CONTEXT_MAX_CHARS else "…" + text[-CONTEXT_MAX_CHARS:]


def rewrite_messages(
    messages: list[dict[str, Any]], content: str, check: CheckKey
) -> list[ChatMessage]:
    """다시 쓰기를 LLM에 묻는 말들 (시스템 지시 + 대화 · 지금 응답 · 할 일)."""
    prompt = (
        f"아래는 학습 데이터의 대화다.\n\n{render(messages)}\n\n"
        f"[지금 응답]\n{content or '(비어 있음)'}\n\n"
        f"[할 일]\n{INSTRUCTIONS[check]}"
    )
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    ]


def read_rewrite(reply: str) -> str:
    """LLM 답에서 다시 쓴 응답 글. 비었으면 ExternalServiceError."""
    text = reply.strip()
    if not text:
        raise ExternalServiceError(EMPTY_REPLY_MESSAGE)
    return text


async def rewrite_answer(
    connection: Connection,
    *,
    messages: list[dict[str, Any]],
    content: str,
    check: CheckKey,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """다시 쓴 응답 글. LLM이 답하지 않거나 빈 글이면 ExternalServiceError."""
    reply = await llm.complete(
        connection,
        messages=rewrite_messages(messages, content, check),
        max_tokens=REWRITE_MAX_TOKENS,
        transport=transport,
    )
    return read_rewrite(reply)
