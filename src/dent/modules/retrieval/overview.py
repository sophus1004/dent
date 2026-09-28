"""검색 데이터셋 진단: 흐름 3단계(1 문서 → 2 질의 → 3 하드 네거티브)의 검사, 흐름 줄, 기준 검색 점수, 분포.

검사는 모두 새 점수를 만들지 않고 수와 비율로 등급(통과 · 주의 · 심각)을 매긴다. 경계는 이 파일 위쪽 상수다.
문제인 질의 · 문서를 고르는 조건은 problems.py와 같아서, 진단의 수와 데이터 탭의 거르기가 맞는다.
뜻 분석이 만드는 검사(근접 중복 · 정답 의심 · 거짓 오답 · 빠진 정답 · 쉬운 오답 · 주제 쏠림 · 기준 검색 점수)는
가장 최근에 다 만든 분석의 결과를 쓴다. 분석이 없으면 그 검사는 보이지 않고 흐름 항목은 '뜻 분석 뒤'다.
"""

from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import problems
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    DEFAULT_EASY_PAIR_CAP,
    Analysis,
    DatasetSettings,
    Document,
    DocumentRepeat,
    ItemKind,
    Judgment,
    NearDuplicate,
    Query,
    Ranking,
    Repeat,
    Suggestion,
    SuggestionKind,
)
from dent.modules.retrieval.schemas import (
    BinRead,
    CheckRead,
    FlowItemRead,
    FlowMoveRead,
    FlowRead,
    FlowStageRead,
    KpiRead,
    LadderStepRead,
    OverviewRead,
)
from dent.modules.retrieval.service import (
    DOCUMENT_ACTIVE,
    IS_NEGATIVE,
    IS_POSITIVE,
    QUERY_ACTIVE,
    QUERY_INCLUDED,
)
from dent.modules.retrieval.text_rules import query_type

# ---------- 등급 경계 ----------

# 긴 문서 비율: 5% 미만 통과 · 20% 미만 주의 · 그 위 심각
LONG_GOOD_BELOW = 0.05
LONG_WARN_BELOW = 0.20

# 질의 중복 여분 비율: 2% 미만 통과
DUPLICATE_GOOD_BELOW = 0.02

# 정답 의심 비율: 1% 미만 통과 · 3% 미만 주의
SUSPECT_GOOD_BELOW = 0.01
SUSPECT_WARN_BELOW = 0.03

# 문맥 의존 질의 비율: 1% 미만 통과 · 5% 미만 주의
CONTEXT_GOOD_BELOW = 0.01
CONTEXT_WARN_BELOW = 0.05

# 하드 오답 비율 (오답 가운데 오답 찾기 범위 안): 50% 이상 통과
HARD_NEGATIVE_GOOD_FROM = 0.5

# 주제 쏠림: 가장 큰 무리 40% 이상이면 주의
TOPIC_SKEW_WARN_FROM = 0.4

# 질의 유형 분포를 셀 때 읽는 질의 수
QUERY_TYPE_SAMPLE = 20_000

# 흐름의 단계 · 항목 (화면의 흐름 줄과 같은 순서)
STAGES: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "문서", ("clean", "boiler", "chunk", "dedup_doc", "pick", "topics")),
    (2, "질의", ("clean_q", "dedup_q", "answer", "missing", "dist")),
    (
        3,
        "하드 네거티브",
        ("false_neg", "same_neg", "easy_neg", "count", "length", "score", "teacher"),
    ),
)

# 흐름 순서 (지금 = 이 순서에서 남은 일이 있는 첫 칸)
FLOW_ORDER: tuple[int | str, ...] = (1, "generate", 2, "mine", 3, "export")

# 흐름 칸 상태의 무게 (여럿 가운데 가장 무거운 것이 칸의 상태)
STATE_WEIGHT = {
    "bad": 7,
    "warn": 6,
    "ask": 5,
    "todo": 4,
    "done": 3,
    "wait": 2,
    "skip": 1,
    "lock": 0,
}

# 남은 일로 보는 칸 상태
OPEN_STATES = ("bad", "warn", "todo", "ask")

# 뜻 분석이 있어야 재는 항목의 글. 뜻 분석은 세 단계가 같이 기다리는 한 번의 일이라(진단의 뜻 검사 카드에
# [뜻 분석 만들기]가 따로 있다) 아직 재지 않은 항목은 기다림(wait)으로 둔다. 남은 일로 세면 글자 검사를
# 모두 통과한 단계가 '지금'으로 남는다(예: 세 쌍 입구에서 문서 단계).
ANALYSIS_PENDING_TEXT = "뜻 분석 뒤"

# 첫 정답 순위 칸
FIRST_RANK_BINS = (("1위", 1, 1), ("2~3위", 2, 3), ("4~10위", 4, 10), ("11~100위", 11, 100))

# 질의 유형의 이름 (분포 칸)
QUERY_TYPE_NAMES = {
    "who": "누가 · 무엇",
    "when_where": "언제 · 어디",
    "why_how": "왜 · 어떻게",
    "keyword": "검색어형",
    "other": "그 밖",
}

# 판정 출처의 이름 (분포 칸)
SOURCE_NAMES = {
    "original": "원본",
    "mined": "찾기",
    "synthetic": "합성",
    "helper": "도우미",
    "human": "사람",
}


def _grade(value: float, *, good_below: float, warn_below: float | None = None) -> str:
    """값이 작을수록 좋은 검사의 등급."""
    if value < good_below:
        return "good"
    if warn_below is not None and value < warn_below:
        return "warn"
    return "warn" if warn_below is None else "bad"


def _rate_ladder(good_below: float, warn_below: float | None) -> list[LadderStepRead]:
    steps = [LadderStepRead(grade="good", text=f"< {good_below * 100:g}%")]
    if warn_below is None:
        steps.append(LadderStepRead(grade="warn", text=f"≥ {good_below * 100:g}%"))
    else:
        steps.append(LadderStepRead(grade="warn", text=f"< {warn_below * 100:g}%"))
        steps.append(LadderStepRead(grade="bad", text=f"≥ {warn_below * 100:g}%"))
    return steps


def _count_ladder(bad: bool = False) -> list[LadderStepRead]:
    """0건이면 통과, 1건 이상이면 주의(bad면 심각)."""
    return [
        LadderStepRead(grade="good", text="0"),
        LadderStepRead(grade="bad" if bad else "warn", text="≥ 1"),
    ]


def _percent(rate: float) -> str:
    if 0 < rate < 0.001:
        return "< 0.1"
    return f"{rate * 100:.1f}"


def _check(
    key: str,
    *,
    stage: int,
    item: str | None,
    grade: str,
    value: str,
    unit: str,
    sub: str,
    ladder: list[LadderStepRead],
    target: str | None,
    problem: str | None,
    count: int,
) -> CheckRead:
    return CheckRead(
        key=key,
        stage=stage,
        item=item,
        grade=grade,  # type: ignore[arg-type]
        value=value,
        unit=unit,
        sub=sub,
        ladder=ladder,
        view_target=target,  # type: ignore[arg-type]
        view_problem=problem,
        view_count=count,
    )


async def get_overview(db: AsyncSession, *, dataset_id: int) -> OverviewRead:
    """데이터셋 진단. 없으면 NotFoundError."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
    analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
    analysis_id = analysis.id if analysis else None
    entry = retrieval_service.entry_stage(settings)

    async def count_queries(condition: Any, *, included: bool = False) -> int:
        base = QUERY_INCLUDED if included else QUERY_ACTIVE
        return int(
            await db.scalar(
                select(func.count())
                .select_from(Query)
                .where(Query.dataset_id == dataset_id, base, condition)
            )
            or 0
        )

    async def count_documents(condition: Any) -> int:
        return int(
            await db.scalar(
                select(func.count())
                .select_from(Document)
                .where(problems.active_document_in(dataset_id), condition)
            )
            or 0
        )

    def query_problem(name: str) -> Any:
        return problems.query_problem(name, settings=settings, analysis_id=analysis_id)

    def document_problem(name: str) -> Any:
        return problems.document_problem(name, settings=settings, analysis_id=analysis_id)

    query_count = await count_queries(True)
    included_count = await count_queries(True, included=True)
    # 질의 검사의 몫은 학습에 쓰는 질의(뺀 것 제외)로 나눈다. 모두 뺐으면 1로.
    rate_base = max(included_count, 1)
    document_count = await count_documents(True)
    judgment_rows = dict(
        (
            await db.execute(
                select(IS_POSITIVE, func.count())
                .select_from(Judgment)
                .join(Query, Query.id == Judgment.query_id)
                .join(Document, Document.id == Judgment.document_id)
                .where(Judgment.dataset_id == dataset_id, QUERY_ACTIVE, DOCUMENT_ACTIVE)
                .group_by(IS_POSITIVE)
            )
        )
        .tuples()
        .all()
    )
    positive_count = int(judgment_rows.get(True, 0))
    negative_count = int(judgment_rows.get(False, 0))
    # 질의를 만들 문서 (입구와 상관없이: 문서만 모양의 문서 · 더한 문서 · 나눈 지문의 답 없는 청크)
    query_targets = await count_documents(problems.generation_target(analysis_id))
    checks: list[CheckRead] = []

    # ----- 1 문서 -----
    if document_count:
        broken = await count_documents(document_problem("broken"))
        checks.append(
            _check(
                "broken",
                stage=1,
                item="clean",
                grade="good" if broken == 0 else "warn",
                value=f"{broken:,}",
                unit="문서",
                sub="깨진 글자 · HTML 찌꺼기",
                ladder=_count_ladder(),
                target="documents",
                problem="broken",
                count=broken,
            )
        )
        checks.append(await _repeat_check(db, dataset_id=dataset_id, settings=settings))
        long_documents = await count_documents(document_problem("long"))
        long_rate = long_documents / document_count
        max_tokens = settings.doc_max_tokens if settings else 512
        checks.append(
            _check(
                "long",
                stage=1,
                item="chunk",
                grade=_grade(long_rate, good_below=LONG_GOOD_BELOW, warn_below=LONG_WARN_BELOW),
                value=_percent(long_rate),
                unit="%",
                sub=f"{max_tokens}토큰 넘음 {long_documents:,}",
                ladder=_rate_ladder(LONG_GOOD_BELOW, LONG_WARN_BELOW),
                target="documents",
                problem="long",
                count=long_documents,
            )
        )
        duplicate_documents = await count_documents(document_problem("duplicate"))
        near_documents = 0
        if analysis_id is not None:
            near_documents = int(
                await db.scalar(
                    select(func.count())
                    .select_from(NearDuplicate)
                    .where(
                        NearDuplicate.analysis_id == analysis_id,
                        NearDuplicate.kind == ItemKind.DOCUMENT.value,
                    )
                )
                or 0
            )
        # 등급은 학습 글이 같은 문서로만 정한다(합치면 풀린다). 근접 중복 문서는 글이 달라 합치지 않고 중복 묶음으로
        # 다루므로(서로 오답이 되지 않음 · 정답 목록에 묶음째) 수만 보인다.
        checks.append(
            _check(
                "duplicate_document",
                stage=1,
                item="dedup_doc",
                grade="good" if duplicate_documents == 0 else "warn",
                value=f"{duplicate_documents:,}",
                unit="문서",
                sub=f"같은 본문 {duplicate_documents:,} · 근접 {near_documents:,} 묶음"
                if analysis_id
                else f"같은 본문 {duplicate_documents:,}",
                ladder=_count_ladder(),
                target="documents",
                problem="duplicate" if duplicate_documents or not near_documents else "near",
                count=duplicate_documents,
            )
        )
        if entry == 1 or query_targets:
            pick = await count_documents(document_problem("pick"))
            checks.append(
                _check(
                    "pick",
                    stage=1,
                    item="pick",
                    grade="good" if pick == 0 else "warn",
                    value=f"{pick:,}",
                    unit="문서",
                    sub="목차 · 표만 · 서명란",
                    ladder=_count_ladder(),
                    target="documents",
                    problem="pick",
                    count=pick,
                )
            )
        topics = (analysis.checks.get("topics") if analysis else None) or None
        if topics:
            largest = float(topics.get("largest_share", 0.0))
            checks.append(
                _check(
                    "topic_skew",
                    stage=1,
                    item="topics",
                    grade="warn" if largest >= TOPIC_SKEW_WARN_FROM else "good",
                    value=f"{largest * 100:.0f}",
                    unit="%",
                    sub=f"가장 큰 무리 · {topics.get('clusters', 0)}무리 · 질의 없는 무리 {len(topics.get('empty', []))}",
                    ladder=[
                        LadderStepRead(grade="good", text=f"< {TOPIC_SKEW_WARN_FROM * 100:g}%"),
                        LadderStepRead(grade="warn", text=f"≥ {TOPIC_SKEW_WARN_FROM * 100:g}%"),
                    ],
                    target=None,
                    problem=None,
                    count=0,
                )
            )

    # ----- 2 질의 -----
    if query_count == 0:
        if entry == 1 or document_count:
            checks.append(
                _check(
                    "no_queries",
                    stage=2,
                    item=None,
                    grade="bad",
                    value="0",
                    unit="질의",
                    sub="질의 만들기로 채움",
                    ladder=_count_ladder(bad=True),
                    target=None,
                    problem=None,
                    count=0,
                )
            )
    else:
        short_long = await count_queries(query_problem("short_long"), included=True)
        checks.append(
            _check(
                "short_long",
                stage=2,
                item="clean_q",
                grade="good" if short_long == 0 else "warn",
                value=f"{short_long:,}",
                unit="건",
                sub=f"5자 미만 · {settings.query_max_tokens if settings else 64}토큰 넘음",
                ladder=_count_ladder(),
                target="queries",
                problem="short_long",
                count=short_long,
            )
        )
        conflict = await count_queries(query_problem("conflict"), included=True)
        checks.append(
            _check(
                "conflict",
                stage=2,
                item="dedup_q",
                grade="good" if conflict == 0 else "warn",
                value=f"{conflict:,}",
                unit="건",
                sub="같은 쌍이 정답이자 오답",
                ladder=_count_ladder(),
                target="queries",
                problem="conflict",
                count=conflict,
            )
        )
        duplicate = await count_queries(query_problem("duplicate"), included=True)
        duplicate_rate = duplicate / rate_base
        checks.append(
            _check(
                "duplicate_query",
                stage=2,
                item="dedup_q",
                grade=_grade(duplicate_rate, good_below=DUPLICATE_GOOD_BELOW),
                value=_percent(duplicate_rate),
                unit="%",
                sub=f"같은 질의 여분 {duplicate:,}",
                ladder=_rate_ladder(DUPLICATE_GOOD_BELOW, None),
                target="queries",
                problem="duplicate",
                count=duplicate,
            )
        )
        no_positive = await count_queries(query_problem("no_positive"), included=True)
        checks.append(
            _check(
                "no_positive",
                stage=2,
                item="answer",
                grade="good" if no_positive == 0 else "bad",
                value=f"{no_positive:,}",
                unit="건",
                sub=f"{_percent(no_positive / rate_base)}%",
                ladder=_count_ladder(bad=True),
                target="queries",
                problem="no_positive",
                count=no_positive,
            )
        )
        if analysis_id is not None:
            suspect = await count_queries(query_problem("suspect"), included=True)
            suspect_rate = suspect / rate_base
            checks.append(
                _check(
                    "suspect",
                    stage=2,
                    item="answer",
                    grade=_grade(
                        suspect_rate, good_below=SUSPECT_GOOD_BELOW, warn_below=SUSPECT_WARN_BELOW
                    ),
                    value=f"{suspect:,}",
                    unit="건",
                    sub=f"{_percent(suspect_rate)}% · 멀고 Jev 아니오",
                    ladder=_rate_ladder(SUSPECT_GOOD_BELOW, SUSPECT_WARN_BELOW),
                    target="suggestions",
                    problem="suspect_positive",
                    count=suspect,
                )
            )
        context = await count_queries(query_problem("context"), included=True)
        context_rate = context / rate_base
        checks.append(
            _check(
                "context",
                stage=2,
                item="answer",
                grade=_grade(
                    context_rate, good_below=CONTEXT_GOOD_BELOW, warn_below=CONTEXT_WARN_BELOW
                ),
                value=f"{context:,}",
                unit="건",
                sub=f'"이 글" · "그는" {_percent(context_rate)}%',
                ladder=_rate_ladder(CONTEXT_GOOD_BELOW, CONTEXT_WARN_BELOW),
                target="queries",
                problem="context",
                count=context,
            )
        )
        easy_pair = await count_queries(query_problem("easy_pair"), included=True)
        easy_rate = easy_pair / rate_base
        # 쉬운 쌍은 버리지 않고 상한(설정)까지 둔다. 넘을 때만 주의이고 내보내기를 막지 않는다.
        easy_cap = settings.easy_pair_cap if settings else DEFAULT_EASY_PAIR_CAP
        checks.append(
            _check(
                "easy_pair",
                stage=2,
                item="answer",
                grade="warn" if easy_rate > easy_cap else "good",
                value=_percent(easy_rate),
                unit="%",
                sub=f"글자 겹침 ≥ 90% · {easy_pair:,}쌍 · 상한 {easy_cap * 100:g}%",
                ladder=[
                    LadderStepRead(grade="good", text=f"≤ {easy_cap * 100:g}%"),
                    LadderStepRead(grade="warn", text=f"> {easy_cap * 100:g}%"),
                ],
                target="queries",
                problem="easy_pair",
                count=easy_pair,
            )
        )
        if analysis_id is not None:
            missing = await count_queries(query_problem("missing"), included=True)
            checks.append(
                _check(
                    "missing",
                    stage=2,
                    item="missing",
                    grade="good" if missing == 0 else "warn",
                    value=f"{missing:,}",
                    unit="질의",
                    sub="판정 없음 · Jev 예 · 오답 찾기 전에",
                    ladder=_count_ladder(),
                    target="suggestions",
                    problem="missing_positive",
                    count=missing,
                )
            )
    # ----- 3 하드 네거티브 -----
    if query_count:
        if analysis_id is not None:
            false_negatives = await count_queries(query_problem("false_negative"), included=True)
            confirmed_false = int(
                await db.scalar(
                    select(func.count())
                    .select_from(Suggestion)
                    .where(
                        Suggestion.analysis_id == analysis_id,
                        Suggestion.kind == SuggestionKind.FALSE_NEGATIVE.value,
                        Suggestion.decision.is_(None),
                        Suggestion.is_confirmed.is_(True),
                    )
                )
                or 0
            )
            grade = "bad" if confirmed_false else "warn" if false_negatives else "good"
            checks.append(
                _check(
                    "false_negative",
                    stage=3,
                    item="false_neg",
                    grade=grade,
                    value=f"{false_negatives:,}",
                    unit="질의",
                    sub=f"정답만큼 가까운 오답 · Jev 확인 {confirmed_false:,}",
                    ladder=_count_ladder(bad=True),
                    target="suggestions",
                    problem="false_negative",
                    count=false_negatives,
                )
            )
        if negative_count:
            same_negative = await count_queries(query_problem("same_negative"), included=True)
            checks.append(
                _check(
                    "same_negative",
                    stage=3,
                    item="same_neg",
                    grade="good" if same_negative == 0 else "warn",
                    value=f"{same_negative:,}",
                    unit="질의",
                    sub="오답이 정답과 같은 글",
                    ladder=_count_ladder(),
                    target="queries",
                    problem="same_negative",
                    count=same_negative,
                )
            )
            ranked_count, hard = 0, 0
            if analysis_id is not None and settings is not None:
                # 분석 뒤에 붙은 오답(찾기 등)은 순위가 없어 쉬운지 모른다. 순위가 있는 오답만 센다.
                ranked_negatives = (
                    select(Ranking.rank)
                    .select_from(Judgment)
                    .join(
                        Ranking,
                        (Ranking.analysis_id == analysis_id)
                        & (Ranking.query_id == Judgment.query_id)
                        & (Ranking.document_id == Judgment.document_id),
                    )
                    .where(Judgment.dataset_id == dataset_id, IS_NEGATIVE)
                    .subquery()
                )
                ranked_count, hard = (
                    await db.execute(
                        select(
                            func.count(),
                            func.count().filter(ranked_negatives.c.rank <= settings.mine_rank_to),
                        ).select_from(ranked_negatives)
                    )
                ).one()
            if analysis_id is not None and settings is not None and ranked_count:
                hard_rate = hard / ranked_count
                easy_queries = await count_queries(query_problem("easy_negative"), included=True)
                checks.append(
                    _check(
                        "easy_negative",
                        stage=3,
                        item="easy_neg",
                        grade="good" if hard_rate >= HARD_NEGATIVE_GOOD_FROM else "warn",
                        value=f"{hard_rate * 100:.0f}",
                        unit="%",
                        sub=f"하드 오답 비율 · {settings.mine_rank_to}위 밖 오답이 있는 질의 {easy_queries:,}",
                        ladder=[
                            LadderStepRead(
                                grade="good", text=f"≥ {HARD_NEGATIVE_GOOD_FROM * 100:g}%"
                            ),
                            LadderStepRead(
                                grade="warn", text=f"< {HARD_NEGATIVE_GOOD_FROM * 100:g}%"
                            ),
                        ],
                        target="queries",
                        problem="easy_negative",
                        count=easy_queries,
                    )
                )
        lacking = await count_queries(query_problem("no_negative"), included=True)
        wanted = settings.negatives if settings else 7
        checks.append(
            _check(
                "no_negative",
                stage=3,
                item="count",
                grade="good" if lacking == 0 else "warn",
                value=f"{lacking:,}",
                unit="질의",
                sub=f"오답 {wanted}개 못 채움 · group {wanted + 1}",
                ladder=_count_ladder(),
                target="queries",
                problem="no_negative",
                count=lacking,
            )
        )

    flow = _flow(
        checks,
        entry=entry,
        document_count=document_count,
        query_count=query_count,
        negative_count=negative_count,
        has_analysis=analysis_id is not None,
        settings=settings,
        orphan_documents=await count_documents(problems.document_use("unused"))
        if query_count
        else 0,
        query_targets=query_targets,
        unmined_documents=await retrieval_service.count_unmined_documents(
            db, dataset_id=dataset_id
        ),
        teacher=await _teacher_count(db, dataset_id=dataset_id),
        kpi_text=_kpi_text(analysis),
    )
    return OverviewRead(
        dataset_id=dataset_id,
        query_count=query_count,
        document_count=document_count,
        positive_count=positive_count,
        negative_count=negative_count,
        flow=flow,
        checks=checks,
        kpi=_kpi(analysis, settings),
        first_rank=await _first_rank_bins(
            db, dataset_id=dataset_id, analysis_id=analysis_id, total=included_count
        ),
        negatives_per_query=await _negative_bins(
            db, dataset_id=dataset_id, wanted=settings.negatives if settings else 7
        ),
        query_types=await _query_type_bins(db, dataset_id=dataset_id),
        document_lengths=await _length_bins(
            db, dataset_id=dataset_id, max_tokens=settings.doc_max_tokens if settings else 512
        ),
        judgment_sources=await _source_bins(db, dataset_id=dataset_id),
        computed_at=datetime.now(UTC),
    )


# ---------- 흐름 ----------


def _flow(
    checks: list[CheckRead],
    *,
    entry: int | None,
    document_count: int,
    query_count: int,
    negative_count: int,
    has_analysis: bool,
    settings: DatasetSettings | None,
    orphan_documents: int,
    query_targets: int,
    unmined_documents: int,
    teacher: int,
    kpi_text: str | None,
) -> FlowRead:
    """검사로 흐름 줄(단계 셋 · 넘어가기 셋 · 입구 · 지금)을 만든다."""
    by_item: dict[str, list[CheckRead]] = {}
    for check in checks:
        if check.item:
            by_item.setdefault(check.item, []).append(check)

    def item_state(stage: int, key: str) -> FlowItemRead:
        found = by_item.get(key)
        if found:
            worst = min(found, key=lambda check: {"bad": 0, "warn": 1, "good": 2}[check.grade])
            state = {"bad": "bad", "warn": "warn", "good": "done"}[worst.grade]
            text = (
                worst.sub.split(" · ")[0]
                if worst.grade == "good"
                else f"{_check_name(worst.key)} {worst.value}{'%' if worst.unit == '%' else ''}"
            )
            return FlowItemRead(key=key, state=state, text=text)  # type: ignore[arg-type]
        stage_empty = (stage == 1 and document_count == 0) or (stage >= 2 and query_count == 0)
        if stage_empty:
            return FlowItemRead(
                key=key,
                state="wait",
                text="질의 뒤" if stage == 2 else "오답 뒤" if stage == 3 else "문서 뒤",
            )
        if key == "pick":
            # 질의를 만들 문서가 없으면 고를 것도 없다(고르기 검사를 재지 않았다).
            return FlowItemRead(key=key, state="skip", text="필요 없음")
        if key == "topics":
            return FlowItemRead(
                key=key,
                state="wait" if not has_analysis else "done",
                text=ANALYSIS_PENDING_TEXT if not has_analysis else "고름",
            )
        if key == "missing":
            return FlowItemRead(key=key, state="wait", text=ANALYSIS_PENDING_TEXT)
        if key in ("false_neg", "easy_neg"):
            if negative_count == 0:
                return FlowItemRead(key=key, state="wait", text="오답 뒤")
            return FlowItemRead(key=key, state="wait", text=ANALYSIS_PENDING_TEXT)
        if key == "same_neg":
            return FlowItemRead(key=key, state="wait", text="오답 뒤")
        if key == "length":
            max_query = settings.query_max_tokens if settings else 64
            max_doc = settings.doc_max_tokens if settings else 512
            long_open = any(
                check.key in ("long", "short_long") and check.grade != "good" for check in checks
            )
            return FlowItemRead(
                key=key,
                state="warn" if long_open else "done",
                text=f"질의 {max_query} · 문서 {max_doc}",
            )
        if key == "score":
            return FlowItemRead(
                key=key,
                state="done" if kpi_text else "wait",
                text=kpi_text or ANALYSIS_PENDING_TEXT,
            )
        if key == "teacher":
            return FlowItemRead(
                key=key,
                state="done" if teacher else "skip",
                text=f"Jev 점수 {teacher:,}" if teacher else "선택",
            )
        if key == "dist":
            return FlowItemRead(key=key, state="done", text="유형 · 주제")
        return FlowItemRead(key=key, state="skip", text="—")

    stages = []
    for no, name, items in STAGES:
        item_reads = [item_state(no, key) for key in items]
        counted = [item.state for item in item_reads if item.state not in ("skip",)]
        if not counted or all(state == "wait" for state in counted):
            state = "wait" if counted else "skip"
        else:
            state = max((s for s in counted if s != "wait"), key=lambda s: STATE_WEIGHT[s])
        open_count = sum(1 for check in checks if check.stage == no and check.grade != "good")
        count = document_count if no == 1 else query_count if no == 2 else negative_count
        stages.append(
            FlowStageRead(
                no=no, name=name, count=count, state=state, open_count=open_count, items=item_reads
            )
        )  # type: ignore[arg-type]

    lacking = next((check for check in checks if check.key == "no_negative"), None)
    missing = next(
        (check for check in checks if check.key == "missing" and check.grade != "good"), None
    )
    bad_count = sum(1 for check in checks if check.grade == "bad")
    if query_count == 0 and document_count:
        generate = FlowMoveRead(key="generate", state="bad", text="질의 0 · 허락")
    elif query_targets:
        # 질의가 있어도 질의가 필요한 문서(더한 문서 · 답 없는 청크)가 있으면 흐름이 여기로 돌아온다.
        generate = FlowMoveRead(
            key="generate", state="todo", text=f"질의 없는 문서 {query_targets:,} · 허락"
        )
    elif orphan_documents:
        generate = FlowMoveRead(
            key="generate", state="skip", text=f"선택 · 고아 문서 {orphan_documents:,}"
        )
    else:
        generate = FlowMoveRead(key="generate", state="skip", text="필요 없음")
    if query_count == 0:
        mine = FlowMoveRead(key="mine", state="wait", text="질의 뒤")
    elif unmined_documents:
        # 모든 질의의 오답을 찾은 뒤 문서 풀이 늘었다: 찾기로 붙인 오답을 떼고 모든 질의를 다시 찾는다.
        mine = FlowMoveRead(
            key="mine",
            state="warn" if missing else "todo",
            text=f"새 문서 {unmined_documents:,} · 다시 찾기"
            + (" · 빠진 정답 먼저" if missing else ""),
        )
    elif lacking is not None and lacking.grade != "good":
        mine = FlowMoveRead(
            key="mine",
            state="warn" if missing else "todo",
            text=f"질의 {lacking.value}" + (" · 빠진 정답 먼저" if missing else ""),
        )
    else:
        mine = FlowMoveRead(
            key="mine", state="done", text=f"질의마다 {settings.negatives if settings else 7}"
        )
    if query_count == 0:
        export = FlowMoveRead(key="export", state="lock", text="질의 없음")
    elif bad_count:
        export = FlowMoveRead(key="export", state="lock", text=f"심각 {bad_count}")
    else:
        export = FlowMoveRead(key="export", state="todo", text="내보낼 수 있음")

    states: dict[int | str, str] = {stage.no: stage.state for stage in stages}
    states["generate"] = generate.state
    states["mine"] = mine.state
    states["export"] = export.state
    current = next((key for key in FLOW_ORDER if states[key] in OPEN_STATES), None)
    return FlowRead(stages=stages, moves=[generate, mine, export], entry=entry, current=current)


# 흐름 항목 글에 쓰는 검사 이름
CHECK_NAMES = {
    "broken": "깨진 글자",
    "repeat": "반복 구간",
    "long": "긴 문서",
    "duplicate_document": "중복 문서",
    "pick": "질의 안 만들 청크",
    "topic_skew": "주제 쏠림",
    "no_queries": "질의 없음",
    "short_long": "짧은 · 긴 질의",
    "conflict": "판정 충돌",
    "duplicate_query": "질의 중복",
    "no_positive": "정답 없음",
    "suspect": "정답 의심",
    "context": "문맥 의존",
    "easy_pair": "쉬운 쌍",
    "missing": "빠진 정답",
    "false_negative": "거짓 오답",
    "same_negative": "정답과 같은 오답",
    "easy_negative": "하드 오답",
    "no_negative": "오답 부족",
}


def _check_name(key: str) -> str:
    return CHECK_NAMES.get(key, key)


# ---------- 기준 검색 점수 · 분포 ----------


def _kpi(analysis: Analysis | None, settings: DatasetSettings | None) -> KpiRead | None:
    kpi = (analysis.checks.get("kpi") if analysis else None) or None
    if not kpi:
        return None
    baseline = (settings.baseline if settings else {}) or {}
    return KpiRead(
        recall_at_10=float(kpi["recall_at_10"]),
        mrr_at_10=float(kpi["mrr_at_10"]),
        evaluated=int(kpi["evaluated"]),
        baseline_recall_at_10=baseline.get("recall_at_10"),
        baseline_mrr_at_10=baseline.get("mrr_at_10"),
    )


def _kpi_text(analysis: Analysis | None) -> str | None:
    kpi = (analysis.checks.get("kpi") if analysis else None) or None
    if not kpi:
        return None
    return f"R@10 {float(kpi['recall_at_10']) * 100:.1f}%"


async def _repeat_check(
    db: AsyncSession, *, dataset_id: int, settings: DatasetSettings | None
) -> CheckRead:
    """반복 구간 검사: 고를 것(떼기 · 남김을 아직 안 고른 틀)이 있으면 주의. 살피기 전이면 주의."""
    decisions = {
        decision or "undecided": count
        for decision, count in await db.execute(
            select(Repeat.decision, func.count())
            .where(Repeat.dataset_id == dataset_id)
            .group_by(Repeat.decision)
        )
    }
    undecided = int(decisions.get("undecided", 0))
    undecided_documents = int(
        await db.scalar(
            select(func.count(func.distinct(DocumentRepeat.document_id)))
            .join(Repeat, Repeat.id == DocumentRepeat.repeat_id)
            .join(Document, Document.id == DocumentRepeat.document_id)
            .where(Repeat.dataset_id == dataset_id, Repeat.decision.is_(None), DOCUMENT_ACTIVE)
        )
        or 0
    )
    is_scanned = settings is not None and settings.scanned_at is not None
    sub = (
        f"떼기 {int(decisions.get('remove', 0))} · 남김 {int(decisions.get('keep', 0))}"
        f" · 고를 것 {undecided} · 문서 {undecided_documents:,}"
        if is_scanned
        else "살피기 전"
    )
    return _check(
        "repeat",
        stage=1,
        item="boiler",
        grade="good" if is_scanned and undecided == 0 else "warn",
        value=f"{undecided:,}",
        unit="틀",
        sub=sub,
        ladder=_count_ladder(),
        target="documents",
        problem="repeat",
        count=undecided_documents,
    )


async def _teacher_count(db: AsyncSession, *, dataset_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Judgment)
            .where(Judgment.dataset_id == dataset_id, Judgment.teacher_score.is_not(None))
        )
        or 0
    )


async def _first_rank_bins(
    db: AsyncSession, *, dataset_id: int, analysis_id: int | None, total: int
) -> list[BinRead]:
    """학습에 쓰는 질의의 첫 정답 순위 분포. 분석이 없으면 빈 목록.

    '정답 없음'은 지금 정답 판정이 없는 질의만이다(진단의 '정답 없음' 검사와 같은 조건).
    정답은 있는데 분석에 순위가 없는 질의(분석 뒤에 만든 질의 · 분석 뒤에 붙은 정답)는 '분석에 없음'으로 따로 센다.
    둘을 합쳐 세면 분석 뒤에 만든 질의가 모두 정답 없음으로 보인다.
    """
    if analysis_id is None:
        return []
    first = (
        select(Ranking.query_id, func.min(Ranking.rank).label("first"))
        .join(
            Judgment,
            (Judgment.query_id == Ranking.query_id) & (Judgment.document_id == Ranking.document_id),
        )
        .join(Query, Query.id == Ranking.query_id)
        .join(Document, Document.id == Ranking.document_id)
        .where(
            Ranking.analysis_id == analysis_id,
            IS_POSITIVE,
            Query.dataset_id == dataset_id,
            QUERY_INCLUDED,
            DOCUMENT_ACTIVE,
        )
        .group_by(Ranking.query_id)
        .subquery()
    )
    ranks = [int(rank) for rank in (await db.scalars(select(first.c.first))).all()]
    no_positive = int(
        await db.scalar(
            select(func.count())
            .select_from(Query)
            .where(Query.dataset_id == dataset_id, QUERY_INCLUDED, ~problems.positive_exists())
        )
        or 0
    )
    bins = [
        BinRead(
            label=label,
            count=sum(1 for rank in ranks if low <= rank <= high),
            tone="warn" if low > 10 else "normal",
        )
        for label, low, high in FIRST_RANK_BINS
    ]
    bins.append(
        BinRead(label="100위 밖", count=sum(1 for rank in ranks if rank > 100), tone="warn")
    )
    bins.append(BinRead(label="정답 없음", count=no_positive, tone="bad"))
    not_ranked = max(total - len(ranks) - no_positive, 0)
    if not_ranked:
        bins.append(BinRead(label="분석에 없음", count=not_ranked, tone="muted"))
    return bins


async def _negative_bins(db: AsyncSession, *, dataset_id: int, wanted: int) -> list[BinRead]:
    """학습에 쓰는 질의마다 오답 수 분포: 0 · 1~(n-1) · n 이상."""
    per_query = (
        select(Query.id, func.count(Judgment.document_id).filter(IS_NEGATIVE).label("negatives"))
        .outerjoin(Judgment, Judgment.query_id == Query.id)
        .where(Query.dataset_id == dataset_id, QUERY_INCLUDED)
        .group_by(Query.id)
        .subquery()
    )
    counts = Counter(
        int(value) for value in (await db.scalars(select(per_query.c.negatives))).all()
    )
    none = counts.get(0, 0)
    few = sum(count for value, count in counts.items() if 0 < value < wanted)
    enough = sum(count for value, count in counts.items() if value >= wanted)
    if not (none or few or enough):
        return []
    return [
        BinRead(label="0개", count=none, tone="warn"),
        BinRead(label=f"1~{wanted - 1}개", count=few, tone="warn"),
        BinRead(label=f"{wanted}개 이상", count=enough, tone="normal"),
    ]


async def _query_type_bins(db: AsyncSession, *, dataset_id: int) -> list[BinRead]:
    texts = (
        await db.scalars(
            select(Query.text)
            .where(Query.dataset_id == dataset_id, QUERY_ACTIVE)
            .limit(QUERY_TYPE_SAMPLE)
        )
    ).all()
    if not texts:
        return []
    counts = Counter(query_type(text) for text in texts)
    return [
        BinRead(label=name, count=counts.get(key, 0), tone="normal")
        for key, name in QUERY_TYPE_NAMES.items()
    ]


async def _length_bins(db: AsyncSession, *, dataset_id: int, max_tokens: int) -> list[BinRead]:
    """학습 글 길이(토큰 어림) 분포. 최대 토큰을 넘는 칸은 주의. 수는 SQL로 센다(문서를 읽어 오지 않는다)."""
    edges = [0, max_tokens // 4, max_tokens // 2, (max_tokens * 3) // 4, max_tokens, max_tokens * 2]
    ranges = list(pairwise(edges))
    counts = (
        await db.execute(
            select(
                func.count(),
                *[
                    # 첫 칸은 0토큰(빈 문서)도 넣는다.
                    func.count().filter(
                        Document.token_count >= low if low == 0 else Document.token_count > low,
                        Document.token_count <= high,
                    )
                    for low, high in ranges
                ],
                func.count().filter(Document.token_count > edges[-1]),
            ).where(problems.active_document_in(dataset_id))
        )
    ).one()
    if not counts[0]:
        return []
    bins = [
        BinRead(
            label=f"{low}~{high}",
            count=int(counts[index + 1]),
            tone="warn" if low >= max_tokens else "normal",
        )
        for index, (low, high) in enumerate(ranges)
    ]
    bins.append(BinRead(label=f"{edges[-1]}+", count=int(counts[-1]), tone="warn"))
    return bins


async def _source_bins(db: AsyncSession, *, dataset_id: int) -> list[BinRead]:
    rows = dict(
        (
            await db.execute(
                select(Judgment.source, func.count())
                .where(Judgment.dataset_id == dataset_id)
                .group_by(Judgment.source)
            )
        )
        .tuples()
        .all()
    )
    if not rows:
        return []
    return [
        BinRead(label=name, count=int(rows.get(key, 0)), tone="normal")
        for key, name in SOURCE_NAMES.items()
    ]
