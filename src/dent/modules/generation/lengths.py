"""대화 재기(규칙 검사)와 긴 대화의 길이: 데이터셋 설정에 학습할 모델이 있으면 그 토크나이저로 잰다.

글을 넣거나 바꿀 때 모든 곳(가져오기 뒤 · 검토 결정 · 다시 쓰기 · 응답 만들기)이 measure_marks로 잰다. 설정의 모델로 잰
적이 있으면(measured_model) 그 토크나이저로 대화 전체의 토큰을 세 최대 길이를 넘는지 보고, 없으면 응답의 글자 수로 어림한다.
모델 · 최대 길이를 바꾸면 작업 ("generation", "length")이 토크나이저를 받고 데이터셋의 모든 대화를 다시 잰다.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import importing, rules
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import DatasetSettings, Prompt, Response
from dent.system import jobs, tokenizers
from dent.system.config import get_settings

LENGTH_JOB_KIND = "length"

# 다시 잴 때 한 번에 읽는 대화 수
BATCH_PROMPTS = 2_000


async def tokenizer_of(db: AsyncSession, *, dataset_id: int) -> tuple[Path, int] | None:
    """긴 대화를 잴 토크나이저 파일과 최대 길이. 잰 적이 없거나 파일이 없으면 None(글자 수 어림)."""
    found = await db.get(DatasetSettings, dataset_id)
    if found is None or not found.measured_model or found.measured_max_tokens is None:
        return None
    path = tokenizers.tokenizer_path(get_settings(), found.measured_model)
    return (path, found.measured_max_tokens) if path.exists() else None


def measure_with(
    tokenizer: tuple[Path, int] | None,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """대화 하나(맥락 + 응답)를 잰다. 토크나이저가 있으면 대화 전체의 토큰으로 긴 대화를 본다."""
    if tokenizer is None:
        return rules.measure(messages, tools=tools)
    path, max_tokens = tokenizer
    [count] = tokenizers.count_conversations(path, [messages])
    return rules.measure(messages, tools=tools, token_count=count, max_tokens=max_tokens)


async def measure_marks(
    db: AsyncSession, *, dataset_id: int, messages: list[dict[str, Any]], tools: Any
) -> dict[str, Any]:
    """대화 하나를 잰다(데이터셋의 토크나이저가 있으면 그것으로)."""
    return measure_with(await tokenizer_of(db, dataset_id=dataset_id), messages, tools=tools)


async def remeasure_dataset(
    db: AsyncSession,
    *,
    dataset_id: int,
    job_id: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """설정의 모델 토크나이저를 받고 데이터셋의 모든 대화를 다시 잰다(모델이 비면 글자 수 어림으로). 결과 요약.

    토크나이저를 받지 못하면 도메인 예외(NotFoundError · ExternalServiceError · InvalidInputError).
    """
    found = await generation_service.find_settings(db, dataset_id=dataset_id)
    tokenizer: tuple[Path, int] | None = None
    if found.tokenizer_model:
        path = await tokenizers.ensure_tokenizer(
            get_settings(), found.tokenizer_model, transport=transport
        )
        tokenizer = (path, found.max_tokens)
    total = await db.scalar(
        select(func.count()).select_from(Prompt).where(Prompt.dataset_id == dataset_id)
    )
    done = 0
    last_id = 0
    long_count = 0
    while True:
        prompts = (
            await db.scalars(
                select(Prompt)
                .where(Prompt.dataset_id == dataset_id, Prompt.id > last_id)
                .order_by(Prompt.id)
                .limit(BATCH_PROMPTS)
            )
        ).all()
        if not prompts:
            break
        answers = {
            answer.prompt_id: answer
            for answer in (
                await db.scalars(
                    select(Response).where(
                        Response.prompt_id.in_([p.id for p in prompts]), Response.is_main
                    )
                )
            ).all()
        }
        for prompt in prompts:
            answer = answers.get(prompt.id)
            messages = importing.conversation(
                prompt.messages,
                answer.content if answer else None,
                answer.tool_calls if answer else None,
            )
            prompt.marks = measure_with(tokenizer, messages, tools=prompt.tools)
            prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)
            long_count += rules.CheckKey.LONG.value in prompt.marks
        last_id = prompts[-1].id
        done += len(prompts)
        await jobs.set_progress(db, job_id=job_id, done=done, total=total)
        await db.commit()
    stored = await db.get(DatasetSettings, dataset_id)
    if stored is None:
        stored = found
        db.add(stored)
    stored.measured_model = found.tokenizer_model
    stored.measured_max_tokens = found.max_tokens if found.tokenizer_model else None
    stored.updated_at = datetime.now(UTC)
    await db.commit()
    return {"conversations": done, "long": long_count, "model": found.tokenizer_model}
