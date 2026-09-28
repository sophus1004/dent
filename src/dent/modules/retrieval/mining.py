"""오답 찾기(하드 네거티브): 학습할 모델로 질의마다 코퍼스를 찾아, 정답이 아닌 가까운 문서를 오답으로 붙인다.
오답 훑기: 순위 묶음 × 정답 대비 문턱마다 거짓 오답 비율을 Jev로 재서, 순위 범위와 문턱을 데이터로 고르게 한다.

- 질의마다 오답 풀(설정, 기본 20)까지 모은다. 학습 jsonl에 모두 넣고 학습기가 매 에폭 오답 수(7)만큼 무작위로 고른다.
  이미 있는 오답을 세고 모자란 만큼만 더한다(넘으면 그대로 둔다). 7개도 못 채운 질의가 '오답 부족'이다.
- 순위 범위(설정, 기본 10~200위)의 문서 가운데 가장 가까운 것부터 고른다(가장 하드한 것).
  코퍼스가 작아 그 순위가 없으면(문서 20개 미만) 시작 순위를 코퍼스의 절반으로 낮춘다(2위보다 앞은 고르지 않는다).
- 정답 유사도의 margin배(기본 0.95)를 넘는 문서는 거짓 오답일 수 있어 고르지 않는다.
  정답과 같은 중복 묶음(본문이 같거나 근접 중복)의 문서도 고르지 않는다.
- Jev가 '답을 담음'(예 ≥ 0.8)이라고 한 문서는 오답에서 빼고 다음 문서를 본다(Jev가 없으면 이 확인은 건너뛴다).
- 판정이 이미 있는 문서(정답 · 오답)는 고르지 않는다. 휴지통 · 학습 제외 질의는 찾지 않는다.
- 문서는 학습 글로 임베딩하고 Jev에 보인다(학습 · 색인과 같은 글).
다시 찾기(remine)는 앞서 찾은 오답(출처 mined)을 떼고 지금 설정으로 처음부터 다시 찾는다(선택, 정한 것 13).
모든 질의의 오답을 찾은 뒤(처음 찾기 · 다시 찾기) 코퍼스에 문서가 생기면(데이터 추가 · 나누기) 문서 풀이 늘어,
다음 찾기는 스스로 다시 찾기가 된다(원본 오답 · 사람 판정은 그대로, 찾기로 붙인 오답만 떼고 모든 질의를 다시).
그래서 모든 질의를 보고 찾을 때 코퍼스의 가장 큰 문서 번호를 설정(mined_document_id)에 적는다.

작업 ("retrieval", "mine")이 부르고, 도우미도 같은 함수를 부른다(도우미면 바꾼 판정을 변경 기록에 남긴다).
오답 훑기는 작업 ("retrieval", "negative_scan")이고 결과를 설정(negative_scan)에 적는다.
"""

import asyncio
import itertools
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import edits, judging, semantic
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    POSITIVE_MIN_GRADE,
    DatasetSettings,
    Document,
    HelperField,
    HelperTarget,
    Judgment,
    JudgmentSource,
    Query,
)
from dent.modules.retrieval.service import DOCUMENT_ACTIVE, DOCUMENT_INPUT, QUERY_INCLUDED
from dent.modules.retrieval.text_rules import lexical_overlap
from dent.system import connections, embedding, jobs
from dent.system import datasets as datasets_service
from dent.system.exceptions import ExternalServiceError, InvalidInputError
from dent.system.models import ConnectionRole

# 오답 찾기 · 오답 훑기 작업의 종류
MINE_JOB_KIND = "mine"
NEGATIVE_SCAN_JOB_KIND = "negative_scan"

# 작업 단계 이름 (화면 index.ts의 jobKinds.mine.phases와 같다)
PHASE_EMBEDDING = "embedding"
PHASE_RANKING = "ranking"
PHASE_JUDGING = "judging"
PHASE_SAVING = "saving"

# 한 번에 유사도를 계산하는 질의 수
MINE_BLOCK_ROWS = 256

# 순위 · Jev 진행률을 적는 간격
PROGRESS_EVERY = 200
JEV_PROGRESS_EVERY = 20

# 한 번에 Jev에 묻는 최대 수. 넘으면 그 뒤는 Jev 확인 없이 붙인다(laya 1건 30~60ms면 10,000건에 10분쯤).
# 오답 풀(질의마다 20)을 모으므로 7개만 모을 때보다 넉넉히 둔다.
MAX_JEV_CHECKS = 10_000

# 오답 훑기: 표본 질의 수 · 순위 묶음 · 정답 대비 문턱 · 묶음마다 질의 하나에서 Jev에 묻는 후보 수
SCAN_QUERIES = 200
SCAN_BUCKETS = ((10, 20), (20, 50), (50, 100), (100, 200))
SCAN_MARGINS = (0.90, 0.93, 0.95, 0.97, 0.99)
SCAN_PER_BUCKET = 3

# 저장 단계에서 한 번에 넣는 판정 수
SAVE_CHUNK = 5_000

NOTHING_TO_MINE_MESSAGE = "오답을 찾을 질의가 없습니다. 정답이 있는 질의가 있어야 합니다."
NO_JEV_FOR_SCAN_MESSAGE = "오답 훑기는 Jev로 거짓 오답을 잽니다. 연결 설정에서 Jev를 연결하세요."

# 멈추라고 했는지 보는 함수 (멈췄으면 True)
type CancelCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class MinedPair:
    """고른 오답 하나: 질의 · 문서 · 순위 · 유사도 · Jev 예 확률."""

    query_id: int
    document_id: int
    rank: int
    similarity: float
    jev_probability: float | None


@dataclass(frozen=True)
class MineResult:
    """오답 찾기 결과의 수."""

    queries: int
    added: int
    jev_rejected: int
    lacking: int

    # 실제로 찾은 순위 범위 (코퍼스가 작으면 설정보다 앞에서 시작한다)
    rank_from: int = 0
    rank_to: int = 0

    # 문서 풀이 늘어 스스로 다시 찾았으면 그 뒤 생긴 문서 수 (아니면 0)
    new_documents: int = 0


async def start_mining(db: AsyncSession, *, dataset_id: int, remine: bool) -> int:
    """오답 찾기 작업을 넣고 작업 번호를 돌려준다. 임베딩 미연결이면 InvalidInputError."""
    await retrieval_service.get_settings(db, dataset_id=dataset_id)
    if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
        raise InvalidInputError(semantic.EMBEDDING_NOT_CONNECTED_MESSAGE)
    job = await jobs.enqueue_job(
        db,
        module=retrieval_service.MODULE_NAME,
        kind=MINE_JOB_KIND,
        params={"dataset_id": dataset_id, "remine": remine},
        dataset_id=dataset_id,
    )
    await db.commit()
    return job.id


async def mine_negatives(
    db: AsyncSession,
    *,
    dataset_id: int,
    job_id: int,
    remine: bool = False,
    log: edits.ChangeLog | None = None,
    is_canceled: CancelCheck | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> MineResult | None:
    """오답을 찾아 판정으로 붙인다. 멈추라고 했으면 None. 커밋은 단계마다 한다.

    임베딩이 끊겼거나 서버가 안 되면 도메인 예외를 낸다.
    """
    settings = await retrieval_service.get_settings(db, dataset_id=dataset_id)
    connection = await semantic.embedding_connection(db, settings=settings)
    model = await embedding.ensure_model(db, connection, transport=transport)
    new_documents = await retrieval_service.count_unmined_documents(db, dataset_id=dataset_id)
    remine = remine or new_documents > 0
    # 모든 질의가 지금 코퍼스를 보는 판(처음 찾기 · 다시 찾기)이면 끝에 코퍼스의 가장 큰 번호를 적는다.
    sees_whole_corpus = remine or settings.mined_document_id is None
    if remine:
        await _drop_mined(db, dataset_id=dataset_id, log=log)
        await db.commit()

    targets = await _targets(db, dataset_id=dataset_id, settings=settings)
    if not targets:
        if sees_whole_corpus:
            settings.mined_document_id = await _latest_document_id(db, dataset_id=dataset_id)
            await db.commit()
        return MineResult(
            queries=0, added=0, jev_rejected=0, lacking=0, new_documents=new_documents
        )
    corpus = await _corpus(db, dataset_id=dataset_id)
    if not corpus.document_ids:
        raise InvalidInputError(NOTHING_TO_MINE_MESSAGE)
    document_ids = corpus.document_ids
    query_hashes = [target.text_hash for target in targets]

    # 1. 임베딩 (캐시에 없는 것만)
    unique_hashes = sorted({*query_hashes, *corpus.input_hashes})
    missing = await embedding.find_missing_hashes(db, model_id=model.id, text_hashes=unique_hashes)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_EMBEDDING, total=len(missing))
    await db.commit()

    async def never() -> bool:
        return False

    embedded = await semantic.embed_missing_hashes(
        db,
        dataset_id=dataset_id,
        job_id=job_id,
        connection=connection,
        model=model,
        missing=missing,
        is_canceled=is_canceled or never,
        transport=transport,
    )
    if embedded is None:
        return None

    # 2. 순위: 질의마다 고를 후보(가까운 것부터)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_RANKING, total=len(targets))
    await db.commit()
    vectors = await embedding.load_vectors(db, model=model, text_hashes=unique_hashes)
    position = {text_hash: index for index, text_hash in enumerate(unique_hashes)}
    unit_documents = _normalized(vectors[[position[h] for h in corpus.input_hashes]])
    unit_queries = _normalized(vectors[[position[h] for h in query_hashes]])
    del vectors
    document_index = {document_id: index for index, document_id in enumerate(document_ids)}
    rank_from = _effective_rank_from(settings.mine_rank_from, corpus_size=len(document_ids))
    candidates = await asyncio.to_thread(
        _rank_candidates,
        unit_queries,
        unit_documents,
        targets,
        document_index,
        corpus.group_of,
        rank_from,
        settings.mine_rank_to,
        settings.mine_margin,
    )
    await jobs.set_progress(db, job_id=job_id, done=len(targets), total=len(targets))
    await db.commit()
    if is_canceled is not None and await is_canceled():
        return None

    # 3. Jev 확인 · 고르기
    picked, rejected = await _pick(
        db,
        job_id=job_id,
        targets=targets,
        candidates=candidates,
        document_ids=document_ids,
        is_canceled=is_canceled,
        transport=jev_transport,
    )
    if picked is None:
        return None

    # 4. 저장
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_SAVING, total=len(picked))
    await _save(db, dataset_id=dataset_id, picked=picked, log=log)
    if sees_whole_corpus:
        settings.mined_document_id = max(document_ids)
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    picked_per_query: dict[int, int] = {}
    for pair in picked:
        picked_per_query[pair.query_id] = picked_per_query.get(pair.query_id, 0) + 1
    lacking = sum(
        1
        for target in targets
        if target.have + picked_per_query.get(target.query_id, 0) < settings.negatives
    )
    return MineResult(
        queries=len(targets),
        added=len(picked),
        jev_rejected=rejected,
        lacking=lacking,
        rank_from=rank_from,
        rank_to=min(settings.mine_rank_to, len(document_ids)),
        new_documents=new_documents,
    )


# ---------- 안에서만 쓰는 것 ----------


# 시작 순위를 낮추지 않는 코퍼스 크기 · 가장 앞의 시작 순위 (1위는 정답 자리라 늘 뺀다)
SMALL_CORPUS = 20
MIN_RANK_FROM = 2


async def _latest_document_id(db: AsyncSession, *, dataset_id: int) -> int | None:
    """코퍼스의 가장 큰 문서 번호. 코퍼스가 비었으면 None."""
    return await db.scalar(
        select(func.max(Document.id)).where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
    )


def _effective_rank_from(rank_from: int, *, corpus_size: int) -> int:
    """코퍼스가 작으면 시작 순위를 코퍼스의 절반까지 낮춘다(그 순위가 아예 없으면 오답을 하나도 못 찾으므로)."""
    if corpus_size >= SMALL_CORPUS:
        return rank_from
    return min(rank_from, max(MIN_RANK_FROM, corpus_size // 2))


@dataclass(frozen=True)
class Corpus:
    """코퍼스 문서의 번호 · 학습 글 해시 · 중복 묶음 대표 (번호 순)."""

    document_ids: list[int]
    input_hashes: list[str]
    group_of: list[int]


async def _corpus(db: AsyncSession, *, dataset_id: int) -> Corpus:
    rows = (
        await db.execute(
            select(Document.id, Document.input_hash)
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
            .order_by(Document.id)
        )
    ).all()
    groups = await retrieval_service.duplicate_groups(db, dataset_id=dataset_id)
    return Corpus(
        document_ids=[row[0] for row in rows],
        input_hashes=[row[1] for row in rows],
        group_of=[groups.get(row[0], row[0]) for row in rows],
    )


@dataclass(frozen=True)
class Target:
    """오답을 찾을 질의 하나."""

    query_id: int
    text_hash: str
    # 이미 있는 오답 수 · 더할 수
    have: int
    need: int
    # 판정이 있는 문서 번호 · 정답 문서 번호
    judged: frozenset[int]
    positives: frozenset[int]


async def _targets(db: AsyncSession, *, dataset_id: int, settings: DatasetSettings) -> list[Target]:
    """오답 풀이 모자란 학습 질의 (정답이 있는 것만)."""
    queries = (
        await db.execute(
            select(Query.id, Query.text_hash)
            .where(Query.dataset_id == dataset_id, QUERY_INCLUDED)
            .order_by(Query.id)
        )
    ).all()
    judged: dict[int, dict[int, int]] = {}
    for query_id, document_id, grade in await db.execute(
        select(Judgment.query_id, Judgment.document_id, Judgment.grade)
        .join(Document, Document.id == Judgment.document_id)
        .where(Judgment.dataset_id == dataset_id, DOCUMENT_ACTIVE)
    ):
        judged.setdefault(query_id, {})[document_id] = grade
    targets = []
    for query_id, text_hash in queries:
        grades = judged.get(query_id, {})
        positives = frozenset(doc for doc, grade in grades.items() if grade >= POSITIVE_MIN_GRADE)
        have = sum(1 for grade in grades.values() if grade < POSITIVE_MIN_GRADE)
        need = max(settings.negative_pool, settings.negatives) - have
        if positives and need > 0:
            targets.append(
                Target(
                    query_id=query_id,
                    text_hash=text_hash,
                    have=have,
                    need=need,
                    judged=frozenset(grades),
                    positives=positives,
                )
            )
    return targets


def _normalized(vectors: np.ndarray) -> np.ndarray:
    unit = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(unit, axis=1, keepdims=True)
    return unit / np.where(norms == 0, 1, norms)


def _rank_candidates(
    unit_queries: np.ndarray,
    unit_documents: np.ndarray,
    targets: list[Target],
    document_index: dict[int, int],
    group_of: list[int],
    rank_from: int,
    rank_to: int,
    margin: float,
) -> list[list[tuple[int, int, float]]]:
    """질의마다 후보 [(문서 자리, 순위, 유사도)] (가까운 것부터). 스레드에서 돈다.

    정답과 같은 중복 묶음(group_of가 같은 문서)은 고르지 않는다.
    """
    top = min(rank_to, len(unit_documents))
    results: list[list[tuple[int, int, float]]] = []
    for start in range(0, len(targets), MINE_BLOCK_ROWS):
        end = min(start + MINE_BLOCK_ROWS, len(targets))
        block = unit_queries[start:end] @ unit_documents.T
        order = np.argpartition(-block, top - 1, axis=1)[:, :top]
        for offset, target in enumerate(targets[start:end]):
            similarities = block[offset]
            ranked = order[offset][np.argsort(-similarities[order[offset]], kind="stable")]
            positive_rows = [
                document_index[doc] for doc in target.positives if doc in document_index
            ]
            best = float(similarities[positive_rows].max()) if positive_rows else 1.0
            positive_groups = {group_of[row] for row in positive_rows}
            judged_rows = {document_index[doc] for doc in target.judged if doc in document_index}
            chosen = []
            for rank, row in enumerate(ranked, start=1):
                if rank < rank_from:
                    continue
                similarity = float(similarities[row])
                is_too_close = similarity > best * margin
                is_same_as_positive = group_of[row] in positive_groups
                if row in judged_rows or is_too_close or is_same_as_positive:
                    continue
                chosen.append((int(row), rank, similarity))
            results.append(chosen)
    return results


async def _pick(
    db: AsyncSession,
    *,
    job_id: int,
    targets: list[Target],
    candidates: list[list[tuple[int, int, float]]],
    document_ids: list[int],
    is_canceled: CancelCheck | None,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[list[MinedPair] | None, int]:
    """질의마다 모자란 수만큼 고른다. Jev가 있으면 '답을 담음'이라고 한 문서는 빼고 다음을 본다."""
    connection = await connections.get_connection(db, role=ConnectionRole.JEV)
    budget = MAX_JEV_CHECKS if connection is not None else 0
    total_needed = sum(
        min(target.need, len(found)) for target, found in zip(targets, candidates, strict=True)
    )
    query_texts: dict[int, str] = {}
    document_texts: dict[int, str] = {}
    if budget:
        await jobs.add_event(
            db,
            job_id=job_id,
            event_type=jobs.EVENT_CONNECTION,
            role=ConnectionRole.JEV.value,
            base_url=connection.base_url,  # type: ignore[union-attr]
            model=connection.model,  # type: ignore[union-attr]
        )
        await jobs.start_phase(
            db, job_id=job_id, phase=PHASE_JUDGING, total=min(total_needed, budget)
        )
        await db.commit()
        query_texts = dict(
            (
                await db.execute(
                    select(Query.id, Query.text).where(Query.id.in_([t.query_id for t in targets]))
                )
            )
            .tuples()
            .all()
        )
    picked: list[MinedPair] = []
    rejected = 0
    asked = 0
    for target, found in zip(targets, candidates, strict=True):
        taken = 0
        for row, rank, similarity in found:
            if taken >= target.need:
                break
            document_id = document_ids[row]
            probability = None
            if budget and connection is not None and asked < budget:
                if document_id not in document_texts:
                    document_texts[document_id] = str(
                        await db.scalar(select(DOCUMENT_INPUT).where(Document.id == document_id))
                    )
                try:
                    probability = await judging.ask_contains(
                        connection,
                        query=query_texts[target.query_id],
                        document=document_texts[document_id],
                        transport=transport,
                    )
                except ExternalServiceError:
                    # Jev가 멈추면 그 뒤는 Jev 확인 없이 고른다.
                    budget = 0
                asked += 1
                if asked % JEV_PROGRESS_EVERY == 0:
                    await jobs.set_progress(
                        db, job_id=job_id, done=asked, total=min(total_needed, MAX_JEV_CHECKS)
                    )
                    await db.commit()
                    if is_canceled is not None and await is_canceled():
                        return None, rejected
                if probability is not None and probability >= judging.CONFIRM_PROBABILITY:
                    rejected += 1
                    continue
            picked.append(
                MinedPair(
                    query_id=target.query_id,
                    document_id=document_id,
                    rank=rank,
                    similarity=similarity,
                    jev_probability=probability,
                )
            )
            taken += 1
    return picked, rejected


async def _save(
    db: AsyncSession, *, dataset_id: int, picked: list[MinedPair], log: edits.ChangeLog | None
) -> None:
    """고른 오답을 판정(등급 0, 출처 mined)으로 넣는다. 이미 판정이 생긴 쌍은 건드리지 않는다."""
    query_texts = dict(
        (
            await db.execute(
                select(Query.id, Query.text).where(Query.id.in_({p.query_id for p in picked}))
            )
        )
        .tuples()
        .all()
    )
    for chunk in itertools.batched(picked, SAVE_CHUNK):
        document_texts = dict(
            (
                await db.execute(
                    select(Document.id, Document.text).where(
                        Document.id.in_({p.document_id for p in chunk})
                    )
                )
            )
            .tuples()
            .all()
        )
        rows = [
            {
                "query_id": pair.query_id,
                "document_id": pair.document_id,
                "dataset_id": dataset_id,
                "grade": 0,
                "source": JudgmentSource.MINED.value,
                "teacher_score": pair.jev_probability,
                "overlap": lexical_overlap(
                    query_texts[pair.query_id], document_texts[pair.document_id]
                ),
            }
            for pair in chunk
        ]
        await db.execute(insert(Judgment).on_conflict_do_nothing(), rows)
        if log is not None:
            for pair in chunk:
                log.add(
                    target=HelperTarget.JUDGMENT,
                    field_name=HelperField.GRADE,
                    query_id=pair.query_id,
                    document_id=pair.document_id,
                    before=None,
                    after=0,
                )


async def _drop_mined(db: AsyncSession, *, dataset_id: int, log: edits.ChangeLog | None) -> int:
    """앞서 찾은 오답(출처 mined)을 뗀다. 뗀 수."""
    if log is not None:
        # 되돌리면 출처 mined · 교사 점수 그대로 되살아나게 판정의 값을 남긴다.
        for judgment in await db.scalars(
            select(Judgment).where(
                Judgment.dataset_id == dataset_id, Judgment.source == JudgmentSource.MINED.value
            )
        ):
            log.add(
                target=HelperTarget.JUDGMENT,
                field_name=HelperField.GRADE,
                query_id=judgment.query_id,
                document_id=judgment.document_id,
                before=edits.judgment_state(judgment),
                after=None,
            )
    result = await db.execute(
        delete(Judgment).where(
            Judgment.dataset_id == dataset_id, Judgment.source == JudgmentSource.MINED.value
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def count_mined(db: AsyncSession, *, dataset_id: int) -> int:
    """찾은 오답 수 (다시 찾기 확인 창)."""
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Judgment)
            .where(Judgment.dataset_id == dataset_id, Judgment.source == JudgmentSource.MINED.value)
        )
        or 0
    )


# ---------- 오답 훑기 ----------


async def start_negative_scan(db: AsyncSession, *, dataset_id: int) -> int:
    """오답 훑기 작업을 넣고 작업 번호를 돌려준다. 임베딩 · Jev 미연결이면 InvalidInputError."""
    await retrieval_service.get_settings(db, dataset_id=dataset_id)
    if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
        raise InvalidInputError(semantic.EMBEDDING_NOT_CONNECTED_MESSAGE)
    if await connections.get_connection(db, role=ConnectionRole.JEV) is None:
        raise InvalidInputError(NO_JEV_FOR_SCAN_MESSAGE)
    job = await jobs.enqueue_job(
        db,
        module=retrieval_service.MODULE_NAME,
        kind=NEGATIVE_SCAN_JOB_KIND,
        params={"dataset_id": dataset_id},
        dataset_id=dataset_id,
    )
    await db.commit()
    return job.id


@dataclass(frozen=True)
class ScanSample:
    """훑기에서 Jev에 물은 후보 하나: 순위 묶음 자리 · 정답 대비 비율 · Jev 예 확률."""

    bucket: int
    ratio: float
    probability: float | None


async def scan_negatives(
    db: AsyncSession,
    *,
    dataset_id: int,
    job_id: int,
    is_canceled: CancelCheck | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any] | None:
    """질의 표본으로 순위 묶음 × 정답 대비 문턱의 거짓 오답 비율을 재서 설정에 적는다. 멈췄으면 None.

    질의마다 묶음 안에서 고르게 SCAN_PER_BUCKET개를 뽑아 Jev에 묻는다(판정이 있는 문서 · 정답의 중복 묶음은 뺀다).
    칸의 수: 문턱을 통과하는 후보(정답 유사도 × 문턱 이하) 가운데 Jev 예(≥ 0.8)인 것이 거짓 오답이다.
    """
    settings = await retrieval_service.get_settings(db, dataset_id=dataset_id)
    jev_connection = await connections.get_connection(db, role=ConnectionRole.JEV)
    if jev_connection is None:
        raise InvalidInputError(NO_JEV_FOR_SCAN_MESSAGE)
    connection = await semantic.embedding_connection(db, settings=settings)
    model = await embedding.ensure_model(db, connection, transport=transport)
    all_targets = [
        target
        for target in await _targets_with_positives(db, dataset_id=dataset_id)
        if target.positives
    ]
    step = max(1, math.ceil(len(all_targets) / SCAN_QUERIES))
    targets = all_targets[::step][:SCAN_QUERIES]
    corpus = await _corpus(db, dataset_id=dataset_id)
    if not targets or not corpus.document_ids:
        raise InvalidInputError(NOTHING_TO_MINE_MESSAGE)
    unique_hashes = sorted({*(target.text_hash for target in targets), *corpus.input_hashes})
    missing = await embedding.find_missing_hashes(db, model_id=model.id, text_hashes=unique_hashes)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_EMBEDDING, total=len(missing))
    await db.commit()

    async def never() -> bool:
        return False

    embedded = await semantic.embed_missing_hashes(
        db,
        dataset_id=dataset_id,
        job_id=job_id,
        connection=connection,
        model=model,
        missing=missing,
        is_canceled=is_canceled or never,
        transport=transport,
    )
    if embedded is None:
        return None
    vectors = await embedding.load_vectors(db, model=model, text_hashes=unique_hashes)
    position = {text_hash: index for index, text_hash in enumerate(unique_hashes)}
    unit_documents = _normalized(vectors[[position[h] for h in corpus.input_hashes]])
    unit_queries = _normalized(vectors[[position[t.text_hash] for t in targets]])
    del vectors
    document_index = {document_id: index for index, document_id in enumerate(corpus.document_ids)}
    picks = await asyncio.to_thread(
        _scan_picks, unit_queries, unit_documents, targets, document_index, corpus.group_of
    )

    total = sum(len(found) for found in picks)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_JUDGING, total=total)
    await db.commit()
    query_texts = dict(
        (
            await db.execute(
                select(Query.id, Query.text).where(Query.id.in_([t.query_id for t in targets]))
            )
        )
        .tuples()
        .all()
    )
    samples: list[ScanSample] = []
    asked = 0
    for target, found in zip(targets, picks, strict=True):
        for bucket, row, ratio in found:
            document_text = str(
                await db.scalar(
                    select(DOCUMENT_INPUT).where(Document.id == corpus.document_ids[row])
                )
            )
            probability = await judging.ask_contains(
                jev_connection,
                query=query_texts[target.query_id],
                document=document_text,
                transport=jev_transport,
            )
            samples.append(ScanSample(bucket=bucket, ratio=ratio, probability=probability))
            asked += 1
            if asked % JEV_PROGRESS_EVERY == 0:
                await jobs.set_progress(db, job_id=job_id, done=asked, total=total)
                await db.commit()
                if is_canceled is not None and await is_canceled():
                    return None
    result = {
        "queries": len(targets),
        "cells": _scan_cells(samples),
        "measured_at": datetime.now(UTC).isoformat(),
    }
    settings.negative_scan = result
    await db.commit()
    return result


async def _targets_with_positives(db: AsyncSession, *, dataset_id: int) -> list[Target]:
    """학습 · 평가에 쓰는 질의마다 판정 문서 · 정답 문서 (오답 수와 상관없이)."""
    queries = (
        await db.execute(
            select(Query.id, Query.text_hash)
            .where(Query.dataset_id == dataset_id, QUERY_INCLUDED)
            .order_by(Query.id)
        )
    ).all()
    judged: dict[int, dict[int, int]] = {}
    for query_id, document_id, grade in await db.execute(
        select(Judgment.query_id, Judgment.document_id, Judgment.grade)
        .join(Document, Document.id == Judgment.document_id)
        .where(Judgment.dataset_id == dataset_id, DOCUMENT_ACTIVE)
    ):
        judged.setdefault(query_id, {})[document_id] = grade
    return [
        Target(
            query_id=query_id,
            text_hash=text_hash,
            have=0,
            need=0,
            judged=frozenset(judged.get(query_id, {})),
            positives=frozenset(
                doc
                for doc, grade in judged.get(query_id, {}).items()
                if grade >= POSITIVE_MIN_GRADE
            ),
        )
        for query_id, text_hash in queries
    ]


def _scan_picks(
    unit_queries: np.ndarray,
    unit_documents: np.ndarray,
    targets: list[Target],
    document_index: dict[int, int],
    group_of: list[int],
) -> list[list[tuple[int, int, float]]]:
    """질의마다 순위 묶음에서 고르게 뽑은 후보 [(묶음 자리, 문서 자리, 정답 대비 비율)]. 스레드에서 돈다."""
    top = min(SCAN_BUCKETS[-1][1], len(unit_documents))
    results: list[list[tuple[int, int, float]]] = []
    for start in range(0, len(targets), MINE_BLOCK_ROWS):
        end = min(start + MINE_BLOCK_ROWS, len(targets))
        block = unit_queries[start:end] @ unit_documents.T
        order = np.argpartition(-block, top - 1, axis=1)[:, :top]
        for offset, target in enumerate(targets[start:end]):
            similarities = block[offset]
            ranked = order[offset][np.argsort(-similarities[order[offset]], kind="stable")]
            positive_rows = [
                document_index[doc] for doc in target.positives if doc in document_index
            ]
            best = float(similarities[positive_rows].max()) if positive_rows else 1.0
            positive_groups = {group_of[row] for row in positive_rows}
            judged_rows = {document_index[doc] for doc in target.judged if doc in document_index}
            found = []
            for bucket, (rank_from, rank_to) in enumerate(SCAN_BUCKETS):
                rows = [
                    int(row)
                    for rank, row in enumerate(ranked, start=1)
                    if rank_from <= rank < rank_to
                    and int(row) not in judged_rows
                    and group_of[int(row)] not in positive_groups
                ]
                if not rows:
                    continue
                # 묶음 안에서 고르게: 앞 · 가운데 · 뒤
                wanted = min(SCAN_PER_BUCKET, len(rows))
                spots = {
                    round(index * (len(rows) - 1) / max(wanted - 1, 1)) for index in range(wanted)
                }
                for spot in sorted(spots):
                    row = rows[spot]
                    found.append((bucket, row, float(similarities[row]) / best if best else 0.0))
            results.append(found)
    return results


def _scan_cells(samples: list[ScanSample]) -> list[dict[str, Any]]:
    """묶음 × 문턱 칸: 문턱을 통과하는 후보 수 · 그 가운데 Jev 예(거짓 오답) 수 · 남는 오답 수."""
    cells = []
    for bucket, (rank_from, rank_to) in enumerate(SCAN_BUCKETS):
        in_bucket = [sample for sample in samples if sample.bucket == bucket]
        for margin in SCAN_MARGINS:
            passing = [sample for sample in in_bucket if sample.ratio <= margin]
            false_negatives = sum(
                1
                for sample in passing
                if sample.probability is not None
                and sample.probability >= judging.CONFIRM_PROBABILITY
            )
            cells.append(
                {
                    "rank_from": rank_from,
                    "rank_to": rank_to,
                    "margin": margin,
                    "asked": len(passing),
                    "false_negatives": false_negatives,
                    "kept": len(passing) - false_negatives,
                }
            )
    return cells
