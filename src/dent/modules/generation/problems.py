"""검사마다 '문제가 남은 대화'를 고르는 SQL 조건. 진단의 수 · 대화 목록의 문제 거르기 · 검토 큐 · 지도 표시가 같이 쓴다.

- 글의 모양 검사: 가져올 때 · 고칠 때 한 번 잰 open_checks(남은 검사)에 든 대화.
- 같은 지시: 같은 맥락 해시 무리에서 번호가 가장 작은 것을 남긴 나머지(여분).
- 근접 중복: 다 만든 뜻 분석의 근접 중복 쌍에서, 번호가 더 작은 짝이 휴지통 밖에 있는 대화(여분).
- 지시 불일치: 다 만든 뜻 분석에서 Jev가 '따르지 않음'을 확인한 대화. 까닭이 다른 검사로 이미 드러난 대화는 뺀다.
모두 휴지통 밖만 보고, 사람이 '그대로 두기'로 둔 검사는 뺀다. 번호 순.
"""

from typing import Any

from sqlalchemy import Select, and_, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from dent.modules.generation import judging
from dent.modules.generation.models import (
    Analysis,
    AnalysisStatus,
    Judgment,
    NearDuplicate,
    Prompt,
)
from dent.modules.generation.rules import CheckKey
from dent.modules.generation.service import HAS_OPEN_CHECKS, IS_ACTIVE, has_open

# 지시를 따르지 않는 까닭이 이 검사들로 이미 드러난 대화(빈 지시 · 거절 · 짧은 · 빈 · 잘린 응답 …)는 지시 불일치로 세지 않는다.
# 한 문제를 두 검사가 두 번 고치지 않게 하고, 사람이 '그대로 두기'로 둔 거절이 지시 불일치로 다시 뜨지 않게 한다.
EXPLAINED_MISMATCH = (
    CheckKey.STRUCTURE,
    CheckKey.EMPTY_PROMPT,
    CheckKey.EMPTY_ANSWER,
    CheckKey.TRUNCATED,
    CheckKey.LOOP,
    CheckKey.REFUSAL,
    CheckKey.SHORT,
    CheckKey.LANG_MIX,
    CheckKey.CODE_TRANSLATED,
)


def is_kept(key: CheckKey) -> Any:
    """사람이 이 검사를 '그대로 두기'로 둔 대화."""
    return Prompt.kept.contains([key.value])


async def done_analysis(db: AsyncSession, *, dataset_id: int) -> Analysis | None:
    """다 만든 가장 최근 뜻 분석. 없으면 None."""
    return await db.scalar(
        select(Analysis)
        .where(Analysis.dataset_id == dataset_id, Analysis.status == AnalysisStatus.DONE)
        .order_by(Analysis.id.desc())
        .limit(1)
    )


def duplicate_extras(dataset_id: int) -> Select[tuple[int]]:
    """같은 지시의 여분 대화 번호들: 같은 맥락 해시 무리에서 번호가 가장 작은 것을 뺀 나머지."""
    ranked = _ranked(dataset_id)
    return (
        select(ranked.c.id)
        .where(ranked.c.position > 1, not_(ranked.c.kept.contains([CheckKey.DUPLICATE.value])))
        .order_by(ranked.c.id)
    )


def near_extras(dataset_id: int, analysis_id: int) -> Select[tuple[int]]:
    """근접 중복의 여분 대화 번호들: 번호가 더 작은 가까운 짝이 휴지통 밖에 있는 대화."""
    earlier = aliased(Prompt)
    has_earlier_partner = exists().where(
        NearDuplicate.analysis_id == analysis_id,
        NearDuplicate.prompt_b == Prompt.id,
        NearDuplicate.prompt_a == earlier.id,
        earlier.trashed_at.is_(None),
    )
    return (
        select(Prompt.id)
        .where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            not_(is_kept(CheckKey.NEAR_DUPLICATE)),
            has_earlier_partner,
        )
        .order_by(Prompt.id)
    )


def mismatch_ids(dataset_id: int, analysis_id: int) -> Select[tuple[int]]:
    """Jev가 '지시를 따르지 않음'을 확인한 대화 번호들 (까닭이 다른 검사로 드러난 대화는 뺀다)."""
    return (
        select(Prompt.id)
        .join(
            Judgment,
            and_(Judgment.prompt_id == Prompt.id, Judgment.analysis_id == analysis_id),
        )
        .where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            not_(is_kept(CheckKey.MISMATCH)),
            Judgment.follows <= 1 - judging.CONFIRM_PROBABILITY,
            # 표시(marks)는 '그대로 두기'로 둔 검사도 남아 있어, 둔 거절도 여기서 빠진다.
            not_(or_(*(Prompt.marks.has_key(key.value) for key in EXPLAINED_MISMATCH))),
        )
        .order_by(Prompt.id)
    )


def jev_fine_ids(dataset_id: int, analysis_id: int, check: CheckKey) -> Select[tuple[int]]:
    """규칙이 잡은 검사(거절 · 짧은 응답)가 남았는데 Jev가 괜찮다고 본 대화 번호들."""
    confirm = judging.CONFIRM_PROBABILITY
    return (
        select(Prompt.id)
        .join(
            Judgment,
            and_(Judgment.prompt_id == Prompt.id, Judgment.analysis_id == analysis_id),
        )
        .where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            has_open(check.value),
            or_(Judgment.follows >= confirm, Judgment.refusal_ok >= confirm),
        )
        .order_by(Prompt.id)
    )


def open_ids(dataset_id: int, check: CheckKey, *, analysis_id: int | None) -> Select[tuple[int]]:
    """한 검사의 문제가 남은 휴지통 밖 대화 번호들(번호 순). 뜻 검사인데 분석이 없으면 빈 결과."""
    if check == CheckKey.DUPLICATE:
        return duplicate_extras(dataset_id)
    if check == CheckKey.NEAR_DUPLICATE:
        return near_extras(dataset_id, analysis_id) if analysis_id else _none()
    if check == CheckKey.MISMATCH:
        return mismatch_ids(dataset_id, analysis_id) if analysis_id else _none()
    if check == CheckKey.TASK_SKEW:
        # 한 대화의 문제가 아니다(데이터셋의 모양).
        return _none()
    return (
        select(Prompt.id)
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE, has_open(check.value))
        .order_by(Prompt.id)
    )


def any_problem(dataset_id: int, *, analysis_id: int | None) -> Any:
    """문제가 하나라도 남은 대화 조건 (목록의 '문제 있음')."""
    conditions = [HAS_OPEN_CHECKS, Prompt.id.in_(duplicate_extras(dataset_id).scalar_subquery())]
    if analysis_id:
        conditions.append(Prompt.id.in_(near_extras(dataset_id, analysis_id).scalar_subquery()))
        conditions.append(Prompt.id.in_(mismatch_ids(dataset_id, analysis_id).scalar_subquery()))
    return or_(*conditions)


def _ranked(dataset_id: int) -> Any:
    """휴지통 밖 대화마다 같은 맥락 해시 무리 안의 순서(번호 순)."""
    return (
        select(
            Prompt.id,
            Prompt.kept,
            func.row_number()
            .over(partition_by=Prompt.text_hash, order_by=Prompt.id)
            .label("position"),
        )
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .subquery()
    )


def _none() -> Select[tuple[int]]:
    """빈 결과 (대화 번호 없음)."""
    return select(Prompt.id).where(Prompt.id.is_(None))
