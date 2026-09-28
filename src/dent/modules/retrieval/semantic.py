"""검색 뜻 분석: 질의 · 문서를 임베딩해 지도 · 주제 무리 · 근접 중복 · 기준 검색 순위 · 기준 검색 점수 · 제안을 만든다.
시작 · 상태 · 멈추기 · 지도 점과 작업 실행. 분석 한 줄(retrieval_analyses)이 뜻 분석 한 번이다.

사용자가 [뜻 분석 만들기]를 누르면 분석 한 줄과 작업 하나를 넣고, 작업 실행기가 build_analysis를 부른다.
1. 임베딩: 휴지통 밖 질의 · 코퍼스 문서의 서로 다른 글(해시) 가운데 캐시에 없는 것만 묶음으로 계산해 저장한다.
   문서는 학습 글(머리말 + 떼기로 고른 구간을 뺀 본문, input_hash)로 임베딩한다(학습 · 색인과 같은 글).
   학습할 모델(설정)이 있으면 연결된 임베딩 서버에 그 모델 이름으로 묻는다(없으면 연결의 모델).
2. 좌표: 질의 · 문서 벡터를 한 지도에 UMAP으로 놓는다(스레드에서).
3. 분석: 주제 무리 · 근접 중복 · 기준 검색 순위 · 기준 검색 점수 · 제안 후보(semantic_checks.analyze, 스레드에서).
4. Jev 확인: 제안 후보를 Jev에 묻는다(최대 MAX_JEV_JUDGMENTS건). 없거나 실패해도 분석은 끝난다.
   빠진 정답은 Jev가 예라고 한 것만 제안으로 남긴다.
5. 저장: 점 · 순위 · 근접 중복 · 제안과 요약(checks). 앞 분석에서 사람이 '유지'한 제안은 이어 간다.
   첫 분석이면 기준 검색 점수를 설정의 baseline으로 적는다(정제 전 → 후를 견준다).
끝나면 이 데이터셋의 다른 분석(앞의 것, 실패 · 취소한 시도)을 지운다. 실패하면 앞의 분석은 그대로 남는다.
"""

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import numpy as np
from sqlalchemy import (
    REAL,
    BigInteger,
    ColumnElement,
    Float,
    Integer,
    Numeric,
    case,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from dent.modules.retrieval import judging, problems, semantic_checks
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    ANALYSIS_BUILDING_STATUSES,
    POSITIVE_MIN_GRADE,
    Analysis,
    AnalysisStatus,
    DatasetSettings,
    Document,
    ItemKind,
    Judgment,
    MapPoint,
    NearDuplicate,
    Query,
    Ranking,
    Suggestion,
    SuggestionDecision,
    SuggestionKind,
)
from dent.modules.retrieval.schemas import MapPointsRead
from dent.modules.retrieval.service import (
    DOCUMENT_ACTIVE,
    DOCUMENT_INPUT,
    QUERY_ACTIVE,
    QUERY_INCLUDED,
)
from dent.system import connections, embedding, jobs, projection
from dent.system.db import flush_or_conflict
from dent.system.exceptions import (
    ConflictError,
    ExternalServiceError,
    InvalidInputError,
    NotFoundError,
)
from dent.system.models import Connection, ConnectionRole, EmbeddingModel
from dent.system.text import normalize_text

# 뜻 분석 작업의 종류. 작업 실행기는 (모듈 이름, ANALYSIS_JOB_KIND)로 처리 함수를 찾는다.
ANALYSIS_JOB_KIND = "analysis"

# 작업 단계 이름 (작업 result의 phase). 화면(map/analysis.ts)과 같은 이름이다.
PHASE_EMBEDDING = "embedding"
PHASE_PROJECTING = "projecting"
PHASE_ANALYZING = "analyzing"
PHASE_JUDGING = "judging"
PHASE_SAVING = "saving"

# 임베딩 단계에서 한 번에 계산하고 저장하는 글 수 (요청 묶음 × 동시 요청)
EMBED_CHUNK = embedding.EMBEDDING_BATCH_SIZE * embedding.EMBEDDING_MAX_CONCURRENCY

# Jev 확인: 이만큼 할 때마다 진행률을 적고 멈추라고 했는지 본다. 묻는 최대 수(laya 1건 30~60ms면 2분쯤).
JEV_PROGRESS_EVERY = 20
MAX_JEV_JUDGMENTS = 2_000

# 저장 단계에서 쿼리 하나로 적는 줄 수
SAVE_CHUNK = 20_000

# 점 좌표를 내보낼 때 남기는 소수 자리
COORD_DECIMALS = 4

# 검색어가 든 질의 · 문서를 찾을 때의 최대 수 (지도 강조)
MATCHES_LIMIT = 50_000

# 지도 말풍선의 문서 앞부분 글자 수
MAP_TEXT_LENGTH = 300

# 지도 점의 문제 표시 비트 (flag_names의 자리). 질의 · 문서가 같은 이름을 쓰면 같은 비트다.
QUERY_FLAGS = ("no_positive", "false_negative", "suspect", "missing", "no_negative")
DOCUMENT_FLAGS = ("near", "long", "duplicate")
FLAG_NAMES = (*QUERY_FLAGS, *DOCUMENT_FLAGS)

# 멈추라고 했는지 보는 함수 (멈췄으면 뒷정리까지 하고 True)
type CancelCheck = Callable[[], Awaitable[bool]]

EMBEDDING_NOT_CONNECTED_MESSAGE = "임베딩 미연결: 연결 설정에서 임베딩 서버를 연결하세요."
ALREADY_BUILDING_MESSAGE = "뜻 분석을 이미 만드는 중입니다."
NOT_BUILDING_MESSAGE = "만드는 중인 뜻 분석이 없습니다."
NOTHING_TO_ANALYZE_MESSAGE = "분석할 질의 · 문서가 없습니다."
NO_ANALYSIS_MESSAGE = "뜻 분석이 없습니다. 먼저 만들어 주세요."
TEXTS_CHANGED_MESSAGE = "만드는 동안 질의 · 문서가 바뀌었습니다. 다시 만들어 주세요."


@dataclass(frozen=True)
class AnalysisState:
    """데이터셋의 뜻 분석 상태."""

    # 다 만든 가장 최근 분석. 없으면 None
    done: Analysis | None

    # done보다 나중에 누른 시도(만드는 중 · 실패 · 취소). 없으면 None
    run: Analysis | None

    # done 이후 질의 · 문서가 바뀌었는지
    outdated: bool

    # done을 만든 임베딩 모델 이름
    model: str | None


@dataclass(frozen=True)
class Rows:
    """분석할 질의 · 문서 행(번호 · 해시, 행 순서)."""

    query_ids: list[int]
    query_hashes: list[str]
    document_ids: list[int]
    document_hashes: list[str]


# ---------- 시작 · 상태 · 멈추기 (API) ----------


async def start_analysis(db: AsyncSession, *, dataset_id: int) -> AnalysisState:
    """뜻 분석 만들기를 대기열에 넣는다.

    데이터셋이 없으면 NotFoundError, 임베딩 미연결이거나 질의 · 문서가 없으면 InvalidInputError,
    이미 만드는 중이면 ConflictError.
    """
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
        raise InvalidInputError(EMBEDDING_NOT_CONNECTED_MESSAGE)
    if await _building(db, dataset_id=dataset_id) is not None:
        raise ConflictError(ALREADY_BUILDING_MESSAGE)
    _query_count, document_count = await _counts(db, dataset_id=dataset_id)
    if document_count == 0:
        raise InvalidInputError(NOTHING_TO_ANALYZE_MESSAGE)

    await db.execute(
        delete(Analysis).where(
            Analysis.dataset_id == dataset_id,
            Analysis.status.in_((AnalysisStatus.FAILED, AnalysisStatus.CANCELED)),
        )
    )
    analysis = Analysis(dataset_id=dataset_id)
    db.add(analysis)
    # 두 창에서 동시에 눌러도 부분 UNIQUE 인덱스가 둘째를 막는다.
    await flush_or_conflict(db, message=ALREADY_BUILDING_MESSAGE)
    job = await jobs.enqueue_job(
        db,
        module=retrieval_service.MODULE_NAME,
        kind=ANALYSIS_JOB_KIND,
        params={"analysis_id": analysis.id},
        dataset_id=dataset_id,
    )
    analysis.job_id = job.id
    await db.commit()
    return await get_analysis_state(db, dataset_id=dataset_id)


async def get_analysis_state(db: AsyncSession, *, dataset_id: int) -> AnalysisState:
    """다 만든 분석, 그 뒤의 시도, 분석 이후 바뀌었는지. 데이터셋이 없으면 NotFoundError."""
    dataset = await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    rows = list(
        await db.scalars(
            select(Analysis)
            .where(Analysis.dataset_id == dataset_id)
            .order_by(Analysis.id.desc())
            .execution_options(populate_existing=True)
        )
    )
    done = next((row for row in rows if row.status == AnalysisStatus.DONE), None)
    latest = rows[0] if rows else None
    run = latest if latest is not None and latest is not done else None
    outdated = False
    model_name = None
    if done is not None:
        query_count, document_count = await _counts(db, dataset_id=dataset_id)
        is_count_changed = (query_count, document_count) != (done.query_count, done.document_count)
        is_touched_later = (
            done.dataset_updated_at is None or dataset.updated_at > done.dataset_updated_at
        )
        outdated = is_count_changed or is_touched_later
        if done.model_id is not None:
            model = await db.get(EmbeddingModel, done.model_id)
            model_name = model.name if model else None
    return AnalysisState(done=done, run=run, outdated=outdated, model=model_name)


async def cancel_analysis(db: AsyncSession, *, dataset_id: int) -> AnalysisState:
    """만드는 중인 분석을 멈춘다. 없으면 ConflictError."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    building = await _building(db, dataset_id=dataset_id)
    if building is None:
        raise ConflictError(NOT_BUILDING_MESSAGE)
    is_stopped_now = building.job_id is None or await jobs.request_cancel(
        db, job_id=building.job_id
    )
    if is_stopped_now:
        building.status = AnalysisStatus.CANCELED
        building.finished_at = datetime.now(UTC)
    await db.commit()
    return await get_analysis_state(db, dataset_id=dataset_id)


async def list_map_points(db: AsyncSession, *, dataset_id: int) -> MapPointsRead:
    """다 만든 분석의 점(문서 먼저, 질의가 위에 그려지게 뒤에). 출처 · 표시는 지금 값. 없으면 NotFoundError."""
    state = await get_analysis_state(db, dataset_id=dataset_id)
    if state.done is None:
        raise NotFoundError(NO_ANALYSIS_MESSAGE)
    analysis_id = state.done.id
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    document_flags = _flag_sum(
        [
            (
                FLAG_NAMES.index(name),
                problems.document_problem(name, settings=settings, analysis_id=analysis_id),
            )
            for name in DOCUMENT_FLAGS
        ]
    )
    query_flags = _flag_sum(
        [
            (
                FLAG_NAMES.index(name),
                problems.query_problem(name, settings=settings, analysis_id=analysis_id),
            )
            for name in QUERY_FLAGS
        ]
    )
    documents = (
        await db.execute(
            select(
                MapPoint.document_id,
                _rounded(MapPoint.x),
                _rounded(MapPoint.y),
                MapPoint.cluster,
                document_flags,
            )
            .join(Document, Document.id == MapPoint.document_id)
            .where(MapPoint.analysis_id == analysis_id, DOCUMENT_ACTIVE)
            .order_by(MapPoint.document_id)
        )
    ).all()
    queries = (
        await db.execute(
            select(
                MapPoint.query_id,
                _rounded(MapPoint.x),
                _rounded(MapPoint.y),
                MapPoint.cluster,
                Query.source,
                query_flags,
            )
            .join(Query, Query.id == MapPoint.query_id)
            .where(MapPoint.analysis_id == analysis_id, QUERY_ACTIVE)
            .order_by(MapPoint.query_id)
        )
    ).all()
    topics = state.done.checks.get("topics") or {}
    return MapPointsRead(
        analysis_id=analysis_id,
        kinds=[ItemKind.DOCUMENT.value] * len(documents) + [ItemKind.QUERY.value] * len(queries),
        ids=[row[0] for row in documents] + [row[0] for row in queries],
        x=[row[1] for row in documents] + [row[1] for row in queries],
        y=[row[2] for row in documents] + [row[2] for row in queries],
        clusters=[row[3] for row in documents] + [row[3] for row in queries],
        sources=[None] * len(documents) + [row[4] for row in queries],
        flags=[row[4] for row in documents] + [row[5] for row in queries],
        flag_names=list(FLAG_NAMES),
        empty_clusters=[int(cluster) for cluster in topics.get("empty", [])],
    )


async def map_matches(db: AsyncSession, *, dataset_id: int, q: str) -> tuple[list[int], list[int]]:
    """검색어가 든 휴지통 밖 질의 · 코퍼스 문서 번호들 (지도 강조)."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    pattern = f"%{_escape_like(q)}%"
    query_ids = list(
        await db.scalars(
            select(Query.id)
            .where(Query.dataset_id == dataset_id, QUERY_ACTIVE, Query.text.ilike(pattern))
            .limit(MATCHES_LIMIT)
        )
    )
    document_ids = list(
        await db.scalars(
            select(Document.id)
            .where(
                Document.dataset_id == dataset_id,
                DOCUMENT_ACTIVE,
                or_(Document.text.ilike(pattern), Document.title.ilike(pattern)),
            )
            .limit(MATCHES_LIMIT)
        )
    )
    return query_ids, document_ids


async def map_text(
    db: AsyncSession, *, dataset_id: int, kind: ItemKind, item_id: int
) -> tuple[str, str]:
    """지도 점 하나의 (글, 제목). 질의면 제목은 빈 글. 없거나 다른 데이터셋이면 NotFoundError."""
    if kind == ItemKind.QUERY:
        query = await db.get(Query, item_id)
        if query is None or query.dataset_id != dataset_id:
            raise NotFoundError("질의를 찾을 수 없습니다.")
        return query.text, ""
    document = await db.get(Document, item_id)
    if document is None or document.dataset_id != dataset_id:
        raise NotFoundError("문서를 찾을 수 없습니다.")
    return document.text[:MAP_TEXT_LENGTH], document.title


# ---------- 만들기 (작업 실행기) ----------


async def build_analysis(
    db: AsyncSession,
    *,
    analysis_id: int,
    job_id: int,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """뜻 분석 하나를 처음부터 끝까지 만든다. 멈추라고 하면 멈춘 것으로 끝낸다.

    임베딩이 끊겼거나 서버가 안 되면 도메인 예외를 낸다(부른 쪽이 fail_analysis로 실패 처리한다).
    """
    analysis = await _get(db, analysis_id=analysis_id)
    dataset_id = analysis.dataset_id
    dataset = await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    connection = await embedding_connection(db, settings=settings)
    model = await embedding.ensure_model(db, connection, transport=transport)

    rows = await _rows(db, dataset_id=dataset_id)
    unique_hashes = sorted({*rows.query_hashes, *rows.document_hashes})
    analysis.status = AnalysisStatus.RUNNING
    analysis.model_id = model.id
    analysis.params = {}
    analysis.checks = {}
    analysis.error = None
    analysis.finished_at = None
    analysis.query_count = len(rows.query_ids)
    analysis.document_count = len(rows.document_ids)
    analysis.dataset_updated_at = dataset.updated_at
    await jobs.add_event(
        db,
        job_id=job_id,
        event_type=jobs.EVENT_CONNECTION,
        role=ConnectionRole.EMBEDDING.value,
        base_url=connection.base_url,
        model=connection.model,
    )
    await db.commit()
    timings: dict[str, float] = {}

    # 1. 임베딩
    started = time.perf_counter()
    missing = await embedding.find_missing_hashes(db, model_id=model.id, text_hashes=unique_hashes)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_EMBEDDING, total=len(missing))
    await db.commit()
    embedded = await _embed_missing(
        db,
        analysis=analysis,
        job_id=job_id,
        connection=connection,
        model=model,
        missing=missing,
        transport=transport,
    )
    if embedded is None:
        return
    timings["embed_seconds"] = time.perf_counter() - started

    # 2. 좌표: 문서 · 질의를 한 지도에
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_PROJECTING, total=None)
    await db.commit()
    started = time.perf_counter()
    unique_vectors = await embedding.load_vectors(db, model=model, text_hashes=unique_hashes)
    position = {text_hash: index for index, text_hash in enumerate(unique_hashes)}
    document_vectors = unique_vectors[[position[h] for h in rows.document_hashes]]
    query_vectors = unique_vectors[[position[h] for h in rows.query_hashes]]
    del unique_vectors
    all_vectors = (
        np.concatenate([document_vectors, query_vectors])
        if len(query_vectors)
        else (document_vectors)
    )
    projected = await asyncio.to_thread(projection.project_2d, all_vectors)
    del all_vectors
    timings["projection_seconds"] = time.perf_counter() - started
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return

    # 3. 분석
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_ANALYZING, total=None)
    await db.commit()
    started = time.perf_counter()
    facts = await _query_facts(db, dataset_id=dataset_id, rows=rows)
    margin = settings.mine_margin if settings else 0.95
    findings = await asyncio.to_thread(
        semantic_checks.analyze, query_vectors, document_vectors, facts, margin=margin
    )
    del query_vectors, document_vectors
    timings["analysis_seconds"] = time.perf_counter() - started
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return

    # 4. Jev 확인
    started = time.perf_counter()
    judged = await _judge_candidates(
        db, analysis=analysis, job_id=job_id, rows=rows, findings=findings, transport=jev_transport
    )
    if judged is None:
        return
    probabilities, jev_state = judged
    timings["judge_seconds"] = time.perf_counter() - started

    # 5. 저장
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_SAVING, total=None)
    await _clear_results(db, analysis_id=analysis_id)
    await _save_points(
        db, analysis_id=analysis_id, rows=rows, findings=findings, coords=projected.coords
    )
    await _save_rankings(db, analysis_id=analysis_id, rows=rows, findings=findings)
    await _save_near_pairs(db, analysis_id=analysis_id, rows=rows, findings=findings)
    suggestion_counts = await _save_suggestions(
        db,
        analysis=analysis,
        rows=rows,
        findings=findings,
        probabilities=probabilities,
    )
    analysis.checks = {
        "kpi": findings.kpi,
        "topics": {
            "largest_share": round(findings.largest_share, 4),
            "clusters": findings.cluster_count,
            "empty": findings.empty_clusters,
        },
        "near": {"queries": len(findings.query_pairs), "documents": len(findings.document_pairs)},
        "suggestions": suggestion_counts,
        "jev": jev_state,
    }
    if settings is not None and findings.kpi and not settings.baseline:
        settings.baseline = {
            "recall_at_10": findings.kpi["recall_at_10"],
            "mrr_at_10": findings.kpi["mrr_at_10"],
        }
    analysis.status = AnalysisStatus.DONE
    analysis.params = projected.params
    analysis.finished_at = datetime.now(UTC)
    # 다 만든 분석은 데이터셋마다 하나다. 앞의 분석과 실패 · 취소한 시도를 지운다(점 · 순위 · 제안도 함께).
    await db.execute(
        delete(Analysis).where(Analysis.dataset_id == dataset_id, Analysis.id != analysis_id)
    )
    await jobs.finish_job(
        db,
        job_id=job_id,
        result={
            "analysis_id": analysis_id,
            "queries": len(rows.query_ids),
            "documents": len(rows.document_ids),
            "embedded": embedded,
            **{name: round(seconds, 1) for name, seconds in timings.items()},
            "suggestions": sum(suggestion_counts.values()),
        },
    )
    await db.commit()


async def fail_analysis(db: AsyncSession, *, analysis_id: int, job_id: int, message: str) -> None:
    """분석과 작업을 실패로 끝낸다. 반쯤 적은 결과는 지운다. 앞의 분석은 그대로 둔다."""
    await db.rollback()
    analysis = await db.get(Analysis, analysis_id, populate_existing=True)
    if analysis is not None:
        await _clear_results(db, analysis_id=analysis_id)
        analysis.status = AnalysisStatus.FAILED
        analysis.error = message
        analysis.finished_at = datetime.now(UTC)
    await jobs.fail_job(db, job_id=job_id, message=message)


async def embedding_connection(db: AsyncSession, *, settings: DatasetSettings | None) -> Connection:
    """이 데이터셋이 쓸 임베딩 연결. 학습할 모델(설정)이 있으면 같은 서버에 그 모델 이름으로 묻는다.

    임베딩이 연결되지 않았으면 InvalidInputError. 오답 찾기도 이 연결을 쓴다.
    """
    connection = await connections.get_connection(db, role=ConnectionRole.EMBEDDING)
    if connection is None:
        raise InvalidInputError(EMBEDDING_NOT_CONNECTED_MESSAGE)
    target = settings.target_model if settings else None
    if not target or target == connection.model:
        return connection
    # 세션에 넣지 않는 임시 연결이다(저장된 연결은 그대로 둔다).
    return Connection(
        role=connection.role,
        base_url=connection.base_url,
        model=target,
        provider=connection.provider,
        api_key=connection.api_key,
    )


async def texts_for(db: AsyncSession, *, dataset_id: int, text_hashes: Sequence[str]) -> list[str]:
    """해시마다 임베딩할 글(정규화한 것)을 해시 순서대로. 질의 글 · 문서 학습 글 어디서든 찾는다.

    사라졌으면 InvalidInputError.
    """
    found: dict[str, str] = {}
    sources = (
        (Query.text_hash, Query.text, Query),
        (Document.input_hash, DOCUMENT_INPUT, Document),
    )
    for hash_column, text_column, model in sources:
        query = (
            select(hash_column, text_column)
            .where(model.dataset_id == dataset_id, hash_column.in_(text_hashes))
            .distinct(hash_column)
            .order_by(hash_column, model.id)
        )
        for text_hash, text in await db.execute(query):
            found.setdefault(text_hash, text)
    if not all(text_hash in found for text_hash in text_hashes):
        raise InvalidInputError(TEXTS_CHANGED_MESSAGE)
    return [normalize_text(found[text_hash]) for text_hash in text_hashes]


async def embed_missing_hashes(
    db: AsyncSession,
    *,
    dataset_id: int,
    job_id: int,
    connection: Connection,
    model: EmbeddingModel,
    missing: Sequence[str],
    is_canceled: CancelCheck,
    transport: httpx.AsyncBaseTransport | None,
) -> int | None:
    """캐시에 없는 글을 묶음마다 임베딩해 저장하고 저장한 수를 돌려준다. 멈추라고 했으면 None.

    다음 묶음 요청을 먼저 보내 두고 이번 묶음을 저장한다(분류 뜻 분석과 같은 방식). 오답 찾기도 쓴다.
    """
    chunks = list(itertools.batched(missing, EMBED_CHUNK))
    if not chunks:
        return 0

    async def note_retry(attempt: int, reason: str) -> None:
        await jobs.record_event(
            job_id=job_id,
            event_type=jobs.EVENT_RETRY,
            role=ConnectionRole.EMBEDDING.value,
            attempt=attempt,
            reason=reason,
        )

    async def request(chunk: tuple[str, ...]) -> asyncio.Task[list[list[float]]]:
        texts = await texts_for(db, dataset_id=dataset_id, text_hashes=chunk)
        return asyncio.create_task(
            embedding.embed_texts(connection, texts, on_retry=note_retry, transport=transport)
        )

    embedded = 0
    pending = await request(chunks[0])
    try:
        for index, chunk in enumerate(chunks):
            vectors = await pending
            has_next = index + 1 < len(chunks)
            if has_next:
                pending = await request(chunks[index + 1])
            await embedding.save_vectors(db, model=model, text_hashes=chunk, vectors=vectors)
            embedded += len(chunk)
            await jobs.set_progress(db, job_id=job_id, done=embedded, total=len(missing))
            await db.commit()
            if has_next and await is_canceled():
                return None
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    return embedded


# ---------- 안에서만 쓰는 함수 ----------


async def _get(db: AsyncSession, *, analysis_id: int) -> Analysis:
    analysis = await db.get(Analysis, analysis_id, populate_existing=True)
    if analysis is None:
        raise NotFoundError(NO_ANALYSIS_MESSAGE)
    return analysis


async def _building(db: AsyncSession, *, dataset_id: int) -> Analysis | None:
    return await db.scalar(
        select(Analysis).where(
            Analysis.dataset_id == dataset_id, Analysis.status.in_(ANALYSIS_BUILDING_STATUSES)
        )
    )


async def _counts(db: AsyncSession, *, dataset_id: int) -> tuple[int, int]:
    """휴지통 밖 질의 수 · 코퍼스 문서 수."""
    query_count = await db.scalar(
        select(func.count()).select_from(Query).where(Query.dataset_id == dataset_id, QUERY_ACTIVE)
    )
    document_count = await db.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
    )
    return int(query_count or 0), int(document_count or 0)


async def _rows(db: AsyncSession, *, dataset_id: int) -> Rows:
    """분석할 질의 · 문서의 번호와 해시(번호 순)."""
    queries = (
        await db.execute(
            select(Query.id, Query.text_hash)
            .where(Query.dataset_id == dataset_id, QUERY_ACTIVE)
            .order_by(Query.id)
        )
    ).all()
    documents = (
        await db.execute(
            select(Document.id, Document.input_hash)
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
            .order_by(Document.id)
        )
    ).all()
    return Rows(
        query_ids=[row[0] for row in queries],
        query_hashes=[row[1] for row in queries],
        document_ids=[row[0] for row in documents],
        document_hashes=[row[1] for row in documents],
    )


async def _query_facts(
    db: AsyncSession, *, dataset_id: int, rows: Rows
) -> list[semantic_checks.QueryFacts]:
    """질의마다 학습에 쓰는지 · 정답 · 오답 문서 자리 (행 순서)."""
    document_position = {document_id: index for index, document_id in enumerate(rows.document_ids)}
    info = {
        query_id: bool(included)
        for query_id, included in await db.execute(
            select(Query.id, QUERY_INCLUDED).where(Query.dataset_id == dataset_id, QUERY_ACTIVE)
        )
    }
    positives: dict[int, list[int]] = {}
    negatives: dict[int, list[int]] = {}
    for query_id, document_id, grade in await db.execute(
        select(Judgment.query_id, Judgment.document_id, Judgment.grade).where(
            Judgment.dataset_id == dataset_id
        )
    ):
        position = document_position.get(document_id)
        if position is None or query_id not in info:
            continue
        target = positives if grade >= POSITIVE_MIN_GRADE else negatives
        target.setdefault(query_id, []).append(position)
    facts = []
    for query_id in rows.query_ids:
        if query_id not in info:
            raise InvalidInputError(TEXTS_CHANGED_MESSAGE)
        facts.append(
            semantic_checks.QueryFacts(
                included=info[query_id],
                positives=tuple(positives.get(query_id, ())),
                negatives=tuple(negatives.get(query_id, ())),
            )
        )
    return facts


async def _embed_missing(
    db: AsyncSession,
    *,
    analysis: Analysis,
    job_id: int,
    connection: Connection,
    model: EmbeddingModel,
    missing: Sequence[str],
    transport: httpx.AsyncBaseTransport | None,
) -> int | None:
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return None

    async def is_canceled() -> bool:
        return await _stop_if_canceled(db, analysis=analysis, job_id=job_id)

    return await embed_missing_hashes(
        db,
        dataset_id=analysis.dataset_id,
        job_id=job_id,
        connection=connection,
        model=model,
        missing=missing,
        is_canceled=is_canceled,
        transport=transport,
    )


async def _judge_candidates(
    db: AsyncSession,
    *,
    analysis: Analysis,
    job_id: int,
    rows: Rows,
    findings: semantic_checks.Findings,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[dict[int, float], dict[str, str]] | None:
    """제안 후보를 Jev에 묻고 (후보 자리 → 예 확률, Jev 상태)를 돌려준다. 멈추라고 했으면 None.

    Jev가 없거나 실패하면 거기까지의 판정과 그 상태를 돌려준다(뜻 분석은 이어 간다).
    """
    candidates = findings.candidates[:MAX_JEV_JUDGMENTS]
    if not candidates:
        return {}, {"status": judging.JEV_JUDGED, "detail": "후보 없음"}
    connection = await connections.get_connection(db, role=ConnectionRole.JEV)
    if connection is None:
        return {}, {"status": judging.JEV_NOT_CONNECTED, "detail": "미연결"}
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
    query_texts = await _texts_by_id(db, Query, {rows.query_ids[c.query_index] for c in candidates})
    document_texts = await _texts_by_id(
        db, Document, {rows.document_ids[c.document_index] for c in candidates}
    )
    probabilities: dict[int, float] = {}
    for done, candidate in enumerate(candidates, start=1):
        try:
            probability = await judging.ask_contains(
                connection,
                query=query_texts[rows.query_ids[candidate.query_index]],
                document=document_texts[rows.document_ids[candidate.document_index]],
                transport=transport,
            )
        except ExternalServiceError as error:
            return probabilities, {"status": judging.JEV_FAILED, "detail": error.message}
        if probability is not None:
            probabilities[done - 1] = probability
        is_checkpoint = done % JEV_PROGRESS_EVERY == 0 or done == len(candidates)
        if is_checkpoint:
            await jobs.set_progress(db, job_id=job_id, done=done, total=len(candidates))
            await db.commit()
            if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
                return None
    is_capped = len(findings.candidates) > len(candidates)
    detail = f"{len(probabilities)}건" + (f" · 상한 {MAX_JEV_JUDGMENTS}" if is_capped else "")
    return probabilities, {"status": judging.JEV_JUDGED, "detail": detail}


async def _texts_by_id(
    db: AsyncSession, model: type[Query] | type[Document], ids: set[int]
) -> dict[int, str]:
    """번호마다 Jev에 보일 글: 질의 글, 문서는 학습 글."""
    column = DOCUMENT_INPUT if model is Document else Query.text
    texts: dict[int, str] = {}
    for chunk in itertools.batched(sorted(ids), SAVE_CHUNK):
        for item_id, text in await db.execute(select(model.id, column).where(model.id.in_(chunk))):
            texts[item_id] = text
    return texts


async def _clear_results(db: AsyncSession, *, analysis_id: int) -> None:
    """이 분석의 점 · 순위 · 근접 중복 · 제안을 비운다."""
    for model in (MapPoint, Ranking, NearDuplicate, Suggestion):
        await db.execute(delete(model).where(model.analysis_id == analysis_id))


async def _save_points(
    db: AsyncSession,
    *,
    analysis_id: int,
    rows: Rows,
    findings: semantic_checks.Findings,
    coords: np.ndarray,
) -> None:
    """문서 · 질의마다 점 하나 (좌표 행은 문서 먼저, 그 뒤 질의)."""
    document_count = len(rows.document_ids)
    xs = coords[:, 0].tolist()
    ys = coords[:, 1].tolist()
    document_clusters = findings.document_clusters or [0] * document_count
    query_clusters = findings.query_clusters or [0] * len(rows.query_ids)
    points = [
        {
            "analysis_id": analysis_id,
            "kind": ItemKind.DOCUMENT.value,
            "query_id": None,
            "document_id": document_id,
            "x": xs[index],
            "y": ys[index],
            "cluster": document_clusters[index],
        }
        for index, document_id in enumerate(rows.document_ids)
    ] + [
        {
            "analysis_id": analysis_id,
            "kind": ItemKind.QUERY.value,
            "query_id": query_id,
            "document_id": None,
            "x": xs[document_count + index],
            "y": ys[document_count + index],
            "cluster": max(query_clusters[index], 0),
        }
        for index, query_id in enumerate(rows.query_ids)
    ]
    for chunk in itertools.batched(points, SAVE_CHUNK):
        await db.execute(insert(MapPoint), list(chunk))


async def _save_rankings(
    db: AsyncSession, *, analysis_id: int, rows: Rows, findings: semantic_checks.Findings
) -> None:
    """기준 검색 줄들. 배열 인자로 한 번에 넘겨 DB 안에서 펼친다(줄마다 파이썬을 오가지 않게)."""
    for chunk in itertools.batched(findings.rankings, SAVE_CHUNK):
        unnested = (
            func.unnest(
                literal([rows.query_ids[row.query_index] for row in chunk], ARRAY(BigInteger)),
                literal(
                    [rows.document_ids[row.document_index] for row in chunk], ARRAY(BigInteger)
                ),
                literal([row.rank for row in chunk], ARRAY(Integer)),
                literal([row.similarity for row in chunk], ARRAY(REAL)),
            )
            .table_valued("query_id", "document_id", "rank", "similarity")
            .render_derived()
        )
        await db.execute(
            insert(Ranking)
            .from_select(
                ["analysis_id", "query_id", "document_id", "rank", "similarity"],
                select(
                    literal(analysis_id, BigInteger),
                    unnested.c.query_id,
                    unnested.c.document_id,
                    unnested.c.rank,
                    unnested.c.similarity,
                ),
            )
            .on_conflict_do_nothing()
        )


async def _save_near_pairs(
    db: AsyncSession, *, analysis_id: int, rows: Rows, findings: semantic_checks.Findings
) -> None:
    pairs = [
        {
            "analysis_id": analysis_id,
            "kind": ItemKind.QUERY.value,
            "item_a": min(rows.query_ids[pair.index_a], rows.query_ids[pair.index_b]),
            "item_b": max(rows.query_ids[pair.index_a], rows.query_ids[pair.index_b]),
            "similarity": pair.similarity,
        }
        for pair in findings.query_pairs
    ] + [
        {
            "analysis_id": analysis_id,
            "kind": ItemKind.DOCUMENT.value,
            "item_a": min(rows.document_ids[pair.index_a], rows.document_ids[pair.index_b]),
            "item_b": max(rows.document_ids[pair.index_a], rows.document_ids[pair.index_b]),
            "similarity": pair.similarity,
        }
        for pair in findings.document_pairs
    ]
    for chunk in itertools.batched(pairs, SAVE_CHUNK):
        await db.execute(insert(NearDuplicate).on_conflict_do_nothing(), list(chunk))


async def _save_suggestions(
    db: AsyncSession,
    *,
    analysis: Analysis,
    rows: Rows,
    findings: semantic_checks.Findings,
    probabilities: dict[int, float],
) -> dict[str, int]:
    """제안을 적고 종류별 대기 수를 돌려준다. 앞 분석에서 '유지'한 (종류, 질의, 문서)는 유지로 이어 간다.

    빠진 정답은 Jev가 예라고 확인한 것만 남긴다. 물어본 판정 쌍의 Jev 확률은 교사 점수로 적는다.
    """
    kept = {
        (kind, query_id, document_id): decided_at
        for kind, query_id, document_id, decided_at in await db.execute(
            select(
                Suggestion.kind, Suggestion.query_id, Suggestion.document_id, Suggestion.decided_at
            )
            .join(Analysis, Analysis.id == Suggestion.analysis_id)
            .where(
                Analysis.dataset_id == analysis.dataset_id,
                Analysis.id != analysis.id,
                Suggestion.decision == SuggestionDecision.KEPT.value,
            )
        )
    }
    counts = {kind.value: 0 for kind in SuggestionKind}
    confirmed = 0
    suggestions = []
    teacher_scores: list[tuple[int, int, float]] = []
    for position, candidate in enumerate(findings.candidates):
        probability = probabilities.get(position)
        is_confirmed = judging.is_confirmed(candidate.kind, probability)
        query_id = rows.query_ids[candidate.query_index]
        document_id = rows.document_ids[candidate.document_index]
        if candidate.kind == SuggestionKind.MISSING_POSITIVE.value and not is_confirmed:
            continue
        if probability is not None and candidate.kind != SuggestionKind.MISSING_POSITIVE.value:
            teacher_scores.append((query_id, document_id, probability))
        key = (candidate.kind, query_id, document_id)
        is_kept = key in kept
        suggestions.append(
            {
                "analysis_id": analysis.id,
                "kind": candidate.kind,
                "query_id": query_id,
                "document_id": document_id,
                "rank": candidate.rank,
                "similarity": candidate.similarity,
                "jev_probability": probability,
                "is_confirmed": is_confirmed,
                "decision": SuggestionDecision.KEPT.value if is_kept else None,
                "decided_at": kept.get(key),
            }
        )
        if not is_kept:
            counts[candidate.kind] += 1
            confirmed += int(is_confirmed)
    for chunk in itertools.batched(suggestions, SAVE_CHUNK):
        await db.execute(insert(Suggestion), list(chunk))
    for query_id, document_id, probability in teacher_scores:
        await db.execute(
            update(Judgment)
            .where(Judgment.query_id == query_id, Judgment.document_id == document_id)
            .values(teacher_score=probability)
        )
    return {**counts, "confirmed": confirmed}


async def _stop_if_canceled(db: AsyncSession, *, analysis: Analysis, job_id: int) -> bool:
    """멈추라고 했으면 분석과 작업을 멈춘 것으로 끝내고 True. 계산한 임베딩은 캐시에 남긴다."""
    if not await jobs.is_cancel_requested(db, job_id=job_id):
        return False
    analysis.status = AnalysisStatus.CANCELED
    analysis.finished_at = datetime.now(UTC)
    await jobs.cancel_job(db, job_id=job_id)
    await db.commit()
    return True


def _flag_sum(conditions: list[tuple[int, ColumnElement[bool]]]) -> ColumnElement[int]:
    """조건마다 비트를 더한 표시 값."""
    total: ColumnElement[int] = literal(0, Integer)
    for bit, condition in conditions:
        total = total + case((condition, 1 << bit), else_=0)
    return cast(total, Integer)


def _rounded(column: InstrumentedAttribute[float]) -> ColumnElement[float]:
    """좌표를 COORD_DECIMALS 자리로 줄여 float으로."""
    return cast(func.round(cast(column, Numeric), COORD_DECIMALS), Float)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
