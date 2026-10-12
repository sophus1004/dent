"""응답 만들기 · 비교 만들기를 여러 대화에 한다(도우미의 허락 단계 · AI로 고치기가 쓴다).

- 응답 만들기: 응답이 없는 지시(problems.answer_targets)마다 LLM이 응답을 쓴다. 모양이 걸리면 한 번 더 쓰고,
  그래도 걸리면 넣지 않는다(모자람으로 센다).
- 비교 만들기: 주 응답이 있고 쌍이 없는 지시(problems.pair_targets)마다 LLM이 다른 응답을 쓰고, Jev가 두 응답이
  지시를 따르는지 따로 본 점수로 고른 응답을 정한다. 두 점수 차이가 작아도 쌍은 더하고 진단의 '작은 마진'이 알린다.
여기서는 만들기만 하고 넣지 않는다. 넣기(결정 기록과 함께)는 부른 쪽이 conversations로 한다.
LLM · Jev 요청은 MAKE_CONCURRENCY건씩 함께 보낸다(DB에 쓰는 일은 부른 쪽이 한 세션으로 차례로 한다).
허락 전 시험은 앞 TRIAL_PROMPTS건으로 만들어 통과 수와 쓴 토큰을 보이고, 남은 수에 곱해 비용을 어림한다.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import answering, judging, problems
from dent.modules.generation.models import Prompt, Response
from dent.system import llm
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection

# 허락 전에 시험으로 만드는 지시 수
TRIAL_PROMPTS = 5

# 한 번 실행에 만드는 최대 지시 수 (넘으면 다음 실행 · AI로 고치기에서 이어 한다)
MAX_PROMPTS_PER_RUN = 1_000

# 함께 보내는 LLM · Jev 요청 수 (공급자의 요청 한도를 넘지 않게 작게)
MAKE_CONCURRENCY = 4

# 응답 하나의 어림 토큰 (시험이 없을 때: 지시 · 시스템 말 입력 + 응답 출력)
TOKENS_EACH_ESTIMATE = 900

# 넣지 않은 까닭: 두 번 다 모양이 걸림 · Jev가 답하지 않음
SHAPE_FAILED = "모양"
JEV_FAILED = "Jev 실패"


@dataclass(frozen=True)
class Made:
    """지시 하나에 만든 결과."""

    # 대화(지시) 번호
    prompt_id: int

    # 만든 응답 글. 두 번 다 모양이 걸렸거나 LLM이 실패하면 None
    text: str | None

    # 쓴 LLM 토큰
    tokens: int

    # 넣지 않은 까닭 (모양 · Jev 실패). 넣으면 빈 글
    reason: str = ""

    # 비교 만들기: Jev가 본 주 응답 · 만든 응답의 '지시를 따른다' 확률
    main_follows: float | None = None
    other_follows: float | None = None


@dataclass
class Outcome:
    """만든 결과의 수와 보기."""

    # 넣은 수 · 모양이 걸려 넣지 않은 수 · Jev가 답하지 않아 넣지 않은 수
    made: int = 0
    short: int = 0
    failed: int = 0

    # 쓴 LLM 토큰 · Jev 요청 수
    tokens: int = 0
    jev_calls: int = 0

    # 비교 만들기: 만든 응답이 고른 응답이 된 수 · 두 점수 차이가 작은 쌍 수
    other_won: int = 0
    small_margin: int = 0

    # 보기 (만든 응답의 앞부분)
    samples: list[str] = field(default_factory=list)


async def answer_target_ids(db: AsyncSession, *, dataset_id: int, limit: int) -> list[int]:
    """응답 만들기의 대상 대화 번호들(번호 순, 최대 limit)."""
    return list((await db.scalars(problems.answer_targets(dataset_id).limit(limit))).all())


async def pair_target_ids(db: AsyncSession, *, dataset_id: int, limit: int) -> list[int]:
    """비교 만들기의 대상 대화 번호들(번호 순, 최대 limit)."""
    return list((await db.scalars(problems.pair_targets(dataset_id).limit(limit))).all())


async def contexts(db: AsyncSession, *, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """대화마다 맥락 메시지 (응답 만들기에 보일 것)."""
    rows = await db.execute(select(Prompt.id, Prompt.messages).where(Prompt.id.in_(ids)))
    return {prompt_id: messages for prompt_id, messages in rows.all()}


async def main_answers(db: AsyncSession, *, ids: list[int]) -> dict[int, Response]:
    """대화마다 주 응답."""
    found = await db.scalars(select(Response).where(Response.prompt_id.in_(ids), Response.is_main))
    return {answer.prompt_id: answer for answer in found}


async def write_answers(
    connection: Connection,
    items: list[tuple[int, list[dict[str, Any]], str | None]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Made]:
    """(번호, 맥락, 견줄 응답)마다 LLM이 응답을 쓴다. 견줄 응답이 있으면(비교 만들기) 그와 다른 응답을 쓴다.

    MAKE_CONCURRENCY건씩 함께 보낸다. 크레딧 없음 · 한도 초과처럼 실행을 멈춰야 하는 LLM 실패는
    ExternalServiceError로 그대로 낸다(한 건만 실패한 빈 답은 그 건만 넣지 않는다).
    """
    gate = asyncio.Semaphore(MAKE_CONCURRENCY)

    async def one(prompt_id: int, messages: list[dict[str, Any]], other: str | None) -> Made:
        async with gate:
            return await _write_one(connection, prompt_id, messages, other, transport=transport)

    return list(await asyncio.gather(*(one(*item) for item in items)))


async def judge_pairs(
    connection: Connection,
    items: list[tuple[list[dict[str, Any]], str, Made]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Made]:
    """(맥락, 주 응답, 만든 결과)마다 Jev가 두 응답이 지시를 따르는지 따로 본 점수를 붙인다.

    받지 못한 건은 넣지 않는다(까닭 'Jev 실패'). MAKE_CONCURRENCY건씩 함께 보낸다.
    """
    gate = asyncio.Semaphore(MAKE_CONCURRENCY)

    async def one(messages: list[dict[str, Any]], main: str, made: Made) -> Made:
        if made.text is None:
            return made
        async with gate:
            try:
                main_follows, other_follows = await judging.judge_pair(
                    connection,
                    messages=messages,
                    chosen=main,
                    rejected=made.text,
                    transport=transport,
                )
            except ExternalServiceError:
                return Made(made.prompt_id, None, made.tokens, reason=JEV_FAILED)
        if main_follows is None or other_follows is None:
            return Made(made.prompt_id, None, made.tokens, reason=JEV_FAILED)
        return Made(
            made.prompt_id,
            made.text,
            made.tokens,
            main_follows=main_follows,
            other_follows=other_follows,
        )

    return list(await asyncio.gather(*(one(*item) for item in items)))


async def _write_one(
    connection: Connection,
    prompt_id: int,
    messages: list[dict[str, Any]],
    other: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None,
) -> Made:
    """지시 하나에 응답을 쓴다(모양이 걸리면 한 번 더). 두 번 다 걸리면 text 없이 돌려준다."""
    tokens = 0
    for _attempt in range(answering.MAX_ATTEMPTS):
        reply = await llm.chat(
            connection,
            messages=answering.answer_messages(messages, other=other),
            tools=[],
            max_tokens=answering.ANSWER_MAX_TOKENS,
            transport=transport,
        )
        tokens += reply.input_tokens + reply.output_tokens
        text = reply.content.strip()
        if text and not answering.answer_problems(messages, text):
            return Made(prompt_id, text, tokens)
    return Made(prompt_id, None, tokens, reason=SHAPE_FAILED)
