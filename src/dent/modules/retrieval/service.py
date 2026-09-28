"""검색 모듈의 업무 규칙: 검색 데이터셋 요약 · 지우기, 설정, 가져오기 시작.

데이터셋 목록(system_datasets)과 가져오기 기록(system_imports)은 시스템 층의 것이다. 검색 모듈은 그
데이터셋에 질의 · 문서 · 판정을 둔다. 같은 모듈 안에서 질의 · 문서 · 판정 다루기는 records.py, 진단은 overview.py,
가져오기 실행은 importing.py, 필드 맞춤 짐작은 mapping.py, 뜻 분석은 semantic.py가 맡는다.
질의 · 문서의 상태 조건(QUERY_ACTIVE 등)은 여기 한 곳에 두고 모두 가져다 쓴다.
"""

from dataclasses import dataclass

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval.models import (
    ANALYSIS_BUILDING_STATUSES,
    ENTRY_STAGE,
    POSITIVE_MIN_GRADE,
    Analysis,
    DatasetSettings,
    Document,
    ItemKind,
    Judgment,
    NearDuplicate,
    Query,
    Shape,
)
from dent.modules.retrieval.schemas import ImportCreate, SettingsUpdate
from dent.system import datasets as datasets_service
from dent.system import imports as imports_service
from dent.system import sources
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.models import IMPORTING_STATUSES, Dataset, Import

# 이 모듈의 이름. 데이터셋 목록의 module, 작업 대기열의 module, API 주소, 테이블 앞머리가 모두 이 이름이다.
MODULE_NAME = "retrieval"

DATASET_ANALYZING_MESSAGE = (
    "뜻 분석을 만드는 중인 데이터셋은 지울 수 없습니다. 뜻 분석을 멈춘 뒤 지우세요."
)
NO_SETTINGS_MESSAGE = "아직 가져온 데이터가 없는 데이터셋입니다."
RANK_RANGE_MESSAGE = "오답 찾기 순위 범위의 앞이 뒤보다 클 수 없습니다."

# 도우미 실행 설정 칸: 데이터를 바꾸지 않고 다음 도우미 실행의 방법만 정한다
RUN_SETTING_FIELDS = {"chunk_overlap", "queries_per_chunk", "question_share"}

# 질의 상태 조건. 휴지통 밖(active) 가운데 뺀 이유가 없으면 학습에 쓴다(included).
QUERY_ACTIVE = Query.trashed_at.is_(None)
QUERY_TRASHED = Query.trashed_at.is_not(None)
QUERY_INCLUDED = and_(QUERY_ACTIVE, Query.exclude_reason.is_(None))
QUERY_EXCLUDED = and_(QUERY_ACTIVE, Query.exclude_reason.is_not(None))

# 문서 상태 조건. 휴지통 밖이고 나누기로 대신하지 않은 것이 코퍼스다.
DOCUMENT_ACTIVE = and_(Document.trashed_at.is_(None), Document.replaced_at.is_(None))
DOCUMENT_TRASHED = Document.trashed_at.is_not(None)
# 나누기로 청크가 대신하는 원문 (휴지통 밖). 되돌릴 때 쓰려고 원문과 원문의 판정을 남긴다.
DOCUMENT_SPLIT = and_(Document.trashed_at.is_(None), Document.replaced_at.is_not(None))

# 문서의 학습 글 (본문과 같으면 input_text를 비워 두므로 본문으로 채운다)
DOCUMENT_INPUT = func.coalesce(Document.input_text, Document.text)

# 판정 조건
IS_POSITIVE = Judgment.grade >= POSITIVE_MIN_GRADE
IS_NEGATIVE = Judgment.grade < POSITIVE_MIN_GRADE


@dataclass(frozen=True)
class DatasetSummary:
    """데이터셋 한 건과 그 개수 요약. 목록의 한 줄이 된다."""

    # 데이터셋 (시스템의 데이터셋 목록 한 줄)
    dataset: Dataset

    # 휴지통 밖 질의 · 문서 수
    query_count: int
    document_count: int

    # 판정 수 · 정답 · 오답 (휴지통 밖 질의 · 문서)
    judgment_count: int
    positive_count: int
    negative_count: int

    # 휴지통의 질의 · 문서 수
    trash_count: int

    # 가져오는 중인지
    importing: bool

    # 설정 (가져오기 전이면 None)
    settings: DatasetSettings | None


@dataclass(frozen=True)
class DatasetDetail:
    """데이터셋 하나의 자세한 정보."""

    summary: DatasetSummary


def entry_stage(settings: DatasetSettings | None) -> int | None:
    """설정의 모양으로 정한 흐름의 입구 단계. 가져오기 전이면 None."""
    return ENTRY_STAGE[Shape(settings.shape)] if settings is not None else None


# ---------- 데이터셋 ----------


async def list_datasets(db: AsyncSession) -> list[DatasetSummary]:
    """검색 데이터셋 목록을 개수 요약과 함께 돌려준다. 최근에 바뀐 것부터."""
    datasets = await datasets_service.list_datasets(db, module=MODULE_NAME)
    return await _summarize(db, datasets)


async def get_dataset(db: AsyncSession, *, dataset_id: int) -> Dataset:
    """검색 데이터셋 하나. 없거나 다른 모듈의 데이터셋이면 NotFoundError."""
    return await datasets_service.get_dataset(db, dataset_id=dataset_id, module=MODULE_NAME)


async def get_dataset_detail(db: AsyncSession, *, dataset_id: int) -> DatasetDetail:
    """데이터셋 하나의 개수 요약. 없으면 NotFoundError."""
    dataset = await get_dataset(db, dataset_id=dataset_id)
    [summary] = await _summarize(db, [dataset])
    return DatasetDetail(summary=summary)


async def delete_dataset(db: AsyncSession, *, dataset_id: int) -> None:
    """데이터셋과 그 안의 질의 · 문서 · 판정 · 뜻 분석 · 가져오기 기록을 모두 지운다.

    없으면 NotFoundError, 가져오거나 뜻 분석을 만드는 중이면 ConflictError. 임베딩 캐시는 남긴다.
    """
    await get_dataset(db, dataset_id=dataset_id)
    is_analyzing = await db.scalar(
        select(
            exists().where(
                Analysis.dataset_id == dataset_id, Analysis.status.in_(ANALYSIS_BUILDING_STATUSES)
            )
        )
    )
    if is_analyzing:
        raise ConflictError(DATASET_ANALYZING_MESSAGE)
    # 질의 · 문서 · 판정 · 분석은 데이터셋을 참조하는 외래 키(CASCADE)로 함께 지워진다.
    await datasets_service.delete_dataset(db, dataset_id=dataset_id, module=MODULE_NAME)
    await db.commit()


# ---------- 설정 ----------


async def get_settings(db: AsyncSession, *, dataset_id: int) -> DatasetSettings:
    """데이터셋 설정. 데이터셋이 없으면 NotFoundError, 아직 가져온 것이 없으면 NotFoundError."""
    await get_dataset(db, dataset_id=dataset_id)
    settings = await db.get(DatasetSettings, dataset_id, populate_existing=True)
    if settings is None:
        raise NotFoundError(NO_SETTINGS_MESSAGE)
    return settings


async def find_settings(db: AsyncSession, *, dataset_id: int) -> DatasetSettings | None:
    """데이터셋 설정. 아직 없으면 None (데이터셋이 있는지는 보지 않는다)."""
    return await db.get(DatasetSettings, dataset_id, populate_existing=True)


async def count_unmined_documents(db: AsyncSession, *, dataset_id: int) -> int:
    """모든 질의의 오답을 마지막으로 찾은 뒤 코퍼스에 생긴 문서 수(데이터 추가 · 나누기). 찾은 적이 없으면 0."""
    settings = await find_settings(db, dataset_id=dataset_id)
    if settings is None or settings.mined_document_id is None:
        return 0
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.dataset_id == dataset_id,
                DOCUMENT_ACTIVE,
                Document.id > settings.mined_document_id,
            )
        )
        or 0
    )


async def update_settings(
    db: AsyncSession, *, dataset_id: int, data: SettingsUpdate
) -> DatasetSettings:
    """설정을 고친다. 없으면 NotFoundError, 순위 범위가 뒤집히면 InvalidInputError."""
    settings = await get_settings(db, dataset_id=dataset_id)
    fields = data.model_dump(exclude_unset=True)
    # 도우미 실행 설정만 바꾸면 데이터는 그대로라 데이터셋 수정 시각을 건드리지 않는다
    # (건드리면 뜻 분석이 '분석 이후 변경'이 되어 도우미가 시작할 때 분석을 다시 만든다).
    is_run_setting_only = set(fields) <= RUN_SETTING_FIELDS
    if "target_model" in fields:
        settings.target_model = fields.pop("target_model") or None
    for name, value in fields.items():
        if value is not None:
            setattr(settings, name, value)
    if settings.mine_rank_from > settings.mine_rank_to:
        raise InvalidInputError(RANK_RANGE_MESSAGE)
    if not is_run_setting_only:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return await get_settings(db, dataset_id=dataset_id)


# ---------- 가져오기 시작 ----------


async def create_import(db: AsyncSession, *, data: ImportCreate) -> Import:
    """가져오기를 대기열에 넣는다. 새 데이터셋이면 함께 만들고, 기록과 작업을 한 번에 저장한다.

    데이터셋이 없으면 NotFoundError, 새 이름이 겹치면 ConflictError, 입력이 모자라면 InvalidInputError.
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
    """데이터셋마다 질의 · 문서 · 판정 수와 가져오는 중인지를 센다. 데이터셋 수와 상관없이 쿼리 몇 번."""
    dataset_ids = [dataset.id for dataset in datasets]
    query_counts = {
        row[0]: row[1:]
        for row in (
            await db.execute(
                select(
                    Query.dataset_id,
                    func.count().filter(QUERY_ACTIVE),
                    func.count().filter(QUERY_TRASHED),
                )
                .where(Query.dataset_id.in_(dataset_ids))
                .group_by(Query.dataset_id)
            )
        ).all()
    }
    document_counts = {
        row[0]: row[1:]
        for row in (
            await db.execute(
                select(
                    Document.dataset_id,
                    func.count().filter(DOCUMENT_ACTIVE),
                    func.count().filter(DOCUMENT_TRASHED),
                )
                .where(Document.dataset_id.in_(dataset_ids))
                .group_by(Document.dataset_id)
            )
        ).all()
    }
    judgment_counts = {
        row[0]: row[1:]
        for row in (
            await db.execute(
                select(
                    Judgment.dataset_id,
                    func.count(),
                    func.count().filter(IS_POSITIVE),
                    func.count().filter(IS_NEGATIVE),
                )
                .join(Query, Query.id == Judgment.query_id)
                .join(Document, Document.id == Judgment.document_id)
                .where(Judgment.dataset_id.in_(dataset_ids), QUERY_ACTIVE, DOCUMENT_ACTIVE)
                .group_by(Judgment.dataset_id)
            )
        ).all()
    }
    importing_ids = set(
        (
            await db.scalars(
                select(Import.dataset_id).where(
                    Import.dataset_id.in_(dataset_ids), Import.status.in_(IMPORTING_STATUSES)
                )
            )
        ).all()
    )
    settings_by_id = {
        settings.dataset_id: settings
        for settings in (
            await db.scalars(
                select(DatasetSettings).where(DatasetSettings.dataset_id.in_(dataset_ids))
            )
        ).all()
    }
    summaries = []
    for dataset in datasets:
        queries, trashed_queries = query_counts.get(dataset.id, (0, 0))
        documents, trashed_documents = document_counts.get(dataset.id, (0, 0))
        judgments, positives, negatives = judgment_counts.get(dataset.id, (0, 0, 0))
        summaries.append(
            DatasetSummary(
                dataset=dataset,
                query_count=queries,
                document_count=documents,
                judgment_count=judgments,
                positive_count=positives,
                negative_count=negatives,
                trash_count=trashed_queries + trashed_documents,
                importing=dataset.id in importing_ids,
                settings=settings_by_id.get(dataset.id),
            )
        )
    return summaries


async def duplicate_groups(db: AsyncSession, *, dataset_id: int) -> dict[int, int]:
    """코퍼스 문서마다 중복 묶음의 대표 번호(묶음에서 가장 작은 번호).

    본문이 같거나(해시) 가장 최근 뜻 분석의 근접 중복 쌍이면 한 묶음이다(이어진 쌍도 한 묶음).
    묶음은 서로 오답이 되지 않고, 정답 목록(BEIR qrels) · 질의 만들기(대표 하나)의 단위가 된다.
    """
    parent: dict[int, int] = {}

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    first_by_hash: dict[str, int] = {}
    for document_id, text_hash in await db.execute(
        select(Document.id, Document.text_hash)
        .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
        .order_by(Document.id)
    ):
        parent[document_id] = document_id
        first = first_by_hash.setdefault(text_hash, document_id)
        if first != document_id:
            union(first, document_id)
    analysis = await latest_done_analysis(db, dataset_id=dataset_id)
    if analysis is not None:
        for item_a, item_b in await db.execute(
            select(NearDuplicate.item_a, NearDuplicate.item_b).where(
                NearDuplicate.analysis_id == analysis.id,
                NearDuplicate.kind == ItemKind.DOCUMENT.value,
            )
        ):
            if item_a in parent and item_b in parent:
                union(item_a, item_b)
    return {document_id: find(document_id) for document_id in parent}


async def latest_done_analysis(db: AsyncSession, *, dataset_id: int) -> Analysis | None:
    """가장 최근에 다 만든 뜻 분석. 없으면 None."""
    return await db.scalar(
        select(Analysis)
        .where(Analysis.dataset_id == dataset_id, Analysis.status == "done")
        .order_by(Analysis.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
