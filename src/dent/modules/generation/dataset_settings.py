"""생성 데이터셋 설정 저장과 그 뒤에 넣는 작업.

- 학습할 모델 · 최대 길이를 바꾸면 길이 작업(lengths)을 넣는다(토크나이저를 받고 모든 대화를 다시 잰다).
- 평가셋 표시를 바꾸면 다른 데이터셋들의 오염 대조(contamination)를 다시 넣는다. 평가셋이 되면 자기 대조 결과는 지운다.
- 가져오기를 마치면(after_import) 같은 작업을 넣는다: 학습할 모델이 있으면 길이, 평가셋이면 모두 대조 · 아니면 자기만.
데이터를 바꾸지 않는 값이라 데이터셋 수정 시각을 건드리지 않는다.
"""

from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import contamination, lengths
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import Contamination, DatasetSettings
from dent.modules.generation.schemas import SettingsUpdate
from dent.system import jobs, tokenizers


async def update_settings(
    db: AsyncSession, *, dataset_id: int, data: SettingsUpdate
) -> DatasetSettings:
    """설정을 고친다(준 값만). 데이터셋이 없으면 NotFoundError, 모델 이름 모양이 틀리면 InvalidInputError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    stored = await db.get(DatasetSettings, dataset_id)
    if stored is None:
        stored = await generation_service.find_settings(db, dataset_id=dataset_id)
        db.add(stored)
    given = data.model_fields_set
    length_changed = False
    eval_changed = False
    if "goal" in given and data.goal is not None:
        stored.goal = data.goal.value
    if "tokenizer_model" in given:
        model = data.tokenizer_model or None
        if model is not None:
            tokenizers.check_model_name(model)
        length_changed = model != stored.tokenizer_model
        stored.tokenizer_model = model
    if "max_tokens" in given and data.max_tokens is not None:
        length_changed = length_changed or data.max_tokens != stored.max_tokens
        stored.max_tokens = data.max_tokens
    if "is_eval" in given and data.is_eval is not None:
        eval_changed = data.is_eval != stored.is_eval
        stored.is_eval = data.is_eval
    stored.updated_at = datetime.now(UTC)
    await db.flush()
    if length_changed:
        await queue_length(db, dataset_id=dataset_id)
    if eval_changed:
        if stored.is_eval:
            # 평가셋은 자기를 대조하지 않는다.
            await db.execute(delete(Contamination).where(Contamination.dataset_id == dataset_id))
            stored.contamination_signature = None
        await contamination.queue_all(db)
    await db.commit()
    return stored


async def queue_length(db: AsyncSession, *, dataset_id: int) -> None:
    """길이를 다시 재는 작업을 넣는다(이미 기다리는 것이 있으면 그것을 쓴다). 커밋은 부른 쪽이 한다."""
    await jobs.enqueue_job(
        db,
        module=generation_service.MODULE_NAME,
        kind=lengths.LENGTH_JOB_KIND,
        params={"dataset_id": dataset_id},
        dataset_id=dataset_id,
        dedupe_key=jobs.dedupe_key_of(
            generation_service.MODULE_NAME, lengths.LENGTH_JOB_KIND, dataset_id
        ),
    )


async def after_import(db: AsyncSession, *, dataset_id: int) -> None:
    """가져오기를 마친 뒤 넣는 작업: 학습할 모델이 있으면 길이 다시 재기, 평가셋이면 모두 · 아니면 자기 오염 대조. 커밋한다."""
    found = await generation_service.find_settings(db, dataset_id=dataset_id)
    if found.tokenizer_model:
        await queue_length(db, dataset_id=dataset_id)
    if found.is_eval:
        await contamination.queue_all(db)
    else:
        await contamination.queue_check(db, dataset_id=dataset_id)
    await db.commit()
