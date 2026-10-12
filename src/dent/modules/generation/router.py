"""생성 모듈 API (/api/v1/generation). 요청을 받아 service에 넘기고, 결과를 응답 모양으로 바꿔 돌려준다.

파일 올리기 · 허깅페이스 미리 보기 · 데이터셋 목록 · 가져오기 기록 · 작업 · 내려받기는 시스템 API가 맡는다.
도메인 예외는 잡지 않는다. api/error_handlers.py가 상태 코드로 바꾼다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import conversations as conversations_service
from dent.modules.generation import examples as examples_service
from dent.modules.generation import exporting as exporting_service
from dent.modules.generation import helper as helper_agent
from dent.modules.generation import mapping as mapping_service
from dent.modules.generation import overview as overview_service
from dent.modules.generation import semantic as semantic_service
from dent.modules.generation import service as generation_service
from dent.modules.generation.rules import CheckKey
from dent.modules.generation.schemas import (
    AnalysisRead,
    AnalysisStateRead,
    ChangedRead,
    ChangeRead,
    ConversationBulkUpdate,
    ConversationPageRead,
    ConversationRead,
    ConversationStatusFilter,
    DatasetRead,
    DatasetSummaryRead,
    DecideAllCreate,
    DecisionCreate,
    DecisionRead,
    ExportCreate,
    ExportPreviewRead,
    FieldMappingRead,
    HelperStateRead,
    HelperUndo,
    HelperUndoRead,
    ImportCreate,
    MapMatchesRead,
    MappingSuggestCreate,
    MapPointsRead,
    MapTextRead,
    NearPartnerRead,
    OverviewRead,
    ProblemFilter,
    ReviewQueueRead,
)
from dent.system import helper as helper_service
from dent.system.db import get_db
from dent.system.schemas import (
    ExampleRead,
    ExportRead,
    HelperFixCreate,
    HelperLeftItemRead,
    HelperLeftRead,
    HelperRunRead,
    ImportRead,
)

router = APIRouter(prefix=f"/{generation_service.MODULE_NAME}", tags=["생성"])

# 대화 목록 한 쪽의 기본 크기
DEFAULT_PAGE_SIZE = 50

# 검색어의 최대 길이
SEARCH_MAX_LENGTH = 200

Db = Annotated[AsyncSession, Depends(get_db)]


# ---------- 데이터셋 ----------


@router.get("/datasets")
async def list_datasets(db: Db) -> list[DatasetSummaryRead]:
    """생성 데이터셋 목록과 개수. 최근에 바뀐 것부터."""
    summaries = await generation_service.list_datasets(db)
    return [_dataset_summary_read(summary) for summary in summaries]


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: int, db: Db) -> DatasetRead:
    """생성 데이터셋 하나."""
    summary = await generation_service.get_dataset_summary(db, dataset_id=dataset_id)
    return DatasetRead(
        **_dataset_summary_read(summary).model_dump(), entry_stage=generation_service.ENTRY_STAGE
    )


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(dataset_id: int, db: Db) -> None:
    """데이터셋 지우기(대화 · 결정 기록 · 가져오기 기록까지). 가져오는 중이면 409."""
    await generation_service.delete_dataset(db, dataset_id=dataset_id)


# ---------- 가져오기 ----------


@router.post("/mapping/suggest")
async def suggest_mapping(data: MappingSuggestCreate) -> FieldMappingRead:
    """필드 맞춤 짐작: 원본의 열 이름으로 모양(messages · ShareGPT · Alpaca · 턴마다 칸)과 칸을 고른다."""
    return mapping_service.suggest_mapping(data.columns)


@router.get("/examples")
async def list_examples(db: Db) -> list[ExampleRead]:
    """내장 예시 데이터 목록 (심은 문제 · 이미 넣었으면 그 데이터셋 번호)."""
    return await examples_service.list_examples(db)


@router.post("/examples/{key}", status_code=status.HTTP_201_CREATED)
async def install_example(key: str, db: Db) -> ImportRead:
    """내장 예시를 새 데이터셋으로 가져오기 시작. 없는 예시면 404, 이미 넣었으면 409."""
    import_row = await examples_service.install_example(db, key=key)
    return ImportRead.model_validate(import_row)


@router.post("/imports", status_code=status.HTTP_201_CREATED)
async def create_import(data: ImportCreate, db: Db) -> ImportRead:
    """가져오기 시작. 새 데이터셋이면 함께 만든다. 진행률은 /api/v1/jobs/{job_id}로 본다."""
    import_row = await generation_service.create_import(db, data=data)
    return ImportRead.model_validate(import_row)


# ---------- 진단 ----------


@router.get("/datasets/{dataset_id}/overview")
async def get_overview(dataset_id: int, db: Db) -> OverviewRead:
    """데이터셋 진단: 검사마다 문제가 남은 대화 수 · 등급과 흐름의 지금 단계."""
    return await overview_service.get_overview(db, dataset_id=dataset_id)


# ---------- 대화 · 검토 ----------


@router.get("/datasets/{dataset_id}/conversations")
async def list_conversations(
    dataset_id: int,
    db: Db,
    conversation_status: Annotated[ConversationStatusFilter, Query(alias="status")] = "active",
    problem: ProblemFilter | None = None,
    q: Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=conversations_service.MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConversationPageRead:
    """대화 목록. 상태 · 문제(검사 이름 · any · none) · 검색어로 거른다."""
    page = await conversations_service.list_conversations(
        db,
        dataset_id=dataset_id,
        status=conversation_status,
        problem=problem,
        q=q,
        limit=limit,
        offset=offset,
    )
    return ConversationPageRead(
        items=[conversations_service.conversation_read(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int, db: Db) -> ConversationRead:
    """대화 한 건 (메시지 전체 · 남은 문제)."""
    item = await conversations_service.get_conversation(db, prompt_id=conversation_id)
    return conversations_service.conversation_read(item)


@router.get("/datasets/{dataset_id}/review")
async def get_review_queue(dataset_id: int, check: CheckKey, db: Db) -> ReviewQueueRead:
    """검토 큐: 한 검사의 문제가 남은 대화 번호들(번호 순)과 전체 수."""
    ids, total = await conversations_service.review_queue(db, dataset_id=dataset_id, check=check)
    return ReviewQueueRead(check=check, ids=ids, total=total)


@router.post("/conversations/{conversation_id}/decide")
async def decide(conversation_id: int, data: DecisionCreate, db: Db) -> DecisionRead:
    """검토 결정 하나(빼기 · 그대로 두기 · 가리기 · 자르기). 할 수 없는 결정이면 422, 이미 풀렸으면 409."""
    item, change = await conversations_service.decide(db, prompt_id=conversation_id, data=data)
    return DecisionRead(
        conversation=conversations_service.conversation_read(item),
        change=conversations_service.change_read(change),
    )


@router.post("/datasets/{dataset_id}/decide-all")
async def decide_all(dataset_id: int, data: DecideAllCreate, db: Db) -> ChangedRead:
    """한 검사의 남은 대화 모두에 같은 결정(모두 빼기 · 모두 그대로 두기). changed는 바꾼 수."""
    changed = await conversations_service.decide_all(db, dataset_id=dataset_id, data=data)
    return ChangedRead(changed=changed)


@router.get("/datasets/{dataset_id}/changes")
async def list_changes(
    dataset_id: int,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=conversations_service.MAX_PAGE_SIZE)] = (
        conversations_service.CHANGE_PAGE_SIZE
    ),
) -> list[ChangeRead]:
    """되돌리지 않은 결정 기록, 최근 것부터."""
    changes = await conversations_service.list_changes(db, dataset_id=dataset_id, limit=limit)
    return [conversations_service.change_read(change) for change in changes]


@router.post("/changes/{change_id}/undo")
async def undo_change(change_id: int, db: Db) -> ConversationRead:
    """결정 하나 되돌리기. 이미 되돌렸거나 그 뒤에 대화가 바뀌었으면 409."""
    item = await conversations_service.undo_change(db, change_id=change_id)
    return conversations_service.conversation_read(item)


@router.get("/conversations/{conversation_id}/near")
async def list_near_partners(conversation_id: int, db: Db) -> list[NearPartnerRead]:
    """다 만든 뜻 분석에서 이 대화와 뜻이 가까운 짝들. 분석이 없으면 빈 목록."""
    partners = await semantic_service.near_partners(db, prompt_id=conversation_id)
    return [
        NearPartnerRead(
            prompt_id=partner.prompt_id,
            similarity=partner.similarity,
            text=partner.text,
            trashed=partner.trashed,
        )
        for partner in partners
    ]


@router.post("/datasets/{dataset_id}/conversations/bulk")
async def bulk_update_conversations(
    dataset_id: int, data: ConversationBulkUpdate, db: Db
) -> ChangedRead:
    """여러 대화를 휴지통에 넣거나 되살린다."""
    changed = await conversations_service.bulk_update(db, dataset_id=dataset_id, data=data)
    return ChangedRead(changed=changed)


@router.delete("/datasets/{dataset_id}/trash")
async def empty_trash(dataset_id: int, db: Db) -> ChangedRead:
    """휴지통 비우기: 휴지통의 대화를 영구히 지운다. changed는 지운 수."""
    deleted = await conversations_service.empty_trash(db, dataset_id=dataset_id)
    return ChangedRead(changed=deleted)


# ---------- 내보내기 ----------


# ---------- 뜻 분석 ----------


@router.get("/datasets/{dataset_id}/map")
async def get_analysis(dataset_id: int, db: Db) -> AnalysisStateRead:
    """뜻 분석 상태: 다 만든 것 · 도는 것 · 분석 이후 바뀌었는지."""
    state = await semantic_service.get_analysis_state(db, dataset_id=dataset_id)
    return _analysis_state_read(state)


@router.post("/datasets/{dataset_id}/map")
async def start_analysis(dataset_id: int, db: Db) -> AnalysisStateRead:
    """뜻 분석 만들기. 임베딩 미연결 · 대화 없음이면 422, 이미 만드는 중이면 409."""
    state = await semantic_service.start_analysis(db, dataset_id=dataset_id)
    return _analysis_state_read(state)


@router.post("/datasets/{dataset_id}/map/cancel")
async def cancel_analysis(dataset_id: int, db: Db) -> AnalysisStateRead:
    """만드는 중인 뜻 분석 멈추기. 없으면 409."""
    state = await semantic_service.cancel_analysis(db, dataset_id=dataset_id)
    return _analysis_state_read(state)


@router.get("/datasets/{dataset_id}/map/points")
async def get_map_points(dataset_id: int, db: Db) -> MapPointsRead:
    """의미 지도의 점들. 분석이 없으면 404."""
    return await semantic_service.list_map_points(db, dataset_id=dataset_id)


@router.get("/datasets/{dataset_id}/map/matches")
async def get_map_matches(
    dataset_id: int, q: Annotated[str, Query(min_length=1, max_length=SEARCH_MAX_LENGTH)], db: Db
) -> MapMatchesRead:
    """검색어가 든 대화 번호들 (지도 강조)."""
    ids = await semantic_service.map_matches(db, dataset_id=dataset_id, q=q)
    return MapMatchesRead(ids=ids)


@router.get("/datasets/{dataset_id}/map/text")
async def get_map_text(dataset_id: int, conversation_id: int, db: Db) -> MapTextRead:
    """지도 점 하나의 글 (말풍선)."""
    text = await semantic_service.map_text(db, dataset_id=dataset_id, prompt_id=conversation_id)
    return MapTextRead(text=text)


# ---------- LLM 도우미 ----------


@router.get("/datasets/{dataset_id}/helper")
async def get_helper(dataset_id: int, db: Db) -> HelperStateRead:
    """데이터셋의 가장 최근 도우미 실행과 되돌린 카드 번호들. 데이터셋이 없으면 404."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    run = await helper_service.latest_run(
        db, module=generation_service.MODULE_NAME, dataset_id=dataset_id
    )
    if run is None:
        return HelperStateRead(run=None, undone_events=[])
    undone = await helper_agent.undone_event_ids(db, run_id=run.id)
    return HelperStateRead(run=HelperRunRead.model_validate(run), undone_events=undone)


@router.post("/datasets/{dataset_id}/helper", status_code=status.HTTP_201_CREATED)
async def start_helper(dataset_id: int, db: Db) -> HelperRunRead:
    """도우미 시작. 뜻 분석이 없거나 오래됐으면 먼저 다시 만든다. LLM이 없으면 422, 이미 도는 중이면 409."""
    run = await helper_agent.start_helper(db, dataset_id=dataset_id)
    return HelperRunRead.model_validate(run)


@router.post("/helper/{run_id}/undo")
async def undo_helper(run_id: int, db: Db, data: HelperUndo | None = None) -> HelperUndoRead:
    """도우미가 바꾼 것을 되돌린다(event_id가 있으면 그 카드만). 도는 중이면 409."""
    result = await helper_agent.undo_helper(
        db, run_id=run_id, event_id=data.event_id if data is not None else None
    )
    return HelperUndoRead(reverted=result.reverted, skipped=result.skipped)


@router.get("/datasets/{dataset_id}/helper/left")
async def list_helper_left(dataset_id: int, db: Db) -> HelperLeftRead:
    """도우미가 끝난 뒤 남은 것 (지금 진단으로 센다). 실행이 없으면 빈 목록. 데이터셋이 없으면 404."""
    items = await helper_agent.left_items(db, dataset_id=dataset_id)
    return HelperLeftRead(items=[HelperLeftItemRead(**vars(item)) for item in items])


@router.post("/datasets/{dataset_id}/helper/fix", status_code=status.HTTP_201_CREATED)
async def fix_helper(dataset_id: int, data: HelperFixCreate, db: Db) -> HelperRunRead:
    """AI로 고치기 (고른 남은 것). LLM이 없거나 고칠 수 없는 것을 고르면 422, 도는 중이면 409."""
    run = await helper_agent.start_fix(db, dataset_id=dataset_id, keys=data.keys)
    return HelperRunRead.model_validate(run)


@router.post("/datasets/{dataset_id}/exports/preview")
async def preview_export(dataset_id: int, data: ExportCreate, db: Db) -> ExportPreviewRead:
    """내보내기 미리 보기: 첫 줄 · 파일마다 대화 수 · 남은 심각 · 주의 · 막힘."""
    return await exporting_service.preview(db, dataset_id=dataset_id, data=data)


@router.post("/datasets/{dataset_id}/exports", status_code=status.HTTP_201_CREATED)
async def create_export(dataset_id: int, data: ExportCreate, db: Db) -> list[ExportRead]:
    """내보내기 시작(고른 모양마다 하나). 심각이 남으면 409, 넣을 대화가 없으면 422."""
    exports = await exporting_service.create_exports(db, dataset_id=dataset_id, data=data)
    return [ExportRead.model_validate(export) for export in exports]


# ---------- 응답 모양으로 바꾸기 ----------


def _analysis_state_read(state: semantic_service.AnalysisState) -> AnalysisStateRead:
    return AnalysisStateRead(
        done=AnalysisRead.model_validate(state.done) if state.done else None,
        run=AnalysisRead.model_validate(state.run) if state.run else None,
        outdated=state.outdated,
        model=state.model,
    )


def _dataset_summary_read(summary: generation_service.DatasetSummary) -> DatasetSummaryRead:
    """데이터셋과 개수 요약을 목록 한 줄로 바꾼다."""
    dataset = summary.dataset
    return DatasetSummaryRead(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        conversation_count=summary.conversation_count,
        problem_count=summary.problem_count,
        trash_count=summary.trash_count,
        importing=summary.importing,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )
