"""데이터셋 진단: 한눈에 보는 결론을 위한 숫자와 등급.

모든 수는 SQL 집계(GROUP BY)로 DB가 센다. 수백만 건이라도 문장을 파이썬으로 불러오지 않는다.
휴지통에 없는 문장만 세고, 'included'는 학습에서 빼지 않은 문장이다.
같은 문장은 text_hash가 같은 것이다(시스템의 text.make_text_hash).

같은 문장이 여러 번 나오면 셋 가운데 하나로 센다. 문제마다 고치는 방법이 다르기 때문이다.
- 중복: 문장·라벨이 모두 같다. '중복 빼기'가 무리마다 한 건만 남긴다(records.DUPLICATE_KEY).
- 라벨 충돌: 문장은 같은데 라벨이 다르다. 어느 라벨이 맞는지 사람이 고른다.
train · valid · test는 나누지 않는다(한 덩어리). 그래서 분할 간 중복 같은 검사가 없다.
같은 문장 무리(중복·충돌)는 쿼리 한 번으로 센다. 표를 여러 번 훑으면 수백만 건에서 느리다.

등급은 검사마다 등급 사다리(ThresholdRead) 하나로 매기고, 같은 사다리를 응답(thresholds)으로도 내보낸다.
화면은 기준을 따로 적지 않고 이것을 보여 준다.
문제마다 보기(examples)의 같은 문장 무리는 세면서 함께 얻은 '첫 무리의 해시'로만 찾는다.
그래서 표를 다시 훑지 않고, 같은 문장 인덱스로 몇 줄만 더 읽는다.
짧은 문장 보기는 짧은 문장이 있을 때만 따로 찾는다(세는 쿼리에 넣으면 글자 수를 문장마다 더 세야 한다).

진단 결과는 DB의 한 줄이 아니라 계산한 값이라서, 응답 모양(OverviewRead)으로 바로 만든다.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import CTE, ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import records as records_service
from dent.modules.classification import service as classification_service
from dent.modules.classification.models import Label, Record
from dent.modules.classification.schemas import (
    BalanceRead,
    ConflictExampleRead,
    ConflictsRead,
    DuplicateExampleRead,
    DuplicatesRead,
    ExampleLabelRead,
    ExamplesRead,
    Grade,
    GradeStepRead,
    HistogramBinRead,
    OverviewLabelRead,
    OverviewRead,
    ProblemRead,
    SemanticRead,
    ShortExampleRead,
    ShortRead,
    ThresholdRead,
    ThresholdsRead,
)
from dent.system import connections
from dent.system.models import ConnectionRole

# 라벨 균형 등급: 가장 많은 라벨 ÷ 가장 적은 라벨이 1.5배 이하면 좋음, 2.5배 이하면 살펴볼 것.
BALANCE_GOOD_MAX_RATIO = 1.5
BALANCE_WARN_MAX_RATIO = 2.5

# 중복 등급: 빠질 중복이 학습 문장의 2% 미만이면 좋음, 10% 미만이면 살펴볼 것.
DUPLICATE_GOOD_BELOW_RATE = 0.02
DUPLICATE_WARN_BELOW_RATE = 0.10

# 라벨 충돌 등급: 없으면 좋음, 학습 문장의 1% 미만이면 살펴볼 것.
# 같은 문장에 다른 답이 붙어 있으면 모델이 무엇을 배워야 할지 흔들린다.
CONFLICT_WARN_BELOW_RATE = 0.01

# 등급 사다리. 위의 기준 값으로 만들고, 등급 매기기와 응답(thresholds)이 같이 쓴다.
# 짧은 문장은 있으면 살펴볼 것이다.
BALANCE_THRESHOLD = ThresholdRead(
    unit="ratio",
    steps=[
        GradeStepRead(grade="good", op="le", value=BALANCE_GOOD_MAX_RATIO),
        GradeStepRead(grade="warn", op="le", value=BALANCE_WARN_MAX_RATIO),
        GradeStepRead(grade="bad"),
    ],
)
DUPLICATE_THRESHOLD = ThresholdRead(
    unit="rate",
    steps=[
        GradeStepRead(grade="good", op="lt", value=DUPLICATE_GOOD_BELOW_RATE),
        GradeStepRead(grade="warn", op="lt", value=DUPLICATE_WARN_BELOW_RATE),
        GradeStepRead(grade="bad"),
    ],
)
CONFLICT_THRESHOLD = ThresholdRead(
    unit="rate",
    steps=[
        GradeStepRead(grade="good", op="le", value=0),
        GradeStepRead(grade="warn", op="lt", value=CONFLICT_WARN_BELOW_RATE),
        GradeStepRead(grade="bad"),
    ],
)
SHORT_THRESHOLD = ThresholdRead(
    unit="records",
    steps=[GradeStepRead(grade="good", op="le", value=0), GradeStepRead(grade="warn")],
)
THRESHOLDS = ThresholdsRead(
    balance=BALANCE_THRESHOLD,
    duplicates=DUPLICATE_THRESHOLD,
    conflicts=CONFLICT_THRESHOLD,
    short=SHORT_THRESHOLD,
)

# 길이 분포 막대 수의 상한. 마지막 막대는 끝이 열려 있어 아주 긴 문장을 모두 담는다.
# 길이 폭이 좁으면 막대를 이보다 적게 둔다(가장 긴 문장 너머에 빈 막대가 생기지 않게).
HISTOGRAM_BINS = 20

# 길이 분포의 눈금은 이 백분위까지를 고르게 나눈다. 아주 긴 문장 몇 개가 막대를 뭉개지 않게.
HISTOGRAM_UPPER_PERCENTILE = 0.99

# 짧은 문장 보기의 수. 화면의 '대상' 칸 한 줄에 들어가는 만큼.
SHORT_EXAMPLES = 2

SEMANTIC_UNAVAILABLE_DETAIL = "근접 중복·의미 쏠림·오라벨 의심은 임베딩 서버를 연결하면 봅니다."
SEMANTIC_PENDING_DETAIL = (
    "임베딩 서버는 연결돼 있습니다. 근접 중복·의미 쏠림·오라벨 의심 분석은 아직 준비 중입니다."
)


@dataclass(frozen=True)
class RecordCounts:
    """데이터셋 문장 수 요약."""

    # 휴지통에 없는 문장 수
    total: int

    # 학습에 쓰는 문장 수
    included: int

    # 학습에서 뺀 문장 수
    excluded: int

    # 휴지통에 있는 문장 수
    trashed: int

    # 학습에 쓰는 문장 가운데 짧은 것
    short: int


@dataclass(frozen=True)
class SameTextCounts:
    """학습에 쓰는 문장을 같은 문장끼리 묶어 센 결과."""

    # 서로 다른 문장 수
    distinct_texts: int

    # 중복 무리 수 (문장·라벨이 같은 것이 2건 이상)
    duplicate_groups: int

    # 중복 무리마다 한 건만 남기면 빠질 수
    duplicate_extra: int

    # 라벨 충돌 무리 수 (같은 문장에 라벨이 둘 이상)
    conflict_groups: int

    # 라벨 충돌 무리에 든 문장 수
    conflict_records: int

    # 문제마다 해시가 가장 작은 무리의 해시. 없으면 None. 보기(examples)를 찾는 데 쓴다.
    first_duplicate_hash: str | None
    first_conflict_hash: str | None


@dataclass(frozen=True)
class SameTextGroup:
    """같은 문장 무리 안에서 라벨이 같은 묶음 하나."""

    # 라벨 번호
    label_id: int | None

    # 묶음의 문장 수
    size: int

    # 묶음에서 가장 작은 문장 번호
    first_id: int


@dataclass(frozen=True)
class LabelProblemCounts:
    """라벨 하나에 걸린 같은 문장 문제의 수."""

    # 중복 빼기로 빠질 수
    duplicate_extra: int = 0

    # 라벨 충돌 무리에 든 문장 수
    conflict_records: int = 0


async def get_overview(db: AsyncSession, *, dataset_id: int) -> OverviewRead:
    """데이터셋 진단을 계산한다. 데이터셋이 없으면 NotFoundError."""
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    counts = await _count_records(db, dataset_id=dataset_id)
    same_text = await _count_same_texts(db, dataset_id=dataset_id)
    labels = await _label_rows(
        db, dataset_id=dataset_id, included_total=counts.included, same_text=same_text
    )
    balance = _balance(labels)
    duplicates = _duplicates(same_text, included_total=counts.included)
    conflicts = _conflicts(same_text, included_total=counts.included)
    short = _short(counts)
    return OverviewRead(
        dataset_id=dataset_id,
        total=counts.total,
        included=counts.included,
        excluded=counts.excluded,
        trashed=counts.trashed,
        effective_count=same_text.distinct_texts,
        labels=labels,
        balance=balance,
        duplicates=duplicates,
        conflicts=conflicts,
        short=short,
        length_histogram=await _length_histogram(db, dataset_id=dataset_id),
        problems=_problems(
            labels=labels,
            balance=balance,
            duplicates=duplicates,
            conflicts=conflicts,
            short=short,
        ),
        thresholds=THRESHOLDS,
        examples=await _examples(
            db, dataset_id=dataset_id, counts=counts, same_text=same_text, labels=labels
        ),
        semantic=await _semantic(db),
        computed_at=datetime.now(UTC),
    )


# ---------- 세기 (SQL) ----------


async def _count_records(db: AsyncSession, *, dataset_id: int) -> RecordCounts:
    """상태별 문장 수와 짧은 문장 수. 표를 한 번 훑는다."""
    is_short = func.char_length(Record.text) < records_service.SHORT_TEXT_LENGTH
    total, included, excluded, trashed, short = (
        await db.execute(
            select(
                func.count().filter(classification_service.IS_ACTIVE),
                func.count().filter(classification_service.IS_INCLUDED),
                func.count().filter(classification_service.IS_EXCLUDED),
                func.count().filter(classification_service.IS_TRASHED),
                func.count().filter(classification_service.IS_INCLUDED, is_short),
            ).where(Record.dataset_id == dataset_id)
        )
    ).one()
    return RecordCounts(
        total=total, included=included, excluded=excluded, trashed=trashed, short=short
    )


def _same_text_groups(dataset_id: int) -> tuple[CTE, CTE]:
    """학습에 쓰는 문장의 같은 문장 무리 두 층 (WITH 절).

    same: 문장·라벨이 같은 무리와 그 크기. 2건 이상이면 중복이다.
    by_text: 문장이 같은 무리. 라벨이 둘 이상이면 충돌.
    """
    same = (
        select(*records_service.DUPLICATE_KEY, func.count().label("size"))
        .where(Record.dataset_id == dataset_id, classification_service.IS_INCLUDED)
        .group_by(*records_service.DUPLICATE_KEY)
        .cte("same")
    )
    by_text = (
        select(
            same.c.text_hash,
            func.sum(same.c.size).label("size"),
            func.count().filter(same.c.size > 1).label("duplicate_groups"),
            func.sum(same.c.size - 1).label("duplicate_extra"),
            # 라벨 수를 count(DISTINCT)로 세면 정렬이 필요해 느리다.
            # 가장 작은 라벨 번호와 가장 큰 번호가 다르면 라벨이 둘 이상이다.
            (func.min(same.c.label_id) != func.max(same.c.label_id)).label("is_conflict"),
        )
        .group_by(same.c.text_hash)
        .cte("by_text")
    )
    return same, by_text


async def _count_same_texts(db: AsyncSession, *, dataset_id: int) -> SameTextCounts:
    """서로 다른 문장 수와 중복·충돌 무리를 쿼리 한 번으로 센다.

    세는 김에 문제마다 해시가 가장 작은 무리의 해시도 얻는다(보기를 찾을 때 표를 다시 훑지 않게).
    """
    _same, by_text = _same_text_groups(dataset_id)
    query = select(
        func.count().label("distinct_texts"),
        func.sum(by_text.c.duplicate_groups).label("duplicate_groups"),
        func.sum(by_text.c.duplicate_extra).label("duplicate_extra"),
        func.count().filter(by_text.c.is_conflict).label("conflict_groups"),
        func.sum(by_text.c.size).filter(by_text.c.is_conflict).label("conflict_records"),
        func.min(by_text.c.text_hash).filter(by_text.c.duplicate_groups > 0),
        func.min(by_text.c.text_hash).filter(by_text.c.is_conflict),
    ).select_from(by_text)
    *numbers, first_duplicate_hash, first_conflict_hash = (await db.execute(query)).one()
    # 무리가 하나도 없으면 sum은 null이다. 0으로 센다.
    (
        distinct_texts,
        duplicate_groups,
        duplicate_extra,
        conflict_groups,
        conflict_records,
    ) = (int(number or 0) for number in numbers)
    return SameTextCounts(
        distinct_texts=distinct_texts,
        duplicate_groups=duplicate_groups,
        duplicate_extra=duplicate_extra,
        conflict_groups=conflict_groups,
        conflict_records=conflict_records,
        first_duplicate_hash=first_duplicate_hash,
        first_conflict_hash=first_conflict_hash,
    )


async def _label_problem_counts(
    db: AsyncSession, *, dataset_id: int
) -> dict[int | None, LabelProblemCounts]:
    """라벨마다 중복으로 빠질 수와 충돌 무리에 든 문장 수 {라벨 번호: 수}."""
    same, by_text = _same_text_groups(dataset_id)
    # 충돌 무리는 대개 적다. 그것만 골라 붙여야 수백만 건끼리 맞대지 않는다.
    problem_texts = (
        select(by_text.c.text_hash, by_text.c.is_conflict)
        .where(by_text.c.is_conflict)
        .cte("problem_texts")
    )
    query = (
        select(
            same.c.label_id,
            func.sum(same.c.size - 1),
            func.sum(same.c.size).filter(problem_texts.c.is_conflict),
        )
        .select_from(same.outerjoin(problem_texts, same.c.text_hash == problem_texts.c.text_hash))
        .group_by(same.c.label_id)
    )
    return {
        label_id: LabelProblemCounts(
            duplicate_extra=int(duplicate_extra or 0),
            conflict_records=int(conflict_records or 0),
        )
        for label_id, duplicate_extra, conflict_records in await db.execute(query)
    }


async def _label_rows(
    db: AsyncSession, *, dataset_id: int, included_total: int, same_text: SameTextCounts
) -> list[OverviewLabelRead]:
    """라벨마다 문장 수·길이와 걸린 문제 수. 문장이 없는 라벨도 0으로 넣는다. 많은 순."""
    is_included = classification_service.IS_INCLUDED
    length = func.char_length(Record.text)
    stats_query = (
        select(
            Record.label_id,
            func.count().filter(is_included),
            func.count().filter(classification_service.IS_EXCLUDED),
            func.avg(length).filter(is_included),
            func.min(length).filter(is_included),
            func.max(length).filter(is_included),
            func.count().filter(is_included, length < records_service.SHORT_TEXT_LENGTH),
        )
        .where(Record.dataset_id == dataset_id, classification_service.IS_ACTIVE)
        .group_by(Record.label_id)
    )
    stats = {row[0]: row[1:] for row in (await db.execute(stats_query)).all()}

    # 같은 문장 문제가 하나도 없으면 라벨별로 다시 셀 것이 없다.
    # 깨끗한 데이터에서 표를 한 번 덜 훑는다.
    problem_records = same_text.duplicate_extra + same_text.conflict_records
    has_same_text_problems = problem_records > 0
    problem_counts = (
        await _label_problem_counts(db, dataset_id=dataset_id) if has_same_text_problems else {}
    )

    labels = (await db.scalars(select(Label).where(Label.dataset_id == dataset_id))).all()
    empty_stats = (0, 0, None, None, None, 0)
    rows = []
    for label in labels:
        (
            label_included,
            label_excluded,
            avg_length,
            min_length,
            max_length,
            short_records,
        ) = stats.get(label.id, empty_stats)
        problems = problem_counts.get(label.id, LabelProblemCounts())
        rows.append(
            OverviewLabelRead(
                label_id=label.id,
                name=label.name,
                included=label_included,
                excluded=label_excluded,
                share=label_included / included_total if included_total else 0.0,
                avg_length=float(avg_length) if avg_length is not None else None,
                min_length=min_length,
                max_length=max_length,
                duplicate_extra=problems.duplicate_extra,
                conflict_records=problems.conflict_records,
                short_records=short_records,
            )
        )
    # 많은 순. 같으면 이름 순으로 늘 같은 자리에 오게 한다.
    rows.sort(key=lambda row: (-row.included, row.name))
    return rows


async def _length_histogram(db: AsyncSession, *, dataset_id: int) -> list[HistogramBinRead]:
    """학습 문장 길이 분포(막대 많아야 HISTOGRAM_BINS개). 문장이 없으면 빈 목록."""
    in_dataset: ColumnElement[bool] = Record.dataset_id == dataset_id
    length = func.char_length(Record.text)
    shortest, upper = (
        await db.execute(
            select(
                func.min(length),
                func.percentile_cont(HISTOGRAM_UPPER_PERCENTILE).within_group(length),
            ).where(in_dataset, classification_service.IS_INCLUDED)
        )
    ).one()
    if shortest is None:
        return []
    # 마지막 막대를 뺀 나머지가 shortest ~ upper를 고르게 나눈다.
    # 폭을 올림하면 upper를 넘는 빈 막대가 생기므로, upper까지 닿는 만큼만 막대를 둔다.
    closed_bin_limit = HISTOGRAM_BINS - 1
    width = max(1, math.ceil((upper - shortest + 1) / closed_bin_limit))
    last_bin = min(closed_bin_limit, math.ceil((upper - shortest + 1) / width))
    buckets = (
        select(func.least((length - shortest) // width, last_bin).label("bucket"))
        .where(in_dataset, classification_service.IS_INCLUDED)
        .subquery()
    )
    counted = await db.execute(select(buckets.c.bucket, func.count()).group_by(buckets.c.bucket))
    count_by_bucket = dict(counted.tuples().all())
    return [
        HistogramBinRead(
            start=shortest + index * width,
            end=shortest + (index + 1) * width if index < last_bin else None,
            count=count_by_bucket.get(index, 0),
        )
        for index in range(last_bin + 1)
    ]


# ---------- 등급 매기기 ----------


def _grade(value: float, threshold: ThresholdRead) -> Grade:
    """값을 등급 사다리에 견준다. 위 칸부터 보고 처음 맞는 칸의 등급. 경계가 없는 칸은 늘 맞는다."""
    for step in threshold.steps:
        if step.op is None or step.value is None:
            return step.grade
        fits = value <= step.value if step.op == "le" else value < step.value
        if fits:
            return step.grade
    return threshold.steps[-1].grade


def _balance(labels: list[OverviewLabelRead]) -> BalanceRead:
    """학습 문장이 있는 라벨 가운데 가장 많은 것과 가장 적은 것의 비율."""
    with_records = [label for label in labels if label.included > 0]
    if len(with_records) < 2:
        return BalanceRead(ratio=None, max_label=None, min_label=None, grade="good")
    # labels는 이미 많은 순이다.
    largest, smallest = with_records[0], with_records[-1]
    ratio = largest.included / smallest.included
    return BalanceRead(
        ratio=ratio,
        max_label=largest.name,
        min_label=smallest.name,
        grade=_grade(ratio, BALANCE_THRESHOLD),
    )


def _duplicates(same_text: SameTextCounts, *, included_total: int) -> DuplicatesRead:
    """문장·라벨이 같은 무리와, 무리마다 하나만 남기면 빠질 수."""
    extra_records = same_text.duplicate_extra
    rate = extra_records / included_total if included_total else 0.0
    return DuplicatesRead(
        groups=same_text.duplicate_groups,
        extra_records=extra_records,
        rate=rate,
        grade=_grade(rate, DUPLICATE_THRESHOLD),
    )


def _conflicts(same_text: SameTextCounts, *, included_total: int) -> ConflictsRead:
    """같은 문장인데 라벨이 둘 이상인 무리."""
    records = same_text.conflict_records
    rate = records / included_total if included_total else 0.0
    return ConflictsRead(
        groups=same_text.conflict_groups, records=records, grade=_grade(rate, CONFLICT_THRESHOLD)
    )


def _short(counts: RecordCounts) -> ShortRead:
    """학습 문장 가운데 너무 짧은 것."""
    return ShortRead(
        threshold=records_service.SHORT_TEXT_LENGTH,
        records=counts.short,
        grade=_grade(counts.short, SHORT_THRESHOLD),
    )


def _problems(
    *,
    labels: list[OverviewLabelRead],
    balance: BalanceRead,
    duplicates: DuplicatesRead,
    conflicts: ConflictsRead,
    short: ShortRead,
) -> list[ProblemRead]:
    """등급이 좋음이 아닌 것마다 문제 하나. 고칠 것(bad) 먼저, 그다음 걸린 문장이 많은 순."""
    smallest = next((label for label in labels if label.name == balance.min_label), None)
    candidates = [
        (
            "imbalance",
            balance.grade,
            smallest.included if smallest else 0,
            smallest.label_id if smallest else None,
        ),
        ("duplicate", duplicates.grade, duplicates.extra_records, None),
        ("conflict", conflicts.grade, conflicts.records, None),
        ("short", short.grade, short.records, None),
    ]
    problems = [
        ProblemRead(kind=kind, severity=grade, count=count, label_id=label_id)
        for kind, grade, count, label_id in candidates
        if grade != "good"
    ]
    problems.sort(key=lambda problem: (problem.severity != "bad", -problem.count))
    return problems


# ---------- 문제마다 보기 ----------


async def _examples(
    db: AsyncSession,
    *,
    dataset_id: int,
    counts: RecordCounts,
    same_text: SameTextCounts,
    labels: list[OverviewLabelRead],
) -> ExamplesRead:
    """문제마다 보기. 같은 문장 무리는 세면서 얻은 해시로만 찾아서, 인덱스로 몇 줄만 더 읽는다."""
    first_hashes = [
        text_hash
        for text_hash in (
            same_text.first_duplicate_hash,
            same_text.first_conflict_hash,
        )
        if text_hash is not None
    ]
    groups = await _groups_by_hash(db, dataset_id=dataset_id, text_hashes=first_hashes)
    duplicate_groups = groups.get(same_text.first_duplicate_hash or "", [])
    conflict_groups = groups.get(same_text.first_conflict_hash or "", [])

    # 중복 무리: 그 해시에서 라벨이 같은 것이 2건 이상인 첫 묶음
    duplicate_group = next((group for group in duplicate_groups if group.size > 1), None)
    wanted_ids = {_first_id(conflict_groups)}
    if duplicate_group is not None:
        wanted_ids.add(duplicate_group.first_id)
    texts = await _texts_by_id(db, record_ids=[i for i in wanted_ids if i is not None])
    label_names = {label.label_id: label.name for label in labels}

    # 짧은 문장이 없으면 찾지 않는다(찾으려면 문장마다 글자 수를 세야 한다).
    has_short = counts.short > 0
    return ExamplesRead(
        duplicate=_duplicate_example(duplicate_group, texts=texts, label_names=label_names),
        conflict=_conflict_example(conflict_groups, texts=texts, label_names=label_names),
        short=await _short_examples(db, dataset_id=dataset_id) if has_short else [],
    )


async def _groups_by_hash(
    db: AsyncSession, *, dataset_id: int, text_hashes: list[str]
) -> dict[str, list[SameTextGroup]]:
    """해시마다 학습에 쓰는 문장의 라벨별 묶음. 같은 문장 인덱스로 그 해시만 읽는다."""
    if not text_hashes:
        return {}
    query = (
        select(Record.text_hash, Record.label_id, func.count(), func.min(Record.id))
        .where(
            Record.dataset_id == dataset_id,
            classification_service.IS_INCLUDED,
            Record.text_hash.in_(text_hashes),
        )
        .group_by(Record.text_hash, Record.label_id)
        .order_by(Record.text_hash, Record.label_id)
    )
    groups: dict[str, list[SameTextGroup]] = defaultdict(list)
    for text_hash, label_id, size, first_id in await db.execute(query):
        groups[text_hash].append(SameTextGroup(label_id=label_id, size=size, first_id=first_id))
    return groups


async def _texts_by_id(db: AsyncSession, *, record_ids: list[int]) -> dict[int, str]:
    """문장 번호 → 문장."""
    if not record_ids:
        return {}
    rows = await db.execute(select(Record.id, Record.text).where(Record.id.in_(record_ids)))
    return dict(rows.tuples().all())


async def _short_examples(db: AsyncSession, *, dataset_id: int) -> list[ShortExampleRead]:
    """학습에 쓰는 짧은 문장 앞의 몇 건과 글자 수. 번호 순(데이터 탭을 짧은 문장으로 거른 첫 줄들과 같다)."""
    length = func.char_length(Record.text)
    query = (
        select(Record.id, Record.text, length)
        .where(
            Record.dataset_id == dataset_id,
            classification_service.IS_INCLUDED,
            length < records_service.SHORT_TEXT_LENGTH,
        )
        .order_by(Record.id)
        .limit(SHORT_EXAMPLES)
    )
    return [
        ShortExampleRead(record_id=record_id, text=text, length=length)
        for record_id, text, length in await db.execute(query)
    ]


def _first_id(groups: list[SameTextGroup]) -> int | None:
    """무리에서 가장 작은 문장 번호. 무리가 없으면 None."""
    return min((group.first_id for group in groups), default=None)


def _duplicate_example(
    group: SameTextGroup | None, *, texts: dict[int, str], label_names: dict[int, str]
) -> DuplicateExampleRead | None:
    """중복 묶음 하나의 문장·라벨·문장 수."""
    if group is None:
        return None
    return DuplicateExampleRead(
        text=texts[group.first_id],
        label_name=label_names.get(group.label_id) if group.label_id is not None else None,
        copies=group.size,
    )


def _conflict_example(
    groups: list[SameTextGroup], *, texts: dict[int, str], label_names: dict[int, str]
) -> ConflictExampleRead | None:
    """라벨 충돌 무리의 문장과, 라벨마다 붙은 수(많은 순)."""
    first_id = _first_id(groups)
    if first_id is None:
        return None
    count_by_label: dict[int | None, int] = defaultdict(int)
    for group in groups:
        count_by_label[group.label_id] += group.size
    labels = [
        ExampleLabelRead(
            label_id=label_id,
            name=label_names.get(label_id) if label_id is not None else None,
            count=count,
        )
        for label_id, count in count_by_label.items()
    ]
    # 많은 순. 같으면 이름 순으로 늘 같은 자리에 오게 한다.
    labels.sort(key=lambda label: (-label.count, label.name or ""))
    return ConflictExampleRead(text=texts[first_id], labels=labels)


async def _semantic(db: AsyncSession) -> SemanticRead:
    """임베딩 분석을 볼 수 있는지. 외부 서버를 부르지 않고 저장된 연결만 본다(진단이 멈추지 않게)."""
    # 임베딩 분석은 아직 만들지 않았으므로 서버가 있어도 볼 수 없다. 있는 척하지 않는다.
    connection = await connections.get_connection(db, role=ConnectionRole.EMBEDDING)
    if connection is not None:
        return SemanticRead(available=False, detail=SEMANTIC_PENDING_DETAIL)
    return SemanticRead(available=False, detail=SEMANTIC_UNAVAILABLE_DETAIL)
