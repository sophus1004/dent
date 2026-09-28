"""Jev에 묻는 검색의 질문 하나: '이 문서가 질의의 답을 담고 있나'(예 · 아니오).

뜻 분석(거짓 오답 · 빠진 정답 · 정답 의심 확인), 오답 찾기(Jev가 예라고 한 문서는 오답에서 뺀다),
질의 만들기(만든 질의의 정답 확인), 내보내기(교사 점수)가 같은 질문을 쓴다. 결과는 제안이고 사람이 받아들여야 반영한다.
"""

from typing import Any

import httpx

from dent.system import jev
from dent.system.jev import JevDecision
from dent.system.models import Connection

# 질문 이름 · 설명 · 답 두 개의 기준
ANSWER_QUESTION = "answers"
ANSWER_INSTRUCTIONS = "문서가 질의의 답을 담고 있는지"
ANSWER_CRITERIA = {
    "yes": "The document contains the answer to the query.",
    "no": "The document does not contain the answer to the query.",
}

# Jev가 확인한 것으로 보는 확률 (예 ≥ 0.8, 정답 의심은 아니오 ≥ 0.8 = 예 ≤ 0.2). 정한 것 4 · 5와 같다.
CONFIRM_PROBABILITY = 0.8

# 질의 · 문서를 Jev에 보낼 때의 최대 글자 수 (긴 문서는 앞부분만 본다)
QUERY_MAX_CHARS = 1_000
DOCUMENT_MAX_CHARS = 4_000

# Jev 판정 상태 (분석 checks.jev.status)
JEV_JUDGED = "judged"
JEV_NOT_CONNECTED = "not_connected"
JEV_FAILED = "failed"


def answer_questions() -> dict[str, Any]:
    """Jev 질문 묶음 (질문 하나)."""
    return {
        ANSWER_QUESTION: {
            "type": "choice",
            "instructions": ANSWER_INSTRUCTIONS,
            "criteria": ANSWER_CRITERIA,
        }
    }


def yes_probability(decision: JevDecision) -> float | None:
    """판정에서 '예'의 확률. 답이 없으면 None."""
    answer = decision.answers.get(ANSWER_QUESTION)
    if answer is None:
        return None
    probability = answer.probabilities.get("yes")
    if probability is not None:
        return float(probability)
    return 1.0 if answer.choice == "yes" else 0.0 if answer.choice == "no" else None


async def ask_contains(
    connection: Connection,
    *,
    query: str,
    document: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> float | None:
    """문서가 질의의 답을 담았다는 '예' 확률. 받지 못하면 ExternalServiceError."""
    decision = await jev.decide(
        connection,
        state={"query": query[:QUERY_MAX_CHARS], "document": document[:DOCUMENT_MAX_CHARS]},
        questions=answer_questions(),
        transport=transport,
    )
    return yes_probability(decision)


def is_confirmed(kind: str, probability: float | None) -> bool:
    """제안을 Jev가 확인했는지: 정답 의심은 아니오 ≥ 기준, 나머지는 예 ≥ 기준."""
    if probability is None:
        return False
    if kind == "suspect_positive":
        return probability <= 1 - CONFIRM_PROBABILITY
    return probability >= CONFIRM_PROBABILITY
