"""분류 모듈 API (/api/v1/classification). 요청을 받아 service에 넘기고, 결과를 응답 모양으로 바꿔 돌려준다.

파일 올리기·허깅페이스 미리 보기·데이터셋 목록·가져오기 기록·작업은 시스템 API(/api/v1/uploads 등)가 맡는다.
도메인 예외는 잡지 않는다. api/error_handlers.py가 상태 코드로 바꾼다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import examples as examples_service
from dent.modules.classification import exporting as exporting_service
from dent.modules.classification import helper as helper_agent
from dent.modules.classification import helper_tools
from dent.modules.classification import mapping as mapping_service
from dent.modules.classification import overview as overview_service
from dent.modules.classification import records as records_service
from dent.modules.classification import semantic_checks as checks_service
from dent.modules.classification import semantic_map as map_service
from dent.modules.classification import service as classification_service
from dent.modules.classification.models import Map
from dent.modules.classification.schemas import (
    ChangedRead,
    DatasetRead,
    DatasetSummaryRead,
    ExportCreate,
    ExportPreviewRead,
    FieldMappingRead,
    HelperStateRead,
    HelperUndo,
    HelperUndoRead,
    ImportCreate,
    JevCheckRead,
    LabelCreate,
    LabelRead,
    LabelUpdate,
    MapChecksRead,
    MapLabelRead,
    MapMatchesRead,
    MappingSuggestCreate,
    MapPointsRead,
    MapRead,
    MapStateRead,
    NearDuplicateCountsRead,
    NearDuplicateRead,
    OverviewRead,
    ProblemFilter,
    RecordBulkUpdate,
    RecordPageRead,
    RecordRead,
    RecordStatusFilter,
    RecordUpdate,
    SemanticGradesRead,
    SettingsRead,
    SettingsUpdate,
    SkewLabelRead,
    SuspectAccept,
    SuspectCountsRead,
    SuspectDecisionFilter,
    SuspectPageRead,
    SuspectRead,
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

router = APIRouter(prefix=f"/{classification_service.MODULE_NAME}", tags=["분류"])

# 문장 목록 한 쪽의 기본 크기
DEFAULT_PAGE_SIZE = 50

# 검색어의 최대 길이
SEARCH_MAX_LENGTH = 200

# 오라벨 의심 목록 한 쪽의 기본 크기
DEFAULT_SUSPECT_PAGE_SIZE = 50

Db = Annotated[AsyncSession, Depends(get_db)]


# ---------- 데이터셋 ----------


@router.get("/datasets")
async def list_datasets(db: Db) -> list[DatasetSummaryRead]:
    """분류 데이터셋 목록과 개수. 최근에 바뀐 것부터."""
    summaries = await classification_service.list_datasets(db)
    return [_dataset_summary_read(summary) for summary in summaries]


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: int, db: Db) -> DatasetRead:
    """분류 데이터셋 하나와 라벨."""
    detail = await classification_service.get_dataset_detail(db, dataset_id=dataset_id)
    return _dataset_read(detail)


@router.patch("/datasets/{dataset_id}/settings")
async def update_settings(dataset_id: int, data: SettingsUpdate, db: Db) -> SettingsRead:
    """도우미 실행 설정 고치기 (새 문장 목표 배율)."""
    settings = await classification_service.update_settings(db, dataset_id=dataset_id, data=data)
    return SettingsRead.model_validate(settings)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(dataset_id: int, db: Db) -> None:
    """데이터셋 지우기(문장 · 라벨 · 의미 지도 · 가져오기 기록까지). 가져오거나 지도를 만드는 중이면 409."""
    await classification_service.delete_dataset(db, dataset_id=dataset_id)


# ---------- 라벨 ----------


@router.post("/datasets/{dataset_id}/labels", status_code=status.HTTP_201_CREATED)
async def create_label(dataset_id: int, data: LabelCreate, db: Db) -> LabelRead:
    """라벨 만들기."""
    summary = await classification_service.create_label(db, dataset_id=dataset_id, data=data)
    return _label_read(summary)


@router.patch("/labels/{label_id}")
async def update_label(label_id: int, data: LabelUpdate, db: Db) -> LabelRead:
    """라벨 이름·설명 고치기."""
    summary = await classification_service.update_label(db, label_id=label_id, data=data)
    return _label_read(summary)


# ---------- 가져오기 ----------


@router.post("/mapping/suggest")
async def suggest_mapping(data: MappingSuggestCreate) -> FieldMappingRead:
    """필드 맞춤 짐작: 원본의 열 이름으로 문장·라벨 열을 고른다."""
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
    import_row = await classification_service.create_import(db, data=data)
    return ImportRead.model_validate(import_row)


# ---------- 문장 ----------


@router.get("/datasets/{dataset_id}/records")
async def list_records(
    dataset_id: int,
    db: Db,
    record_status: Annotated[RecordStatusFilter, Query(alias="status")] = "active",
    label_id: int | None = None,
    problem: ProblemFilter | None = None,
    q: Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)] = None,
    same_as: int | None = None,
    limit: Annotated[int, Query(ge=1, le=records_service.MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecordPageRead:
    """문장 목록. 상태·라벨·문제·검색어로, same_as를 주면 그 문장과 같은 문장만 거른다."""
    page = await records_service.list_records(
        db,
        dataset_id=dataset_id,
        status=record_status,
        label_id=label_id,
        problem=problem,
        q=q,
        same_as=same_as,
        limit=limit,
        offset=offset,
    )
    return RecordPageRead(
        items=[_record_read(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/records/{record_id}")
async def get_record(record_id: int, db: Db) -> RecordRead:
    """문장 하나. 의미 지도에서 점에 올렸을 때 문장을 보인다."""
    item = await records_service.get_record_item(db, record_id=record_id)
    return _record_read(item)


@router.patch("/records/{record_id}")
async def update_record(record_id: int, data: RecordUpdate, db: Db) -> RecordRead:
    """문장 하나 고치기. 불러온 뒤 다른 곳에서 먼저 고쳤으면 409."""
    item = await records_service.update_record(db, record_id=record_id, data=data)
    return _record_read(item)


@router.post("/datasets/{dataset_id}/records/bulk")
async def bulk_update_records(dataset_id: int, data: RecordBulkUpdate, db: Db) -> ChangedRead:
    """여러 문장에 한 번에: 빼기·넣기·휴지통·되살리기·라벨 바꾸기."""
    changed = await records_service.bulk_update(db, dataset_id=dataset_id, data=data)
    return ChangedRead(changed=changed)


@router.post("/datasets/{dataset_id}/cleanup/duplicates")
async def cleanup_duplicates(dataset_id: int, db: Db) -> ChangedRead:
    """중복 정리: 문장·라벨이 모두 같으면 하나만 남기고 학습에서 뺀다."""
    changed = await records_service.cleanup_duplicates(db, dataset_id=dataset_id)
    return ChangedRead(changed=changed)


@router.post("/datasets/{dataset_id}/cleanup/duplicates/undo")
async def undo_duplicate_cleanup(dataset_id: int, db: Db) -> ChangedRead:
    """중복 정리 되돌리기."""
    changed = await records_service.undo_duplicate_cleanup(db, dataset_id=dataset_id)
    return ChangedRead(changed=changed)


@router.delete("/datasets/{dataset_id}/trash")
async def empty_trash(dataset_id: int, db: Db) -> ChangedRead:
    """휴지통 비우기: 휴지통의 문장을 영구히 지운다. changed는 지운 수."""
    deleted = await records_service.empty_trash(db, dataset_id=dataset_id)
    return ChangedRead(changed=deleted)


# ---------- 진단 ----------


@router.get("/datasets/{dataset_id}/overview")
async def get_overview(dataset_id: int, db: Db) -> OverviewRead:
    """데이터셋 진단: 균형·중복·충돌·짧은 문장과 길이 분포."""
    return await overview_service.get_overview(db, dataset_id=dataset_id)


# ---------- 의미 지도 ----------


@router.post("/datasets/{dataset_id}/map", status_code=status.HTTP_202_ACCEPTED)
async def start_map(dataset_id: int, db: Db) -> MapStateRead:
    """의미 지도 만들기 시작. 진행률은 run.job_id로 본다. 이미 만드는 중이면 409, 임베딩 미연결이면 422."""
    state = await map_service.start_map(db, dataset_id=dataset_id)
    return _map_state_read(state)


@router.get("/datasets/{dataset_id}/map")
async def get_map(dataset_id: int, db: Db) -> MapStateRead:
    """의미 지도 상태: 다 만든 지도, 그 뒤의 시도(만드는 중 · 실패 · 취소), 지도 이후 바뀌었는지."""
    state = await map_service.get_map_state(db, dataset_id=dataset_id)
    return _map_state_read(state)


@router.get("/datasets/{dataset_id}/map/points")
async def list_map_points(dataset_id: int, db: Db) -> MapPointsRead:
    """의미 지도의 점들(칸마다 목록). 지도가 없으면 404."""
    return await map_service.list_map_points(db, dataset_id=dataset_id)


@router.get("/datasets/{dataset_id}/map/matches")
async def list_map_matches(
    dataset_id: int,
    db: Db,
    q: Annotated[str, Query(min_length=1, max_length=SEARCH_MAX_LENGTH)],
) -> MapMatchesRead:
    """검색어가 든 문장 번호들(휴지통 밖). 지도가 검색어에 맞는 점을 강조할 때 쓴다."""
    record_ids = await records_service.search_record_ids(db, dataset_id=dataset_id, q=q)
    return MapMatchesRead(record_ids=record_ids)


@router.post("/datasets/{dataset_id}/map/cancel")
async def cancel_map(dataset_id: int, db: Db) -> MapStateRead:
    """만드는 중인 뜻 분석 멈추기. 만드는 중인 것이 없으면 409."""
    state = await map_service.cancel_map(db, dataset_id=dataset_id)
    return _map_state_read(state)


# ---------- 뜻 분석: 오라벨 의심 · 근접 중복 ----------


@router.get("/datasets/{dataset_id}/suspects")
async def list_suspects(
    dataset_id: int,
    db: Db,
    decision: SuspectDecisionFilter = "open",
    confirmed: bool = False,
    limit: Annotated[
        int, Query(ge=1, le=checks_service.MAX_SUSPECT_PAGE_SIZE)
    ] = DEFAULT_SUSPECT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SuspectPageRead:
    """다 만든 뜻 분석의 오라벨 의심. Jev가 확인한 것 · 지금 라벨 확률이 낮은 것부터. confirmed면 Jev 확인만."""
    items, total = await checks_service.list_suspects(
        db,
        dataset_id=dataset_id,
        decision=decision,
        confirmed_only=confirmed,
        limit=limit,
        offset=offset,
    )
    return SuspectPageRead(items=[_suspect_read(item) for item in items], total=total)


@router.post("/datasets/{dataset_id}/suspects/accept-confirmed")
async def accept_confirmed_suspects(dataset_id: int, db: Db) -> ChangedRead:
    """Jev도 확인한 오라벨 의심을 모두 추천 라벨로 수락. changed는 수락한 의심 수."""
    accepted = await checks_service.accept_confirmed_suspects(db, dataset_id=dataset_id)
    return ChangedRead(changed=accepted)


@router.post("/datasets/{dataset_id}/suspects/{text_hash}/accept")
async def accept_suspect(
    dataset_id: int, text_hash: str, db: Db, data: SuspectAccept | None = None
) -> ChangedRead:
    """오라벨 의심 수락: 추천(또는 준) 라벨로 바꾼다. changed는 바뀐 문장 수. 이미 판단했으면 409."""
    changed = await checks_service.accept_suspect(
        db,
        dataset_id=dataset_id,
        text_hash=text_hash,
        label_id=data.label_id if data is not None else None,
    )
    return ChangedRead(changed=changed)


@router.post("/datasets/{dataset_id}/suspects/{text_hash}/keep")
async def keep_suspect(dataset_id: int, text_hash: str, db: Db) -> ChangedRead:
    """오라벨 의심 유지: 지금 라벨이 맞다(다음 뜻 분석에서도 묻지 않는다). 이미 판단했으면 409."""
    await checks_service.keep_suspect(db, dataset_id=dataset_id, text_hash=text_hash)
    return ChangedRead(changed=1)


@router.get("/datasets/{dataset_id}/helper")
async def get_helper(dataset_id: int, db: Db) -> HelperStateRead:
    """데이터셋의 가장 최근 도우미 실행과 되돌린 카드 번호들. 데이터셋이 없으면 404."""
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    run = await helper_service.latest_run(
        db, module=classification_service.MODULE_NAME, dataset_id=dataset_id
    )
    if run is None:
        return HelperStateRead(run=None, undone_events=[])
    undone = await helper_tools.undone_event_ids(db, run_id=run.id)
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
    run = await helper_agent.start_fix(db, dataset_id=dataset_id, keys=data.keys, adds=data.adds)
    return HelperRunRead.model_validate(run)


@router.get("/records/{record_id}/near-duplicates")
async def list_near_duplicates(record_id: int, db: Db) -> list[NearDuplicateRead]:
    """문장과 근접 중복인 다른 문장들(유사도가 큰 것부터). 뜻 분석이 없으면 빈 목록."""
    items = await checks_service.list_near_duplicates(db, record_id=record_id)
    return [
        NearDuplicateRead(
            record_id=item.record_id,
            text=item.text,
            label_name=item.label_name,
            similarity=item.similarity,
        )
        for item in items
    ]


# ---------- 응답 모양으로 바꾸기 ----------


def _label_read(summary: classification_service.LabelSummary) -> LabelRead:
    """라벨과 문장 수를 응답으로 바꾼다."""
    label = summary.label
    return LabelRead(
        id=label.id,
        dataset_id=label.dataset_id,
        name=label.name,
        description=label.description,
        record_count=summary.record_count,
        created_at=label.created_at,
    )


def _dataset_summary_read(summary: classification_service.DatasetSummary) -> DatasetSummaryRead:
    """데이터셋과 개수 요약을 목록 한 줄로 바꾼다."""
    dataset = summary.dataset
    return DatasetSummaryRead(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        record_count=summary.record_count,
        included_count=summary.included_count,
        excluded_count=summary.excluded_count,
        trash_count=summary.trash_count,
        label_count=summary.label_count,
        importing=summary.importing,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


def _dataset_read(detail: classification_service.DatasetDetail) -> DatasetRead:
    """데이터셋의 자세한 정보를 응답으로 바꾼다."""
    return DatasetRead(
        **_dataset_summary_read(detail.summary).model_dump(),
        labels=[_label_read(label) for label in detail.labels],
        settings=SettingsRead.model_validate(detail.settings),
    )


def _record_read(item: records_service.RecordItem) -> RecordRead:
    """문장과 같은 문장 무리 정보를 응답으로 바꾼다."""
    record = item.record
    return RecordRead(
        id=record.id,
        dataset_id=record.dataset_id,
        text=record.text,
        label_id=record.label_id,
        label_name=record.label.name if record.label is not None else None,
        exclude_reason=record.exclude_reason,
        is_trashed=record.trashed_at is not None,
        trashed_at=record.trashed_at,
        row_version=record.row_version,
        extra=record.extra,
        import_id=record.import_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        duplicate_count=item.duplicate_count,
        has_conflict=item.has_conflict,
    )


def _map_read(map_row: Map) -> MapRead:
    """지도 한 줄(모델을 미리 불러 둔 것)을 응답으로 바꾼다."""
    return MapRead(
        id=map_row.id,
        status=map_row.status,
        model_name=map_row.model.name if map_row.model is not None else None,
        params=map_row.params,
        text_count=map_row.text_count,
        point_count=map_row.point_count,
        record_count=map_row.record_count,
        job_id=map_row.job_id,
        error=map_row.error,
        created_at=map_row.created_at,
        finished_at=map_row.finished_at,
    )


def _map_state_read(state: map_service.MapState) -> MapStateRead:
    """지도 상태를 응답으로 바꾼다."""
    return MapStateRead(
        map=_map_read(state.map) if state.map is not None else None,
        run=_map_read(state.run) if state.run is not None else None,
        outdated=state.outdated,
        checks=_checks_read(state.checks) if state.checks is not None else None,
    )


def _checks_read(checks: checks_service.Checks) -> MapChecksRead:
    """뜻 분석 요약을 응답으로 바꾼다."""
    counts = checks.suspects
    return MapChecksRead(
        thresholds=checks.thresholds,
        near_duplicates=NearDuplicateCountsRead(**checks.near_duplicates),
        suspects=SuspectCountsRead(
            found=counts.found,
            open=counts.open,
            confirmed_open=counts.confirmed_open,
            accepted=counts.accepted,
            kept=counts.kept,
            judged=counts.judged,
            unjudged_labels=counts.unjudged_labels,
            open_rate=counts.open_rate,
            open_by_label=counts.open_by_label,
        ),
        jev=JevCheckRead(status=checks.jev["status"], detail=checks.jev["detail"]),
        skews=[
            SkewLabelRead(
                label_id=skew.label_id,
                label_name=skew.label_name,
                text_count=skew.text_count,
                cluster_count=skew.cluster_count,
                largest_share=skew.largest_share,
                evenness=skew.evenness,
                is_skewed=skew.is_skewed,
                examples=skew.examples,
            )
            for skew in checks.skews
        ],
        unmeasured_labels=checks.unmeasured_labels,
        grades=SemanticGradesRead(**checks.grades),
    )


def _suspect_read(item: checks_service.SuspectItem) -> SuspectRead:
    """오라벨 의심 한 건을 응답으로 바꾼다."""
    suspect = item.suspect
    jev_label = (
        MapLabelRead(id=suspect.jev_label_id, name=item.jev_label_name)
        if suspect.jev_label_id is not None and item.jev_label_name is not None
        else None
    )
    return SuspectRead(
        text_hash=suspect.text_hash,
        text=item.text,
        record_count=item.record_count,
        label=MapLabelRead(id=suspect.label_id, name=item.label_name),
        suggested_label=MapLabelRead(id=suspect.suggested_label_id, name=item.suggested_label_name),
        label_probability=suspect.label_probability,
        suggested_probability=suspect.suggested_probability,
        jev_label=jev_label,
        jev_label_probability=suspect.jev_label_probability,
        jev_confidence=suspect.jev_confidence,
        is_confirmed=suspect.is_confirmed,
        decision=suspect.decision,
        decided_at=suspect.decided_at,
    )


# ---------- 내보내기 ----------


@router.post("/datasets/{dataset_id}/exports/preview")
async def preview_export(dataset_id: int, data: ExportCreate, db: Db) -> ExportPreviewRead:
    """내보내기 미리 보기: 첫 줄 · 파일마다 수 · 출처별 문장 수 · 남은 심각 · 주의."""
    return await exporting_service.preview(db, dataset_id=dataset_id, data=data)


@router.post("/datasets/{dataset_id}/exports", status_code=status.HTTP_201_CREATED)
async def create_export(dataset_id: int, data: ExportCreate, db: Db) -> list[ExportRead]:
    """내보내기 시작 (고른 모양마다 하나). 넣을 문장이 없으면 422. 파일은 /api/v1/exports/{id}/file."""
    exports = await exporting_service.create_exports(db, dataset_id=dataset_id, data=data)
    return [ExportRead.model_validate(export) for export in exports]
