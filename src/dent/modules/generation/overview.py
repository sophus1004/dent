"""데이터셋 진단: 검사마다 문제가 남은 대화 수와 등급, 흐름에서 지금 단계.

모든 수는 SQL 집계로 DB가 센다. 수백만 건이라도 대화를 파이썬으로 불러오지 않는다.
검사 표시는 가져올 때 · 고칠 때 한 번 잰 open_checks(남은 검사)를 센다. 같은 지시(중복)만 해시로 센다:
같은 맥락 해시 무리에서 번호가 가장 작은 것을 남기고 나머지가 '여분'이다(사람이 '그대로 두기'로 둔 여분은 빼고).
뜻 검사(근접 중복 · 과제 쏠림 · 지시 불일치)는 다 만든 뜻 분석으로 센다. 분석이 없거나 Jev가 묻지 못했으면
'기다림'(waiting)으로 두고 종합 상태 · 지금 단계에 세지 않는다. 조건은 problems.py 한 곳에 있다.
진단 결과는 DB의 한 줄이 아니라 계산한 값이라서, 응답 모양(OverviewRead)으로 바로 만든다.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import judging, problems, rules
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import Prompt, Role
from dent.modules.generation.rules import CHECKS, RULE_CHECKS, SEMANTIC_CHECKS, CheckKey
from dent.modules.generation.schemas import CheckRead, OverviewRead, WaitingRead
from dent.modules.generation.service import IS_ACTIVE, IS_TRASHED, has_open

# 해시로 세는 검사 (표시가 아니라 대화끼리 견줘서)
HASH_CHECKS = {CheckKey.DUPLICATE}

# 흐름의 지금 단계가 '내보내기'일 때의 번호 (1 지시 · 2 응답 다음)
EXPORT_STAGE = 3

# 맥락 메시지 가운데 응답만 고르는 JSON 경로
ASSISTANT_PATH = literal_column(f"'$[*] ? (@.role == \"{Role.ASSISTANT.value}\")'::jsonpath")

# 기다림의 까닭
NOT_ANALYZED_REASON = "뜻 분석 전"
JEV_REASONS = {judging.JEV_NOT_CONNECTED: "Jev 미연결", judging.JEV_FAILED: "Jev 실패"}


async def get_overview(db: AsyncSession, *, dataset_id: int) -> OverviewRead:
    """데이터셋 진단을 계산한다. 데이터셋이 없으면 NotFoundError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    mark_keys = [key for key in RULE_CHECKS if key not in HASH_CHECKS]
    row = (
        await db.execute(
            select(
                func.count().filter(IS_ACTIVE),
                func.count().filter(IS_TRASHED),
                *[func.count().filter(IS_ACTIVE, has_open(key)) for key in mark_keys],
            ).where(Prompt.dataset_id == dataset_id)
        )
    ).one()
    active, trashed, *mark_counts = row
    counts = dict(zip(mark_keys, mark_counts, strict=True))
    counts[CheckKey.DUPLICATE] = await _count(db, problems.duplicate_extras(dataset_id))
    totals = {key: int(active) for key in CHECKS}
    waiting: list[WaitingRead] = []
    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    if analysis is None:
        waiting = [_waiting(key, NOT_ANALYZED_REASON) for key in SEMANTIC_CHECKS]
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
        else:
            reason = JEV_REASONS.get(jev_status, NOT_ANALYZED_REASON)
            waiting.append(_waiting(CheckKey.MISMATCH, reason))
    checks = [
        _check_read(key, count=int(counts[key]), total=totals[key])
        for key in CHECKS
        if key in counts
    ]
    return OverviewRead(
        dataset_id=dataset_id,
        conversations=int(active),
        trashed=int(trashed),
        turns=await _turn_counts(db, dataset_id=dataset_id),
        checks=checks,
        waiting=waiting,
        now_stage=_now_stage(checks),
        computed_at=datetime.now(UTC),
    )


async def blocking_checks(db: AsyncSession, *, dataset_id: int) -> list[CheckRead]:
    """심각인 검사들 (내보내기를 막는다)."""
    overview = await get_overview(db, dataset_id=dataset_id)
    return [check for check in overview.checks if check.grade == "bad"]


async def _count(db: AsyncSession, query: Any) -> int:
    """대화 번호 쿼리의 줄 수."""
    return int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)


def _waiting(key: CheckKey, reason: str) -> WaitingRead:
    return WaitingRead(key=key, stage=CHECKS[key].stage, reason=reason)


def _check_read(key: CheckKey, *, count: int, total: int) -> CheckRead:
    spec = CHECKS[key]
    return CheckRead(
        key=key,
        stage=spec.stage,
        count=count,
        rate=count / total if total else 0.0,
        grade=rules.grade_of(key, count=count, total=total),  # type: ignore[arg-type]
        kind=spec.kind,
        good_max=spec.good_max,
        warn_max=spec.warn_max,
        who=list(spec.who),
        actions=list(spec.actions),  # type: ignore[arg-type]
    )


def _now_stage(checks: list[CheckRead]) -> int:
    """흐름에서 지금 단계: 문제가 남은 첫 단계, 없으면 내보내기."""
    for stage in (1, 2):
        if any(check.stage == stage and check.grade != "good" for check in checks):
            return stage
    return EXPORT_STAGE


async def _turn_counts(db: AsyncSession, *, dataset_id: int) -> dict[int, int]:
    """응답 수(턴 수)별 대화 수. 맥락 안의 응답 수 + 마지막 응답 1."""
    turns = (
        func.jsonb_array_length(func.jsonb_path_query_array(Prompt.messages, ASSISTANT_PATH)) + 1
    ).label("turns")
    rows = await db.execute(
        select(turns, func.count())
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .group_by(turns)
        .order_by(turns)
    )
    return {int(turn): int(count) for turn, count in rows}
