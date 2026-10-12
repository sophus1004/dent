"""생성 모듈의 업무 규칙: 데이터셋 요약 · 지우기, 가져오기 시작.

데이터셋 목록(system_datasets)과 가져오기 기록(system_imports)은 시스템 층의 것이다. 생성 모듈은 그 데이터셋에
대화(지시 · 응답)를 둔다. 같은 모듈 안에서 대화 목록 · 검토 결정은 conversations.py, 진단은 overview.py,
가져오기 실행은 importing.py, 필드 맞춤 짐작은 mapping.py, 검사 규칙은 rules.py가 맡는다.
대화의 상태 조건(IS_ACTIVE 등)은 여기 한 곳에 두고 모두 가져다 쓴다.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation.models import (
    ANALYSIS_BUILDING_STATUSES,
    DEFAULT_MAX_TOKENS,
    Analysis,
    DatasetSettings,
    Goal,
    Pair,
    Prompt,
    Response,
    Source,
)
from dent.modules.generation.rules import CheckKey
from dent.modules.generation.schemas import ImportCreate
from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system import sources
from dent.system.exceptions import ConflictError, InvalidInputError
from dent.system.models import IMPORTING_STATUSES, Dataset, Import

# 이 모듈의 이름. 데이터셋 목록의 module, 작업 대기열의 module, API 주소, 테이블 앞머리가 모두 이 이름이다.
MODULE_NAME = "generation"

ANALYZING_MESSAGE = "뜻 분석을 만드는 중입니다. 끝나거나 멈춘 뒤 지워 주세요."

# 흐름의 입구 단계: 지시만 → 1(지시, 응답 만들기 앞) · 지시 · 응답 → 2(응답) · 선호 쌍 → 3(선호)
ENTRY_PROMPTS, ENTRY_ANSWERS, ENTRY_PAIRS = 1, 2, 3

# 대화 상태 조건. 휴지통 밖이면 학습에 쓴다(첫 판에는 따로 '학습 제외'를 두지 않는다).
IS_ACTIVE = Prompt.trashed_at.is_(None)
IS_TRASHED = Prompt.trashed_at.is_not(None)

# 문제가 남은 대화 (같은 지시는 해시로 세므로 여기 들지 않는다)
HAS_OPEN_CHECKS = func.jsonb_array_length(Prompt.open_checks) > 0

# 주 응답이 있는 대화 (없으면 지시만: 응답 만들기로 채운다)
HAS_MAIN_ANSWER = exists().where(Response.prompt_id == Prompt.id, Response.is_main)

# 휴지통 밖 선호 쌍 (지시도 휴지통 밖이어야 한다. 쌍 조건은 Prompt와 이어 쓴다)
PAIR_IS_ACTIVE = Pair.trashed_at.is_(None)

# 응답 만들기가 남은 대화 (응답이 없고 '그대로 두기'로 두지 않은 것). 목록의 '문제' 수에 든다.
NEEDS_ANSWER = and_(~HAS_MAIN_ANSWER, ~Prompt.kept.contains([CheckKey.NO_ANSWER.value]))

# 글의 표시로 잰 쌍 문제(같은 쌍 · 동점)가 남은 대화. 목록의 '문제' 수에 든다.
HAS_PAIR_PROBLEM = exists().where(
    Pair.prompt_id == Prompt.id,
    PAIR_IS_ACTIVE,
    Pair.marks.has_key(CheckKey.SAME_PAIR.value),
    ~Pair.kept.contains([CheckKey.SAME_PAIR.value]),
)


def has_open(key: str) -> ColumnElement[bool]:
    """이 검사가 남은 대화 (사람이 '그대로 두기'로 둔 것은 빠진다)."""
    return Prompt.open_checks.contains([key])


def open_checks(marks: dict[str, Any], kept: list[str]) -> list[str]:
    """남은 검사들: marks의 검사 가운데 kept에 없는 것 (Prompt.open_checks에 둔다)."""
    return [key for key in marks if key not in kept]


@dataclass(frozen=True)
class DatasetSummary:
    """데이터셋 한 건과 그 개수 요약. 목록의 한 줄이 된다."""

    # 데이터셋 (시스템의 데이터셋 목록 한 줄)
    dataset: Dataset

    # 휴지통에 없는 대화 수
    conversation_count: int

    # 문제가 남은 대화 수
    problem_count: int

    # 휴지통에 있는 대화 수
    trash_count: int

    # 가져오기가 대기 중이거나 실행 중인지
    importing: bool


# ---------- 데이터셋 ----------


async def list_datasets(db: AsyncSession) -> list[DatasetSummary]:
    """생성 데이터셋 목록을 개수 요약과 함께 돌려준다. 최근에 바뀐 것부터(같으면 번호가 큰 것부터)."""
    datasets = await datasets_service.list_datasets(db, module=MODULE_NAME)
    return await _summarize(db, datasets)


async def get_dataset(db: AsyncSession, *, dataset_id: int) -> Dataset:
    """생성 데이터셋 하나. 없거나 다른 모듈의 데이터셋이면 NotFoundError."""
    return await datasets_service.get_dataset(db, dataset_id=dataset_id, module=MODULE_NAME)


async def get_dataset_summary(db: AsyncSession, *, dataset_id: int) -> DatasetSummary:
    """데이터셋 하나의 개수 요약. 없으면 NotFoundError."""
    dataset = await get_dataset(db, dataset_id=dataset_id)
    [summary] = await _summarize(db, [dataset])
    return summary


async def delete_dataset(db: AsyncSession, *, dataset_id: int) -> None:
    """데이터셋과 그 안의 대화 · 결정 기록 · 뜻 분석 · 가져오기 기록을 모두 지운다.

    없으면 NotFoundError, 뜻 분석을 만드는 중이면 ConflictError, 가져오는 중이면 ConflictError(시스템이 막는다).
    """
    await get_dataset(db, dataset_id=dataset_id)
    is_analyzing = await db.scalar(
        select(Analysis.id).where(
            Analysis.dataset_id == dataset_id, Analysis.status.in_(ANALYSIS_BUILDING_STATUSES)
        )
    )
    if is_analyzing:
        raise ConflictError(ANALYZING_MESSAGE)
    # 지시 · 응답 · 결정 기록은 데이터셋을 참조하는 외래 키(CASCADE)로 함께 지워진다.
    await datasets_service.delete_dataset(db, dataset_id=dataset_id, module=MODULE_NAME)
    await db.commit()


# ---------- 가져오기 시작 ----------


async def create_import(db: AsyncSession, *, data: ImportCreate) -> Import:
    """가져오기를 대기열에 넣는다. 새 데이터셋이면 함께 만들고, 기록과 작업을 한 번에 저장한다.

    데이터셋이 없으면 NotFoundError, 새 이름이 겹치면 ConflictError,
    입력이 모자라면 InvalidInputError, 올린 파일이 없으면 NotFoundError.
    """
    has_dataset_id = data.dataset_id is not None
    has_new_name = data.new_dataset_name is not None
    if has_dataset_id == has_new_name:
        raise InvalidInputError("기존 데이터셋 번호와 새 데이터셋 이름 가운데 하나만 주세요.")
    source_name, options = await sources.describe_source(db, data=data)
    options |= {"mapping": data.mapping.model_dump(mode="json")}

    if data.dataset_id is not None:
        dataset = await get_dataset(db, dataset_id=data.dataset_id)
    else:
        dataset = await datasets_service.create_dataset(
            db, module=MODULE_NAME, name=str(data.new_dataset_name)
        )

    import_row = await imports_service.create_import(
        db,
        module=MODULE_NAME,
        dataset_id=dataset.id,
        source=data.source,
        source_name=source_name,
        options=options,
    )
    await db.commit()
    return await imports_service.get_import(db, import_id=import_row.id)


# ---------- 안에서만 쓰는 함수 ----------


async def _summarize(db: AsyncSession, datasets: list[Dataset]) -> list[DatasetSummary]:
    """데이터셋마다 대화 수 · 문제 대화 수 · 가져오는 중인지를 센다. 데이터셋 수와 상관없이 쿼리 두 번."""
    dataset_ids = [dataset.id for dataset in datasets]
    count_query = (
        select(
            Prompt.dataset_id,
            func.count().filter(IS_ACTIVE),
            func.count().filter(IS_ACTIVE, or_(HAS_OPEN_CHECKS, NEEDS_ANSWER, HAS_PAIR_PROBLEM)),
            func.count().filter(IS_TRASHED),
        )
        .where(Prompt.dataset_id.in_(dataset_ids))
        .group_by(Prompt.dataset_id)
    )
    counts = {row[0]: row[1:] for row in (await db.execute(count_query)).all()}

    importing_query = select(Import.dataset_id).where(
        Import.dataset_id.in_(dataset_ids),
        Import.status.in_(IMPORTING_STATUSES),
    )
    importing_ids = set((await db.scalars(importing_query)).all())

    summaries = []
    for dataset in datasets:
        active, problems, trashed = counts.get(dataset.id, (0, 0, 0))
        summaries.append(
            DatasetSummary(
                dataset=dataset,
                conversation_count=active,
                problem_count=problems,
                trash_count=trashed,
                importing=dataset.id in importing_ids,
            )
        )
    return summaries


# ---------- 데이터셋 설정 ----------


async def find_settings(db: AsyncSession, *, dataset_id: int) -> DatasetSettings:
    """데이터셋 설정. 저장한 적이 없으면 기본값(세션에 넣지 않은 새 줄)."""
    found = await db.get(DatasetSettings, dataset_id)
    if found is not None:
        return found
    return DatasetSettings(
        dataset_id=dataset_id,
        goal=await _default_goal(db, dataset_id=dataset_id),
        is_eval=False,
        tokenizer_model=None,
        max_tokens=DEFAULT_MAX_TOKENS,
    )


async def entry_stage(db: AsyncSession, *, dataset_id: int) -> int:
    """흐름의 입구(가져온 모양): 원본 선호 쌍이 있으면 3, 원본 응답이 있으면 2, 지시만이면 1.

    원본(가져온 것)으로만 본다. 응답 만들기 · 비교 만들기로 늘어난 것은 입구를 바꾸지 않는다.
    """
    original = Source.ORIGINAL.value
    has_pairs = await db.scalar(
        select(exists().where(Pair.dataset_id == dataset_id, Pair.source == original))
    )
    if has_pairs:
        return ENTRY_PAIRS
    has_answers = await db.scalar(
        select(exists().where(Response.dataset_id == dataset_id, Response.source == original))
    )
    has_prompts = await db.scalar(select(exists().where(Prompt.dataset_id == dataset_id)))
    return ENTRY_ANSWERS if has_answers or not has_prompts else ENTRY_PROMPTS


async def _default_goal(db: AsyncSession, *, dataset_id: int) -> str:
    """설정이 없을 때의 목표: 선호 쌍을 가져왔으면 선호, 아니면 SFT."""
    has_pairs = await db.scalar(select(exists().where(Pair.dataset_id == dataset_id)))
    return Goal.PREFERENCE.value if has_pairs else Goal.SFT.value
