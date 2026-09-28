"""LLM 도우미가 분류 데이터를 읽고 고치는 몸통. 도우미(helper.py)의 도구가 이 함수들을 부른다.

읽기: 문제마다 학습에 쓰는 문장 무리(라벨 충돌), 짧은 문장, 중복 여분, 근접 중복 쌍, 오라벨 의심.
고치기: 학습에서 빼기(뺀 이유 helper) · 라벨 바꾸기 · 새 문장 더하기. 영구 삭제는 하지 않는다.
바꿀 때마다 바꾼 카드(사건) 하나를 적고, 문장마다 전 · 후를 변경 기록(classification_helper_changes)에 남긴다.
되돌리기는 카드 하나 또는 실행 전체 단위이고, 지금 값이 바꾼 뒤 값과 같을 때만 전 값으로 돌린다
(그 사이 사람이 고친 문장은 건드리지 않는다).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import overview as overview_service
from dent.modules.classification import records as records_service
from dent.modules.classification import semantic_checks, semantic_map
from dent.modules.classification import service as classification_service
from dent.modules.classification.models import (
    ExcludeReason,
    HelperChange,
    HelperChangeField,
    Label,
    LabelSuspect,
    NearDuplicate,
    Record,
    SuspectDecision,
)
from dent.modules.classification.schemas import ProblemFilter
from dent.system import datasets as datasets_service
from dent.system import helper as helper_service
from dent.system.models import HelperEventKind

# 한 문제에서 읽어 들이는 문장의 최대 수. 이보다 많으면 앞의 것만 본다(도우미가 여러 번에 나눠 고친다).
MAX_PROBLEM_RECORDS = 20_000

# 바꾼 카드에 보여 줄 문장 수
CHANGE_SAMPLE_COUNT = 3

# 변경 기록을 한 번에 넣는 줄 수
CHANGE_INSERT_CHUNK = 5_000

# 바꾼 카드의 동작 이름
ACTION_EXCLUDE = "학습에서 빼기"
ACTION_RELABEL = "라벨 바꾸기"
ACTION_CREATE = "새 문장 더하기"


@dataclass
class GroupRecord:
    """문장 무리 안의 문장 한 건."""

    id: int
    label_id: int | None
    label_name: str | None


@dataclass
class TextGroup:
    """같은 문장 무리 하나 (학습에 쓰는 문장만)."""

    text_hash: str
    text: str
    records: list[GroupRecord] = field(default_factory=list)

    def label_counts(self) -> dict[str, int]:
        """라벨 이름 → 건수."""
        counts: dict[str, int] = defaultdict(int)
        for record in self.records:
            counts[record.label_name or "—"] += 1
        return dict(counts)


@dataclass(frozen=True)
class SuspectInfo:
    """판단을 기다리는 오라벨 의심 한 건과 그 판정들."""

    text_hash: str
    text: str
    label_id: int
    label_name: str
    suggested_label_id: int
    suggested_name: str
    label_probability: float
    suggested_probability: float
    # Jev가 고른 라벨 (묻지 않았으면 None)
    jev_label_id: int | None
    jev_name: str | None
    jev_confidence: float | None
    is_confirmed: bool
    # 이 문장 · 라벨인 학습 포함 문장
    record_ids: list[int]


@dataclass(frozen=True)
class NearPairInfo:
    """근접 중복 한 쌍 (양쪽 모두 학습에 쓰는 문장이 있을 때만)."""

    a: TextGroup
    b: TextGroup
    similarity: float


@dataclass(frozen=True)
class UndoResult:
    """되돌린 결과."""

    # 전 값으로 돌린 문장 수
    reverted: int

    # 그 사이 다른 곳에서 고쳐 두고 건너뛴 수
    skipped: int


# ---------- 읽기 ----------


async def count_problem(db: AsyncSession, *, dataset_id: int, problem: ProblemFilter) -> int:
    """학습에 쓰는 문장 가운데 그 문제에 든 수 (진단과 같은 규칙)."""
    condition = records_service.included_problem_condition(problem, dataset_id=dataset_id)
    return int(await db.scalar(select(func.count()).select_from(Record).where(condition)) or 0)


async def problem_groups(
    db: AsyncSession, *, dataset_id: int, problem: ProblemFilter
) -> list[TextGroup]:
    """그 문제의 같은 문장 무리들 (라벨 충돌). 문장 해시 순."""
    condition = records_service.included_problem_condition(problem, dataset_id=dataset_id)
    rows = await db.execute(
        select(Record.id, Record.text_hash, Record.text, Record.label_id, Label.name)
        .outerjoin(Label, Label.id == Record.label_id)
        .where(condition)
        # 같은 무리가 붙어 나오고, 무리 안에서는 먼저 들어온 문장이 앞이다.
        .order_by(Record.text_hash, Record.id)
        .limit(MAX_PROBLEM_RECORDS)
    )
    return _groups_of(rows.all())


async def problem_records(
    db: AsyncSession, *, dataset_id: int, problem: ProblemFilter
) -> list[tuple[int, str]]:
    """그 문제의 문장들 (번호, 문장). 번호 순."""
    condition = records_service.included_problem_condition(problem, dataset_id=dataset_id)
    rows = await db.execute(
        select(Record.id, Record.text)
        .where(condition)
        .order_by(Record.id)
        .limit(MAX_PROBLEM_RECORDS)
    )
    return [(row.id, row.text) for row in rows]


async def duplicate_extra_ids(db: AsyncSession, *, dataset_id: int) -> list[int]:
    """중복 무리(문장 · 라벨이 같음)마다 번호가 가장 작은 것을 뺀 나머지 (중복 빼기와 같은 규칙)."""
    ranked = (
        select(
            Record.id,
            func.row_number()
            .over(partition_by=records_service.DUPLICATE_KEY, order_by=Record.id)
            .label("position"),
        )
        .where(Record.dataset_id == dataset_id, classification_service.IS_INCLUDED)
        .subquery()
    )
    rows = await db.scalars(
        select(ranked.c.id)
        .where(ranked.c.position > 1)
        .order_by(ranked.c.id)
        .limit(MAX_PROBLEM_RECORDS)
    )
    return list(rows)


async def near_pairs(db: AsyncSession, *, dataset_id: int) -> list[NearPairInfo]:
    """다 만든 뜻 분석의 근접 중복 쌍 가운데 양쪽 모두 학습에 쓰는 문장이 있는 것. 유사도 높은 순."""
    map_id = records_service.done_map_id(dataset_id)
    pairs = (
        await db.execute(
            select(NearDuplicate.text_hash_a, NearDuplicate.text_hash_b, NearDuplicate.similarity)
            .where(NearDuplicate.map_id == map_id)
            .order_by(NearDuplicate.similarity.desc())
        )
    ).all()
    if not pairs:
        return []
    hashes = {pair.text_hash_a for pair in pairs} | {pair.text_hash_b for pair in pairs}
    groups = await _groups_by_hash(db, dataset_id=dataset_id, hashes=hashes)
    return [
        NearPairInfo(
            a=groups[pair.text_hash_a], b=groups[pair.text_hash_b], similarity=pair.similarity
        )
        for pair in pairs
        if pair.text_hash_a in groups and pair.text_hash_b in groups
    ]


async def open_suspects(db: AsyncSession, *, dataset_id: int) -> list[SuspectInfo]:
    """판단을 기다리는 오라벨 의심들 (다 만든 뜻 분석). 학습에 쓰는 문장이 남은 것만."""
    map_id = records_service.done_map_id(dataset_id)
    suspects = list(
        await db.scalars(
            select(LabelSuspect)
            .where(LabelSuspect.map_id == map_id, LabelSuspect.decision.is_(None))
            .order_by(LabelSuspect.label_probability, LabelSuspect.text_hash)
        )
    )
    if not suspects:
        return []
    names = {
        label.id: label.name
        for label in await db.scalars(select(Label).where(Label.dataset_id == dataset_id))
    }
    rows = await db.execute(
        select(Record.id, Record.text_hash, Record.text, Record.label_id)
        .where(
            Record.dataset_id == dataset_id,
            classification_service.IS_INCLUDED,
            Record.text_hash.in_([suspect.text_hash for suspect in suspects]),
        )
        .order_by(Record.id)
    )
    by_key: dict[tuple[str, int | None], list[int]] = defaultdict(list)
    texts: dict[str, str] = {}
    for row in rows:
        by_key[(row.text_hash, row.label_id)].append(row.id)
        texts.setdefault(row.text_hash, row.text)
    found = []
    for suspect in suspects:
        record_ids = by_key.get((suspect.text_hash, suspect.label_id), [])
        if not record_ids:
            continue
        found.append(
            SuspectInfo(
                text_hash=suspect.text_hash,
                text=texts[suspect.text_hash],
                label_id=suspect.label_id,
                label_name=names.get(suspect.label_id, "—"),
                suggested_label_id=suspect.suggested_label_id,
                suggested_name=names.get(suspect.suggested_label_id, "—"),
                label_probability=suspect.label_probability,
                suggested_probability=suspect.suggested_probability,
                jev_label_id=suspect.jev_label_id,
                jev_name=names.get(suspect.jev_label_id) if suspect.jev_label_id else None,
                jev_confidence=suspect.jev_confidence,
                is_confirmed=suspect.is_confirmed,
                record_ids=record_ids,
            )
        )
    return found


async def included_label_counts(db: AsyncSession, *, dataset_id: int) -> dict[str, int]:
    """라벨 이름 → 학습에 쓰는 문장 수 (없는 라벨은 0)."""
    joined = (Record.label_id == Label.id) & classification_service.IS_INCLUDED
    rows = await db.execute(
        select(Label.name, func.count(Record.id))
        .outerjoin(Record, joined)
        .where(Label.dataset_id == dataset_id)
        .group_by(Label.id, Label.name)
        .order_by(Label.name)
    )
    return {row[0]: int(row[1]) for row in rows}


async def label_samples(
    db: AsyncSession, *, dataset_id: int, label_id: int, limit: int
) -> list[str]:
    """라벨의 학습 포함 문장 몇 개 (새 문장을 만들 때 모양을 보여 준다). 고르게 뽑으려고 해시 순."""
    query = select(Record.text).where(
        Record.dataset_id == dataset_id,
        Record.label_id == label_id,
        classification_service.IS_INCLUDED,
    )
    rows = await db.scalars(query.order_by(Record.text_hash).limit(limit))
    return list(rows)


async def label_texts(
    db: AsyncSession, *, dataset_id: int, label_id: int, limit: int
) -> list[tuple[str, str]]:
    """라벨의 학습 포함 문장을 해시마다 하나씩 (해시, 문장). 새 문장이 이미 있는 문장과 근접한지 볼 때 쓴다."""
    query = (
        select(Record.text_hash, Record.text)
        .where(
            Record.dataset_id == dataset_id,
            Record.label_id == label_id,
            classification_service.IS_INCLUDED,
        )
        .distinct(Record.text_hash)
        .order_by(Record.text_hash, Record.id)
        .limit(limit)
    )
    return [(text_hash, text) for text_hash, text in await db.execute(query)]


async def existing_hashes(db: AsyncSession, *, dataset_id: int, hashes: list[str]) -> set[str]:
    """이미 데이터셋에 있는(휴지통 밖) 문장 해시들."""
    if not hashes:
        return set()
    rows = await db.scalars(
        select(Record.text_hash).where(
            Record.dataset_id == dataset_id,
            Record.trashed_at.is_(None),
            Record.text_hash.in_(hashes),
        )
    )
    return set(rows)


# ---------- 고치기 ----------


async def exclude(
    db: AsyncSession, *, run_id: int, step: int, dataset_id: int, record_ids: list[int], tool: str
) -> int:
    """학습에 쓰는 문장들을 뺀다(뺀 이유 helper). 바꾼 카드 하나를 적고 뺀 수를 돌려준다. 커밋한다."""
    rows = (
        await db.execute(
            select(Record.id, Record.text)
            .where(
                Record.dataset_id == dataset_id,
                Record.id.in_(record_ids),
                classification_service.IS_INCLUDED,
            )
            .order_by(Record.id)
        )
    ).all()
    if not rows:
        return 0
    ids = [row.id for row in rows]
    event = await helper_service.add_event(
        db,
        run_id=run_id,
        step=step,
        kind=HelperEventKind.CHANGE,
        payload={
            "action": ACTION_EXCLUDE,
            "count": len(ids),
            "tool": tool,
            "samples": [row.text for row in rows[:CHANGE_SAMPLE_COUNT]],
        },
    )
    await _log_changes(
        db,
        run_id=run_id,
        event_id=event.id,
        field=HelperChangeField.EXCLUDE,
        changes=[(record_id, None, ExcludeReason.HELPER.value) for record_id in ids],
    )
    await db.execute(
        update(Record)
        .where(Record.id.in_(ids))
        .values(
            exclude_reason=ExcludeReason.HELPER,
            row_version=Record.row_version + 1,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    await _finish_change(db, run_id=run_id, dataset_id=dataset_id, changed=len(ids))
    return len(ids)


async def relabel(
    db: AsyncSession,
    *,
    run_id: int,
    step: int,
    dataset_id: int,
    targets: dict[int, int],
    tool: str,
) -> int:
    """문장마다 라벨을 바꾼다({문장 번호: 새 라벨 번호}). 이미 그 라벨이거나 학습에서 뺀 문장은 건너뛴다.

    바꾼 카드 하나를 적고 바꾼 수를 돌려준다. 커밋한다.
    """
    rows = (
        await db.execute(
            select(Record.id, Record.text, Record.label_id, Label.name)
            .outerjoin(Label, Label.id == Record.label_id)
            .where(
                Record.dataset_id == dataset_id,
                Record.id.in_(list(targets)),
                classification_service.IS_INCLUDED,
            )
            .order_by(Record.id)
        )
    ).all()
    changes = [row for row in rows if row.label_id != targets[row.id]]
    if not changes:
        return 0
    names = {
        label.id: label.name
        for label in await db.scalars(select(Label).where(Label.dataset_id == dataset_id))
    }
    event = await helper_service.add_event(
        db,
        run_id=run_id,
        step=step,
        kind=HelperEventKind.CHANGE,
        payload={
            "action": ACTION_RELABEL,
            "count": len(changes),
            "tool": tool,
            "samples": [
                f"{row.text} · {row.name or '—'} → {names.get(targets[row.id], '—')}"
                for row in changes[:CHANGE_SAMPLE_COUNT]
            ],
        },
    )
    await _log_changes(
        db,
        run_id=run_id,
        event_id=event.id,
        field=HelperChangeField.LABEL,
        changes=[(row.id, row.label_id, targets[row.id]) for row in changes],
    )
    by_label: dict[int, list[int]] = defaultdict(list)
    for row in changes:
        by_label[targets[row.id]].append(row.id)
    for label_id, ids in by_label.items():
        await db.execute(
            update(Record)
            .where(Record.id.in_(ids))
            .values(label_id=label_id, row_version=Record.row_version + 1, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
    await _finish_change(db, run_id=run_id, dataset_id=dataset_id, changed=len(changes))
    return len(changes)


async def log_created(
    db: AsyncSession,
    *,
    run_id: int,
    step: int,
    dataset_id: int,
    records: list[Record],
    tool: str,
) -> int:
    """방금 넣은 새 문장들을 바꾼 카드 하나와 변경 기록으로 남긴다(되돌리면 휴지통으로). 커밋한다."""
    if not records:
        return 0
    event = await helper_service.add_event(
        db,
        run_id=run_id,
        step=step,
        kind=HelperEventKind.CHANGE,
        payload={
            "action": ACTION_CREATE,
            "count": len(records),
            "tool": tool,
            "samples": [record.text for record in records[:CHANGE_SAMPLE_COUNT]],
        },
    )
    await _log_changes(
        db,
        run_id=run_id,
        event_id=event.id,
        field=HelperChangeField.CREATED,
        changes=[(record.id, None, record.label_id) for record in records],
    )
    await _finish_change(db, run_id=run_id, dataset_id=dataset_id, changed=len(records))
    return len(records)


async def decide_suspects(
    db: AsyncSession, *, dataset_id: int, keys: list[tuple[str, int]], decision: SuspectDecision
) -> None:
    """오라벨 의심에 사람 대신 판단을 적는다((문장 해시, 그때 라벨) 목록). 커밋은 부른 쪽이 한다."""
    if not keys:
        return
    map_id = records_service.done_map_id(dataset_id)
    for text_hash, label_id in keys:
        await db.execute(
            update(LabelSuspect)
            .where(
                LabelSuspect.map_id == map_id,
                LabelSuspect.text_hash == text_hash,
                LabelSuspect.label_id == label_id,
                LabelSuspect.decision.is_(None),
            )
            .values(decision=decision, decided_at=func.now())
        )


async def undo(db: AsyncSession, *, run_id: int, event_id: int | None = None) -> UndoResult:
    """도우미가 바꾼 것을 되돌린다(event_id가 있으면 그 카드만, 없으면 실행 전체). 커밋한다.

    지금 값이 바꾼 뒤 값과 같을 때만 돌리고, 그 사이 고친 문장은 건너뛴다. 새로 만든 문장은 휴지통으로 보낸다.
    """
    run = await helper_service.get_run(db, run_id=run_id)
    query = (
        select(
            HelperChange.id,
            HelperChange.record_id,
            HelperChange.field,
            HelperChange.before,
            HelperChange.after,
            Record.label_id,
            Record.exclude_reason,
            Record.trashed_at,
        )
        .join(Record, Record.id == HelperChange.record_id)
        .where(HelperChange.run_id == run_id, HelperChange.undone_at.is_(None))
    )
    if event_id is not None:
        query = query.where(HelperChange.event_id == event_id)
    rows = (await db.execute(query)).all()

    reverted: list[int] = []
    label_back: dict[int | None, list[int]] = defaultdict(list)
    exclude_back: dict[str | None, list[int]] = defaultdict(list)
    trash: list[int] = []
    for row in rows:
        if row.field == HelperChangeField.LABEL and row.label_id == row.after:
            label_back[row.before].append(row.record_id)
        elif row.field == HelperChangeField.EXCLUDE and row.exclude_reason == row.after:
            exclude_back[row.before].append(row.record_id)
        elif row.field == HelperChangeField.CREATED and row.trashed_at is None:
            trash.append(row.record_id)
        else:
            continue
        reverted.append(row.id)

    touched = {"row_version": Record.row_version + 1, "updated_at": func.now()}
    for label_id, ids in label_back.items():
        await db.execute(
            update(Record).where(Record.id.in_(ids)).values(label_id=label_id, **touched)
        )
    for reason, ids in exclude_back.items():
        await db.execute(
            update(Record).where(Record.id.in_(ids)).values(exclude_reason=reason, **touched)
        )
    if trash:
        await db.execute(
            update(Record).where(Record.id.in_(trash)).values(trashed_at=func.now(), **touched)
        )
    if reverted:
        await db.execute(
            update(HelperChange).where(HelperChange.id.in_(reverted)).values(undone_at=func.now())
        )
        await helper_service.add_usage(db, run_id=run_id, changed=-len(reverted))
        await datasets_service.touch_dataset(db, dataset_id=run.dataset_id)
    skipped = len(rows) - len(reverted)
    await helper_service.add_event(
        db,
        run_id=run_id,
        step=run.step_now,
        kind=HelperEventKind.NOTICE,
        payload={
            "text": f"되돌림 {len(reverted)}건" + (f" · 건너뜀 {skipped}건" if skipped else ""),
            "undo_of": event_id,
        },
    )
    await db.commit()
    return UndoResult(reverted=len(reverted), skipped=skipped)


async def undone_event_ids(db: AsyncSession, *, run_id: int) -> list[int]:
    """모든 줄을 되돌린 카드(사건) 번호들. 화면이 그 카드의 ↶를 끈다."""
    rows = await db.execute(
        select(HelperChange.event_id)
        .where(HelperChange.run_id == run_id)
        .group_by(HelperChange.event_id)
        .having(func.bool_and(HelperChange.undone_at.is_not(None)))
    )
    return [row[0] for row in rows]


# ---------- 안쪽 ----------


def _groups_of(rows: Any) -> list[TextGroup]:
    """(번호, 해시, 문장, 라벨 번호, 라벨 이름) 줄들을 해시별 무리로 묶는다. 줄 순서를 지킨다."""
    groups: dict[str, TextGroup] = {}
    for row in rows:
        group = groups.get(row.text_hash)
        if group is None:
            group = groups[row.text_hash] = TextGroup(text_hash=row.text_hash, text=row.text)
        group.records.append(GroupRecord(id=row.id, label_id=row.label_id, label_name=row.name))
    return list(groups.values())


async def _groups_by_hash(
    db: AsyncSession, *, dataset_id: int, hashes: set[str]
) -> dict[str, TextGroup]:
    """해시마다 학습에 쓰는 문장 무리."""
    rows = await db.execute(
        select(Record.id, Record.text_hash, Record.text, Record.label_id, Label.name)
        .outerjoin(Label, Label.id == Record.label_id)
        .where(
            Record.dataset_id == dataset_id,
            classification_service.IS_INCLUDED,
            Record.text_hash.in_(list(hashes)),
        )
        .order_by(Record.text_hash, Record.id)
    )
    return {group.text_hash: group for group in _groups_of(rows.all())}


async def _log_changes(
    db: AsyncSession,
    *,
    run_id: int,
    event_id: int,
    field: HelperChangeField,
    changes: list[tuple[int, Any, Any]],
) -> None:
    """(문장 번호, 전, 후) 목록을 변경 기록에 넣는다. 많으면 나눠 넣는다."""
    values = [
        {
            "run_id": run_id,
            "event_id": event_id,
            "record_id": record_id,
            "field": field.value,
            "before": before,
            "after": after,
        }
        for record_id, before, after in changes
    ]
    for start in range(0, len(values), CHANGE_INSERT_CHUNK):
        await db.execute(insert(HelperChange), values[start : start + CHANGE_INSERT_CHUNK])


async def _finish_change(db: AsyncSession, *, run_id: int, dataset_id: int, changed: int) -> None:
    """바꾼 수를 실행에 더하고 데이터셋 수정 시각을 바꾼 뒤 커밋한다(화면이 바로 보게)."""
    await helper_service.add_usage(db, run_id=run_id, changed=changed)
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()


# ---------- 재기 (도우미 단계 · 보고 · 남은 것) ----------

# 검사 열쇠 → 이름 (진단과 같은 낱말)
CHECK_TITLES = {
    "conflict": "라벨 충돌",
    "suspect": "오라벨 의심",
    "short": "짧은 문장",
    "duplicate": "중복 여분",
    "near_duplicate": "근접 중복",
    "balance": "라벨 균형",
    "skew": "의미 쏠림",
}


@dataclass(frozen=True)
class Measure:
    """검사 하나의 지금 값."""

    # 남은 수 (0이면 그 단계는 할 일이 없다)
    value: float

    # 보이는 값 (단위까지. 예: '2건', '1.8배')
    text: str

    # 등급 (good · warn · bad)
    grade: str


def pair_kind(pair: NearPairInfo) -> str:
    """근접 중복 쌍의 종류: label(라벨 다름) · same(같은 라벨)."""
    labels = {record.label_id for group in (pair.a, pair.b) for record in group.records}
    return "label" if len(labels) > 1 else "same"


async def measure_all(db: AsyncSession, *, dataset_id: int) -> dict[str, Measure]:
    """검사마다 지금 값 (진단과 같은 규칙과 등급): 다섯 단계 · 라벨 균형 · 의미 쏠림."""
    overview = await overview_service.get_overview(db, dataset_id=dataset_id)
    measures = {
        "conflict": Measure(
            overview.conflicts.records,
            f"{overview.conflicts.records}건",
            overview.conflicts.grade,
        ),
        "short": Measure(
            overview.short.records, f"{overview.short.records}건", overview.short.grade
        ),
        "duplicate": Measure(
            overview.duplicates.extra_records,
            f"{overview.duplicates.rate * 100:.1f}%",
            overview.duplicates.grade,
        ),
    }
    suspects = await open_suspects(db, dataset_id=dataset_id)
    texts = max(overview.effective_count, 1)
    rate = len(suspects) / texts
    suspect_grade = (
        "good"
        if rate < semantic_checks.SUSPECT_GOOD_BELOW_RATE
        else "warn"
        if rate < semantic_checks.SUSPECT_WARN_BELOW_RATE
        else "bad"
    )
    measures["suspect"] = Measure(len(suspects), f"{len(suspects)}건", suspect_grade)
    pairs = await near_pairs(db, dataset_id=dataset_id)
    kinds = {pair_kind(pair) for pair in pairs}
    near_grade = "warn" if "label" in kinds else "good"
    measures["near_duplicate"] = Measure(len(pairs), f"{len(pairs)}쌍", near_grade)
    balance = overview.balance
    ratio = balance.ratio or 1.0
    # 불균형은 '1배'를 넘는 만큼이 남은 몫이다(좋음이면 0으로 본다).
    measures["balance"] = Measure(
        0 if balance.grade == "good" else ratio, f"{ratio:.1f}배", balance.grade
    )
    state = await semantic_map.get_map_state(db, dataset_id=dataset_id)
    skewed = sum(1 for skew in (state.checks.skews if state.checks else []) if skew.is_skewed)
    measures["skew"] = Measure(skewed, f"{skewed}라벨", "warn" if skewed else "good")
    return measures
