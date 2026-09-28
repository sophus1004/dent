"""검색 모듈 API (/api/v1/retrieval). 요청을 받아 service에 넘기고, 결과를 응답 모양으로 바꿔 돌려준다.

파일 올리기 · 허깅페이스 미리 보기 · 가져오기 기록 · 작업 · 도우미 실행 · 내보낸 파일은 시스템 API가 맡는다.
도메인 예외는 잡지 않는다. api/error_handlers.py가 상태 코드로 바꾼다.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import examples as examples_service
from dent.modules.retrieval import exporting as exporting_service
from dent.modules.retrieval import generation as generation_service
from dent.modules.retrieval import helper as helper_service
from dent.modules.retrieval import helper_changes
from dent.modules.retrieval import mapping as mapping_service
from dent.modules.retrieval import mining as mining_service
from dent.modules.retrieval import overview as overview_service
from dent.modules.retrieval import records as records_service
from dent.modules.retrieval import repeats as repeats_service
from dent.modules.retrieval import semantic as semantic_service
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval import suggestions as suggestions_service
from dent.modules.retrieval.models import ItemKind, QuerySource, SuggestionKind
from dent.modules.retrieval.schemas import (
    CHUNK_OVERLAP_RANGE,
    MAX_TOKENS_RANGE,
    QUERIES_PER_CHUNK_RANGE,
    AnalysisRead,
    AnalysisStateRead,
    ChangedRead,
    DatasetRead,
    DatasetSummaryRead,
    DocumentBulkUpdate,
    DocumentDetailRead,
    DocumentPageRead,
    DocumentProblem,
    DocumentRead,
    DocumentUpdate,
    DocumentUse,
    ExportCreate,
    ExportPreviewRead,
    FieldMappingRead,
    HelperEstimateRead,
    HelperPermission,
    HelperStateRead,
    HelperUndo,
    HelperUndoRead,
    ImportCreate,
    JobStartedRead,
    JudgedQueryRead,
    JudgmentRead,
    JudgmentSet,
    MapMatchesRead,
    MappingSuggestCreate,
    MapPointsRead,
    MapTextRead,
    OverviewRead,
    QueryBulkUpdate,
    QueryDetailRead,
    QueryPageRead,
    QueryProblem,
    QueryRead,
    QueryUpdate,
    RankedDocumentRead,
    RepeatBulkUpdate,
    RepeatListRead,
    RepeatRead,
    RepeatSampleRead,
    RepeatUpdate,
    SettingsRead,
    SettingsUpdate,
    StatusFilter,
    SuggestionPageRead,
    SuggestionRead,
)
from dent.system import helper as system_helper
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

router = APIRouter(prefix=f"/{retrieval_service.MODULE_NAME}", tags=["검색"])

# 목록 한 쪽의 기본 크기
DEFAULT_PAGE_SIZE = 50

# 검색어의 최대 길이
SEARCH_MAX_LENGTH = 200

Db = Annotated[AsyncSession, Depends(get_db)]
PageLimit = Annotated[int, Query(ge=1, le=records_service.MAX_PAGE_SIZE)]
PageOffset = Annotated[int, Query(ge=0)]
Search = Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)]

# 휴지통 비우기의 대상
TrashKind = Literal["queries", "documents"]


# ---------- 데이터셋 · 설정 ----------


@router.get("/datasets")
async def list_datasets(db: Db) -> list[DatasetSummaryRead]:
    """검색 데이터셋 목록과 개수. 최근에 바뀐 것부터."""
    summaries = await retrieval_service.list_datasets(db)
    return [_dataset_summary_read(summary) for summary in summaries]


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: int, db: Db) -> DatasetRead:
    """검색 데이터셋 하나와 설정."""
    detail = await retrieval_service.get_dataset_detail(db, dataset_id=dataset_id)
    summary = _dataset_summary_read(detail.summary)
    settings = detail.summary.settings
    return DatasetRead(
        **summary.model_dump(),
        settings=SettingsRead.model_validate(settings) if settings else None,
    )


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(dataset_id: int, db: Db) -> None:
    """데이터셋 지우기(질의 · 문서 · 판정 · 뜻 분석 · 가져오기 기록까지). 가져오거나 분석하는 중이면 409."""
    await retrieval_service.delete_dataset(db, dataset_id=dataset_id)


@router.patch("/datasets/{dataset_id}/settings")
async def update_settings(dataset_id: int, data: SettingsUpdate, db: Db) -> SettingsRead:
    """데이터셋 설정 고치기 (학습할 모델 · 길이 · 오답 수 · 오답 찾기 · 구획 제목 · 되찾기 · 쉬운 쌍 상한)."""
    settings = await repeats_service.update_settings(db, dataset_id=dataset_id, data=data)
    return SettingsRead.model_validate(settings)


# ---------- 가져오기 ----------


@router.post("/mapping/suggest")
async def suggest_mapping(data: MappingSuggestCreate) -> FieldMappingRead:
    """필드 맞춤 짐작: 원본의 열 이름으로 모양과 칸을 고른다."""
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
    import_row = await retrieval_service.create_import(db, data=data)
    return ImportRead.model_validate(import_row)


# ---------- 진단 ----------


@router.get("/datasets/{dataset_id}/overview")
async def get_overview(dataset_id: int, db: Db) -> OverviewRead:
    """진단: 흐름 · 단계별 검사 · 기준 검색 점수 · 분포."""
    return await overview_service.get_overview(db, dataset_id=dataset_id)


# ---------- 질의 ----------


@router.get("/datasets/{dataset_id}/queries")
async def list_queries(
    dataset_id: int,
    db: Db,
    q: Search = None,
    source: QuerySource | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = "active",
    problem: QueryProblem | None = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    offset: PageOffset = 0,
) -> QueryPageRead:
    """질의 한 쪽. 거르기: 검색어 · 출처 · 상태 · 문제."""
    rows, total = await records_service.list_queries(
        db,
        dataset_id=dataset_id,
        q=q,
        source=source,
        status=status_filter,
        problem=problem,
        limit=limit,
        offset=offset,
    )
    return QueryPageRead(
        items=[_query_read(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/queries/{query_id}")
async def get_query(query_id: int, db: Db) -> QueryDetailRead:
    """질의 패널: 질의와 판정 · 기준 검색 순위(상위 10 + 10위 밖 판정)."""
    detail = await records_service.get_query_detail(db, query_id=query_id)
    return QueryDetailRead(
        query=_query_read(detail.row),
        first_positive_rank=detail.first_positive_rank,
        ranked=[_ranked_read(item) for item in detail.ranked],
        outside=[_ranked_read(item) for item in detail.outside],
        has_ranking=detail.has_ranking,
    )


@router.patch("/queries/{query_id}")
async def update_query(query_id: int, data: QueryUpdate, db: Db) -> QueryDetailRead:
    """질의 고치기 (글 · 분할 · 학습 제외 · 휴지통). 그 사이 고쳤으면 409."""
    await records_service.update_query(db, query_id=query_id, data=data)
    return await get_query(query_id, db)


@router.post("/datasets/{dataset_id}/queries/bulk")
async def bulk_update_queries(dataset_id: int, data: QueryBulkUpdate, db: Db) -> ChangedRead:
    """여러 질의에 한 번에 (학습 제외 · 포함 · 휴지통 · 되살리기 · 분할)."""
    changed = await records_service.bulk_update_queries(db, dataset_id=dataset_id, data=data)
    return ChangedRead(changed=changed)


# ---------- 문서 ----------


@router.get("/datasets/{dataset_id}/documents")
async def list_documents(
    dataset_id: int,
    db: Db,
    q: Search = None,
    use: DocumentUse | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = "active",
    problem: DocumentProblem | None = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    offset: PageOffset = 0,
) -> DocumentPageRead:
    """문서 한 쪽. 거르기: 검색어 · 쓰임 · 상태 · 문제."""
    rows, total = await records_service.list_documents(
        db,
        dataset_id=dataset_id,
        q=q,
        use=use,
        status=status_filter,
        problem=problem,
        limit=limit,
        offset=offset,
    )
    return DocumentPageRead(
        items=[_document_read(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/documents/{document_id}")
async def get_document(document_id: int, db: Db) -> DocumentDetailRead:
    """문서 패널: 문서와 쓰는 질의 · 가까운 질의 · 만든 질의 · 같은 원문의 청크."""
    detail = await records_service.get_document_detail(db, document_id=document_id)
    return DocumentDetailRead(
        document=_document_read(detail.row),
        uses=[_judged_query_read(item) for item in detail.uses],
        nearby=[_judged_query_read(item) for item in detail.nearby],
        synthetic=[_judged_query_read(item) for item in detail.synthetic],
        siblings=[records_service.brief(document) for document in detail.siblings],
    )


@router.patch("/documents/{document_id}")
async def update_document(document_id: int, data: DocumentUpdate, db: Db) -> DocumentDetailRead:
    """문서 고치기 (제목 · 본문 · 질의 안 만듦 · 휴지통). 그 사이 고쳤으면 409."""
    await records_service.update_document(db, document_id=document_id, data=data)
    return await get_document(document_id, db)


@router.post("/datasets/{dataset_id}/documents/bulk")
async def bulk_update_documents(dataset_id: int, data: DocumentBulkUpdate, db: Db) -> ChangedRead:
    """여러 문서에 한 번에 (휴지통 · 되살리기 · 질의 안 만듦 · 분할)."""
    changed = await records_service.bulk_update_documents(db, dataset_id=dataset_id, data=data)
    return ChangedRead(changed=changed)


# ---------- 반복 구간 ----------


@router.get("/datasets/{dataset_id}/repeats")
async def list_repeats(dataset_id: int, db: Db) -> RepeatListRead:
    """반복 구간 목록(메타 모양 먼저, 많이 든 것부터)과 결정별 수."""
    found = await repeats_service.list_repeats(db, dataset_id=dataset_id)
    return RepeatListRead(
        items=[RepeatRead.model_validate(item) for item in found.items],
        counts=found.counts,
        documents=found.documents,
        scanned_at=found.scanned_at,
    )


@router.patch("/repeats/{repeat_id}")
async def decide_repeat(repeat_id: int, data: RepeatUpdate, db: Db) -> RepeatRead:
    """반복 구간 고르기 (떼기 · 남김 · 고르기 전). 든 문서의 학습 글을 다시 만든다."""
    repeat = await repeats_service.decide_repeat(db, repeat_id=repeat_id, decision=data.decision)
    return RepeatRead.model_validate(repeat)


@router.post("/datasets/{dataset_id}/repeats/bulk")
async def decide_repeats(dataset_id: int, data: RepeatBulkUpdate, db: Db) -> ChangedRead:
    """반복 구간 여럿을 한 번에 고르기 (떼기 · 남김 · 고르기 전 · 제안대로). changed는 결정이 바뀐 수."""
    changed = await repeats_service.decide_repeats(
        db,
        dataset_id=dataset_id,
        repeat_ids=data.repeat_ids,
        decision=data.decision,
        follow_suggestion=data.follow_suggestion,
    )
    return ChangedRead(changed=changed)


@router.get("/repeats/{repeat_id}/samples")
async def get_repeat_samples(repeat_id: int, db: Db) -> list[RepeatSampleRead]:
    """반복 구간이 든 보기 문서 몇 개와 그 안의 구간(앞뒤 글과 함께)."""
    samples = await repeats_service.repeat_samples(db, repeat_id=repeat_id)
    return [
        RepeatSampleRead(
            document_id=sample.document_id,
            title=sample.title,
            before=sample.before,
            span=sample.span,
            after=sample.after,
        )
        for sample in samples
    ]


@router.post("/datasets/{dataset_id}/repeats/scan", status_code=status.HTTP_201_CREATED)
async def scan_repeats(dataset_id: int, db: Db) -> JobStartedRead:
    """반복 구간 살피기: 다시 찾고 모든 문서의 학습 글 · 표시를 다시 만드는 작업을 넣는다."""
    job_id = await repeats_service.start_scan(db, dataset_id=dataset_id)
    return JobStartedRead(job_id=job_id)


# ---------- 판정 ----------


@router.put("/queries/{query_id}/judgments/{document_id}")
async def set_judgment(query_id: int, document_id: int, data: JudgmentSet, db: Db) -> JudgmentRead:
    """판정 두기 (등급 0 = 오답 · 1~3 = 정답). 이 쌍의 대기 중인 제안은 닫는다."""
    judgment = await records_service.set_judgment(
        db, query_id=query_id, document_id=document_id, grade=data.grade
    )
    return JudgmentRead.model_validate(judgment)


@router.delete(
    "/queries/{query_id}/judgments/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_judgment(query_id: int, document_id: int, db: Db) -> None:
    """판정 떼기 (모름으로). 판정이 없으면 404."""
    await records_service.delete_judgment(db, query_id=query_id, document_id=document_id)


@router.delete("/datasets/{dataset_id}/trash")
async def empty_trash(
    dataset_id: int, db: Db, kind: Annotated[TrashKind, Query()] = "queries"
) -> ChangedRead:
    """휴지통 비우기: 휴지통의 질의(또는 문서)를 영구히 지운다. 되돌릴 수 없다."""
    changed = await records_service.empty_trash(db, dataset_id=dataset_id, kind=kind)
    return ChangedRead(changed=changed)


@router.post("/documents/{document_id}/split")
async def split_document(document_id: int, db: Db) -> ChangedRead:
    """문서 나누기: 원문을 가리고 청크를 더한다. changed는 만든 청크 수. 길지 않으면 422."""
    pieces = await records_service.split_document(db, document_id=document_id)
    return ChangedRead(changed=pieces)


@router.post("/documents/{document_id}/unsplit")
async def unsplit_document(document_id: int, db: Db) -> ChangedRead:
    """나누기 되돌리기: 청크를 지우고 원문을 되살린다. changed는 지운 청크 수."""
    removed = await records_service.unsplit_document(db, document_id=document_id)
    return ChangedRead(changed=removed)


# ---------- 뜻 분석 · 지도 ----------


@router.get("/datasets/{dataset_id}/map")
async def get_analysis(dataset_id: int, db: Db) -> AnalysisStateRead:
    """뜻 분석 상태: 다 만든 것 · 도는(또는 막 끝난) 것 · 분석 이후 바뀌었는지 · 모델."""
    state = await semantic_service.get_analysis_state(db, dataset_id=dataset_id)
    return _analysis_state_read(state)


@router.post("/datasets/{dataset_id}/map")
async def start_analysis(dataset_id: int, db: Db) -> AnalysisStateRead:
    """뜻 분석 시작. 이미 도는 중이면 409, 임베딩 미연결 · 문서 없음이면 422."""
    state = await semantic_service.start_analysis(db, dataset_id=dataset_id)
    return _analysis_state_read(state)


@router.post("/datasets/{dataset_id}/map/cancel")
async def cancel_analysis(dataset_id: int, db: Db) -> AnalysisStateRead:
    """도는 뜻 분석 멈추기. 도는 것이 없으면 409."""
    state = await semantic_service.cancel_analysis(db, dataset_id=dataset_id)
    return _analysis_state_read(state)


@router.get("/datasets/{dataset_id}/map/points")
async def get_map_points(dataset_id: int, db: Db) -> MapPointsRead:
    """지도의 점들 (칸마다 목록). 뜻 분석이 없으면 404."""
    return await semantic_service.list_map_points(db, dataset_id=dataset_id)


@router.get("/datasets/{dataset_id}/map/matches")
async def get_map_matches(
    dataset_id: int, db: Db, q: Annotated[str, Query(min_length=1, max_length=SEARCH_MAX_LENGTH)]
) -> MapMatchesRead:
    """검색어가 든 질의 · 문서 번호들 (지도 강조)."""
    query_ids, document_ids = await semantic_service.map_matches(db, dataset_id=dataset_id, q=q)
    return MapMatchesRead(query_ids=query_ids, document_ids=document_ids)


@router.get("/datasets/{dataset_id}/map/text")
async def get_map_text(dataset_id: int, kind: ItemKind, item_id: int, db: Db) -> MapTextRead:
    """지도 점 하나의 글 (말풍선)."""
    text, title = await semantic_service.map_text(
        db, dataset_id=dataset_id, kind=kind, item_id=item_id
    )
    return MapTextRead(text=text, title=title)


# ---------- 제안 ----------


@router.get("/datasets/{dataset_id}/suggestions")
async def list_suggestions(
    dataset_id: int,
    db: Db,
    kind: SuggestionKind | None = None,
    decided: bool = False,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    offset: PageOffset = 0,
) -> SuggestionPageRead:
    """제안 한 쪽과 종류별 대기 수. 뜻 분석이 없으면 404."""
    page = await suggestions_service.list_suggestions(
        db, dataset_id=dataset_id, kind=kind, decided=decided, limit=limit, offset=offset
    )
    return SuggestionPageRead(
        items=[
            SuggestionRead(
                id=row.suggestion.id,
                kind=SuggestionKind(row.suggestion.kind),
                query_id=row.suggestion.query_id,
                query_text=row.query_text,
                document=records_service.brief(row.document),
                grade=row.grade,
                rank=row.suggestion.rank,
                similarity=row.suggestion.similarity,
                jev_probability=row.suggestion.jev_probability,
                is_confirmed=row.suggestion.is_confirmed,
                decision=row.suggestion.decision,  # type: ignore[arg-type]
            )
            for row in page.rows
        ],
        total=page.total,
        limit=limit,
        offset=offset,
        pending_counts=page.pending_counts,
        confirmed_pending=page.confirmed_pending,
    )


@router.post("/suggestions/{suggestion_id}/accept")
async def accept_suggestion(suggestion_id: int, db: Db) -> ChangedRead:
    """제안 수락 (판정을 바꾼다). 이미 판단했으면 409."""
    changed = await suggestions_service.accept_suggestion(db, suggestion_id=suggestion_id)
    return ChangedRead(changed=changed)


@router.post("/suggestions/{suggestion_id}/keep")
async def keep_suggestion(suggestion_id: int, db: Db) -> ChangedRead:
    """제안 유지 (판정 그대로, 다음 분석에서도 묻지 않음). 이미 판단했으면 409."""
    changed = await suggestions_service.keep_suggestion(db, suggestion_id=suggestion_id)
    return ChangedRead(changed=changed)


@router.post("/datasets/{dataset_id}/suggestions/accept-confirmed")
async def accept_confirmed_suggestions(dataset_id: int, db: Db) -> ChangedRead:
    """Jev가 확인한 대기 제안을 모두 수락한다."""
    changed = await suggestions_service.accept_confirmed(db, dataset_id=dataset_id)
    return ChangedRead(changed=changed)


# ---------- 도우미 ----------


@router.get("/datasets/{dataset_id}/helper")
async def get_helper(dataset_id: int, db: Db) -> HelperStateRead:
    """데이터셋의 가장 최근 도우미 실행과 되돌린 카드 번호들."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    run = await system_helper.latest_run(
        db, module=retrieval_service.MODULE_NAME, dataset_id=dataset_id
    )
    undone = await helper_changes.undone_event_ids(db, run_id=run.id) if run else []
    return HelperStateRead(
        run=HelperRunRead.model_validate(run) if run else None, undone_events=undone
    )


@router.post("/datasets/{dataset_id}/helper", status_code=status.HTTP_201_CREATED)
async def start_helper(dataset_id: int, db: Db) -> HelperRunRead:
    """도우미 시작. LLM이 없으면 422, 이미 도는 중이면 409."""
    run = await helper_service.start_helper(db, dataset_id=dataset_id)
    return HelperRunRead.model_validate(run)


@router.post("/helper/{run_id}/undo")
async def undo_helper(run_id: int, data: HelperUndo, db: Db) -> HelperUndoRead:
    """도우미가 바꾼 것을 되돌린다(카드 하나 또는 실행 전체). 도는 중이면 409."""
    result = await helper_service.undo_helper(db, run_id=run_id, event_id=data.event_id)
    return HelperUndoRead(reverted=result.reverted, skipped=result.skipped)


@router.post("/helper/{run_id}/permission")
async def answer_helper_permission(run_id: int, data: HelperPermission, db: Db) -> HelperRunRead:
    """허락에 답한다 (문서 나누기 · 질의 만들기). 기다리는 중이 아니면 409."""
    run = await helper_service.answer_permission(
        db, run_id=run_id, approve=data.approve, action=data.action
    )
    return HelperRunRead.model_validate(run)


@router.get("/datasets/{dataset_id}/helper/estimate")
async def estimate_helper(
    dataset_id: int,
    db: Db,
    chunk_tokens: Annotated[int, Query(ge=MAX_TOKENS_RANGE[0], le=MAX_TOKENS_RANGE[1])],
    overlap: Annotated[int, Query(ge=CHUNK_OVERLAP_RANGE[0], le=CHUNK_OVERLAP_RANGE[1])] = 0,
    per_chunk: Annotated[
        int, Query(ge=QUERIES_PER_CHUNK_RANGE[0], le=QUERIES_PER_CHUNK_RANGE[1])
    ] = 2,
) -> HelperEstimateRead:
    """도우미 실행 설정 카드의 어림: 고른 값으로 나누면 청크 몇 · 질의 몇 · LLM 토큰 얼마. 없으면 404."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    found = await generation_service.estimate(
        db, dataset_id=dataset_id, chunk_tokens=chunk_tokens, overlap=overlap, per_chunk=per_chunk
    )
    return HelperEstimateRead(**vars(found))


@router.get("/datasets/{dataset_id}/helper/left")
async def list_helper_left(dataset_id: int, db: Db) -> HelperLeftRead:
    """도우미가 끝난 뒤 남은 것 (지금 진단으로 센다). 실행이 없으면 빈 목록. 데이터셋이 없으면 404."""
    items = await helper_service.left_items(db, dataset_id=dataset_id)
    return HelperLeftRead(items=[HelperLeftItemRead(**vars(item)) for item in items])


@router.post("/datasets/{dataset_id}/helper/fix", status_code=status.HTTP_201_CREATED)
async def fix_helper(dataset_id: int, data: HelperFixCreate, db: Db) -> HelperRunRead:
    """AI로 고치기 (고른 남은 것). LLM이 없거나 고칠 수 없는 것을 고르면 422, 도는 중이면 409."""
    run = await helper_service.start_fix(db, dataset_id=dataset_id, keys=data.keys)
    return HelperRunRead.model_validate(run)


# ---------- 내보내기 · 다시 찾기 ----------


@router.post("/datasets/{dataset_id}/exports/preview")
async def preview_export(dataset_id: int, data: ExportCreate, db: Db) -> ExportPreviewRead:
    """내보내기 미리 보기: 한 줄 · 파일마다 수 · 출처별 판정 수 · 남은 주의."""
    return await exporting_service.preview(db, dataset_id=dataset_id, data=data)


@router.post("/datasets/{dataset_id}/exports", status_code=status.HTTP_201_CREATED)
async def create_export(dataset_id: int, data: ExportCreate, db: Db) -> list[ExportRead]:
    """내보내기 시작 (고른 모양마다 하나). 심각이 남았으면 409. 파일은 /api/v1/exports/{id}/file."""
    exports = await exporting_service.create_exports(db, dataset_id=dataset_id, data=data)
    return [ExportRead.model_validate(export) for export in exports]


@router.post("/datasets/{dataset_id}/remine", status_code=status.HTTP_201_CREATED)
async def remine(dataset_id: int, db: Db) -> JobStartedRead:
    """오답 다시 찾기: 찾은 오답을 떼고 지금 설정으로 다시 찾는 작업을 넣는다."""
    job_id = await mining_service.start_mining(db, dataset_id=dataset_id, remine=True)
    return JobStartedRead(job_id=job_id)


@router.post("/datasets/{dataset_id}/mine", status_code=status.HTTP_201_CREATED)
async def mine(dataset_id: int, db: Db) -> JobStartedRead:
    """오답 찾기: 오답 풀이 모자란 질의만 채우는 작업을 넣는다."""
    job_id = await mining_service.start_mining(db, dataset_id=dataset_id, remine=False)
    return JobStartedRead(job_id=job_id)


@router.post("/datasets/{dataset_id}/negative-scan", status_code=status.HTTP_201_CREATED)
async def scan_negatives(dataset_id: int, db: Db) -> JobStartedRead:
    """오답 훑기: 순위 묶음 × 문턱의 거짓 오답 비율을 재는 작업을 넣는다. 결과는 설정의 negative_scan."""
    job_id = await mining_service.start_negative_scan(db, dataset_id=dataset_id)
    return JobStartedRead(job_id=job_id)


# ---------- 응답 모양 ----------


def _analysis_state_read(state: semantic_service.AnalysisState) -> AnalysisStateRead:
    return AnalysisStateRead(
        done=AnalysisRead.model_validate(state.done) if state.done else None,
        run=AnalysisRead.model_validate(state.run) if state.run else None,
        outdated=state.outdated,
        model=state.model,
    )


def _dataset_summary_read(summary: retrieval_service.DatasetSummary) -> DatasetSummaryRead:
    dataset = summary.dataset
    settings = summary.settings
    return DatasetSummaryRead(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        query_count=summary.query_count,
        document_count=summary.document_count,
        judgment_count=summary.judgment_count,
        positive_count=summary.positive_count,
        negative_count=summary.negative_count,
        trash_count=summary.trash_count,
        importing=summary.importing,
        shape=settings.shape if settings else None,  # type: ignore[arg-type]
        entry_stage=retrieval_service.entry_stage(settings),
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


def _query_read(row: records_service.QueryRow) -> QueryRead:
    query = row.query
    return QueryRead(
        id=query.id,
        dataset_id=query.dataset_id,
        text=query.text,
        source=QuerySource(query.source),
        source_document_id=query.source_document_id,
        answer=query.answer,
        exclude_reason=query.exclude_reason,  # type: ignore[arg-type]
        is_trashed=query.trashed_at is not None,
        trashed_at=query.trashed_at,
        row_version=query.row_version,
        extra=query.extra,
        import_id=query.import_id,
        created_at=query.created_at,
        updated_at=query.updated_at,
        positive_count=row.positive_count,
        negative_count=row.negative_count,
        positives=row.positives,
        marks=row.marks,
    )


def _document_read(row: records_service.DocumentRow) -> DocumentRead:
    document = row.document
    return DocumentRead(
        id=document.id,
        dataset_id=document.dataset_id,
        doc_key=document.doc_key,
        title=document.title,
        text=document.text,
        header=document.header,
        section=document.section,
        group_key=document.group_key,
        training_text=document.training_text,
        token_count=document.token_count,
        source_document_id=document.source_document_id,
        chunk_index=document.chunk_index,
        skip_generation=document.skip_generation,
        is_trashed=document.trashed_at is not None,
        is_replaced=document.replaced_at is not None,
        row_version=document.row_version,
        extra=document.extra,
        import_id=document.import_id,
        created_at=document.created_at,
        updated_at=document.updated_at,
        positive_count=row.positive_count,
        negative_count=row.negative_count,
        synthetic_count=row.synthetic_count,
        marks=row.marks,
    )


def _ranked_read(item: records_service.RankedItem) -> RankedDocumentRead:
    return RankedDocumentRead(
        document=records_service.brief(item.document),
        grade=item.grade,
        source=item.source,  # type: ignore[arg-type]
        rank=item.rank,
        similarity=item.similarity,
        flag=item.flag,  # type: ignore[arg-type]
        jev_probability=item.jev_probability,
    )


def _judged_query_read(item: records_service.JudgedQuery) -> JudgedQueryRead:
    return JudgedQueryRead(
        query_id=item.query.id,
        text=item.query.text,
        grade=item.grade,
        rank=item.rank,
        similarity=item.similarity,
        jev_probability=item.jev_probability,
    )
