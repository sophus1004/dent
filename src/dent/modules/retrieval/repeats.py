"""반복 구간과 학습 글: 여러 문서에 되풀이되는 문장 · 메타 모양을 찾고(살피기), 사람이 떼기 · 남김을 고르게 하고,
문서마다 학습 글 · 표시(marks) · 든 반복 구간을 다시 만든다.

- 살피기(작업 ("retrieval", "scan"), 가져오기 끝에도 돈다): 표본 문서로 후보 문장을 고르고 → 모든 문서에서 세고
  → 문서의 REPEAT_MIN_SHARE(최소 REPEAT_MIN_DOCUMENTS) 이상에 든 것을 반복 구간으로 둔다 → 문서마다 다시 만든다.
  같은 열쇠의 결정은 다시 살펴도 이어 간다.
- 규칙은 모양만 본다. 메타 모양(이메일 · 웹 주소 · 저작권 표시 …)과 메타 모양이 든 문장은 '떼기'를, 나머지는 '남김'을
  제안할 뿐이고, 결정은 사람이 한다. 도우미는 보기를 읽고 제안(까닭)만 바꾼다.
- 떼기로 고른 구간은 본문을 바꾸지 않고 학습 글에서만 뺀다. 되돌리기는 결정을 바꾸는 것이다.
- 문서 표시: 깨진 글자(broken) · 질의 안 만들 까닭(pick). 진단은 이 표시만 센다(요청마다 글을 다시 읽지 않는다).
"""

import itertools
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    DatasetSettings,
    Document,
    DocumentRepeat,
    Repeat,
    RepeatDecision,
    RepeatKind,
)
from dent.modules.retrieval.schemas import SettingsUpdate
from dent.modules.retrieval.service import DOCUMENT_ACTIVE
from dent.modules.retrieval.text_rules import (
    META_KEY_PREFIX,
    META_NAMES,
    RepeatHit,
    covered_chars,
    cut_spans,
    document_input,
    estimate_tokens,
    has_broken_text,
    is_edge,
    meta_hits,
    sentence_hits,
    skip_generation_reason,
)
from dent.system import datasets as datasets_service
from dent.system import jobs
from dent.system.exceptions import NotFoundError
from dent.system.text import make_text_hash

# 살피기 작업의 종류와 단계 이름 (화면 index.ts의 jobKinds.scan.phases와 같다)
SCAN_JOB_KIND = "scan"
PHASE_SAMPLE = "sample"
PHASE_COUNT = "count"
PHASE_REFRESH = "refresh"

# 후보 문장을 고를 때 읽는 문서 수 (고르게 건너뛰며). 수백만 건을 두 번 다 읽지 않는다.
SCAN_SAMPLE = 20_000

# 한 번에 읽고 고치는 문서 수
SCAN_BATCH = 2_000

# 반복 구간으로 두는 기준: 문서의 이 몫 이상, 그리고 이 수 이상에 든 것
REPEAT_MIN_SHARE = 0.005
REPEAT_MIN_DOCUMENTS = 3

# 화면에 올리는 반복 문장의 최대 수 (많이 든 것부터). 메타 모양은 종류가 적어 모두 둔다.
REPEAT_MAX_SENTENCES = 200

# 반복 구간마다 남기는 보기 원문 수
REPEAT_SAMPLES = 3

# 보기 문서: 보이는 문서 수 · 구간 앞뒤로 보이는 글자 수
SAMPLE_DOCUMENTS = 3
SAMPLE_CONTEXT_CHARS = 80

# 제안의 까닭 (규칙)
REASON_META = "메타 모양"
REASON_HAS_META = "메타 모양이 든 문장"
REASON_CONTENT = "내용일 수 있음"

REPEAT_NOT_FOUND_MESSAGE = "반복 구간을 찾을 수 없습니다."


@dataclass(frozen=True)
class Rules:
    """학습 글을 만드는 규칙: 구획 제목 붙이기 · 알고 있는 반복 구간 · 떼기로 고른 것."""

    # 학습 글 머리말에 구획 경로를 붙이는지 (설정)
    section_header: bool

    # 반복 문장 열쇠 → 번호 · 메타 모양 열쇠 → 번호
    sentence_ids: dict[str, int] = field(default_factory=dict)
    meta_ids: dict[str, int] = field(default_factory=dict)

    # 떼기로 고른 열쇠들
    removed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Refreshed:
    """문서 하나를 다시 잰 값."""

    text_hash: str
    input_text: str | None
    input_hash: str
    token_count: int
    marks: dict[str, Any]
    repeat_ids: frozenset[int]


@dataclass
class _Stat:
    """살피기에서 반복 구간 하나를 센 것."""

    kind: RepeatKind
    count: int = 0
    head: int = 0
    tail: int = 0
    samples: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScanResult:
    """살피기 결과의 수."""

    documents: int
    sentences: int
    metas: int
    changed: int


@dataclass(frozen=True)
class RepeatSample:
    """반복 구간이 든 보기 문서 하나 (구간 앞뒤는 잘라서)."""

    document_id: int
    title: str
    before: str
    span: str
    after: str


@dataclass(frozen=True)
class RepeatList:
    """반복 구간 목록과 결정별 수."""

    items: list[Repeat]
    counts: dict[str, int]
    documents: int
    scanned_at: datetime | None


# ---------- 규칙 · 문서 하나 ----------


async def load_rules(db: AsyncSession, *, dataset_id: int) -> Rules:
    """데이터셋의 학습 글 규칙 (설정 · 반복 구간 · 결정)."""
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    sentence_ids: dict[str, int] = {}
    meta_ids: dict[str, int] = {}
    removed: set[str] = set()
    for repeat_id, key, kind, decision in await db.execute(
        select(Repeat.id, Repeat.key, Repeat.kind, Repeat.decision).where(
            Repeat.dataset_id == dataset_id
        )
    ):
        target = meta_ids if kind == RepeatKind.META.value else sentence_ids
        target[key] = repeat_id
        if decision == RepeatDecision.REMOVE.value:
            removed.add(key)
    return Rules(
        section_header=settings.section_header if settings else True,
        sentence_ids=sentence_ids,
        meta_ids=meta_ids,
        removed=frozenset(removed),
    )


def refresh_values(*, title: str, header: str, section: str, text: str, rules: Rules) -> Refreshed:
    """문서 하나의 학습 글 · 표시 · 든 반복 구간을 잰다."""
    hits: list[RepeatHit] = []
    if rules.sentence_ids:
        hits += sentence_hits(text, set(rules.sentence_ids))
    if rules.meta_ids:
        hits += [hit for hit in meta_hits(text) if hit.key in rules.meta_ids]
    removed = [(hit.start, hit.end) for hit in hits if hit.key in rules.removed]
    body = cut_spans(text, removed) if removed else text
    training = document_input(
        title=title, header=header, section=section, body=body, section_header=rules.section_header
    )
    share = covered_chars((hit.start, hit.end) for hit in hits) / max(len(text), 1)
    marks: dict[str, Any] = {}
    if has_broken_text(text):
        marks["broken"] = True
    reason = skip_generation_reason(text, repeat_share=share)
    if reason is not None:
        marks["pick"] = reason
    ids = {rules.sentence_ids.get(hit.key) or rules.meta_ids.get(hit.key) for hit in hits}
    return Refreshed(
        text_hash=make_text_hash(text),
        input_text=None if training == text else training,
        input_hash=make_text_hash(training),
        token_count=estimate_tokens(training),
        marks=marks,
        repeat_ids=frozenset(repeat_id for repeat_id in ids if repeat_id is not None),
    )


def apply_values(document: Document, values: Refreshed) -> bool:
    """잰 값을 문서에 넣는다. 학습 글이 바뀌었으면 True."""
    is_changed = document.input_hash != values.input_hash
    document.text_hash = values.text_hash
    document.input_text = values.input_text
    document.input_hash = values.input_hash
    document.token_count = values.token_count
    document.marks = values.marks
    return is_changed


async def refresh_documents(
    db: AsyncSession, *, dataset_id: int, documents: Sequence[Document], rules: Rules | None = None
) -> int:
    """문서들의 학습 글 · 표시 · 든 반복 구간을 다시 만든다. 학습 글이 바뀐 수. 커밋은 부른 쪽이 한다.

    글 · 제목 · 구획이 바뀌거나(고치기 · 나누기 · 되돌리기) 반복 구간 결정이 바뀌면 부른다.
    """
    if not documents:
        return 0
    rules = rules or await load_rules(db, dataset_id=dataset_id)
    changed = 0
    links: list[dict[str, int]] = []
    for document in documents:
        values = refresh_values(
            title=document.title,
            header=document.header,
            section=document.section,
            text=document.text,
            rules=rules,
        )
        changed += int(apply_values(document, values))
        links += [
            {"document_id": document.id, "repeat_id": repeat_id} for repeat_id in values.repeat_ids
        ]
    await db.flush()
    ids = [document.id for document in documents]
    await db.execute(delete(DocumentRepeat).where(DocumentRepeat.document_id.in_(ids)))
    if links:
        await db.execute(insert(DocumentRepeat).on_conflict_do_nothing(), links)
    return changed


# ---------- 살피기 ----------


async def start_scan(db: AsyncSession, *, dataset_id: int) -> int:
    """반복 구간 살피기 작업을 넣고 작업 번호를 돌려준다. 가져온 것이 없으면 NotFoundError."""
    await retrieval_service.get_settings(db, dataset_id=dataset_id)
    job = await jobs.enqueue_job(
        db,
        module=retrieval_service.MODULE_NAME,
        kind=SCAN_JOB_KIND,
        params={"dataset_id": dataset_id},
        dataset_id=dataset_id,
    )
    await db.commit()
    return job.id


async def scan_dataset(db: AsyncSession, *, dataset_id: int, job_id: int | None) -> ScanResult:
    """반복 구간을 찾아 적고 모든 문서의 학습 글 · 표시를 다시 만든다. 커밋은 묶음마다 한다."""
    settings = await retrieval_service.get_settings(db, dataset_id=dataset_id)
    ids = list(
        await db.scalars(
            select(Document.id)
            .where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
            .order_by(Document.id)
        )
    )
    total = len(ids)

    # 1. 표본 문서로 후보 문장을 고른다(모든 문장을 세면 메모리가 모자란다).
    step = max(1, math.ceil(total / SCAN_SAMPLE))
    sample_ids = ids[::step]
    await _phase(db, job_id=job_id, phase=PHASE_SAMPLE, total=len(sample_ids))
    counter: Counter[str] = Counter()
    done = 0
    for chunk in itertools.batched(sample_ids, SCAN_BATCH):
        for text in await _texts(db, chunk):
            counter.update({hit.key for hit in sentence_hits(text)})
        done += len(chunk)
        await _progress(db, job_id=job_id, done=done, total=len(sample_ids))
    sample_min = max(2, math.ceil(len(sample_ids) * REPEAT_MIN_SHARE / 2))
    candidates = {key for key, count in counter.items() if count >= sample_min}
    del counter

    # 2. 모든 문서에서 후보 문장과 메타 모양을 센다.
    await _phase(db, job_id=job_id, phase=PHASE_COUNT, total=total)
    stats: dict[str, _Stat] = {}
    done = 0
    for chunk in itertools.batched(ids, SCAN_BATCH):
        for text in await _texts(db, chunk):
            hits = (sentence_hits(text, candidates) if candidates else []) + meta_hits(text)
            _count(stats, hits, text_length=len(text))
        done += len(chunk)
        await _progress(db, job_id=job_id, done=done, total=total)
    threshold = max(REPEAT_MIN_DOCUMENTS, math.ceil(total * REPEAT_MIN_SHARE))
    sentences = sorted(
        (
            (key, stat)
            for key, stat in stats.items()
            if stat.kind == RepeatKind.SENTENCE and stat.count >= threshold
        ),
        key=lambda pair: (-pair[1].count, pair[0]),
    )[:REPEAT_MAX_SENTENCES]
    metas = [
        (key, stat)
        for key, stat in stats.items()
        if stat.kind == RepeatKind.META and stat.count >= REPEAT_MIN_DOCUMENTS
    ]
    await _save_repeats(db, dataset_id=dataset_id, found=dict(sentences + metas))

    # 3. 모든 문서의 학습 글 · 표시 · 든 반복 구간을 다시 만든다.
    await _phase(db, job_id=job_id, phase=PHASE_REFRESH, total=total)
    rules = await load_rules(db, dataset_id=dataset_id)
    changed = 0
    done = 0
    for chunk in itertools.batched(ids, SCAN_BATCH):
        documents = list(
            await db.scalars(select(Document).where(Document.id.in_(chunk)).order_by(Document.id))
        )
        changed += await refresh_documents(
            db, dataset_id=dataset_id, documents=documents, rules=rules
        )
        done += len(chunk)
        await _progress(db, job_id=job_id, done=done, total=total)
    settings.scanned_at = datetime.now(UTC)
    if changed:
        # 학습 글이 바뀌었으면 뜻 분석 · 오답이 그 사이 바뀐 것으로 보이게 한다.
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return ScanResult(documents=total, sentences=len(sentences), metas=len(metas), changed=changed)


def _count(stats: dict[str, _Stat], hits: list[RepeatHit], *, text_length: int) -> None:
    """한 문서의 구간들을 센다: 열쇠마다 문서 하나로, 앞머리 · 꼬리에 있으면 따로."""
    seen: dict[str, tuple[bool, bool, str]] = {}
    for hit in hits:
        head, tail = is_edge(hit, text_length)
        before = seen.get(hit.key)
        if before is None:
            seen[hit.key] = (head, tail, hit.text)
        else:
            seen[hit.key] = (before[0] or head, before[1] or tail, before[2])
    for key, (head, tail, text) in seen.items():
        is_meta = key.startswith(META_KEY_PREFIX)
        stat = stats.setdefault(
            key, _Stat(kind=RepeatKind.META if is_meta else RepeatKind.SENTENCE)
        )
        stat.count += 1
        stat.head += int(head)
        stat.tail += int(tail)
        if len(stat.samples) < REPEAT_SAMPLES and text not in stat.samples:
            stat.samples.append(text)


def rule_suggestion(kind: RepeatKind, samples: list[str]) -> tuple[RepeatDecision, str]:
    """규칙의 제안: 메타 모양과 메타 모양이 든 문장은 떼기, 나머지는 남김(내용일 수 있다)."""
    if kind == RepeatKind.META:
        return RepeatDecision.REMOVE, REASON_META
    if any(meta_hits(sample) for sample in samples):
        return RepeatDecision.REMOVE, REASON_HAS_META
    return RepeatDecision.KEEP, REASON_CONTENT


async def _save_repeats(db: AsyncSession, *, dataset_id: int, found: dict[str, _Stat]) -> None:
    """찾은 반복 구간을 적는다. 같은 열쇠는 결정을 이어 가고, 이번에 없는 것은 지운다."""
    existing = {
        repeat.key: repeat
        for repeat in await db.scalars(select(Repeat).where(Repeat.dataset_id == dataset_id))
    }
    for key, stat in found.items():
        text = (
            META_NAMES[key.removeprefix(META_KEY_PREFIX)]
            if stat.kind == RepeatKind.META
            else stat.samples[0]
        )
        suggestion, reason = rule_suggestion(stat.kind, stat.samples)
        repeat = existing.pop(key, None)
        if repeat is None:
            db.add(
                Repeat(
                    dataset_id=dataset_id,
                    key=key,
                    kind=stat.kind.value,
                    text=text,
                    samples=stat.samples,
                    document_count=stat.count,
                    head_count=stat.head,
                    tail_count=stat.tail,
                    suggestion=suggestion.value,
                    reason=reason,
                )
            )
            continue
        repeat.text = text
        repeat.samples = stat.samples
        repeat.document_count = stat.count
        repeat.head_count = stat.head
        repeat.tail_count = stat.tail
    stale = [repeat.id for repeat in existing.values()]
    if stale:
        await db.execute(delete(Repeat).where(Repeat.id.in_(stale)))
    await db.flush()


async def _texts(db: AsyncSession, ids: Sequence[int]) -> list[str]:
    return list(await db.scalars(select(Document.text).where(Document.id.in_(ids))))


async def _phase(db: AsyncSession, *, job_id: int | None, phase: str, total: int) -> None:
    if job_id is not None:
        await jobs.start_phase(db, job_id=job_id, phase=phase, total=total)
        await db.commit()


async def _progress(db: AsyncSession, *, job_id: int | None, done: int, total: int) -> None:
    if job_id is not None:
        await jobs.set_progress(db, job_id=job_id, done=done, total=total)
        await db.commit()


# ---------- 목록 · 결정 (API) ----------


async def list_repeats(db: AsyncSession, *, dataset_id: int) -> RepeatList:
    """반복 구간 목록(메타 모양 먼저, 많이 든 것부터)과 결정별 수. 데이터셋이 없으면 NotFoundError."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    items = list(
        await db.scalars(
            select(Repeat)
            .where(Repeat.dataset_id == dataset_id)
            .order_by(Repeat.kind, Repeat.document_count.desc(), Repeat.id)
        )
    )
    counts = {"remove": 0, "keep": 0, "undecided": 0}
    for item in items:
        counts[item.decision or "undecided"] += 1
    documents = int(
        await db.scalar(
            select(func.count(func.distinct(DocumentRepeat.document_id)))
            .join(Repeat, Repeat.id == DocumentRepeat.repeat_id)
            .where(Repeat.dataset_id == dataset_id)
        )
        or 0
    )
    return RepeatList(
        items=items,
        counts=counts,
        documents=documents,
        scanned_at=settings.scanned_at if settings else None,
    )


async def decide_repeat(
    db: AsyncSession, *, repeat_id: int, decision: RepeatDecision | None
) -> Repeat:
    """반복 구간 하나를 떼기 · 남김으로 고른다(None이면 고르기 전으로). 든 문서의 학습 글을 다시 만든다.

    없으면 NotFoundError.
    """
    repeat = await _get(db, repeat_id=repeat_id)
    await _apply_decisions(db, dataset_id=repeat.dataset_id, wanted={repeat.id: decision})
    await db.commit()
    return await _get(db, repeat_id=repeat_id)


async def decide_repeats(
    db: AsyncSession,
    *,
    dataset_id: int,
    repeat_ids: Sequence[int],
    decision: RepeatDecision | None,
    follow_suggestion: bool,
) -> int:
    """반복 구간 여럿을 한 번에 고른다. follow_suggestion이면 틀마다 제안대로. 결정이 바뀐 수.

    데이터셋이 없으면 NotFoundError. 이 데이터셋의 것이 아닌 번호는 건너뛴다.
    """
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    repeats = list(
        await db.scalars(
            select(Repeat).where(Repeat.dataset_id == dataset_id, Repeat.id.in_(repeat_ids))
        )
    )
    wanted = {
        repeat.id: RepeatDecision(repeat.suggestion) if follow_suggestion else decision
        for repeat in repeats
    }
    changed = await _apply_decisions(db, dataset_id=dataset_id, wanted=wanted)
    await db.commit()
    return changed


async def _apply_decisions(
    db: AsyncSession, *, dataset_id: int, wanted: dict[int, RepeatDecision | None]
) -> int:
    """결정을 적고, 떼기에 들어가거나 빠진 틀이 든 문서만 학습 글을 다시 만든다. 결정이 바뀐 수. 커밋은 부른 쪽이 한다.

    남김 ↔ 고르기 전은 학습 글을 바꾸지 않으므로 문서를 다시 만들지 않는다(수천 문서를 읽지 않게).
    """
    repeats = list(await db.scalars(select(Repeat).where(Repeat.id.in_(list(wanted)))))
    now = datetime.now(UTC)
    changed = 0
    touches_removal: list[int] = []
    for repeat in repeats:
        new = wanted[repeat.id].value if wanted[repeat.id] is not None else None
        if repeat.decision == new:
            continue
        was_removed = repeat.decision == RepeatDecision.REMOVE.value
        is_removed = new == RepeatDecision.REMOVE.value
        if was_removed != is_removed:
            touches_removal.append(repeat.id)
        repeat.decision = new
        repeat.decided_at = now if new else None
        changed += 1
    await db.flush()
    if touches_removal:
        await refresh_linked(db, dataset_id=dataset_id, repeat_ids=touches_removal)
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    return changed


async def repeat_samples(db: AsyncSession, *, repeat_id: int) -> list[RepeatSample]:
    """반복 구간이 든 보기 문서 몇 개(번호 순)와 그 안의 첫 구간. 없으면 NotFoundError."""
    repeat = await _get(db, repeat_id=repeat_id)
    documents = list(
        await db.scalars(
            select(Document)
            .join(DocumentRepeat, DocumentRepeat.document_id == Document.id)
            .where(DocumentRepeat.repeat_id == repeat.id, DOCUMENT_ACTIVE)
            .order_by(Document.id)
            .limit(SAMPLE_DOCUMENTS)
        )
    )
    samples = []
    for document in documents:
        hits = (
            [hit for hit in meta_hits(document.text) if hit.key == repeat.key]
            if repeat.kind == RepeatKind.META.value
            else sentence_hits(document.text, {repeat.key})
        )
        if not hits:
            continue
        hit = hits[0]
        start = max(0, hit.start - SAMPLE_CONTEXT_CHARS)
        end = min(len(document.text), hit.end + SAMPLE_CONTEXT_CHARS)
        samples.append(
            RepeatSample(
                document_id=document.id,
                title=document.title,
                before=("…" if start > 0 else "") + document.text[start : hit.start],
                span=hit.text,
                after=document.text[hit.end : end] + ("…" if end < len(document.text) else ""),
            )
        )
    return samples


async def suggest_repeat(
    db: AsyncSession, *, repeat_id: int, suggestion: RepeatDecision, reason: str
) -> None:
    """반복 구간의 제안과 까닭을 바꾼다(도우미). 결정은 바꾸지 않는다. 커밋은 부른 쪽이 한다."""
    await db.execute(
        update(Repeat)
        .where(Repeat.id == repeat_id)
        .values(suggestion=suggestion.value, reason=reason)
    )


async def refresh_linked(db: AsyncSession, *, dataset_id: int, repeat_ids: Sequence[int]) -> int:
    """반복 구간이 든 문서들의 학습 글을 다시 만든다. 바뀐 수. 커밋은 부른 쪽이 한다."""
    ids = list(
        await db.scalars(
            select(DocumentRepeat.document_id)
            .where(DocumentRepeat.repeat_id.in_(repeat_ids))
            .distinct()
        )
    )
    rules = await load_rules(db, dataset_id=dataset_id)
    changed = 0
    for chunk in itertools.batched(ids, SCAN_BATCH):
        documents = list(await db.scalars(select(Document).where(Document.id.in_(chunk))))
        changed += await refresh_documents(
            db, dataset_id=dataset_id, documents=documents, rules=rules
        )
    return changed


async def refresh_all(db: AsyncSession, *, dataset_id: int) -> int:
    """데이터셋의 모든 코퍼스 문서의 학습 글을 다시 만든다(구획 제목 설정을 바꿨을 때). 바뀐 수."""
    ids = list(
        await db.scalars(
            select(Document.id).where(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
        )
    )
    rules = await load_rules(db, dataset_id=dataset_id)
    changed = 0
    for chunk in itertools.batched(ids, SCAN_BATCH):
        documents = list(await db.scalars(select(Document).where(Document.id.in_(chunk))))
        changed += await refresh_documents(
            db, dataset_id=dataset_id, documents=documents, rules=rules
        )
    return changed


async def update_settings(
    db: AsyncSession, *, dataset_id: int, data: SettingsUpdate
) -> DatasetSettings:
    """설정을 고친다. 구획 제목 붙이기를 바꾸면 모든 문서의 학습 글을 다시 만든다.

    없으면 NotFoundError, 순위 범위가 뒤집히면 InvalidInputError.
    """
    before = (await retrieval_service.get_settings(db, dataset_id=dataset_id)).section_header
    settings = await retrieval_service.update_settings(db, dataset_id=dataset_id, data=data)
    if settings.section_header != before and await refresh_all(db, dataset_id=dataset_id):
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
        await db.commit()
    return await retrieval_service.get_settings(db, dataset_id=dataset_id)


async def _get(db: AsyncSession, *, repeat_id: int) -> Repeat:
    repeat = await db.get(Repeat, repeat_id, populate_existing=True)
    if repeat is None:
        raise NotFoundError(REPEAT_NOT_FOUND_MESSAGE)
    return repeat
