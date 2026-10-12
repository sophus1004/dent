"""평가셋 오염 대조: '평가셋'으로 표시한 생성 데이터셋의 지시와 학습 데이터셋의 지시를 8-gram으로 견준다.

평가셋은 사용자가 가져와 데이터셋 설정에서 표시한다(대부분 다시 나눠 줄 수 없어 DENT에 넣지 않는다).
작업 ("generation", "contamination")이 데이터셋 하나를 대조해 겹친 지시를 generation_contaminations에 적고,
그때의 평가셋 표시(번호 · 수정 시각)를 설정에 남긴다. 평가셋이 바뀌면(표시 · 데이터 추가 · 고치기) 표시가 달라져
진단이 '대조 전'으로 기다리고, 표시를 바꾸거나 가져오기를 마칠 때 대조 작업을 다시 넣는다.
글은 지시(사용자 말을 이은 것, system 뺌)끼리 견준다. 계산은 system/contamination.py다.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import semantic
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import Contamination, DatasetSettings, Prompt
from dent.system import contamination, jobs
from dent.system.models import Dataset, Job

CONTAMINATION_JOB_KIND = "contamination"

# 대조할 때 한 번에 읽는 지시 수
BATCH_PROMPTS = 2_000

# 대조 상태: 평가셋 없음 · 대조 전(평가셋이 바뀜) · 대조함
STATE_NO_EVAL = "no_eval"
STATE_STALE = "stale"
STATE_DONE = "done"


async def eval_dataset_ids(db: AsyncSession) -> list[int]:
    """'평가셋'으로 표시한 생성 데이터셋 번호들."""
    return list(
        (
            await db.scalars(
                select(DatasetSettings.dataset_id)
                .where(DatasetSettings.is_eval)
                .order_by(DatasetSettings.dataset_id)
            )
        ).all()
    )


async def eval_signature(db: AsyncSession) -> str | None:
    """평가셋들의 표시: 번호와 수정 시각을 이은 글. 평가셋이 없으면 None."""
    rows = (
        await db.execute(
            select(Dataset.id, Dataset.updated_at)
            .join(DatasetSettings, DatasetSettings.dataset_id == Dataset.id)
            .where(DatasetSettings.is_eval, Dataset.module == generation_service.MODULE_NAME)
            .order_by(Dataset.id)
        )
    ).all()
    if not rows:
        return None
    return ";".join(f"{dataset_id}:{updated_at.isoformat()}" for dataset_id, updated_at in rows)


async def state(db: AsyncSession, *, dataset_id: int) -> str:
    """이 데이터셋의 대조 상태: 평가셋 없음 · 대조 전 · 대조함."""
    signature = await eval_signature(db)
    if signature is None:
        return STATE_NO_EVAL
    found = await db.get(DatasetSettings, dataset_id)
    if found is None or found.contamination_signature != signature:
        return STATE_STALE
    return STATE_DONE


async def queue_check(db: AsyncSession, *, dataset_id: int) -> Job | None:
    """이 데이터셋의 대조 작업을 넣는다(평가셋이 없거나 자기가 평가셋이거나 이미 기다리면 넣지 않음). 커밋은 부른 쪽이 한다."""
    found = await generation_service.find_settings(db, dataset_id=dataset_id)
    if found.is_eval or await eval_signature(db) is None:
        return None
    waiting = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.module == generation_service.MODULE_NAME,
            Job.kind == CONTAMINATION_JOB_KIND,
            Job.dataset_id == dataset_id,
            Job.status == "queued",
        )
    )
    if waiting:
        return None
    return await jobs.enqueue_job(
        db,
        module=generation_service.MODULE_NAME,
        kind=CONTAMINATION_JOB_KIND,
        params={"dataset_id": dataset_id},
        dataset_id=dataset_id,
    )


async def queue_all(db: AsyncSession) -> int:
    """평가셋이 아닌 모든 생성 데이터셋의 대조 작업을 넣는다(평가셋이 바뀐 때). 넣은 수. 커밋은 부른 쪽이 한다."""
    dataset_ids = (
        await db.scalars(
            select(Dataset.id)
            .where(Dataset.module == generation_service.MODULE_NAME)
            .order_by(Dataset.id)
        )
    ).all()
    queued = 0
    for dataset_id in dataset_ids:
        queued += await queue_check(db, dataset_id=dataset_id) is not None
    return queued


async def run_check(db: AsyncSession, *, dataset_id: int, job_id: int) -> dict[str, Any]:
    """데이터셋 하나를 평가셋들과 대조해 겹친 지시를 적는다(앞서 적은 것은 지운다). 결과 요약."""
    signature = await eval_signature(db)
    eval_ids = [dataset for dataset in await eval_dataset_ids(db) if dataset != dataset_id]
    items = [
        (prompt_id, semantic.instruction_text(messages))
        for prompt_id, messages in (
            await db.execute(
                select(Prompt.id, Prompt.messages).where(
                    Prompt.dataset_id.in_(eval_ids), generation_service.IS_ACTIVE
                )
            )
        ).all()
    ]
    index = contamination.EvalIndex(items)
    await db.execute(delete(Contamination).where(Contamination.dataset_id == dataset_id))
    total = await db.scalar(
        select(func.count()).select_from(Prompt).where(Prompt.dataset_id == dataset_id)
    )
    done = found_count = 0
    last_id = 0
    while True:
        rows = (
            await db.execute(
                select(Prompt.id, Prompt.messages)
                .where(Prompt.dataset_id == dataset_id, Prompt.id > last_id)
                .order_by(Prompt.id)
                .limit(BATCH_PROMPTS)
            )
        ).all()
        if not rows:
            break
        matches = []
        for prompt_id, messages in rows:
            match = index.best_match(semantic.instruction_text(messages)) if len(index) else None
            if match is not None:
                matches.append(
                    {
                        "prompt_id": prompt_id,
                        "dataset_id": dataset_id,
                        "eval_prompt_id": match.eval_id,
                        "coverage": match.coverage,
                    }
                )
        if matches:
            await db.execute(insert(Contamination), matches)
        found_count += len(matches)
        last_id = rows[-1][0]
        done += len(rows)
        await jobs.set_progress(db, job_id=job_id, done=done, total=total)
        await db.commit()
    stored = await db.get(DatasetSettings, dataset_id)
    if stored is None:
        stored = await generation_service.find_settings(db, dataset_id=dataset_id)
        db.add(stored)
    stored.contamination_signature = signature
    stored.contamination_checked_at = datetime.now(UTC)
    await db.commit()
    return {"conversations": done, "contaminated": found_count, "eval_items": len(index)}
