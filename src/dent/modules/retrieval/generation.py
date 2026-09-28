"""질의 만들기(합성): 질의가 없는 문서(청크)마다 LLM으로 질의 후보를 만들고, 걸러 가장 나은 것만 정답 판정과 함께 넣는다.

- 설정 단계: 청크마다 남길 수(설정 queries_per_chunk = N)의 CANDIDATE_FACTOR배 후보를 만든다. 후보마다 유형
  (검색어형 하나 · 질문형 넷: 사실 · 요약 · 예/아니오 · 배경) · 길이 · 난이도를 청크의 해시로 정해 준다(다시 해도 같다).
  검색어형 · 질문형은 설정의 질문형 몫(question_share %)에 맞게 고르게 섞는다. 후보마다 청크의 서로 다른 정보를 겨냥하게 한다.
  데이터셋에 원본 질의가 있으면 몇 개를 예시로 보여 그 모양을 닮게 한다.
- 거르기: 모양 · 문맥 의존("이 글" · "그는") · 같은 질의(글 · 뜻이 거의 같음) · 되찾기(만든 질의로 코퍼스를 찾으면
  그 청크가 10위 안, 설정에서 끌 수 있음) · Jev(청크가 답을 담음 ≥ 0.8). 임베딩 · Jev가 없으면 그 거르기는 건너뛴다.
- 통과한 후보 가운데 되찾기 순위가 높고 Jev 점수가 높은 것부터 청크마다 N개를 넣는다(나머지는 여분).
  N개가 모자란 청크만 모자란 만큼의 후보를 한 번 더 만든다(다른 설정으로). 그래도 모자란 청크 수는 결과에 센다.
- 정답 더하기: 되찾기에서 원 청크보다 위에 다른 청크가 있으면 Jev에 물어 예 ≥ 0.8이면 그 청크도 정답으로 넣는다
  (질의를 만든 청크가 늘 가장 맞는 정답은 아니다). 원 청크가 10위 밖이어도 더한 정답이 있으면 남긴다.
- 쉬운 쌍(청크 글을 거의 베낌)은 버리지 않고, 데이터셋의 쉬운 쌍 비율이 상한(설정)을 넘을 때만 거른다.
- 중복 묶음(같은 본문 · 근접 중복)은 대표 청크 하나로만 만든다(같은 내용의 질의가 서로 거짓 오답이 되지 않게).
- 허락을 묻기 전에 청크 TRIAL_DOCUMENTS개로 시험해 통과 수를 보인다(도우미의 허락 카드).
- train · valid · test는 나누지 않는다. 만든 질의는 출처 synthetic으로 데이터에 더한다(내보낼 때 출처 칸으로 가른다).
LLM · 임베딩 · Jev에는 학습 글(머리말 + 본문)을 보인다. 만든 질의는 출처 synthetic, 정답 판정도 synthetic이다.
도우미 실행이면 만든 질의와 더한 정답을 변경 기록에 남긴다.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

import httpx
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import edits, judging, problems, semantic
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    DEFAULT_EASY_PAIR_CAP,
    DEFAULT_QUERIES_PER_CHUNK,
    DEFAULT_QUESTION_SHARE,
    POSITIVE_MIN_GRADE,
    QUERY_MAX_LENGTH,
    DatasetSettings,
    Document,
    HelperField,
    HelperTarget,
    Judgment,
    JudgmentSource,
    Query,
    QuerySource,
)
from dent.modules.retrieval.service import DOCUMENT_ACTIVE, QUERY_ACTIVE, QUERY_INCLUDED
from dent.modules.retrieval.text_rules import (
    EASY_PAIR_MIN_CHARS,
    EASY_PAIR_OVERLAP,
    is_context_dependent,
    is_easy_pair,
    lexical_overlap,
)
from dent.system import connections, embedding, llm
from dent.system.exceptions import ExternalServiceError
from dent.system.llm import ChatMessage
from dent.system.models import Connection, ConnectionRole
from dent.system.text import make_text_hash, normalize_text

# 청크마다 남길 질의 수의 몇 배를 후보로 만드는지 (넉넉히 만들고 걸러 남긴다)
CANDIDATE_FACTOR = 2

# 설정 단계: 질의 유형 · 길이 · 난이도 (청크의 해시로 돌려 가며 고른다). 검색어형 하나 · 질문형 넷.
KEYWORD_TYPE = "keyword"
QUERY_TYPES = {
    KEYWORD_TYPE: "검색어(물음표 없는 낱말 나열)",
    "fact": "사실을 묻는 질문",
    "summary": "요점 · 개요를 묻는 질문",
    "yes_no": "예/아니오로 답하는 질문",
    "background": "까닭 · 배경 · 방법을 묻는 질문",
}
QUESTION_TYPES = tuple(kind for kind in QUERY_TYPES if kind != KEYWORD_TYPE)

# 검색어형 · 질문형을 고르게 섞는 수열의 걸음(황금비의 소수 부분). 청크 몇 개만 모여도 몫이 설정에 가깝다.
MIX_STEP = 0.6180339887
QUERY_LENGTHS = ("5낱말 미만", "5~10낱말", "10낱말 넘게")
QUERY_DIFFICULTIES = ("쉬움", "보통", "어려움")

# 예시로 보이는 원본 질의 수와 고를 때 읽는 수
EXAMPLE_QUERIES = 3
EXAMPLE_POOL = 200

# LLM 한 번에 보이는 청크 수 · 청크 글의 최대 글자 · 답의 최대 토큰
DOCUMENTS_PER_CALL = 6
DOCUMENT_PROMPT_CHARS = 1_500
GENERATE_MAX_TOKENS = 3_000

# 비용 어림: 호출마다 지시 토큰, 후보 하나의 토큰
PROMPT_OVERHEAD_TOKENS = 450
TOKENS_PER_QUERY = 30

# 허락 전 시험 청크 수
TRIAL_DOCUMENTS = 5

# 한 번 실행에 만드는 최대 청크 수 (넘으면 다음 실행에서 이어 한다)
MAX_DOCUMENTS_PER_RUN = 1_000

# 되찾기 거르기: 만든 질의로 찾았을 때 청크의 순위가 이 안이어야 한다
ROUND_TRIP_TOP = 10

# 정답 더하기: 원 청크보다 위에 있는 청크 가운데 Jev에 묻는 최대 수
RELABEL_MAX = 2

# 근접 중복 거르기의 코사인 경계
NEAR_DUPLICATE_SIMILARITY = 0.95

# 질의의 최소 글자 수
MIN_QUERY_CHARS = 5

# 보기 줄로 모으는 최대 수 (허락 카드 · 도우미 카드)
SAMPLE_ROWS = 12

# 거른 까닭 (화면의 표 · 결과 수)
REASON_ROUND_TRIP = "되찾기"
REASON_JEV = "Jev"
REASON_NEAR = "근접 중복"
REASON_EASY = "쉬운 쌍 상한"
REASON_CONTEXT = "문맥 의존"
REASON_SHAPE = "모양"
REASON_SPARE = "여분"

SYSTEM_PROMPT = (
    "너는 검색 학습 데이터의 질의를 만든다. 청크마다 주어진 설정 수만큼 한국어 질의를 만든다.\n"
    "- 설정마다 유형 · 길이 · 난이도를 지킨다. 질의마다 청크의 서로 다른 정보를 겨냥한다(같은 질문을 바꿔 말하지 않는다).\n"
    "- 청크만 보고 답할 수 있어야 하고, 청크의 문장을 그대로 베끼지 않는다.\n"
    "- '이 글' · '위 문서' · '그는'처럼 청크를 봐야 뜻이 서는 말을 쓰지 않는다. 고유 이름을 넣는다.\n"
    '답은 JSON 하나만: {"items": [{"id": 청크 번호, "queries": ["…", "…"]}]}'
)

# 멈추라고 했는지 보는 함수
type CancelCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class Spec:
    """후보 하나의 설정: 유형 · 길이 · 난이도."""

    kind: str
    length: str
    difficulty: str

    def text(self) -> str:
        return f"{QUERY_TYPES[self.kind]} · {self.length} · {self.difficulty}"


@dataclass(frozen=True)
class Plan:
    """질의 만들기 계획 (허락 카드)."""

    # 만들 청크 수 · 질의 안 만들 청크 수 · 중복 묶음이라 건너뛰는 청크 수
    documents: int
    skipped: int
    duplicates: int

    # 넣을 질의 수 (최대) · 만들 후보 수 · 비용 어림(토큰)
    queries: int
    candidates: int
    tokens: int

    # 청크마다 남길 질의 수 · 질문형 몫(%) (설정)
    per_chunk: int
    question_share: int


@dataclass
class Outcome:
    """만든 결과의 수와 보기."""

    # 만든(넣은) 질의 · 더한 정답 · 거른 수(까닭별)
    added: int = 0
    relabeled: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    # LLM 토큰 · Jev에 물은 수
    tokens: int = 0
    jev_calls: int = 0

    # 넣은 질의 번호 · 보기 줄 [질의, 청크 제목 · 앞부분, 통과 여부, 까닭]
    query_ids: list[int] = field(default_factory=list)
    samples: list[tuple[str, str, bool, str]] = field(default_factory=list)

    # 모자라 한 번 더 만든 청크 수 · 그래도 청크마다 남길 수에 못 미친 청크 수
    retried: int = 0
    short: int = 0

    # 두 판 모두 하나도 남기지 못해 '질의 안 만듦'으로 둔 청크 수
    gave_up: int = 0

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


@dataclass(frozen=True)
class Verdict:
    """후보 하나를 거른 결과."""

    # 거른 까닭 (통과면 None)
    reason: str | None

    # 되찾기 순위 (임베딩이 없으면 None) · Jev 예 확률
    rank: int | None = None
    probability: float | None = None

    # 원 청크보다 위에 있는 청크 번호들 (정답 더하기 후보, 가까운 것부터)
    above: tuple[int, ...] = ()

    # 쉬운 쌍인지
    is_easy: bool = False

    # Jev가 확인해 더할 정답 [(청크 번호, Jev 예 확률)]
    extra_positives: tuple[tuple[int, float], ...] = ()


# ---------- 대상 · 계획 ----------


async def target_documents(
    db: AsyncSession, *, dataset_id: int, limit: int | None = None
) -> list[Document]:
    """질의를 만들 청크: 코퍼스 문서 가운데 질의가 필요하고 중복 묶음의 대표인 것(번호 순,
    problems.generation_target — 진단의 '질의 없는 문서'와 같은 조건)."""
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
    query = (
        select(Document)
        .where(
            Document.dataset_id == dataset_id,
            DOCUMENT_ACTIVE,
            problems.generation_target(analysis.id if analysis else None),
        )
        .order_by(Document.id)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(await db.scalars(query))


async def plan(db: AsyncSession, *, dataset_id: int) -> Plan:
    """질의 만들기 계획: 대상 청크 수 · 넣을 질의 수 · 후보 수 · 비용 어림."""
    documents = await target_documents(db, dataset_id=dataset_id, limit=MAX_DOCUMENTS_PER_RUN)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
    duplicates = int(
        await db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.dataset_id == dataset_id,
                DOCUMENT_ACTIVE,
                problems.needs_queries(),
                problems.duplicate_member(analysis.id if analysis else None),
            )
        )
        or 0
    )
    skipped = int(
        await db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.dataset_id == dataset_id,
                DOCUMENT_ACTIVE,
                Document.skip_generation.is_(True),
            )
        )
        or 0
    )
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    per_chunk, question_share = _mix(settings)
    calls = -(-len(documents) // DOCUMENTS_PER_CALL)
    prompt_tokens = sum(min(document.token_count, DOCUMENT_PROMPT_CHARS) for document in documents)
    candidates = len(documents) * per_chunk * CANDIDATE_FACTOR
    return Plan(
        documents=len(documents),
        skipped=skipped,
        duplicates=duplicates,
        queries=len(documents) * per_chunk,
        candidates=candidates,
        tokens=prompt_tokens + calls * PROMPT_OVERHEAD_TOKENS + candidates * TOKENS_PER_QUERY,
        per_chunk=per_chunk,
        question_share=question_share,
    )


@dataclass(frozen=True)
class Estimate:
    """실행 설정 카드의 어림: 긴 문서 수 · 그 문서가 나뉠 청크 수 · 나누면 코퍼스 청크 수 · 질의를 만들 청크 ·
    남을 질의 · LLM 토큰."""

    long_documents: int
    long_chunks: int
    chunks: int
    targets: int
    queries: int
    tokens: int


async def estimate(
    db: AsyncSession, *, dataset_id: int, chunk_tokens: int, overlap: int, per_chunk: int
) -> Estimate:
    """고른 청크 크기 · 오버랩 · 청크마다 질의 수로 나누기 · 질의 만들기를 어림한다(바꾸지 않는다).

    긴 문서는 실제 규칙으로 나눠 본 평균(청크 수 · 청크 크기)만큼 나뉜다고 본다. 질의를 만들 청크는 지금 대상 문서가
    나뉜 수다.
    """
    counts = list(
        await db.scalars(
            select(Document.token_count).where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
        )
    )
    targets = await target_documents(db, dataset_id=dataset_id, limit=MAX_DOCUMENTS_PER_RUN)
    sample = await edits.split_sample(
        db, dataset_id=dataset_id, limit=chunk_tokens, overlap=overlap
    )

    def pieces(tokens: int) -> float:
        return 1 if tokens <= chunk_tokens else sample.chunks_per_document

    def prompt(tokens: int) -> float:
        # 긴 문서는 나뉜 청크들이 따로 실린다(청크마다 평균 크기).
        if tokens <= chunk_tokens:
            return min(tokens, DOCUMENT_PROMPT_CHARS)
        return sample.chunks_per_document * min(sample.tokens_per_chunk, DOCUMENT_PROMPT_CHARS)

    target_chunks = round(sum(pieces(document.token_count) for document in targets))
    queries = target_chunks * per_chunk
    calls = -(-target_chunks // DOCUMENTS_PER_CALL)
    prompt_tokens = round(sum(prompt(document.token_count) for document in targets))
    candidates = queries * CANDIDATE_FACTOR
    return Estimate(
        long_documents=sum(1 for tokens in counts if tokens > chunk_tokens),
        long_chunks=round(sum(pieces(tokens) for tokens in counts if tokens > chunk_tokens)),
        chunks=round(sum(pieces(tokens) for tokens in counts)),
        targets=target_chunks,
        queries=queries,
        tokens=prompt_tokens + calls * PROMPT_OVERHEAD_TOKENS + candidates * TOKENS_PER_QUERY,
    )


def _mix(settings: DatasetSettings | None) -> tuple[int, int]:
    """설정의 (청크마다 남길 질의 수, 질문형 몫 %). 설정이 없으면 기본값."""
    if settings is None:
        return DEFAULT_QUERIES_PER_CHUNK, DEFAULT_QUESTION_SHARE
    return settings.queries_per_chunk, settings.question_share


# ---------- 만들기 ----------


async def generate(
    db: AsyncSession,
    *,
    dataset_id: int,
    documents: Sequence[Document],
    llm_connection: Connection,
    dry_run: bool = False,
    log: edits.ChangeLog | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    is_canceled: CancelCheck | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    embedding_transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> Outcome:
    """청크들로 질의 후보를 만들고 걸러 넣는다. dry_run이면 넣지 않고 결과만 센다(허락 전 시험).

    청크마다 설정의 N개를 남긴다(후보 N × CANDIDATE_FACTOR). 첫 판에서 N개가 모자란 청크만 모자란 만큼
    한 번 더 만든다(다른 설정 자리로). LLM이 실패하면 ExternalServiceError.
    커밋은 LLM 묶음마다 한다(dry_run이면 하지 않는다).
    """
    outcome = Outcome()
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    per_chunk, question_share = _mix(settings)
    filters = await _Filters.prepare(
        db,
        dataset_id=dataset_id,
        settings=settings,
        transport=embedding_transport,
        jev_transport=jev_transport,
    )
    examples = await _example_queries(db, dataset_id=dataset_id)
    kept: dict[int, int] = {}

    async def take(document: Document, texts: list[str], specs: list[Spec], *, limit: int) -> int:
        """청크 하나의 후보들을 걸러 limit개까지 넣는다. 넣은 수."""
        label = document.title or document.text[:40]
        passed: list[tuple[str, Spec, Verdict]] = []
        for text, spec in zip(texts, specs, strict=False):
            verdict = await filters.check(text, document, outcome=outcome)
            verdict = await filters.relabel(verdict, text, outcome=outcome)
            if verdict.reason is None and verdict.is_easy and not filters.easy_allowed():
                verdict = Verdict(reason=REASON_EASY)
            if verdict.reason is not None:
                outcome.reject(verdict.reason)
                _sample(outcome, text, label, passed=False, reason=verdict.reason)
                continue
            passed.append((text, spec, verdict))
        # 되찾기 순위가 높고(작고) Jev 점수가 높은 것부터 남긴다.
        passed.sort(key=lambda item: (item[2].rank or 0, -(item[2].probability or 0.0)))
        for text, _spec, _verdict in passed[limit:]:
            outcome.reject(REASON_SPARE)
            _sample(outcome, text, label, passed=False, reason=REASON_SPARE)
        for text, spec, verdict in passed[:limit]:
            _sample(outcome, text, label, passed=True, reason="")
            filters.remember(text, is_easy=verdict.is_easy)
            outcome.added += 1
            outcome.relabeled += len(verdict.extra_positives)
            if dry_run:
                continue
            query = await _insert(
                db,
                dataset_id=dataset_id,
                document=document,
                text=text,
                spec=spec,
                jev_probability=verdict.probability,
                extra_positives=verdict.extra_positives,
            )
            outcome.query_ids.append(query.id)
            if log is not None:
                log.add(
                    target=HelperTarget.QUERY,
                    field_name=HelperField.CREATED,
                    query_id=query.id,
                    before=False,
                    after=True,
                )
        return min(len(passed), limit)

    async def run_round(targets: list[tuple[Document, int, int]], *, reports: bool) -> bool:
        """(청크, 설정 시작 자리, 남길 수)마다 후보를 만들고 걸러 넣는다. 멈추라고 했으면 False."""
        done = 0
        for start in range(0, len(targets), DOCUMENTS_PER_CALL):
            if is_canceled is not None and await is_canceled():
                return False
            batch = targets[start : start + DOCUMENTS_PER_CALL]
            specs = {
                document.id: _specs(
                    document,
                    count=wanted * CANDIDATE_FACTOR,
                    offset=offset,
                    question_share=question_share,
                )
                for document, offset, wanted in batch
            }
            generated, used = await _ask(
                llm_connection,
                [document for document, _offset, _wanted in batch],
                specs=specs,
                examples=examples,
                transport=transport,
            )
            outcome.tokens += used
            for document, _offset, wanted in batch:
                texts = generated.get(document.id, [])
                taken = await take(document, texts, specs[document.id], limit=wanted)
                kept[document.id] = kept.get(document.id, 0) + taken
            done += len(batch)
            if not dry_run:
                await db.commit()
            if reports and on_progress is not None:
                await on_progress(done, len(targets))
        return True

    first = [(document, 0, per_chunk) for document in documents]
    is_finished = await run_round(first, reports=True)
    if is_finished:
        # 모자란 청크만 한 번 더: 첫 판과 다른 설정 자리로 모자란 만큼의 후보를 만든다.
        retry = [
            (document, per_chunk * CANDIDATE_FACTOR, per_chunk - kept.get(document.id, 0))
            for document in documents
            if kept.get(document.id, 0) < per_chunk
        ]
        outcome.retried = len(retry)
        if retry:
            is_finished = await run_round(retry, reports=False)
    outcome.short = sum(1 for document in documents if kept.get(document.id, 0) < per_chunk)
    if is_finished and not dry_run:
        _give_up(documents, kept=kept, outcome=outcome, log=log)
        await db.commit()
    return outcome


def _give_up(
    documents: list[Document],
    *,
    kept: dict[int, int],
    outcome: Outcome,
    log: edits.ChangeLog | None,
) -> None:
    """두 판 모두 거르기를 하나도 못 넘긴 청크를 '질의 안 만듦'으로 둔다(변경 기록이 있으면 되돌릴 수 있다).

    그대로 두면 질의가 필요한 문서로 남아, 흐름이 질의 만들기에 머물고 실행마다 같은 청크로 다시 만든다.
    """
    for document in documents:
        if kept.get(document.id, 0) or document.skip_generation:
            continue
        document.skip_generation = True
        document.row_version += 1
        outcome.gave_up += 1
        if log is not None:
            log.add(
                target=HelperTarget.DOCUMENT,
                field_name=HelperField.SKIP_GENERATION,
                document_id=document.id,
                before=False,
                after=True,
            )


def _sample(outcome: Outcome, text: str, label: str, *, passed: bool, reason: str) -> None:
    if len(outcome.samples) < SAMPLE_ROWS:
        outcome.samples.append((text, label, passed, reason))


# ---------- 안에서만 쓰는 것 ----------


def _specs(document: Document, *, count: int, offset: int, question_share: int) -> list[Spec]:
    """청크의 후보 설정 count개 (자리 offset부터). 해시로 시작을 정하고 자리마다 돌려서, 다시 해도 같다.

    자리마다 검색어형 · 질문형을 질문형 몫에 맞게 고르게 섞고(황금비 수열), 질문형은 네 유형을 돌린다.
    길이 · 난이도도 자리마다 돌린다. 한 번 더 만들 때는 다음 자리부터 받아 첫 판과 다른 설정이 된다.
    """
    digest = int(hashlib.sha256(document.text_hash.encode()).hexdigest()[:8], 16)
    start = digest / 2**32
    specs = []
    for slot in range(offset, offset + count):
        position = (start + slot * MIX_STEP) % 1.0
        is_question = position * 100 < question_share
        kind = (
            QUESTION_TYPES[(digest + slot) % len(QUESTION_TYPES)] if is_question else KEYWORD_TYPE
        )
        specs.append(
            Spec(
                kind=kind,
                length=QUERY_LENGTHS[(digest // len(QUERY_TYPES) + slot) % len(QUERY_LENGTHS)],
                difficulty=QUERY_DIFFICULTIES[(digest // 7 + slot) % len(QUERY_DIFFICULTIES)],
            )
        )
    return specs


async def _example_queries(db: AsyncSession, *, dataset_id: int) -> list[str]:
    """예시로 보일 원본 질의 몇 개 (없으면 빈 목록). 고르게 건너뛰며 고른다."""
    pool = list(
        await db.scalars(
            select(Query.text)
            .where(
                Query.dataset_id == dataset_id,
                QUERY_INCLUDED,
                Query.source == QuerySource.ORIGINAL.value,
            )
            .order_by(Query.id)
            .limit(EXAMPLE_POOL)
        )
    )
    if not pool:
        return []
    step = max(1, len(pool) // EXAMPLE_QUERIES)
    return pool[::step][:EXAMPLE_QUERIES]


async def _ask(
    connection: Connection,
    documents: list[Document],
    *,
    specs: dict[int, list[Spec]],
    examples: list[str],
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[dict[int, list[str]], int]:
    """청크 묶음의 질의 후보를 LLM에 JSON으로 받는다. (청크 번호 → 후보들, 쓴 토큰)."""
    lines = []
    for document in documents:
        wanted = specs[document.id]
        listed = "\n".join(f"{index}. {spec.text()}" for index, spec in enumerate(wanted, start=1))
        lines.append(
            f"[{document.id}] 만들 것 {len(wanted)}개:\n{listed}\n"
            + document.training_text[:DOCUMENT_PROMPT_CHARS]
        )
    system = SYSTEM_PROMPT
    if examples:
        system += "\n이 데이터의 실제 질의 보기(모양을 닮게):\n" + "\n".join(
            f"- {example}" for example in examples
        )
    reply = await llm.chat(
        connection,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="\n\n".join(lines)),
        ],
        tools=[],
        max_tokens=GENERATE_MAX_TOKENS,
        transport=transport,
    )
    return _parse(reply.content), reply.input_tokens + reply.output_tokens


def _parse(content: str) -> dict[int, list[str]]:
    """LLM 답에서 {"items": [{"id", "queries"}]}를 읽는다. 앞뒤 글 · 코드 울타리가 있어도 첫 { … 마지막 }을 읽는다."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(content[start : end + 1])
    except ValueError:
        return {}
    items = parsed.get("items") if isinstance(parsed, dict) else None
    result: dict[int, list[str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            document_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        queries = item.get("queries")
        if isinstance(queries, list):
            result[document_id] = [str(text).strip() for text in queries if str(text).strip()]
    return result


class _Filters:
    """거르기 · 정답 더하기 · 쉬운 쌍 상한. 임베딩 · Jev가 없으면 그 거르기는 건너뛴다."""

    def __init__(self) -> None:
        self.embedding_connection: Connection | None = None
        self.transport: httpx.AsyncBaseTransport | None = None
        self.jev_connection: Connection | None = None
        self.jev_transport: httpx.AsyncBaseTransport | None = None
        self.unit_documents: np.ndarray | None = None
        self.document_rows: dict[int, int] = {}
        self.document_ids: list[int] = []
        self.unit_queries: np.ndarray | None = None
        self.hashes: set[str] = set()
        self.round_trip = True
        self.easy_cap = DEFAULT_EASY_PAIR_CAP
        self.easy_count = 0
        self.query_count = 0
        self.dataset_id = 0
        self.db: AsyncSession | None = None

    @classmethod
    async def prepare(
        cls,
        db: AsyncSession,
        *,
        dataset_id: int,
        settings: DatasetSettings | None,
        transport: httpx.AsyncBaseTransport | None,
        jev_transport: httpx.AsyncBaseTransport | None,
    ) -> "_Filters":
        filters = cls()
        filters.db = db
        filters.dataset_id = dataset_id
        filters.transport = transport
        filters.jev_transport = jev_transport
        filters.round_trip = settings.round_trip if settings else True
        filters.easy_cap = settings.easy_pair_cap if settings else DEFAULT_EASY_PAIR_CAP
        filters.jev_connection = await connections.get_connection(db, role=ConnectionRole.JEV)
        filters.hashes = set(
            await db.scalars(
                select(Query.text_hash).where(Query.dataset_id == dataset_id, QUERY_ACTIVE)
            )
        )
        filters.query_count, filters.easy_count = await _easy_counts(db, dataset_id=dataset_id)
        if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
            return filters
        try:
            connection = await semantic.embedding_connection(db, settings=settings)
            model = await embedding.ensure_model(db, connection, transport=transport)
            documents = (
                await db.execute(
                    select(Document.id, Document.input_hash)
                    .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
                    .order_by(Document.id)
                )
            ).all()
            queries = (
                await db.execute(
                    select(Query.text_hash).where(Query.dataset_id == dataset_id, QUERY_ACTIVE)
                )
            ).all()
            hashes = sorted({*(row[1] for row in documents), *(row[0] for row in queries)})
            missing = await embedding.find_missing_hashes(db, model_id=model.id, text_hashes=hashes)
            if missing:
                texts = await semantic.texts_for(db, dataset_id=dataset_id, text_hashes=missing)
                vectors = await embedding.embed_texts(connection, texts, transport=transport)
                await embedding.save_vectors(db, model=model, text_hashes=missing, vectors=vectors)
                await db.commit()
            loaded = await embedding.load_vectors(db, model=model, text_hashes=hashes)
            position = {text_hash: index for index, text_hash in enumerate(hashes)}
            filters.unit_documents = _normalized(loaded[[position[row[1]] for row in documents]])
            filters.document_rows = {row[0]: index for index, row in enumerate(documents)}
            filters.document_ids = [row[0] for row in documents]
            filters.unit_queries = (
                _normalized(loaded[[position[row[0]] for row in queries]]) if queries else None
            )
            filters.embedding_connection = connection
        except ExternalServiceError:
            # 임베딩이 안 되면 되찾기 · 근접 중복 거르기 없이 만든다.
            filters.embedding_connection = None
        return filters

    async def check(self, text: str, document: Document, *, outcome: Outcome) -> Verdict:
        """후보 하나를 거른다. 되찾기 순위 · 원 청크보다 위의 청크 · Jev 확률을 함께 돌려준다."""
        compact = text.strip()
        if len(compact) < MIN_QUERY_CHARS or len(compact) > QUERY_MAX_LENGTH:
            return Verdict(reason=REASON_SHAPE)
        if is_context_dependent(compact):
            return Verdict(reason=REASON_CONTEXT)
        if make_text_hash(compact) in self.hashes:
            return Verdict(reason=REASON_NEAR)
        is_easy = is_easy_pair(compact, document.text)
        rank: int | None = None
        above: tuple[int, ...] = ()
        if self.embedding_connection is not None and self.unit_documents is not None:
            [vector] = await embedding.embed_texts(
                self.embedding_connection, [normalize_text(compact)], transport=self.transport
            )
            unit = _normalized(np.asarray([vector], dtype=np.float32))[0]
            has_queries = self.unit_queries is not None and len(self.unit_queries) > 0
            if has_queries and float((self.unit_queries @ unit).max()) >= NEAR_DUPLICATE_SIMILARITY:
                return Verdict(reason=REASON_NEAR)
            row = self.document_rows.get(document.id)
            if row is not None and self.round_trip:
                similarities = self.unit_documents @ unit
                rank = int(np.count_nonzero(similarities > similarities[row])) + 1
                closer = np.flatnonzero(similarities > similarities[row])
                ordered = closer[np.argsort(-similarities[closer], kind="stable")]
                above = tuple(self.document_ids[index] for index in ordered[:RELABEL_MAX])
        probability = None
        if self.jev_connection is not None:
            probability = await self._ask_jev(compact, document.training_text, outcome=outcome)
            if probability is not None and probability < judging.CONFIRM_PROBABILITY:
                return Verdict(reason=REASON_JEV, rank=rank, probability=probability)
        is_too_far = rank is not None and rank > ROUND_TRIP_TOP
        return Verdict(
            reason=REASON_ROUND_TRIP if is_too_far else None,
            rank=rank,
            probability=probability,
            above=above,
            is_easy=is_easy,
        )

    async def relabel(self, verdict: Verdict, text: str, *, outcome: Outcome) -> Verdict:
        """원 청크보다 위의 청크를 Jev에 물어 정답을 더한다. 되찾기로 걸렸어도 더한 정답이 있으면 살린다."""
        can_relabel = verdict.reason in (None, REASON_ROUND_TRIP) and verdict.above
        if not can_relabel or self.jev_connection is None or self.db is None:
            return verdict
        texts = {
            document_id: input_text if input_text is not None else body
            for document_id, input_text, body in await self.db.execute(
                select(Document.id, Document.input_text, Document.text).where(
                    Document.id.in_(verdict.above)
                )
            )
        }
        confirmed: list[tuple[int, float]] = []
        for document_id in verdict.above:
            probability = await self._ask_jev(text.strip(), texts[document_id], outcome=outcome)
            if probability is not None and probability >= judging.CONFIRM_PROBABILITY:
                confirmed.append((document_id, probability))
        if not confirmed:
            return verdict
        return Verdict(
            reason=None,
            rank=verdict.rank,
            probability=verdict.probability,
            above=verdict.above,
            is_easy=verdict.is_easy,
            extra_positives=tuple(confirmed),
        )

    def easy_allowed(self) -> bool:
        """쉬운 쌍을 하나 더 넣어도 상한 안인지."""
        return (self.easy_count + 1) / (self.query_count + 1) <= self.easy_cap

    def remember(self, text: str, *, is_easy: bool) -> None:
        """넣은 질의를 다음 거르기에 쓴다(같은 글 · 쉬운 쌍 수, 벡터는 다음 실행에서)."""
        self.hashes.add(make_text_hash(text))
        self.query_count += 1
        self.easy_count += int(is_easy)

    async def _ask_jev(self, query: str, document: str, *, outcome: Outcome) -> float | None:
        if self.jev_connection is None:
            return None
        try:
            probability = await judging.ask_contains(
                self.jev_connection, query=query, document=document, transport=self.jev_transport
            )
        except ExternalServiceError:
            self.jev_connection = None
            return None
        outcome.jev_calls += 1
        return probability


async def _easy_counts(db: AsyncSession, *, dataset_id: int) -> tuple[int, int]:
    """학습에 쓰는 질의 수와 그 가운데 쉬운 쌍(정답과 글자 겹침 ≥ 기준)인 질의 수."""
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(Query)
            .where(Query.dataset_id == dataset_id, QUERY_INCLUDED)
        )
        or 0
    )
    easy = int(
        await db.scalar(
            select(func.count(func.distinct(Query.id)))
            .join(Judgment, Judgment.query_id == Query.id)
            .where(
                Query.dataset_id == dataset_id,
                QUERY_INCLUDED,
                Judgment.grade >= POSITIVE_MIN_GRADE,
                Judgment.overlap >= EASY_PAIR_OVERLAP,
                func.char_length(func.replace(Query.text, " ", "")) >= EASY_PAIR_MIN_CHARS,
            )
        )
        or 0
    )
    return total, easy


def _normalized(vectors: np.ndarray) -> np.ndarray:
    unit = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(unit, axis=1, keepdims=True)
    return unit / np.where(norms == 0, 1, norms)


async def _insert(
    db: AsyncSession,
    *,
    dataset_id: int,
    document: Document,
    text: str,
    spec: Spec,
    jev_probability: float | None,
    extra_positives: tuple[tuple[int, float], ...],
) -> Query:
    """만든 질의와 정답 판정(만든 청크 + 더한 정답)을 넣는다."""
    query = Query(
        dataset_id=dataset_id,
        text=text,
        text_hash=make_text_hash(text),
        source=QuerySource.SYNTHETIC.value,
        source_document_id=document.id,
        extra={"질의 유형": spec.kind, "길이": spec.length, "난이도": spec.difficulty},
    )
    db.add(query)
    await db.flush()
    db.add(
        Judgment(
            query_id=query.id,
            document_id=document.id,
            dataset_id=dataset_id,
            grade=POSITIVE_MIN_GRADE,
            source=JudgmentSource.SYNTHETIC.value,
            teacher_score=jev_probability,
            overlap=lexical_overlap(text, document.text),
        )
    )
    for document_id, probability in extra_positives:
        other = await db.get(Document, document_id)
        db.add(
            Judgment(
                query_id=query.id,
                document_id=document_id,
                dataset_id=dataset_id,
                grade=POSITIVE_MIN_GRADE,
                source=JudgmentSource.SYNTHETIC.value,
                teacher_score=probability,
                overlap=lexical_overlap(text, other.text) if other else None,
            )
        )
    return query
