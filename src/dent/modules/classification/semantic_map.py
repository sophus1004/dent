"""뜻 분석(의미 지도 포함): 데이터셋의 문장을 임베딩해 2차원 좌표에 놓고, 같은 벡터로 근접 중복 · 오라벨 의심 ·
의미 쏠림을 계산한다(계산은 semantic_checks.py). 시작 · 상태 · 점 · 멈추기와 작업 실행. 지도 한 줄이 뜻 분석 한 번이다.

사용자가 [뜻 분석 만들기]를 누르면 지도 한 줄과 작업 하나를 넣고, 작업 실행기가 build_map을 부른다.
1. 임베딩: 휴지통 밖 문장의 서로 다른 문장(해시) 가운데 캐시(system_embeddings)에 없는 것만 묶음으로 계산해
   저장한다. 묶음마다 진행률을 적고 멈추라고 했는지 본다. 이미 계산한 문장은 건너뛰므로 다시 돌려도 안전하다.
   다음 묶음 요청을 먼저 보내 두고 이번 묶음을 저장해, 저장하는 동안에도 임베딩 서버가 쉬지 않게 한다.
2. 좌표: 서로 다른 문장의 벡터를 읽어 UMAP으로 2차원에 놓는다(system.projection). 몇 분 걸릴 수 있어
   따로 된 스레드에서 돌린다(작업 실행기가 종료하라는 신호를 계속 받을 수 있게).
3. 분석: 같은 벡터로 근접 중복 · 오라벨 의심 · 의미 쏠림을 계산한다(스레드에서, semantic_checks.analyze).
4. Jev 판정: 오라벨 의심 후보를 Jev에 한 번 더 묻는다. Jev가 없거나 실패하면 판정 없이 상태만 적고 이어 간다.
5. 저장: 휴지통 밖 문장마다 점 하나를 적고, 근접 중복 쌍 · 오라벨 의심과 요약(checks)을 적는다.
끝나면 이 데이터셋의 다른 지도(앞의 지도, 실패 · 취소한 시도)를 지운다. 다 만든 지도는 데이터셋마다 하나다.
실패하면 지도는 '실패'가 되고 앞의 지도는 그대로 남는다.

지도 이후 바뀜(outdated): 만들 때 적어 둔 휴지통 밖 문장 수나 데이터셋 수정 시각이 지금과 다르면.
점의 라벨 · 표시는 지도에 적지 않고 읽을 때 지금 값으로 붙인다(라벨을 고쳐도 지도는 그대로 쓴다).
표시는 문장 목록의 문제 거르기와 같은 규칙으로, 휴지통 밖 문장 안에서 센다.
"""

import asyncio
import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import (
    REAL,
    BigInteger,
    ColumnElement,
    Float,
    Numeric,
    String,
    Subquery,
    case,
    cast,
    delete,
    func,
    literal,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, selectinload

from dent.modules.classification import records as records_service
from dent.modules.classification import semantic_checks
from dent.modules.classification import service as classification_service
from dent.modules.classification.models import (
    MAP_BUILDING_STATUSES,
    Label,
    LabelSuspect,
    Map,
    MapPoint,
    MapStatus,
    NearDuplicate,
    Record,
)
from dent.modules.classification.schemas import MapLabelRead, MapPointsRead
from dent.system import connections, embedding, jev, jobs, projection
from dent.system.db import flush_or_conflict
from dent.system.exceptions import (
    ConflictError,
    ExternalServiceError,
    InvalidInputError,
    NotFoundError,
)
from dent.system.models import Connection, ConnectionRole, EmbeddingModel
from dent.system.text import normalize_text

# 의미 지도 작업의 종류. 작업 실행기는 (모듈 이름, MAP_JOB_KIND)로 처리 함수를 찾는다.
MAP_JOB_KIND = "map"

# 작업 단계 이름 (작업 result의 phase). 화면은 임베딩 n / m · 좌표 계산 · 분석 · Jev 판정 n / m · 저장으로 보인다.
PHASE_EMBEDDING = "embedding"
PHASE_PROJECTING = "projecting"
PHASE_ANALYZING = "analyzing"
PHASE_JUDGING = "judging"
PHASE_SAVING = "saving"

# Jev 판정을 이만큼 할 때마다 진행률을 적고 멈추라고 했는지 본다(laya 1건 30~60ms면 1~2초마다).
JEV_PROGRESS_EVERY = 20

# 임베딩 단계에서 한 번에 계산하고 저장하는 문장 수. 요청 묶음(128) × 동시 요청(4)만큼 보내고,
# 끝날 때마다 저장 · 진행률 · 멈춤 확인을 한다(초당 60여 문장이면 8초쯤마다).
EMBED_CHUNK = embedding.EMBEDDING_BATCH_SIZE * embedding.EMBEDDING_MAX_CONCURRENCY

# 저장 단계에서 쿼리 하나로 점을 적는 서로 다른 문장 수. 진행률도 이만큼씩 오른다.
POINT_CHUNK = 20_000

# 점 좌표를 내보낼 때 남기는 소수 자리. 화면 한 점 크기보다 훨씬 촘촘하고, 응답은 절반쯤 작아진다.
COORD_DECIMALS = 4

# 점의 표시 비트. 여럿이면 더한다. 화면(map/flags.ts의 FLAG)과 같은 값이다.
# 4는 분할 간 중복이 쓰던 자리라 비워 둔다(train · valid · test를 나누지 않는다).
FLAG_DUPLICATE = 1
FLAG_CONFLICT = 2
FLAG_SHORT = 8
FLAG_EXCLUDED = 16
FLAG_NEAR_DUPLICATE = 32
FLAG_SUSPECT = 64

EMBEDDING_NOT_CONNECTED_MESSAGE = "임베딩 미연결: 연결 설정에서 임베딩 서버를 연결하세요."
ALREADY_BUILDING_MESSAGE = "뜻 분석을 이미 만드는 중입니다."
NOT_BUILDING_MESSAGE = "만드는 중인 뜻 분석이 없습니다."
NO_RECORDS_MESSAGE = "지도에 놓을 문장이 없습니다."
NO_MAP_MESSAGE = "의미 지도가 없습니다. 먼저 만들어 주세요."
TEXTS_CHANGED_MESSAGE = "만드는 동안 문장이 바뀌었습니다. 다시 만들어 주세요."


@dataclass(frozen=True)
class MapState:
    """데이터셋의 의미 지도 상태."""

    # 점이 있는 가장 최근 지도(다 만든 것, 모델을 미리 불러 둠). 없으면 None
    map: Map | None

    # map보다 나중에 누른 시도(만드는 중 · 실패 · 취소). 없으면 None
    run: Map | None

    # map을 만든 뒤 문장이 바뀌었는지
    outdated: bool

    # map의 뜻 분석 요약(근접 중복 · 오라벨 의심 · 의미 쏠림). 지도가 없거나 뜻 분석 전의 지도면 None
    checks: semantic_checks.Checks | None = None


# ---------- 시작 · 상태 · 멈추기 (API) ----------


async def start_map(db: AsyncSession, *, dataset_id: int) -> MapState:
    """의미 지도 만들기를 대기열에 넣는다.

    데이터셋이 없으면 NotFoundError, 임베딩이 연결되지 않았거나 문장이 없으면 InvalidInputError,
    이미 만드는 중이면 ConflictError.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.EMBEDDING)
    if connection is None:
        raise InvalidInputError(EMBEDDING_NOT_CONNECTED_MESSAGE)
    if await _building_map(db, dataset_id=dataset_id) is not None:
        raise ConflictError(ALREADY_BUILDING_MESSAGE)
    if await _count_active(db, dataset_id=dataset_id) == 0:
        raise InvalidInputError(NO_RECORDS_MESSAGE)

    # 앞서 실패 · 취소한 시도는 새 시도로 바꾼다(다 만든 지도 하나와 마지막 시도 하나만 둔다).
    await db.execute(
        delete(Map).where(
            Map.dataset_id == dataset_id,
            Map.status.in_((MapStatus.FAILED, MapStatus.CANCELED)),
        )
    )
    map_row = Map(dataset_id=dataset_id)
    db.add(map_row)
    # 두 창에서 동시에 눌러도 부분 UNIQUE 인덱스가 둘째를 막는다.
    await flush_or_conflict(db, message=ALREADY_BUILDING_MESSAGE)
    job = await jobs.enqueue_job(
        db,
        module=classification_service.MODULE_NAME,
        kind=MAP_JOB_KIND,
        params={"map_id": map_row.id},
        dataset_id=dataset_id,
    )
    map_row.job_id = job.id
    await db.commit()
    return await get_map_state(db, dataset_id=dataset_id)


async def get_map_state(db: AsyncSession, *, dataset_id: int) -> MapState:
    """다 만든 지도, 그 뒤의 시도, 지도 이후 바뀌었는지. 데이터셋이 없으면 NotFoundError."""
    dataset = await classification_service.get_dataset(db, dataset_id=dataset_id)
    query = (
        select(Map)
        .options(selectinload(Map.model))
        .where(Map.dataset_id == dataset_id)
        .order_by(Map.id.desc())
        .execution_options(populate_existing=True)
    )
    rows = list(await db.scalars(query))
    done = next((row for row in rows if row.status == MapStatus.DONE), None)
    latest = rows[0] if rows else None
    run = latest if latest is not None and latest is not done else None

    outdated = False
    if done is not None:
        active_count = await _count_active(db, dataset_id=dataset_id)
        is_count_changed = active_count != done.record_count
        is_touched_later = (
            done.dataset_updated_at is None or dataset.updated_at > done.dataset_updated_at
        )
        outdated = is_count_changed or is_touched_later
    checks = await semantic_checks.read_checks(db, map_row=done) if done is not None else None
    return MapState(map=done, run=run, outdated=outdated, checks=checks)


async def cancel_map(db: AsyncSession, *, dataset_id: int) -> MapState:
    """만드는 중인 지도를 멈춘다. 대기 중이면 바로, 도는 중이면 다음 묶음 사이에 멈춘다.

    데이터셋이 없으면 NotFoundError, 만드는 중인 지도가 없으면 ConflictError.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    building = await _building_map(db, dataset_id=dataset_id)
    if building is None:
        raise ConflictError(NOT_BUILDING_MESSAGE)
    is_stopped_now = building.job_id is None or await jobs.request_cancel(
        db, job_id=building.job_id
    )
    if is_stopped_now:
        building.status = MapStatus.CANCELED
        building.finished_at = datetime.now(UTC)
    await db.commit()
    return await get_map_state(db, dataset_id=dataset_id)


async def list_map_points(db: AsyncSession, *, dataset_id: int) -> MapPointsRead:
    """다 만든 지도의 점을 칸마다 긴 목록으로 돌려준다(휴지통 밖 문장만, 라벨 · 분할 · 표시는 지금 값).

    데이터셋이 없거나 지도가 없으면 NotFoundError.
    점 목록은 DB의 한 줄이 아니라 여러 표를 이어 계산한 값이라서, 응답 모양으로 바로 만든다.
    """
    state = await get_map_state(db, dataset_id=dataset_id)
    if state.map is None:
        raise NotFoundError(NO_MAP_MESSAGE)
    flagged = _flagged_records(dataset_id=dataset_id, map_id=state.map.id)
    query = (
        select(
            MapPoint.record_id,
            _rounded(MapPoint.x),
            _rounded(MapPoint.y),
            flagged.c.label_id,
            flagged.c.flags,
        )
        .join(flagged, flagged.c.id == MapPoint.record_id)
        .where(MapPoint.map_id == state.map.id)
        .order_by(MapPoint.record_id)
    )
    rows = (await db.execute(query)).all()
    labels = await db.execute(
        select(Label.id, Label.name)
        .where(Label.dataset_id == dataset_id)
        .order_by(Label.name, Label.id)
    )
    return MapPointsRead(
        map_id=state.map.id,
        record_ids=[row[0] for row in rows],
        x=[row[1] for row in rows],
        y=[row[2] for row in rows],
        label_ids=[row[3] for row in rows],
        flags=[row[4] for row in rows],
        labels=[MapLabelRead(id=label_id, name=name) for label_id, name in labels],
    )


# ---------- 만들기 (작업 실행기) ----------


async def build_map(
    db: AsyncSession,
    *,
    map_id: int,
    job_id: int,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """뜻 분석 하나(지도 포함)를 처음부터 끝까지 만든다. 멈추라고 하면 멈춘 것으로 끝낸다.

    임베딩이 끊겼거나 서버가 안 되면 도메인 예외를 낸다(부른 쪽이 fail_map으로 실패 처리한다).
    transport · jev_transport는 테스트가 임베딩 · Jev 서버 대신 가짜 응답을 줄 때만.
    """
    map_row = await _get_map(db, map_id=map_id)
    dataset_id = map_row.dataset_id
    dataset = await classification_service.get_dataset(db, dataset_id=dataset_id)
    if await _stop_if_canceled(db, map_row=map_row, job_id=job_id):
        return
    connection = await connections.get_connection(db, role=ConnectionRole.EMBEDDING)
    if connection is None:
        raise InvalidInputError(EMBEDDING_NOT_CONNECTED_MESSAGE)
    model = await embedding.ensure_model(db, connection, transport=transport)

    # 다시 도는 작업이면 앞서 적은 것을 비우고 처음부터 적는다.
    text_hashes = await _unique_hashes(db, dataset_id=dataset_id)
    map_row.status = MapStatus.RUNNING
    map_row.model_id = model.id
    map_row.params = {}
    map_row.error = None
    map_row.finished_at = None
    map_row.text_count = len(text_hashes)
    map_row.point_count = 0
    map_row.record_count = await _count_active(db, dataset_id=dataset_id)
    map_row.dataset_updated_at = dataset.updated_at
    # 작업 기록에 이번에 쓴 임베딩 서버를 남긴다(나중에 연결을 바꿔도 그때 무엇을 썼는지 보이게).
    await jobs.add_event(
        db,
        job_id=job_id,
        event_type=jobs.EVENT_CONNECTION,
        role=ConnectionRole.EMBEDDING.value,
        base_url=connection.base_url,
        model=connection.model,
    )
    await db.commit()

    # 1. 임베딩: 캐시에 없는 문장만 계산한다.
    embed_started = time.perf_counter()
    missing = await embedding.find_missing_hashes(db, model_id=model.id, text_hashes=text_hashes)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_EMBEDDING, total=len(missing))
    await db.commit()
    embedded = await _embed_missing(
        db,
        map_row=map_row,
        job_id=job_id,
        connection=connection,
        model=model,
        missing=missing,
        transport=transport,
    )
    if embedded is None:
        return
    embed_seconds = time.perf_counter() - embed_started

    # 2. 좌표
    if await _stop_if_canceled(db, map_row=map_row, job_id=job_id):
        return
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_PROJECTING, total=None)
    await db.commit()
    projection_started = time.perf_counter()
    vectors = await embedding.load_vectors(db, model=model, text_hashes=text_hashes)
    projected = await asyncio.to_thread(projection.project_2d, vectors)
    projection_seconds = time.perf_counter() - projection_started
    if await _stop_if_canceled(db, map_row=map_row, job_id=job_id):
        return

    # 3. 분석: 같은 벡터로 근접 중복 · 오라벨 의심 · 의미 쏠림
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_ANALYZING, total=None)
    await db.commit()
    analysis_started = time.perf_counter()
    facts = await _text_facts(db, dataset_id=dataset_id, text_hashes=text_hashes)
    findings = await asyncio.to_thread(semantic_checks.analyze, vectors, facts)
    del vectors  # 수백 MB일 수 있어 판정 · 저장 단계 전에 놓는다.
    analysis_seconds = time.perf_counter() - analysis_started
    if await _stop_if_canceled(db, map_row=map_row, job_id=job_id):
        return

    # 4. Jev 판정: 오라벨 의심 후보만
    judge_started = time.perf_counter()
    judged = await _judge_suspects(
        db,
        map_row=map_row,
        job_id=job_id,
        text_hashes=text_hashes,
        findings=findings,
        transport=jev_transport,
    )
    if judged is None:
        return
    verdicts, jev_state = judged
    judge_seconds = time.perf_counter() - judge_started

    # 5. 저장. 다시 도는 작업이면 이 지도에 앞서 적은 것을 비우고 처음부터 적는다.
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_SAVING, total=len(text_hashes))
    await _clear_results(db, map_id=map_id)
    xs = projected.coords[:, 0].tolist()
    ys = projected.coords[:, 1].tolist()
    for start in range(0, len(text_hashes), POINT_CHUNK):
        end = start + POINT_CHUNK
        await _insert_points(
            db,
            map_row=map_row,
            text_hashes=text_hashes[start:end],
            xs=xs[start:end],
            ys=ys[start:end],
        )
        await jobs.set_progress(
            db, job_id=job_id, done=min(end, len(text_hashes)), total=len(text_hashes)
        )
        # 묶음마다 확정해 화면이 저장 진행률을 보게 한다. 실패하면 fail_map이 이 지도의 점을 지운다.
        await db.commit()
    point_count = await db.scalar(
        select(func.count()).select_from(MapPoint).where(MapPoint.map_id == map_id)
    )
    example_hashes = tuple(
        {text_hashes[index] for skew in findings.skews for index in skew.example_indices}
    )
    example_texts = dict(
        zip(
            example_hashes,
            await _texts_for(db, dataset_id=dataset_id, text_hashes=example_hashes),
            strict=True,
        )
    )
    await semantic_checks.save_findings(
        db,
        map_row=map_row,
        text_hashes=text_hashes,
        facts=facts,
        findings=findings,
        verdicts=verdicts,
        jev_state=jev_state,
        example_texts=example_texts,
    )

    map_row.status = MapStatus.DONE
    map_row.point_count = int(point_count or 0)
    map_row.params = projected.params
    map_row.finished_at = datetime.now(UTC)
    # 다 만든 지도는 데이터셋마다 하나다. 앞의 지도와 실패 · 취소한 시도를 지운다(점도 함께 지워진다).
    await db.execute(delete(Map).where(Map.dataset_id == dataset_id, Map.id != map_id))
    await jobs.finish_job(
        db,
        job_id=job_id,
        result={
            "map_id": map_id,
            "text_count": len(text_hashes),
            "point_count": map_row.point_count,
            "embedded": embedded,
            "embed_seconds": round(embed_seconds, 1),
            "projection_seconds": round(projection_seconds, 1),
            "analysis_seconds": round(analysis_seconds, 1),
            "judge_seconds": round(judge_seconds, 1),
            "near_duplicate_pairs": len(findings.pairs),
            "suspects": len(findings.suspects),
            "confirmed_suspects": sum(1 for verdict in verdicts.values() if verdict.is_confirmed),
        },
    )
    await db.commit()


async def fail_map(db: AsyncSession, *, map_id: int, job_id: int, message: str) -> None:
    """지도와 작업을 실패로 끝낸다. 반쯤 적은 점 · 결과는 지운다. 앞의 지도는 그대로 둔다."""
    await db.rollback()
    map_row = await db.get(Map, map_id, populate_existing=True)
    if map_row is not None:
        await _clear_results(db, map_id=map_id)
        map_row.status = MapStatus.FAILED
        map_row.error = message
        map_row.finished_at = datetime.now(UTC)
    # fail_job이 함께 커밋한다.
    await jobs.fail_job(db, job_id=job_id, message=message)


# ---------- 안에서만 쓰는 함수 ----------


async def _get_map(db: AsyncSession, *, map_id: int) -> Map:
    """지도 한 줄. 없으면 NotFoundError."""
    map_row = await db.get(Map, map_id, populate_existing=True)
    if map_row is None:
        raise NotFoundError(NO_MAP_MESSAGE)
    return map_row


async def _building_map(db: AsyncSession, *, dataset_id: int) -> Map | None:
    """데이터셋에서 만드는 중(대기 · 실행)인 지도. 없으면 None."""
    query = select(Map).where(Map.dataset_id == dataset_id, Map.status.in_(MAP_BUILDING_STATUSES))
    return await db.scalar(query)


async def _count_active(db: AsyncSession, *, dataset_id: int) -> int:
    """휴지통 밖 문장 수."""
    count = await db.scalar(
        select(func.count())
        .select_from(Record)
        .where(Record.dataset_id == dataset_id, classification_service.IS_ACTIVE)
    )
    return int(count or 0)


async def _clear_results(db: AsyncSession, *, map_id: int) -> None:
    """이 지도(뜻 분석 한 번)의 점 · 근접 중복 · 오라벨 의심을 비운다."""
    for model in (MapPoint, NearDuplicate, LabelSuspect):
        await db.execute(delete(model).where(model.map_id == map_id))


async def _text_facts(
    db: AsyncSession, *, dataset_id: int, text_hashes: Sequence[str]
) -> list[semantic_checks.TextFacts]:
    """서로 다른 문장마다 라벨(여러 라벨이면 None)을 해시 순서대로. SQL로 한 번에 모은다."""
    query = (
        select(
            Record.text_hash,
            func.min(Record.label_id),
            func.max(Record.label_id),
        )
        .where(Record.dataset_id == dataset_id, classification_service.IS_ACTIVE)
        .group_by(Record.text_hash)
    )
    facts = {}
    for text_hash, lowest, highest in await db.execute(query):
        # 라벨이 둘 이상이면 라벨 충돌로 따로 잡히므로 오라벨 의심 · 쏠림에서는 뺀다.
        is_single_label = lowest is not None and lowest == highest
        facts[text_hash] = semantic_checks.TextFacts(label_id=lowest if is_single_label else None)
    missing = [text_hash for text_hash in text_hashes if text_hash not in facts]
    if missing:
        raise InvalidInputError(TEXTS_CHANGED_MESSAGE)
    return [facts[text_hash] for text_hash in text_hashes]


async def _judge_suspects(
    db: AsyncSession,
    *,
    map_row: Map,
    job_id: int,
    text_hashes: Sequence[str],
    findings: semantic_checks.Findings,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[dict[int, semantic_checks.JevVerdict], dict[str, str]] | None:
    """오라벨 의심 후보를 Jev에 묻고 (문장 자리 → 판정, Jev 상태)를 돌려준다. 멈추라고 했으면 None.

    Jev가 없거나 판정이 실패하면 거기까지의 판정과 그 상태를 돌려준다(뜻 분석은 이어 간다).
    가장 의심스러운 것부터 MAX_JEV_JUDGMENTS건까지만 묻는다.
    """
    candidates = findings.suspects[: semantic_checks.MAX_JEV_JUDGMENTS]
    if not candidates:
        return {}, {"status": semantic_checks.JEV_JUDGED, "detail": "후보 없음"}
    connection = await connections.get_connection(db, role=ConnectionRole.JEV)
    if connection is None:
        return {}, {"status": semantic_checks.JEV_NOT_CONNECTED, "detail": "미연결"}
    await jobs.add_event(
        db,
        job_id=job_id,
        event_type=jobs.EVENT_CONNECTION,
        role=ConnectionRole.JEV.value,
        base_url=connection.base_url,
        model=connection.model,
    )
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_JUDGING, total=len(candidates))
    await db.commit()
    labels = list(
        await db.scalars(
            select(Label)
            .where(Label.dataset_id == map_row.dataset_id)
            .order_by(Label.name, Label.id)
        )
    )
    questions = semantic_checks.jev_questions(labels)
    texts = await _texts_for(
        db,
        dataset_id=map_row.dataset_id,
        text_hashes=tuple(text_hashes[suspect.index] for suspect in candidates),
    )
    verdicts: dict[int, semantic_checks.JevVerdict] = {}
    for done, (suspect, text) in enumerate(zip(candidates, texts, strict=True), start=1):
        try:
            decision = await jev.decide(
                connection, state={"text": text}, questions=questions, transport=transport
            )
        except ExternalServiceError as error:
            return verdicts, {"status": semantic_checks.JEV_FAILED, "detail": error.message}
        verdicts[suspect.index] = semantic_checks.read_verdict(
            decision, labels=labels, label_id=suspect.label_id
        )
        is_checkpoint = done % JEV_PROGRESS_EVERY == 0 or done == len(candidates)
        if is_checkpoint:
            await jobs.set_progress(db, job_id=job_id, done=done, total=len(candidates))
            await db.commit()
            if await _stop_if_canceled(db, map_row=map_row, job_id=job_id):
                return None
    is_capped = len(findings.suspects) > len(candidates)
    detail = f"{len(verdicts)}건" + (
        f" · 상한 {semantic_checks.MAX_JEV_JUDGMENTS}" if is_capped else ""
    )
    return verdicts, {"status": semantic_checks.JEV_JUDGED, "detail": detail}


async def _unique_hashes(db: AsyncSession, *, dataset_id: int) -> list[str]:
    """휴지통 밖 문장의 서로 다른 문장 해시들(해시 순). 같은 문장 인덱스만 훑는다."""
    query = (
        select(Record.text_hash)
        .where(Record.dataset_id == dataset_id, classification_service.IS_ACTIVE)
        .group_by(Record.text_hash)
        .order_by(Record.text_hash)
    )
    return list(await db.scalars(query))


async def _embed_missing(
    db: AsyncSession,
    *,
    map_row: Map,
    job_id: int,
    connection: Connection,
    model: EmbeddingModel,
    missing: Sequence[str],
    transport: httpx.AsyncBaseTransport | None,
) -> int | None:
    """캐시에 없는 문장을 묶음마다 임베딩해 저장하고, 저장한 수를 돌려준다. 멈추라고 했으면 None.

    다음 묶음 요청을 먼저 보내 두고 이번 묶음을 저장한다. 저장하는 동안에도 임베딩 서버가 쉬지 않는다.
    멈추거나 저장이 실패하면 보내 둔 요청은 거두고 그 결과는 버린다(저장한 묶음은 캐시에 남는다).
    """
    chunks = list(itertools.batched(missing, EMBED_CHUNK))
    if not chunks:
        return 0
    if await _stop_if_canceled(db, map_row=map_row, job_id=job_id):
        return None

    async def note_retry(attempt: int, reason: str) -> None:
        # 본 세션은 앞 묶음을 저장하는 중일 수 있어서 따로 된 세션으로 적는다.
        await jobs.record_event(
            job_id=job_id,
            event_type=jobs.EVENT_RETRY,
            role=ConnectionRole.EMBEDDING.value,
            attempt=attempt,
            reason=reason,
        )

    embedded = 0
    pending = await _request_embeddings(
        db,
        dataset_id=map_row.dataset_id,
        connection=connection,
        chunk=chunks[0],
        on_retry=note_retry,
        transport=transport,
    )
    try:
        for index, chunk in enumerate(chunks):
            vectors = await pending
            has_next = index + 1 < len(chunks)
            if has_next:
                pending = await _request_embeddings(
                    db,
                    dataset_id=map_row.dataset_id,
                    connection=connection,
                    chunk=chunks[index + 1],
                    on_retry=note_retry,
                    transport=transport,
                )
            await embedding.save_vectors(db, model=model, text_hashes=chunk, vectors=vectors)
            embedded += len(chunk)
            await jobs.set_progress(db, job_id=job_id, done=embedded, total=len(missing))
            await db.commit()
            # 마지막 묶음 뒤에는 build_map이 좌표 단계 전에 본다.
            if has_next and await _stop_if_canceled(db, map_row=map_row, job_id=job_id):
                return None
    finally:
        pending.cancel()
        # 거둔 요청의 결과(취소 · 오류)를 받아 둔다. 받지 않으면 asyncio가 '받지 않은 예외'로 경고한다.
        await asyncio.gather(pending, return_exceptions=True)
    return embedded


async def _request_embeddings(
    db: AsyncSession,
    *,
    dataset_id: int,
    connection: Connection,
    chunk: tuple[str, ...],
    on_retry: embedding.RetryHandler,
    transport: httpx.AsyncBaseTransport | None,
) -> asyncio.Task[list[list[float]]]:
    """묶음의 문장을 읽고 임베딩 요청을 보낸다. 답은 돌려준 작업(Task)에서 받는다.

    작업은 임베딩 서버에만 묻고 이 세션을 쓰지 않는다(다시 보내기는 on_retry가 따로 된 세션으로 적는다).
    그래서 답을 기다리는 동안 같은 세션으로 앞 묶음을 저장해도 된다.
    """
    texts = await _texts_for(db, dataset_id=dataset_id, text_hashes=chunk)
    return asyncio.create_task(
        embedding.embed_texts(connection, texts, on_retry=on_retry, transport=transport)
    )


async def _texts_for(
    db: AsyncSession, *, dataset_id: int, text_hashes: tuple[str, ...]
) -> list[str]:
    """해시마다 임베딩할 문장(정규화한 것)을 해시 순서대로. 만드는 동안 문장이 사라졌으면 InvalidInputError.

    해시는 정규화한 문장으로 만들므로, 정규화한 문장을 보내면 같은 해시에는 늘 같은 벡터가 붙는다.
    """
    query = (
        select(Record.text_hash, Record.text)
        .where(Record.dataset_id == dataset_id, Record.text_hash.in_(text_hashes))
        .distinct(Record.text_hash)
        .order_by(Record.text_hash, Record.id)
    )
    texts = {text_hash: text for text_hash, text in await db.execute(query)}
    is_complete = all(text_hash in texts for text_hash in text_hashes)
    if not is_complete:
        raise InvalidInputError(TEXTS_CHANGED_MESSAGE)
    return [normalize_text(texts[text_hash]) for text_hash in text_hashes]


async def _insert_points(
    db: AsyncSession,
    *,
    map_row: Map,
    text_hashes: list[str],
    xs: list[float],
    ys: list[float],
) -> None:
    """해시별 좌표를 그 해시의 휴지통 밖 문장마다 점으로 적는다. 커밋은 부른 쪽이 한다.

    좌표 목록을 배열 인자로 한 번에 넘겨 DB 안에서 문장과 잇는다(문장마다 파이썬을 오가지 않게).
    """
    coords = (
        func.unnest(
            literal(text_hashes, ARRAY(String)),
            literal(xs, ARRAY(REAL)),
            literal(ys, ARRAY(REAL)),
        )
        .table_valued("text_hash", "x", "y")
        .render_derived()
    )
    points = (
        select(literal(map_row.id, BigInteger), Record.id, coords.c.x, coords.c.y)
        .join_from(Record, coords, Record.text_hash == coords.c.text_hash)
        .where(Record.dataset_id == map_row.dataset_id, classification_service.IS_ACTIVE)
    )
    await db.execute(insert(MapPoint).from_select(["map_id", "record_id", "x", "y"], points))


async def _stop_if_canceled(db: AsyncSession, *, map_row: Map, job_id: int) -> bool:
    """멈추라고 했으면 지도와 작업을 멈춘 것으로 끝내고 True. 이미 계산한 임베딩은 캐시에 남긴다."""
    if not await jobs.is_cancel_requested(db, job_id=job_id):
        return False
    map_row.status = MapStatus.CANCELED
    map_row.finished_at = datetime.now(UTC)
    await jobs.cancel_job(db, job_id=job_id)
    await db.commit()
    return True


def _flagged_records(*, dataset_id: int, map_id: int) -> Subquery:
    """휴지통 밖 문장마다 라벨 · 표시 비트. 표시는 문장 목록의 문제 거르기와 같은 규칙이다.

    근접 중복 · 오라벨 의심은 이 지도(뜻 분석 한 번)의 결과로 표시한다.
    """
    same_text = Record.text_hash
    is_duplicate = func.count().over(partition_by=records_service.DUPLICATE_KEY) > 1
    # 라벨이 둘 이상: 가장 작은 라벨 번호와 가장 큰 번호가 다르다.
    lowest_label = func.min(Record.label_id).over(partition_by=same_text)
    highest_label = func.max(Record.label_id).over(partition_by=same_text)
    is_conflict = lowest_label != highest_label
    is_short = func.char_length(Record.text) < records_service.SHORT_TEXT_LENGTH
    is_excluded = Record.exclude_reason.is_not(None)
    is_near_duplicate = Record.text_hash.in_(records_service.near_duplicate_hashes(map_id))
    is_suspect = tuple_(Record.text_hash, Record.label_id).in_(
        records_service.open_suspect_keys(map_id)
    )
    flags = (
        case((is_duplicate, FLAG_DUPLICATE), else_=0)
        + case((is_conflict, FLAG_CONFLICT), else_=0)
        + case((is_short, FLAG_SHORT), else_=0)
        + case((is_excluded, FLAG_EXCLUDED), else_=0)
        + case((is_near_duplicate, FLAG_NEAR_DUPLICATE), else_=0)
        + case((is_suspect, FLAG_SUSPECT), else_=0)
    )
    return (
        select(Record.id, Record.label_id, flags.label("flags"))
        .where(Record.dataset_id == dataset_id, classification_service.IS_ACTIVE)
        .subquery("flagged")
    )


def _rounded(column: InstrumentedAttribute[float]) -> ColumnElement[float]:
    """좌표를 COORD_DECIMALS 자리로 줄여 float으로. numeric 그대로면 JSON에 글자로 나간다."""
    return cast(func.round(cast(column, Numeric), COORD_DECIMALS), Float)
