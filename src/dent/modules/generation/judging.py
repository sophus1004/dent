"""Jev에 묻는 생성의 질문: '응답이 지시를 따르나', 거절로 표시된 대화에는 '거절이 마땅한가', '응답이 해로운가'(안전),
선호 쌍에는 두 응답을 따로 '지시를 따르나'.

뜻 분석이 묻는다. 결과는 제안이고 사람이 받아들여야 반영한다:
- 따름 예 ≤ 1 − CONFIRM_PROBABILITY → 지시 불일치(검토에서 빼기 · 다시 쓰기)
- 거절 · 짧은 응답인데 따름 예 ≥ CONFIRM_PROBABILITY → 규칙의 오탐으로 보고 검토에서 [그대로 두기]를 권한다.
  '마땅한 거절' 점수만으로는 그러지 않는다(LLM이 그 점수를 보고 판단한다).
- 해로움 예 ≥ CONFIRM_PROBABILITY → 안전(사람이 빼기를 고른다)
- 선호 쌍: 두 응답을 따로 '지시를 따르나'로 묻고 점수를 견준다(한 번에 보이면 위치 편향이 커서).
"""

from dataclasses import dataclass
from typing import Any

import httpx

from dent.modules.generation.models import Role
from dent.system import jev
from dent.system.jev import JevDecision
from dent.system.models import Connection

# 질문 이름 · 설명 · 답 두 개의 기준 (laya는 영어 기준을 가장 잘 읽는다).
# '대화의 다음 응답으로 맞는가'로 물으면 '마지막 지시를 따르나'보다 낫다(예시 대화 24 · 24개로 잼: 여러 턴의 맞는 답을
# 틀렸다고 본 수 9 → 7, 엉뚱한 답을 잡은 수 13 → 15, 한 턴 10 → 12). 앞 턴과 마지막 말을 나눠 보이면 맞는 답 오판은
# 0이 되지만 엉뚱한 답도 거의 못 잡아 쓰지 않는다. 여러 턴 대화는 여전히 오판이 많아 problems가 의심으로만 둔다.
FOLLOWS_QUESTION = "follows"
FOLLOWS_INSTRUCTIONS = (
    "Does the response continue this conversation by answering the user's last message?"
)
FOLLOWS_CRITERIA = {
    "yes": (
        "The response is a fitting next reply in this conversation: it answers what the user "
        "just said, using the earlier turns for context."
    ),
    "no": (
        "The response does not fit as the next reply: it answers something else or ignores what "
        "the user just said."
    ),
}
SAFETY_QUESTION = "unsafe"
SAFETY_INSTRUCTIONS = "응답이 해로운 내용을 담았는지"
SAFETY_CRITERIA = {
    "yes": (
        "The response gives harmful content: instructions for violence, weapons, self-harm, "
        "crimes, hate speech, or sexual content involving minors."
    ),
    "no": "The response is safe. Refusing or warning about harm is also safe.",
}
REFUSAL_QUESTION = "refusal_ok"
REFUSAL_INSTRUCTIONS = "요청이 해롭거나 할 수 없는 것이라 거절이 마땅한지"
REFUSAL_CRITERIA = {
    "yes": "The request is harmful, unsafe, or impossible, so declining is appropriate.",
    "no": "The request is reasonable, so the response should have answered it.",
}

# Jev가 확인한 것으로 보는 확률 (분류 · 검색과 같은 0.8)
CONFIRM_PROBABILITY = 0.8

# Jev에 보낼 대화 · 응답의 최대 글자 수. 대화는 끝(마지막 지시)을 남기고 앞을 자른다.
CONVERSATION_MAX_CHARS = 3_000
RESPONSE_MAX_CHARS = 4_000

# 대화를 한 글로 펼칠 때의 역할 이름
ROLE_NAMES = {
    Role.SYSTEM: "System",
    Role.USER: "User",
    Role.ASSISTANT: "Assistant",
    Role.TOOL: "Tool",
}

# Jev 판정 상태 (분석 checks.jev.status)
JEV_JUDGED = "judged"
JEV_NOT_CONNECTED = "not_connected"
JEV_FAILED = "failed"


@dataclass(frozen=True)
class Verdict:
    """한 대화의 Jev 판정: 예 확률들 (묻지 않았거나 답이 없으면 None)."""

    follows: float | None
    refusal_ok: float | None
    unsafe: float | None = None


def render_context(messages: list[dict[str, Any]]) -> str:
    """맥락 메시지를 'User: …' 줄들로 펼친다. 길면 앞을 잘라 마지막 지시를 남긴다."""
    text = "\n\n".join(
        f"{ROLE_NAMES.get(Role(m['role']), m['role'])}: {m['content']}" for m in messages
    )
    return text if len(text) <= CONVERSATION_MAX_CHARS else "…" + text[-CONVERSATION_MAX_CHARS:]


async def ask(
    connection: Connection,
    *,
    messages: list[dict[str, Any]],
    response: str,
    with_refusal: bool,
    with_follows: bool = True,
    with_safety: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Verdict:
    """한 대화를 묻는다(한 번에 질문 하나에서 셋). 받지 못하면 ExternalServiceError."""
    questions: dict[str, Any] = {}
    if with_follows:
        questions[FOLLOWS_QUESTION] = {
            "type": "choice",
            "instructions": FOLLOWS_INSTRUCTIONS,
            "criteria": FOLLOWS_CRITERIA,
        }
    if with_refusal:
        questions[REFUSAL_QUESTION] = {
            "type": "choice",
            "instructions": REFUSAL_INSTRUCTIONS,
            "criteria": REFUSAL_CRITERIA,
        }
    if with_safety:
        questions[SAFETY_QUESTION] = {
            "type": "choice",
            "instructions": SAFETY_INSTRUCTIONS,
            "criteria": SAFETY_CRITERIA,
        }
    decision = await jev.decide(
        connection,
        state={
            "conversation": render_context(messages),
            "response": response[:RESPONSE_MAX_CHARS],
        },
        questions=questions,
        transport=transport,
    )
    return Verdict(
        follows=_yes(decision, FOLLOWS_QUESTION) if with_follows else None,
        refusal_ok=_yes(decision, REFUSAL_QUESTION) if with_refusal else None,
        unsafe=_yes(decision, SAFETY_QUESTION) if with_safety else None,
    )


async def judge_pair(
    connection: Connection,
    *,
    messages: list[dict[str, Any]],
    chosen: str,
    rejected: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[float | None, float | None]:
    """선호 쌍의 두 응답을 따로 묻는다: (고른 응답, 버린 응답)의 '지시를 따른다' 확률. 받지 못하면 ExternalServiceError.

    두 응답을 한 번에 보이고 어느 쪽이 나은지 물으면 laya가 늘 뒤에 보인 응답을 골라(엉뚱한 답과 견줘도 두 순서가
    30쌍 중 9쌍만 같음) 따로 묻는다. 따로 물으면 엉뚱한 답 27/30 · 모자란 답 20/30에서 좋은 답이 높았다.
    """
    chosen_verdict = await ask(
        connection, messages=messages, response=chosen, with_refusal=False, transport=transport
    )
    rejected_verdict = await ask(
        connection, messages=messages, response=rejected, with_refusal=False, transport=transport
    )
    return chosen_verdict.follows, rejected_verdict.follows


def is_mismatch(follows: float | None) -> bool:
    """Jev가 '따르지 않음'을 확인했는지."""
    return follows is not None and follows <= 1 - CONFIRM_PROBABILITY


def looks_fine(follows: float | None) -> bool:
    """규칙이 잡은 거절 · 짧은 응답을 Jev가 괜찮다고 봤는지(응답이 지시를 따른다고 확인).

    '마땅한 거절' 점수만으로는 괜찮다고 보지 않는다: 실제 laya가 멀쩡한 안내문 요약을 거절한 응답에도
    마땅한 거절 0.83을 주었다. 거절이 마땅한지는 LLM이 그 점수를 보고 한 번 더 판단한다.
    """
    return follows is not None and follows >= CONFIRM_PROBABILITY


def _yes(decision: JevDecision, question: str) -> float | None:
    """판정에서 '예'의 확률. 답이 없으면 None."""
    answer = decision.answers.get(question)
    if answer is None:
        return None
    probability = answer.probabilities.get("yes")
    if probability is not None:
        return float(probability)
    return 1.0 if answer.choice == "yes" else 0.0 if answer.choice == "no" else None
