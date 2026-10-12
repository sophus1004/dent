"""검사마다 '문제가 남은 대화'를 고르는 SQL 조건. 진단의 수 · 대화 목록의 문제 거르기 · 검토 큐 · 지도 표시가 같이 쓴다.

- 글의 모양 검사: 가져올 때 · 고칠 때 한 번 잰 open_checks(남은 검사)에 든 대화.
- 같은 지시: 같은 맥락 해시 무리에서 번호가 가장 작은 것을 남긴 나머지(여분).
- 근접 중복: 다 만든 뜻 분석의 근접 중복 쌍에서, 번호가 더 작은 짝이 휴지통 밖에 있는 대화(여분).
- 지시 불일치: 다 만든 뜻 분석에서 Jev가 '따르지 않음'을 확인한 한 턴 · 글 응답 대화. 까닭이 다른 검사로 드러난 대화는 뺀다.
  Jev가 애매하게 본 것(따름 0.2~0.5)과 여러 턴 · 코드 응답의 '따르지 않음'은 '의심'이라 진단에 세지 않고 도우미의 LLM이 본다.
  선호 쌍의 판정 반대가 남은 대화(엉뚱한 응답이 고른 쪽)는 세지 않는다(3 선호에서 뒤집는다).
- 응답 없음: 주 응답이 없는 대화(지시만). 오염: 평가셋과 겹친 대화. 안전: Jev가 해롭다고 본 응답.
- 3 선호(쌍 번호): 같은 쌍 · 동점(쌍 표시), 판정 반대 · 작은 마진(Jev가 두 응답을 따로 본 따름 점수).
모두 휴지통 밖만 보고, 사람이 '그대로 두기'로 둔 검사는 뺀다. 번호 순.
"""

from typing import Any

from sqlalchemy import Select, and_, exists, func, literal_column, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from dent.modules.generation import judging
from dent.modules.generation.models import (
    Analysis,
    AnalysisStatus,
    Contamination,
    Judgment,
    NearDuplicate,
    Pair,
    Prompt,
    Response,
    Role,
)
from dent.modules.generation.rules import CHECKS, CheckKey
from dent.modules.generation.service import (
    HAS_MAIN_ANSWER,
    HAS_OPEN_CHECKS,
    IS_ACTIVE,
    PAIR_IS_ACTIVE,
    has_open,
)

# 지시 불일치 의심: Jev 따름이 확인 경계(0.2) 위 · 이 값 이하. 진단에 세지 않고 도우미의 LLM이 본다.
MISMATCH_SUSPECT_FOLLOWS = 0.5

# 맥락의 사용자 말만 고르는 JSON 경로 (여러 턴 대화를 가른다)
USER_PATH = literal_column(f"'$[*] ? (@.role == \"{Role.USER.value}\")'::jsonpath")

# 여러 턴 대화: 사용자 말이 둘 이상. Jev는 앞 턴을 잇는 짧은 말('부산은요?' · 주문 번호)을 잘 읽지 못해
# (예시 상담 대화의 맞는 답 24개 중 7개를 따르지 않음으로 봄) 따르지 않음도 확인이 아니라 의심으로 둔다.
IS_MULTI_TURN = func.jsonb_array_length(func.jsonb_path_query_array(Prompt.messages, USER_PATH)) > 1

# 코드 응답의 모양 (코드 울타리 · 줄 머리의 def · class · import · function · SELECT …, rules.CODE_HINT와 같은 뜻).
# Jev는 코드만 있는 맞는 답을 잘 읽지 못해(코드 과제 12개 중 4~8개를 따르지 않음으로 봄) 의심으로만 둔다.
CODE_ANSWER_PATTERN = r"```|(^|\n)\s*(def|class|import|from|function|const|let|SELECT|select)\s"

# 주 응답이 코드인 대화
HAS_CODE_ANSWER = exists().where(
    Response.prompt_id == Prompt.id,
    Response.is_main,
    Response.content.op("~")(CODE_ANSWER_PATTERN),
)

# Jev의 '따르지 않음'을 확인으로 보지 않는 대화 (여러 턴 · 코드 응답): 의심으로만 둔다
IS_JEV_WEAK = or_(IS_MULTI_TURN, HAS_CODE_ANSWER)

# 작은 마진: 두 응답의 Jev 따름 점수 차이가 이보다 작으면 두 응답이 비슷하다(배울 신호가 약하다)
SMALL_MARGIN = 0.1

# 판정 반대: 버린 응답의 따름 점수가 고른 응답보다 이만큼 이상 높으면 라벨이 뒤집혔을 수 있다
REVERSED_MARGIN = 0.3

def _has_reversed_pair() -> Any:
    """판정 반대가 남은 쌍이 있는 대화 (주 응답 = 고른 응답이 엉뚱할 때: 3 선호에서 뒤집어 고친다)."""
    return exists().where(
        Pair.prompt_id == Prompt.id,
        PAIR_IS_ACTIVE,
        Pair.judge_chosen - Pair.judge_rejected <= -REVERSED_MARGIN,
        not_(Pair.kept.contains([CheckKey.JUDGE_REVERSED.value])),
    )


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
            not_(IS_JEV_WEAK),
            # 표시(marks)는 '그대로 두기'로 둔 검사도 남아 있어, 둔 거절도 여기서 빠진다.
            not_(or_(*(Prompt.marks.has_key(key.value) for key in EXPLAINED_MISMATCH))),
            # 선호 쌍의 판정 반대가 까닭이면(엉뚱한 응답을 고른 쪽에 둠) 3 선호에서 뒤집어 고친다.
            not_(_has_reversed_pair()),
        )
        .order_by(Prompt.id)
    )


def mismatch_suspect_ids(dataset_id: int, analysis_id: int) -> Select[tuple[int]]:
    """Jev가 애매하게 본 지시 불일치 의심 대화 번호들 (따름 0.2 초과 · 0.5 이하, 여러 턴 · 코드 응답이면 0.5 이하 모두,
    까닭이 드러난 대화 뺌)."""
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
            or_(Judgment.follows > 1 - judging.CONFIRM_PROBABILITY, IS_JEV_WEAK),
            Judgment.follows <= MISMATCH_SUSPECT_FOLLOWS,
            not_(or_(*(Prompt.marks.has_key(key.value) for key in EXPLAINED_MISMATCH))),
            not_(_has_reversed_pair()),
        )
        .order_by(Prompt.id)
    )


def unsafe_ids(dataset_id: int, analysis_id: int) -> Select[tuple[int]]:
    """Jev가 해롭다고 본(≥ 0.8) 응답의 대화 번호들."""
    return (
        select(Prompt.id)
        .join(
            Judgment,
            and_(Judgment.prompt_id == Prompt.id, Judgment.analysis_id == analysis_id),
        )
        .where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            not_(is_kept(CheckKey.UNSAFE)),
            Judgment.unsafe >= judging.CONFIRM_PROBABILITY,
        )
        .order_by(Prompt.id)
    )


def no_answer_ids(dataset_id: int) -> Select[tuple[int]]:
    """주 응답이 없는 대화(지시만) 번호들."""
    return (
        select(Prompt.id)
        .where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            not_(HAS_MAIN_ANSWER),
            not_(is_kept(CheckKey.NO_ANSWER)),
        )
        .order_by(Prompt.id)
    )


def answer_targets(dataset_id: int) -> Select[tuple[int]]:
    """응답 만들기의 대상: 응답이 없는 대화 가운데 도구 대화가 아닌 것(호출을 지어내지 않게)."""
    return no_answer_ids(dataset_id).where(Prompt.tools.is_(None))


def pair_targets(dataset_id: int) -> Select[tuple[int]]:
    """비교 만들기의 대상: 주 응답이 있고 쌍이 하나도 없는(뺀 쌍도 없는) 휴지통 밖 대화, 도구 대화는 뺀다.

    사람이 뺀 쌍이 있는 지시에는 다시 만들지 않는다. 진단의 '쌍 없음'도 이 조건으로 센다.
    """
    has_pair = select(Pair.id).where(Pair.prompt_id == Prompt.id).exists()
    return (
        select(Prompt.id)
        .where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            HAS_MAIN_ANSWER,
            Prompt.tools.is_(None),
            ~has_pair,
        )
        .order_by(Prompt.id)
    )


def contamination_ids(dataset_id: int) -> Select[tuple[int]]:
    """평가셋과 겹친 대화 번호들 (마지막 대조의 결과)."""
    return (
        select(Prompt.id)
        .join(Contamination, Contamination.prompt_id == Prompt.id)
        .where(
            Prompt.dataset_id == dataset_id,
            IS_ACTIVE,
            not_(is_kept(CheckKey.CONTAMINATION)),
        )
        .order_by(Prompt.id)
    )


# ---------- 선호 쌍 ----------


def active_pairs(dataset_id: int) -> Select[tuple[int]]:
    """휴지통 밖 선호 쌍 번호들 (지시도 휴지통 밖)."""
    return (
        select(Pair.id)
        .join(Prompt, Prompt.id == Pair.prompt_id)
        .where(Pair.dataset_id == dataset_id, PAIR_IS_ACTIVE, IS_ACTIVE)
        .order_by(Pair.id)
    )


def judged_pairs(dataset_id: int) -> Select[tuple[int]]:
    """Jev가 두 응답을 따로 본 휴지통 밖 쌍 번호들."""
    return active_pairs(dataset_id).where(
        Pair.judge_chosen.is_not(None), Pair.judge_rejected.is_not(None)
    )


def pair_open_ids(dataset_id: int, check: CheckKey) -> Select[tuple[int]]:
    """쌍 검사의 문제가 남은 쌍 번호들(번호 순). 길이 편향은 데이터셋의 모양이라 빈 결과."""
    not_kept = not_(Pair.kept.contains([check.value]))
    margin = Pair.judge_chosen - Pair.judge_rejected
    if check == CheckKey.SAME_PAIR:
        return active_pairs(dataset_id).where(Pair.marks.has_key(check.value), not_kept)
    if check == CheckKey.JUDGE_REVERSED:
        return judged_pairs(dataset_id).where(margin <= -REVERSED_MARGIN, not_kept)
    if check == CheckKey.SMALL_MARGIN:
        return judged_pairs(dataset_id).where(func.abs(margin) < SMALL_MARGIN, not_kept)
    return select(Pair.id).where(Pair.id.is_(None))


def pair_problems(pair: Pair) -> list[CheckKey]:
    """쌍 하나에 남은 쌍 검사들 (pair_open_ids와 같은 기준, 사람이 그대로 둔 검사는 뺀다)."""
    found = []
    if CheckKey.SAME_PAIR.value in pair.marks:
        found.append(CheckKey.SAME_PAIR)
    if pair.judge_chosen is not None and pair.judge_rejected is not None:
        margin = pair.judge_chosen - pair.judge_rejected
        if margin <= -REVERSED_MARGIN:
            found.append(CheckKey.JUDGE_REVERSED)
        if abs(margin) < SMALL_MARGIN:
            found.append(CheckKey.SMALL_MARGIN)
    return [check for check in found if check.value not in pair.kept]


def jev_fine_ids(dataset_id: int, analysis_id: int, check: CheckKey) -> Select[tuple[int]]:
    """규칙이 잡은 검사(거절 · 짧은 응답)가 남았는데 Jev가 괜찮다고 본(지시를 따른다고 확인한) 대화 번호들.

    마땅한 거절 점수만으로는 넣지 않는다(judging.looks_fine과 같은 기준).
    """
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
            Judgment.follows >= confirm,
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
    if check == CheckKey.UNSAFE:
        return unsafe_ids(dataset_id, analysis_id) if analysis_id else _none()
    if check == CheckKey.NO_ANSWER:
        return no_answer_ids(dataset_id)
    if check == CheckKey.CONTAMINATION:
        return contamination_ids(dataset_id)
    if check == CheckKey.TASK_SKEW or CHECKS[check].unit == "pair":
        # 한 대화의 문제가 아니다(데이터셋의 모양 · 쌍의 문제는 pair_open_ids).
        return _none()
    return (
        select(Prompt.id)
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE, has_open(check.value))
        .order_by(Prompt.id)
    )


def any_problem(dataset_id: int, *, analysis_id: int | None) -> Any:
    """문제가 하나라도 남은 대화 조건 (목록의 '문제 있음')."""
    conditions = [
        HAS_OPEN_CHECKS,
        Prompt.id.in_(duplicate_extras(dataset_id).scalar_subquery()),
        Prompt.id.in_(no_answer_ids(dataset_id).scalar_subquery()),
        Prompt.id.in_(contamination_ids(dataset_id).scalar_subquery()),
    ]
    if analysis_id:
        conditions.append(Prompt.id.in_(near_extras(dataset_id, analysis_id).scalar_subquery()))
        conditions.append(Prompt.id.in_(mismatch_ids(dataset_id, analysis_id).scalar_subquery()))
        conditions.append(Prompt.id.in_(unsafe_ids(dataset_id, analysis_id).scalar_subquery()))
    return or_(*conditions)


async def kept_counts(db: AsyncSession, *, dataset_id: int) -> dict[str, int]:
    """검사마다 '그대로 두기'로 둔 휴지통 밖 대화 · 쌍 수 (진단이 규칙의 오탐을 가늠하게 싣는다)."""
    # 집합을 내는 함수(jsonb_array_elements_text)는 GROUP BY에 둘 수 없어 안쪽 쿼리로 펼친 뒤 센다.
    prompt_keys = (
        select(func.jsonb_array_elements_text(Prompt.kept).label("key"))
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .subquery()
    )
    pair_keys = (
        select(func.jsonb_array_elements_text(Pair.kept).label("key"))
        .join(Prompt, Prompt.id == Pair.prompt_id)
        .where(Pair.dataset_id == dataset_id, PAIR_IS_ACTIVE, IS_ACTIVE)
        .subquery()
    )
    counts: dict[str, int] = {}
    for keys in (prompt_keys, pair_keys):
        rows = await db.execute(select(keys.c.key, func.count()).group_by(keys.c.key))
        for key, count in rows.all():
            counts[key] = counts.get(key, 0) + int(count)
    return counts


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
