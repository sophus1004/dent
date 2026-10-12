"""생성 뜻 분석: 지시 · 응답을 임베딩해 지도 · 과제 무리 · 근접 중복을 만들고, Jev에 지시 따름을 묻는다.
시작 · 상태 · 멈추기 · 지도 점 · 가까운 짝과 작업 실행. 분석 한 줄(generation_analyses)이 뜻 분석 한 번이다.

사용자가 [뜻 분석 만들기]를 누르면 분석 한 줄과 작업 하나를 넣고, 작업 실행기가 build_analysis를 부른다.
1. 임베딩: 휴지통 밖 대화마다 지시 글(사용자 말들)과 응답 글을 해시로 모아, 캐시에 없는 것만 묶음으로 계산해 저장한다.
   지시 글에 system 말은 넣지 않는다(흔히 모든 대화에 같은 글이라 뜻을 가린다).
2. 좌표: 지시 벡터를 UMAP으로 2차원에 놓는다(스레드에서).
3. 분석: 과제 무리 · 근접 중복 · 지시 불일치 후보(semantic_checks.analyze, 스레드에서).
4. Jev: 규칙이 거절 · 짧은 응답으로 잡은 대화와 지시 불일치 후보를 묻는다(최대 MAX_JEV_JUDGMENTS건).
   Jev가 없거나 실패해도 분석은 끝난다(지시 불일치는 '기다림'으로 남는다).
5. 저장: 점 · 근접 중복 쌍 · 판정과 요약(checks).
끝나면 이 데이터셋의 다른 분석(앞의 것, 실패 · 취소한 시도)을 지운다. 실패하면 앞의 분석은 그대로 남는다.
"""

import asyncio
import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import (
    Float,
    Integer,
    Numeric,
    Text,
    and_,
    case,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased

from dent.modules.generation import judging, problems, semantic_checks
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import (
    ANALYSIS_BUILDING_STATUSES,
    Analysis,
    AnalysisStatus,
    Judgment,
    MapPoint,
    NearDuplicate,
    Pair,
    Prompt,
    Response,
    Role,
)
from dent.modules.generation.rules import CheckKey
from dent.modules.generation.schemas import MapPointsRead
from dent.modules.generation.service import HAS_OPEN_CHECKS, IS_ACTIVE, has_open
from dent.system import connections, embedding, jobs, projection
from dent.system.db import flush_or_conflict
from dent.system.exceptions import (
    ConflictError,
    ExternalServiceError,
    InvalidInputError,
    NotFoundError,
)
from dent.system.models import Connection, ConnectionRole, EmbeddingModel
from dent.system.text import make_text_hash, normalize_text

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

# 임베딩할 글의 최대 글자 수. 긴 응답은 앞부분으로 뜻을 잡는다(임베딩 모델의 최대 길이 안에서).
EMBED_TEXT_MAX_CHARS = 4_000

# 비었을 때 임베딩할 글 (빈 글은 임베딩 서버가 받지 않는다)
EMPTY_INSTRUCTION_TEXT = "(빈 지시)"
EMPTY_RESPONSE_TEXT = "(빈 응답)"

# Jev: 이만큼 할 때마다 진행률을 적고 멈추라고 했는지 본다. 묻는 최대 수(laya 1건 30~60ms면 2분쯤).
JEV_PROGRESS_EVERY = 20
MAX_JEV_JUDGMENTS = 2_000

# 안전을 Jev에 묻는 최대 대화 수. 넘으면 섞인 순서(번호의 해시)로 앞에서부터 묻고 진단에 '표본'으로 보인다.
MAX_SAFETY_JUDGMENTS = 2_000

# 선호 쌍을 Jev에 묻는 최대 쌍 수 (쌍마다 두 번: 고른 응답 · 버린 응답)
MAX_PAIR_JUDGMENTS = 1_000

# 저장 · 읽기에서 쿼리 하나로 다루는 줄 수
SAVE_CHUNK = 20_000
READ_CHUNK = 2_000

# 점 좌표를 내보낼 때 남기는 소수 자리
COORD_DECIMALS = 4

# 검색어가 든 대화를 찾을 때의 최대 수 (지도 강조)
MATCHES_LIMIT = 50_000

# 지도 말풍선 · 가까운 짝의 지시 앞부분 글자 수
MAP_TEXT_LENGTH = 300

# 가까운 짝을 보일 최대 수
NEAR_PARTNERS_LIMIT = 10

# 지도 점의 문제 표시 비트 (자리 = 비트)
FLAG_NAMES = ("rule", "duplicate", "near_duplicate", "mismatch")

# 규칙이 잡았지만 Jev에게 한 번 더 묻는 검사 (정당한 거절 · 맞는 짧은 답일 수 있다)
JEV_REVIEW_CHECKS = (CheckKey.REFUSAL, CheckKey.SHORT)

EMBEDDING_NOT_CONNECTED_MESSAGE = "임베딩 미연결: 연결 설정에서 임베딩 서버를 연결하세요."
ALREADY_BUILDING_MESSAGE = "뜻 분석을 이미 만드는 중입니다."
NOT_BUILDING_MESSAGE = "만드는 중인 뜻 분석이 없습니다."
NOTHING_TO_ANALYZE_MESSAGE = "분석할 대화가 없습니다."
NO_ANALYSIS_MESSAGE = "뜻 분석이 없습니다. 먼저 만들어 주세요."
TEXTS_CHANGED_MESSAGE = "만드는 동안 대화가 바뀌었습니다. 다시 만들어 주세요."
NOT_FOUND_MESSAGE = "대화를 찾을 수 없습니다."


@dataclass(frozen=True)
class AnalysisState:
    """데이터셋의 뜻 분석 상태."""

    # 다 만든 가장 최근 분석. 없으면 None
    done: Analysis | None

    # done보다 나중에 누른 시도(만드는 중 · 실패 · 취소). 없으면 None
    run: Analysis | None

    # done 이후 대화가 바뀌었는지
    outdated: bool

    # done을 만든 임베딩 모델 이름
    model: str | None


@dataclass
class Rows:
    """분석할 대화의 번호와 해시(행 순서 = 번호 순). 임베딩할 글은 해시의 주인 대화에서 다시 만든다."""

    prompt_ids: list[int] = field(default_factory=list)
    context_hashes: list[str] = field(default_factory=list)
    instruction_hashes: list[str] = field(default_factory=list)
    response_hashes: list[str] = field(default_factory=list)

    # 주 응답이 있는지 (지시만인 대화는 지시 불일치 · 안전을 묻지 않는다)
    has_answer: list[bool] = field(default_factory=list)

    # 해시 → (그 글을 가진 대화 번호, 응답 글인지)
    owners: dict[str, tuple[int, bool]] = field(default_factory=dict)


@dataclass(frozen=True)
class NearPartner:
    """가까운 짝 하나: 대화 번호 · 유사도 · 지시 앞부분 · 휴지통에 있는지."""

    prompt_id: int
    similarity: float
    text: str
    trashed: bool


# ---------- 시작 · 상태 · 멈추기 (API) ----------


async def start_analysis(db: AsyncSession, *, dataset_id: int) -> AnalysisState:
    """뜻 분석 만들기를 대기열에 넣는다.

    데이터셋이 없으면 NotFoundError, 임베딩 미연결이거나 대화가 없으면 InvalidInputError,
    이미 만드는 중이면 ConflictError.
    """
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
        raise InvalidInputError(EMBEDDING_NOT_CONNECTED_MESSAGE)
    if await _building(db, dataset_id=dataset_id) is not None:
        raise ConflictError(ALREADY_BUILDING_MESSAGE)
    if await _count(db, dataset_id=dataset_id) == 0:
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
        module=generation_service.MODULE_NAME,
        kind=ANALYSIS_JOB_KIND,
        params={"analysis_id": analysis.id},
        dataset_id=dataset_id,
    )
    analysis.job_id = job.id
    await db.commit()
    return await get_analysis_state(db, dataset_id=dataset_id)


async def get_analysis_state(db: AsyncSession, *, dataset_id: int) -> AnalysisState:
    """다 만든 분석, 그 뒤의 시도, 분석 이후 바뀌었는지. 데이터셋이 없으면 NotFoundError."""
    dataset = await generation_service.get_dataset(db, dataset_id=dataset_id)
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
        is_count_changed = await _count(db, dataset_id=dataset_id) != done.conversation_count
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
    await generation_service.get_dataset(db, dataset_id=dataset_id)
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
    """다 만든 분석의 점(휴지통 밖 대화). 출처 · 표시는 지금 값. 분석이 없으면 NotFoundError."""
    state = await get_analysis_state(db, dataset_id=dataset_id)
    if state.done is None:
        raise NotFoundError(NO_ANALYSIS_MESSAGE)
    analysis_id = state.done.id
    flags = _flag_sum(
        [
            (0, HAS_OPEN_CHECKS),
            (1, Prompt.id.in_(problems.duplicate_extras(dataset_id).scalar_subquery())),
            (2, Prompt.id.in_(problems.near_extras(dataset_id, analysis_id).scalar_subquery())),
            (3, Prompt.id.in_(problems.mismatch_ids(dataset_id, analysis_id).scalar_subquery())),
        ]
    )
    rows = (
        await db.execute(
            select(
                MapPoint.prompt_id,
                _rounded(MapPoint.x),
                _rounded(MapPoint.y),
                MapPoint.cluster,
                Prompt.source,
                flags,
            )
            .join(Prompt, Prompt.id == MapPoint.prompt_id)
            .where(MapPoint.analysis_id == analysis_id, IS_ACTIVE)
            .order_by(MapPoint.prompt_id)
        )
    ).all()
    tasks = state.done.checks.get("tasks") or {}
    return MapPointsRead(
        analysis_id=analysis_id,
        ids=[row[0] for row in rows],
        x=[row[1] for row in rows],
        y=[row[2] for row in rows],
        clusters=[row[3] for row in rows],
        sources=[row[4] for row in rows],
        flags=[row[5] for row in rows],
        flag_names=list(FLAG_NAMES),
        cluster_sizes=[int(size) for size in tasks.get("sizes", [])],
    )


async def map_matches(db: AsyncSession, *, dataset_id: int, q: str) -> list[int]:
    """검색어가 든 휴지통 밖 대화 번호들 (지도 강조). 맥락 · 응답 어디든."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    pattern = f"%{_escape_like(q)}%"
    in_answer = select(Response.prompt_id).where(
        Response.dataset_id == dataset_id, Response.content.ilike(pattern)
    )
    return list(
        await db.scalars(
            select(Prompt.id)
            .where(
                Prompt.dataset_id == dataset_id,
                IS_ACTIVE,
                or_(
                    cast(Prompt.messages, Text).ilike(pattern),
                    Prompt.id.in_(in_answer.scalar_subquery()),
                ),
            )
            .order_by(Prompt.id)
            .limit(MATCHES_LIMIT)
        )
    )


async def map_text(db: AsyncSession, *, dataset_id: int, prompt_id: int) -> str:
    """지도 점 하나의 글 (말풍선): 마지막 사용자 말의 앞부분. 없거나 다른 데이터셋이면 NotFoundError."""
    prompt = await db.get(Prompt, prompt_id)
    if prompt is None or prompt.dataset_id != dataset_id:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    return _last_user(prompt.messages)[:MAP_TEXT_LENGTH]


async def near_partners(db: AsyncSession, *, prompt_id: int) -> list[NearPartner]:
    """다 만든 분석에서 이 대화와 뜻이 가까운 짝들(가까운 것부터). 분석이 없으면 빈 목록."""
    prompt = await db.get(Prompt, prompt_id)
    if prompt is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    analysis = await problems.done_analysis(db, dataset_id=prompt.dataset_id)
    if analysis is None:
        return []
    partner = case(
        (NearDuplicate.prompt_a == prompt_id, NearDuplicate.prompt_b),
        else_=NearDuplicate.prompt_a,
    )
    rows = (
        await db.execute(
            select(Prompt, NearDuplicate.similarity)
            .join(Prompt, Prompt.id == partner)
            .where(
                NearDuplicate.analysis_id == analysis.id,
                or_(NearDuplicate.prompt_a == prompt_id, NearDuplicate.prompt_b == prompt_id),
            )
            .order_by(NearDuplicate.similarity.desc(), Prompt.id)
            .limit(NEAR_PARTNERS_LIMIT)
        )
    ).all()
    return [
        NearPartner(
            prompt_id=other.id,
            similarity=float(similarity),
            text=_last_user(other.messages)[:MAP_TEXT_LENGTH],
            trashed=other.trashed_at is not None,
        )
        for other, similarity in rows
    ]


async def judgments_for(
    db: AsyncSession, *, dataset_id: int, prompt_ids: Sequence[int]
) -> dict[int, Judgment]:
    """다 만든 분석의 Jev 판정 (대화 번호 → 판정). 분석이 없으면 빈 사전."""
    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    if analysis is None or not prompt_ids:
        return {}
    found = await db.scalars(
        select(Judgment).where(
            Judgment.analysis_id == analysis.id, Judgment.prompt_id.in_(prompt_ids)
        )
    )
    return {judgment.prompt_id: judgment for judgment in found}


async def forget_judgments(db: AsyncSession, *, prompt_id: int) -> None:
    """글이 바뀐 대화의 Jev 판정(대화 · 그 지시의 선호 쌍)을 지운다(옛 글의 판정이라서). 커밋은 부른 쪽이 한다."""
    await db.execute(delete(Judgment).where(Judgment.prompt_id == prompt_id))
    await db.execute(
        update(Pair)
        .where(Pair.prompt_id == prompt_id)
        .values(judge_chosen=None, judge_rejected=None)
    )


def instruction_text(messages: list[dict[str, Any]]) -> str:
    """임베딩할 지시 글: 맥락의 사용자 말들을 이어 붙인 것(system은 뺀다). 비면 EMPTY_INSTRUCTION_TEXT."""
    text = "\n\n".join(m["content"] for m in messages if m["role"] == Role.USER).strip()
    return (text or EMPTY_INSTRUCTION_TEXT)[:EMBED_TEXT_MAX_CHARS]


def response_text(content: str) -> str:
    """임베딩할 응답 글. 비면 EMPTY_RESPONSE_TEXT."""
    return (content.strip() or EMPTY_RESPONSE_TEXT)[:EMBED_TEXT_MAX_CHARS]


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
    dataset = await generation_service.get_dataset(db, dataset_id=dataset_id)
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return
    connection = await connections.get_connection(db, role=ConnectionRole.EMBEDDING)
    if connection is None:
        raise InvalidInputError(EMBEDDING_NOT_CONNECTED_MESSAGE)
    model = await embedding.ensure_model(db, connection, transport=transport)

    rows = await _rows(db, dataset_id=dataset_id)
    if not rows.prompt_ids:
        raise InvalidInputError(NOTHING_TO_ANALYZE_MESSAGE)
    unique_hashes = sorted({*rows.instruction_hashes, *rows.response_hashes})
    analysis.status = AnalysisStatus.RUNNING
    analysis.model_id = model.id
    analysis.params = {}
    analysis.checks = {}
    analysis.error = None
    analysis.finished_at = None
    analysis.conversation_count = len(rows.prompt_ids)
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
        rows=rows,
        missing=missing,
        transport=transport,
    )
    if embedded is None:
        return
    timings["embed_seconds"] = time.perf_counter() - started

    # 2. 좌표: 지시만 지도에 놓는다
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_PROJECTING, total=None)
    await db.commit()
    started = time.perf_counter()
    unique_vectors = await embedding.load_vectors(db, model=model, text_hashes=unique_hashes)
    position = {text_hash: index for index, text_hash in enumerate(unique_hashes)}
    instruction_vectors = unique_vectors[[position[h] for h in rows.instruction_hashes]]
    response_vectors = unique_vectors[[position[h] for h in rows.response_hashes]]
    del unique_vectors
    projected = await asyncio.to_thread(projection.project_2d, instruction_vectors)
    timings["projection_seconds"] = time.perf_counter() - started
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return

    # 3. 분석
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_ANALYZING, total=None)
    await db.commit()
    started = time.perf_counter()
    findings = await asyncio.to_thread(
        semantic_checks.analyze, instruction_vectors, response_vectors, rows.context_hashes
    )
    del instruction_vectors, response_vectors
    timings["analysis_seconds"] = time.perf_counter() - started
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return

    # 4. Jev
    started = time.perf_counter()
    judged = await _judge(
        db, analysis=analysis, job_id=job_id, rows=rows, findings=findings, transport=jev_transport
    )
    if judged is None:
        return
    verdicts, jev_state = judged
    timings["judge_seconds"] = time.perf_counter() - started

    # 5. 저장
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_SAVING, total=None)
    await _clear_results(db, analysis_id=analysis_id)
    await _save_points(
        db, analysis_id=analysis_id, rows=rows, findings=findings, projected=projected
    )
    await _save_near_pairs(db, analysis_id=analysis_id, rows=rows, findings=findings)
    await _save_judgments(
        db, analysis_id=analysis_id, rows=rows, findings=findings, verdicts=verdicts
    )
    largest = max(findings.cluster_sizes) if findings.cluster_sizes else 0
    analysis.checks = {
        "tasks": {
            "clusters": findings.cluster_count,
            "sizes": findings.cluster_sizes,
            "largest_share": round(findings.largest_share, 4),
            "largest_count": largest,
        },
        "near": {"pairs": len(findings.near_pairs)},
        "jev": {key: value for key, value in jev_state.items() if key not in ("safety", "pairs")},
        "safety": jev_state.get("safety") or {"judged": 0},
        "pairs": jev_state.get("pairs") or {"judged": 0},
    }
    analysis.status = AnalysisStatus.DONE
    analysis.params = projected.params
    analysis.finished_at = datetime.now(UTC)
    # 다 만든 분석은 데이터셋마다 하나다. 앞의 분석과 실패 · 취소한 시도를 지운다(점 · 쌍 · 판정도 함께).
    await db.execute(
        delete(Analysis).where(Analysis.dataset_id == dataset_id, Analysis.id != analysis_id)
    )
    await jobs.finish_job(
        db,
        job_id=job_id,
        result={
            "analysis_id": analysis_id,
            "conversations": len(rows.prompt_ids),
            "embedded": embedded,
            **{name: round(seconds, 1) for name, seconds in timings.items()},
            "judged": len(verdicts),
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


async def _count(db: AsyncSession, *, dataset_id: int) -> int:
    """휴지통 밖 대화 수."""
    count = await db.scalar(
        select(func.count()).select_from(Prompt).where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
    )
    return int(count or 0)


async def _rows(db: AsyncSession, *, dataset_id: int) -> Rows:
    """분석할 대화의 번호 · 맥락 해시 · 지시 · 응답 글 해시 (번호 순). 글은 묶음마다 읽고 버린다."""
    rows = Rows()
    query = (
        select(Prompt.id, Prompt.text_hash, Prompt.messages, Response.content)
        .outerjoin(Response, and_(Response.prompt_id == Prompt.id, Response.is_main))
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .order_by(Prompt.id)
        .execution_options(yield_per=READ_CHUNK)
    )
    async for prompt_id, context_hash, messages, content in await db.stream(query):
        instruction_hash = make_text_hash(instruction_text(messages))
        response_hash = make_text_hash(response_text(content or ""))
        rows.prompt_ids.append(prompt_id)
        rows.context_hashes.append(context_hash)
        rows.instruction_hashes.append(instruction_hash)
        rows.response_hashes.append(response_hash)
        rows.has_answer.append(content is not None)
        rows.owners.setdefault(instruction_hash, (prompt_id, False))
        rows.owners.setdefault(response_hash, (prompt_id, True))
    return rows


async def _texts_for(db: AsyncSession, *, rows: Rows, text_hashes: Sequence[str]) -> list[str]:
    """해시마다 임베딩할 글(정규화한 것)을 해시 순서대로. 주인 대화에서 다시 만든다.

    주인 대화가 사라졌거나 글이 바뀌었으면 InvalidInputError.
    """
    owner_ids = sorted({rows.owners[text_hash][0] for text_hash in text_hashes})
    found: dict[int, tuple[list[dict[str, Any]], str]] = {}
    for prompt_id, messages, content in await db.execute(
        select(Prompt.id, Prompt.messages, Response.content)
        .join(Response, Response.prompt_id == Prompt.id)
        .where(Prompt.id.in_(owner_ids))
    ):
        found[prompt_id] = (messages, content)
    texts = []
    for text_hash in text_hashes:
        prompt_id, is_response = rows.owners[text_hash]
        if prompt_id not in found:
            raise InvalidInputError(TEXTS_CHANGED_MESSAGE)
        messages, content = found[prompt_id]
        text = response_text(content) if is_response else instruction_text(messages)
        if make_text_hash(text) != text_hash:
            raise InvalidInputError(TEXTS_CHANGED_MESSAGE)
        texts.append(normalize_text(text))
    return texts


async def _embed_missing(
    db: AsyncSession,
    *,
    analysis: Analysis,
    job_id: int,
    connection: Connection,
    model: EmbeddingModel,
    rows: Rows,
    missing: Sequence[str],
    transport: httpx.AsyncBaseTransport | None,
) -> int | None:
    """캐시에 없는 글을 묶음마다 임베딩해 저장하고 저장한 수를 돌려준다. 멈추라고 했으면 None.

    다음 묶음 요청을 먼저 보내 두고 이번 묶음을 저장한다(분류 · 검색 뜻 분석과 같은 방식).
    """
    if await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
        return None
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
        texts = await _texts_for(db, rows=rows, text_hashes=chunk)
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
            if has_next and await _stop_if_canceled(db, analysis=analysis, job_id=job_id):
                return None
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    return embedded


async def _judge(
    db: AsyncSession,
    *,
    analysis: Analysis,
    job_id: int,
    rows: Rows,
    findings: semantic_checks.Findings,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[dict[int, judging.Verdict], dict[str, Any]] | None:
    """Jev에 묻는다: 규칙이 잡은 거절 · 짧은 응답과 지시 불일치 후보는 '따르나'(거절이면 '마땅한가'도),
    안전 표본은 '해로운가', 선호 쌍은 두 응답을 따로. (대화 번호 → 판정, Jev 상태).

    멈추라고 했으면 None. Jev가 없거나 실패하면 거기까지의 판정과 그 상태를 돌려준다(분석은 이어 간다).
    """
    flagged = await _flagged_for_jev(db, dataset_id=analysis.dataset_id)
    candidates = list(dict.fromkeys([*sorted(flagged), *_mismatch_prompt_ids(rows, findings)]))
    capped = candidates[:MAX_JEV_JUDGMENTS]
    follows_ids = set(capped)
    safety_ids = set(_safety_sample(rows))
    asked = sorted(follows_ids | safety_ids)
    pairs = await _pairs_to_judge(db, dataset_id=analysis.dataset_id)
    if not asked and not pairs:
        return {}, {"status": judging.JEV_JUDGED, "detail": "후보 없음", "safety": {"judged": 0}}
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
    total = len(asked) + len(pairs)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_JUDGING, total=total)
    await db.commit()
    conversations = await _conversations(db, prompt_ids=asked)
    verdicts: dict[int, judging.Verdict] = {}
    done = 0
    try:
        for prompt_id in asked:
            if prompt_id not in conversations:
                continue
            messages, content = conversations[prompt_id]
            verdicts[prompt_id] = await judging.ask(
                connection,
                messages=messages,
                response=content,
                with_follows=prompt_id in follows_ids,
                with_refusal=flagged.get(prompt_id, False),
                with_safety=prompt_id in safety_ids,
                transport=transport,
            )
            done += 1
            if await _checkpoint(db, analysis=analysis, job_id=job_id, done=done, total=total):
                return None
        for pair_id, messages, chosen, rejected in pairs:
            chosen_follows, rejected_follows = await judging.judge_pair(
                connection,
                messages=messages,
                chosen=chosen,
                rejected=rejected,
                transport=transport,
            )
            await db.execute(
                update(Pair)
                .where(Pair.id == pair_id)
                .values(judge_chosen=chosen_follows, judge_rejected=rejected_follows)
            )
            done += 1
            if await _checkpoint(db, analysis=analysis, job_id=job_id, done=done, total=total):
                return None
    except ExternalServiceError as error:
        return verdicts, {"status": judging.JEV_FAILED, "detail": error.message}
    is_capped = len(candidates) > len(capped)
    detail = f"{len(verdicts)}건" + (f" · 상한 {MAX_JEV_JUDGMENTS}" if is_capped else "")
    state = {
        "status": judging.JEV_JUDGED,
        "detail": detail,
        "safety": {"judged": len(safety_ids)},
        "pairs": {"judged": len(pairs)},
    }
    return verdicts, state


async def _checkpoint(
    db: AsyncSession, *, analysis: Analysis, job_id: int, done: int, total: int
) -> bool:
    """JEV_PROGRESS_EVERY마다 진행률을 적고 커밋한다. 멈추라고 했으면 True."""
    is_checkpoint = done % JEV_PROGRESS_EVERY == 0 or done == total
    if not is_checkpoint:
        return False
    await jobs.set_progress(db, job_id=job_id, done=done, total=total)
    await db.commit()
    return await _stop_if_canceled(db, analysis=analysis, job_id=job_id)


async def _pairs_to_judge(
    db: AsyncSession, *, dataset_id: int
) -> list[tuple[int, list[dict[str, Any]], str, str]]:
    """아직 Jev가 보지 않은 휴지통 밖 선호 쌍 (번호, 맥락, 고른 응답, 버린 응답), 최대 MAX_PAIR_JUDGMENTS."""
    chosen = aliased(Response)
    rejected = aliased(Response)
    rows = await db.execute(
        select(Pair.id, Prompt.messages, chosen.content, rejected.content)
        .join(Prompt, Prompt.id == Pair.prompt_id)
        .join(chosen, chosen.id == Pair.chosen_id)
        .join(rejected, rejected.id == Pair.rejected_id)
        .where(
            Pair.dataset_id == dataset_id,
            Pair.trashed_at.is_(None),
            IS_ACTIVE,
            or_(Pair.judge_chosen.is_(None), Pair.judge_rejected.is_(None)),
        )
        .order_by(Pair.id)
        .limit(MAX_PAIR_JUDGMENTS)
    )
    return [(pair_id, messages, good, bad) for pair_id, messages, good, bad in rows]


async def _flagged_for_jev(db: AsyncSession, *, dataset_id: int) -> dict[int, bool]:
    """규칙이 거절 · 짧은 응답으로 잡은 휴지통 밖 대화 (번호 → 거절로 잡혔는지)."""
    refusal = has_open(CheckKey.REFUSAL.value)
    rows = await db.execute(
        select(Prompt.id, refusal).where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            or_(*[has_open(check.value) for check in JEV_REVIEW_CHECKS]),
        )
    )
    return {prompt_id: bool(is_refusal) for prompt_id, is_refusal in rows}


def _mismatch_prompt_ids(rows: Rows, findings: semantic_checks.Findings) -> list[int]:
    """지시 불일치 후보 대화 번호들 (주 응답이 없는 지시만은 뺀다)."""
    return [
        rows.prompt_ids[index] for index in findings.mismatch_candidates if rows.has_answer[index]
    ]


def _safety_sample(rows: Rows) -> list[int]:
    """안전을 물을 대화 번호들: 주 응답이 있는 대화를 번호의 해시 순(섞인 순서)으로 앞 MAX_SAFETY_JUDGMENTS개."""
    answered = [pid for pid, has in zip(rows.prompt_ids, rows.has_answer, strict=True) if has]
    ordered = sorted(answered, key=lambda prompt_id: make_text_hash(str(prompt_id)))
    return ordered[:MAX_SAFETY_JUDGMENTS]


async def _conversations(
    db: AsyncSession, *, prompt_ids: Sequence[int]
) -> dict[int, tuple[list[dict[str, Any]], str]]:
    """번호마다 (맥락, 마지막 응답)."""
    found: dict[int, tuple[list[dict[str, Any]], str]] = {}
    for chunk in itertools.batched(prompt_ids, SAVE_CHUNK):
        for prompt_id, messages, content in await db.execute(
            select(Prompt.id, Prompt.messages, Response.content)
            .join(Response, and_(Response.prompt_id == Prompt.id, Response.is_main))
            .where(Prompt.id.in_(chunk))
            .order_by(Prompt.id)
        ):
            found[prompt_id] = (messages, content)
    return found


async def _clear_results(db: AsyncSession, *, analysis_id: int) -> None:
    """이 분석의 점 · 근접 중복 쌍 · 판정을 비운다."""
    for model in (MapPoint, NearDuplicate, Judgment):
        await db.execute(delete(model).where(model.analysis_id == analysis_id))


async def _save_points(
    db: AsyncSession,
    *,
    analysis_id: int,
    rows: Rows,
    findings: semantic_checks.Findings,
    projected: projection.Projection,
) -> None:
    """대화마다 점 하나."""
    xs = projected.coords[:, 0].tolist()
    ys = projected.coords[:, 1].tolist()
    clusters = findings.clusters or [0] * len(rows.prompt_ids)
    points = [
        {
            "analysis_id": analysis_id,
            "prompt_id": prompt_id,
            "x": xs[index],
            "y": ys[index],
            "cluster": clusters[index],
        }
        for index, prompt_id in enumerate(rows.prompt_ids)
    ]
    for chunk in itertools.batched(points, SAVE_CHUNK):
        await db.execute(insert(MapPoint), list(chunk))


async def _save_near_pairs(
    db: AsyncSession, *, analysis_id: int, rows: Rows, findings: semantic_checks.Findings
) -> None:
    pairs = [
        {
            "analysis_id": analysis_id,
            "prompt_a": min(rows.prompt_ids[pair.index_a], rows.prompt_ids[pair.index_b]),
            "prompt_b": max(rows.prompt_ids[pair.index_a], rows.prompt_ids[pair.index_b]),
            "similarity": pair.similarity,
        }
        for pair in findings.near_pairs
    ]
    for chunk in itertools.batched(pairs, SAVE_CHUNK):
        await db.execute(insert(NearDuplicate).on_conflict_do_nothing(), list(chunk))


async def _save_judgments(
    db: AsyncSession,
    *,
    analysis_id: int,
    rows: Rows,
    findings: semantic_checks.Findings,
    verdicts: dict[int, judging.Verdict],
) -> None:
    """물어본 대화마다 판정 한 줄 (지시 ↔ 응답 유사도와 함께)."""
    similarity = dict(zip(rows.prompt_ids, findings.similarities, strict=False))
    judgments = [
        {
            "analysis_id": analysis_id,
            "prompt_id": prompt_id,
            "similarity": similarity.get(prompt_id),
            "follows": verdict.follows,
            "refusal_ok": verdict.refusal_ok,
            "unsafe": verdict.unsafe,
        }
        for prompt_id, verdict in verdicts.items()
    ]
    for chunk in itertools.batched(judgments, SAVE_CHUNK):
        await db.execute(insert(Judgment).on_conflict_do_nothing(), list(chunk))


async def _stop_if_canceled(db: AsyncSession, *, analysis: Analysis, job_id: int) -> bool:
    """멈추라고 했으면 분석과 작업을 멈춘 것으로 끝내고 True. 계산한 임베딩은 캐시에 남긴다."""
    if not await jobs.is_cancel_requested(db, job_id=job_id):
        return False
    analysis.status = AnalysisStatus.CANCELED
    analysis.finished_at = datetime.now(UTC)
    await jobs.cancel_job(db, job_id=job_id)
    await db.commit()
    return True


def _last_user(messages: list[dict[str, Any]]) -> str:
    """맥락의 마지막 사용자 말. 없으면 빈 글."""
    return next((m["content"] for m in reversed(messages) if m["role"] == Role.USER), "")


def _flag_sum(conditions: list[tuple[int, Any]]) -> Any:
    """조건마다 비트를 더한 표시 값."""
    total: Any = literal(0, Integer)
    for bit, condition in conditions:
        total = total + case((condition, 1 << bit), else_=0)
    return cast(total, Integer)


def _rounded(column: InstrumentedAttribute[float]) -> Any:
    """좌표를 COORD_DECIMALS 자리로 줄여 float으로."""
    return cast(func.round(cast(column, Numeric), COORD_DECIMALS), Float)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
