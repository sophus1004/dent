"""데이터셋 진단: 검사마다 문제가 남은 대화(쌍) 수와 등급, 흐름에서 지금 단계.

모든 수는 SQL 집계로 DB가 센다. 수백만 건이라도 대화를 파이썬으로 불러오지 않는다.
- 글의 모양 검사: 가져올 때 · 고칠 때 한 번 잰 open_checks(남은 검사)를 센다. 같은 지시(중복)만 해시로 센다.
- 응답 없음: 주 응답이 없는 대화(지시만). 응답이 없는 대화가 있을 때만 줄로 보인다(할 일: 응답 만들기).
- 도구 호출 형식: 도구 대화가 있을 때만 줄로 보인다.
- 오염: 평가셋을 표시했을 때만 잰다(없으면 '평가셋 없음', 평가셋이 바뀌어 다시 대조할 때까지 '대조 전'으로 기다림).
- 뜻 검사(근접 중복 · 과제 쏠림 · 지시 불일치 · 안전)는 다 만든 뜻 분석으로 센다. 분석이 없거나 Jev가 묻지 못했으면 기다림.
  안전은 Jev가 판정한 표본(앞 몇천 건)으로 세고 분모도 그 수다.
- 3 선호(쌍 단위)는 만들 데이터가 선호이거나 쌍이 있을 때만: 같은 쌍 · 동점은 쌍 표시로, 판정 반대 · 작은 마진은
  Jev가 두 응답을 따로 본 판정이 있는 쌍으로(없으면 기다림), 길이 편향은 고른 응답이 더 긴 쌍의 몫으로 센다.
기다림은 종합 상태 · 지금 단계에 세지 않는다. 조건은 problems.py 한 곳에 있다.
검사마다 '그대로 두기'로 둔 수(kept)도 싣는다. 사람이 문제가 아니라고 둔 것이 많으면 규칙의 오탐을 의심한다.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from dent.modules.generation import contamination, judging, problems, rules
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import Goal, Pair, Prompt, Response, Role
from dent.modules.generation.rules import CHECKS, CheckKey
from dent.modules.generation.schemas import CheckRead, OverviewRead, WaitingRead
from dent.modules.generation.service import HAS_MAIN_ANSWER, IS_ACTIVE, IS_TRASHED, has_open

# 해시 · 따로 된 조건으로 세는 검사 (open_checks 표시가 아니라)
NOT_MARK_CHECKS = {CheckKey.DUPLICATE, CheckKey.NO_ANSWER}

# 도구 대화가 있을 때만 보이는 검사
TOOL_CHECKS = {CheckKey.TOOL_CALL}

# 흐름의 지금 단계가 '내보내기'일 때의 번호 (1 지시 · 2 응답 · 3 선호 다음)
EXPORT_STAGE = 4

# 맥락 메시지 가운데 응답만 고르는 JSON 경로
ASSISTANT_PATH = literal_column(f"'$[*] ? (@.role == \"{Role.ASSISTANT.value}\")'::jsonpath")

# 기다림의 까닭
NOT_ANALYZED_REASON = "뜻 분석 전"
JEV_REASONS = {judging.JEV_NOT_CONNECTED: "Jev 미연결", judging.JEV_FAILED: "Jev 실패"}
NO_EVAL_REASON = "평가셋 없음"
STALE_EVAL_REASON = "대조 전"
NOT_JUDGED_REASON = "판정 전"

# Jev가 판정하는 쌍 검사
JUDGED_PAIR_CHECKS = (CheckKey.JUDGE_REVERSED, CheckKey.SMALL_MARGIN)


async def get_overview(db: AsyncSession, *, dataset_id: int) -> OverviewRead:
    """데이터셋 진단을 계산한다. 데이터셋이 없으면 NotFoundError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    settings = await generation_service.find_settings(db, dataset_id=dataset_id)
    mark_keys = [
        key
        for key in rules.RULE_CHECKS
        if CHECKS[key].unit == "conversation" and key not in NOT_MARK_CHECKS
    ]
    row = (
        await db.execute(
            select(
                func.count().filter(IS_ACTIVE),
                func.count().filter(IS_TRASHED),
                func.count().filter(IS_ACTIVE, ~HAS_MAIN_ANSWER),
                func.count().filter(IS_ACTIVE, Prompt.tools.is_not(None)),
                *[func.count().filter(IS_ACTIVE, has_open(key)) for key in mark_keys],
            ).where(Prompt.dataset_id == dataset_id)
        )
    ).one()
    active, trashed, unanswered, with_tools, *mark_counts = row
    counts: dict[CheckKey, int] = dict(zip(mark_keys, mark_counts, strict=True))
    if not with_tools and not counts.get(CheckKey.TOOL_CALL):
        counts.pop(CheckKey.TOOL_CALL, None)
    counts[CheckKey.DUPLICATE] = await _count(db, problems.duplicate_extras(dataset_id))
    if unanswered:
        counts[CheckKey.NO_ANSWER] = await _count(db, problems.no_answer_ids(dataset_id))
    totals = {key: int(active) for key in CHECKS}
    sampled: set[CheckKey] = set()
    waiting: list[WaitingRead] = []

    if not settings.is_eval:
        await _contamination(db, dataset_id, counts=counts, waiting=waiting)

    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    semantic_keys = [key for key in rules.SEMANTIC_CHECKS if CHECKS[key].unit == "conversation"]
    if analysis is None:
        waiting += [_waiting(key, NOT_ANALYZED_REASON) for key in semantic_keys]
    else:
        counts[CheckKey.NEAR_DUPLICATE] = await _count(
            db, problems.near_extras(dataset_id, analysis.id)
        )
        # 과제 쏠림은 분석할 때의 무리로 잰다(분모도 그때의 대화 수).
        tasks: dict[str, Any] = analysis.checks.get("tasks") or {}
        counts[CheckKey.TASK_SKEW] = int(tasks.get("largest_count", 0))
        totals[CheckKey.TASK_SKEW] = analysis.conversation_count
        jev_status = (analysis.checks.get("jev") or {}).get("status")
        if jev_status == judging.JEV_JUDGED:
            counts[CheckKey.MISMATCH] = await _count(
                db, problems.mismatch_ids(dataset_id, analysis.id)
            )
            safety: dict[str, Any] = analysis.checks.get("safety") or {}
            judged = int(safety.get("judged", 0))
            if judged:
                counts[CheckKey.UNSAFE] = await _count(
                    db, problems.unsafe_ids(dataset_id, analysis.id)
                )
                totals[CheckKey.UNSAFE] = judged
                if judged < analysis.conversation_count:
                    sampled.add(CheckKey.UNSAFE)
            else:
                waiting.append(_waiting(CheckKey.UNSAFE, NOT_JUDGED_REASON))
        else:
            reason = JEV_REASONS.get(jev_status, NOT_ANALYZED_REASON)
            waiting += [_waiting(CheckKey.MISMATCH, reason), _waiting(CheckKey.UNSAFE, reason)]

    pair_total = await _count(db, problems.active_pairs(dataset_id))
    shows_pairs = settings.goal == Goal.PREFERENCE.value or pair_total > 0
    unpaired = 0
    if shows_pairs:
        unpaired = await _count(db, problems.pair_targets(dataset_id))
        if pair_total:
            await _pair_checks(
                db,
                dataset_id,
                pair_total=pair_total,
                counts=counts,
                totals=totals,
                waiting=waiting,
                jev_status=(analysis.checks.get("jev") or {}).get("status") if analysis else None,
            )

    kept = await problems.kept_counts(db, dataset_id=dataset_id)
    checks = [
        _check_read(
            key,
            count=int(counts[key]),
            total=totals[key],
            sampled=key in sampled,
            kept=kept.get(key.value, 0),
        )
        for key in CHECKS
        if key in counts
    ]
    length_by = "tokens" if settings.measured_model else "chars"
    return OverviewRead(
        dataset_id=dataset_id,
        conversations=int(active),
        trashed=int(trashed),
        goal=Goal(settings.goal),
        is_eval=settings.is_eval,
        unanswered=int(unanswered),
        pairs=pair_total,
        unpaired=unpaired,
        length_by=length_by,
        turns=await _turn_counts(db, dataset_id=dataset_id),
        checks=checks,
        waiting=waiting,
        now_stage=_now_stage(
            checks,
            has_pairs=shows_pairs,
            wants_pairs=settings.goal == Goal.PREFERENCE.value and unpaired > 0,
        ),
        computed_at=datetime.now(UTC),
    )


async def blocking_checks(db: AsyncSession, *, dataset_id: int) -> list[CheckRead]:
    """심각인 검사들 (내보내기를 막는다)."""
    overview = await get_overview(db, dataset_id=dataset_id)
    return [check for check in overview.checks if check.grade == "bad"]


async def _contamination(
    db: AsyncSession, dataset_id: int, *, counts: dict[CheckKey, int], waiting: list[WaitingRead]
) -> None:
    """오염: 평가셋이 없으면 기다림(평가셋 없음), 평가셋이 바뀐 뒤 아직 대조하지 않았으면 기다림(대조 전)."""
    state = await contamination.state(db, dataset_id=dataset_id)
    if state == contamination.STATE_NO_EVAL:
        waiting.append(_waiting(CheckKey.CONTAMINATION, NO_EVAL_REASON))
    elif state == contamination.STATE_STALE:
        waiting.append(_waiting(CheckKey.CONTAMINATION, STALE_EVAL_REASON))
    else:
        counts[CheckKey.CONTAMINATION] = await _count(db, problems.contamination_ids(dataset_id))


async def _pair_checks(
    db: AsyncSession,
    dataset_id: int,
    *,
    pair_total: int,
    counts: dict[CheckKey, int],
    totals: dict[CheckKey, int],
    waiting: list[WaitingRead],
    jev_status: str | None,
) -> None:
    """3 선호의 쌍 검사들을 센다."""
    for key in rules.PAIR_CHECKS:
        totals[key] = pair_total
    counts[CheckKey.SAME_PAIR] = await _count(
        db, problems.pair_open_ids(dataset_id, CheckKey.SAME_PAIR)
    )
    counts[CheckKey.LENGTH_BIAS] = await _count(db, _chosen_longer(dataset_id))
    judged = await _count(db, problems.judged_pairs(dataset_id))
    if judged:
        for key in JUDGED_PAIR_CHECKS:
            counts[key] = await _count(db, problems.pair_open_ids(dataset_id, key))
            totals[key] = judged
        return
    reason = (
        NOT_ANALYZED_REASON
        if jev_status is None
        else JEV_REASONS.get(jev_status, NOT_JUDGED_REASON)
    )
    waiting += [_waiting(key, reason) for key in JUDGED_PAIR_CHECKS]


def _chosen_longer(dataset_id: int) -> Any:
    """고른 응답이 버린 응답보다 긴 휴지통 밖 쌍 (길이 편향의 분자)."""
    chosen = aliased(Response)
    rejected = aliased(Response)
    return (
        problems.active_pairs(dataset_id)
        .join(chosen, chosen.id == Pair.chosen_id)
        .join(rejected, rejected.id == Pair.rejected_id)
        .where(func.length(chosen.content) > func.length(rejected.content))
    )


async def _count(db: AsyncSession, query: Any) -> int:
    """번호 쿼리의 줄 수."""
    return int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)


def _waiting(key: CheckKey, reason: str) -> WaitingRead:
    return WaitingRead(key=key, stage=CHECKS[key].stage, reason=reason)


def _check_read(
    key: CheckKey, *, count: int, total: int, sampled: bool, kept: int = 0
) -> CheckRead:
    spec = CHECKS[key]
    return CheckRead(
        key=key,
        stage=spec.stage,
        unit=spec.unit,
        count=count,
        total=total,
        rate=count / total if total else 0.0,
        sampled=sampled,
        kept=kept,
        grade=rules.grade_of(key, count=count, total=total),  # type: ignore[arg-type]
        kind=spec.kind,
        good_max=spec.good_max,
        warn_max=spec.warn_max,
        who=list(spec.who),
        actions=list(spec.actions),  # type: ignore[arg-type]
    )


def _now_stage(checks: list[CheckRead], *, has_pairs: bool, wants_pairs: bool) -> int:
    """흐름에서 지금 단계: 문제가 남은 첫 단계, 없으면 내보내기. 선호가 아니면 3 선호는 보지 않는다.

    만들 데이터가 선호인데 쌍이 없는 지시가 남았으면(비교 만들기의 할 일) 3 선호에 머문다.
    """
    stages = (1, 2, 3) if has_pairs else (1, 2)
    for stage in stages:
        if any(check.stage == stage and check.grade != "good" for check in checks):
            return stage
    return 3 if wants_pairs else EXPORT_STAGE


async def _turn_counts(db: AsyncSession, *, dataset_id: int) -> dict[int, int]:
    """응답 수(턴 수)별 대화 수. 맥락 안의 응답 수 + 주 응답이 있으면 1."""
    turns = (
        func.jsonb_array_length(func.jsonb_path_query_array(Prompt.messages, ASSISTANT_PATH))
        + cast(HAS_MAIN_ANSWER, Integer)
    ).label("turns")
    rows = await db.execute(
        select(turns, func.count())
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .group_by(turns)
        .order_by(turns)
    )
    return {int(turn): int(count) for turn, count in rows}
