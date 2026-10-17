"""도우미가 끝난 뒤 남은 것 (생성): 지금 진단으로 검사마다 한 줄(LeftItem)을 만든다. 저장하지 않는다.

사람이 직접 고치면(검토 결정) 줄이 줄거나 사라진다. 묶음과 AI로 고치면 하는 일:
- ai: 규칙으로 되는 것(빼기 · 가리기 · 자르기 · 쌍 빼기), Jev → LLM 판정(거절 · 짧은 응답 · 안전 · 판정 반대),
  LLM 다시 쓰기(잘린 · 빈 응답 · 번역된 코드 · 언어 섞임 · 지시 불일치), 응답 만들기(응답 없음),
  비교 만들기(쌍 없음: 만들 데이터가 선호이고 Jev가 연결됐을 때). LLM이 글을 쓰는 일은 한 번에
  MAX_REWRITES_PER_FIX · MAX_MAKES_PER_FIX건까지다.
- direct: 뜻을 봐야 하는 것(반복 문장 · 깨진 글자: 인용 · 예시 단어일 수 있음, 긴 대화: 빼면 내용을 잃음,
  작은 마진: 비슷한 두 응답도 쓸모가 있을 수 있음). 고를 수는 있다(LLM 판정 · 규칙 빼기).
- blocked: AI로 못 고침(과제 쏠림 · 길이 편향: 데이터를 더한다).
어림 비용은 대화 수 × 한 건 어림(REWRITE_TOKENS_EACH · JUDGE_TOKENS_EACH · 만들기는 making.TOKENS_EACH_ESTIMATE).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import making
from dent.modules.generation import overview as overview_service
from dent.modules.generation.models import Goal
from dent.modules.generation.rules import CheckKey
from dent.modules.generation.schemas import CheckRead, OverviewRead
from dent.system import connections
from dent.system.agent import LEFT_AI, LEFT_BLOCKED, LEFT_DIRECT, LeftItem
from dent.system.models import ConnectionRole

# 비교 만들기의 열쇠 (검사가 아니라 할 일: 만들 데이터가 선호인데 쌍이 없는 지시)
PAIRS_KEY = "pairs"
PAIRS_NAME = "쌍 없음"

# 검사 이름 (진단 · 검토 화면과 같은 낱말)
CHECK_NAMES: dict[CheckKey, str] = {
    CheckKey.STRUCTURE: "대화 구조",
    CheckKey.EMPTY_PROMPT: "빈 지시",
    CheckKey.DUPLICATE: "같은 지시",
    CheckKey.NEAR_DUPLICATE: "근접 중복",
    CheckKey.PII: "개인정보",
    CheckKey.TASK_SKEW: "과제 쏠림",
    CheckKey.SAFETY_EXAMPLES: "안전 예시",
    CheckKey.EMPTY_ANSWER: "빈 응답",
    CheckKey.TRUNCATED: "잘린 응답",
    CheckKey.CODE_TRANSLATED: "번역된 코드",
    CheckKey.LOOP: "반복 루프",
    CheckKey.REFUSAL: "거절 · 상투구",
    CheckKey.SHORT: "짧은 응답",
    CheckKey.REPEAT: "반복 문장",
    CheckKey.LANG_MIX: "언어 섞임",
    CheckKey.BROKEN: "깨진 글자",
    CheckKey.LONG: "긴 대화",
    CheckKey.MISMATCH: "지시 불일치",
    CheckKey.NO_ANSWER: "응답 없음",
    CheckKey.TOOL_CALL: "도구 호출 형식",
    CheckKey.UNSAFE: "안전",
    CheckKey.CONTAMINATION: "오염",
    CheckKey.SAME_PAIR: "같은 쌍 · 동점",
    CheckKey.JUDGE_REVERSED: "판정 반대",
    CheckKey.SMALL_MARGIN: "작은 마진",
    CheckKey.LENGTH_BIAS: "길이 편향",
}

# AI로 고치기 한 번에 LLM으로 다시 쓰는 최대 대화 수 (토큰 상한 안에서)
MAX_REWRITES_PER_FIX = 40

# AI로 고치기 한 번에 응답 · 비교를 만드는 최대 지시 수 (허락 카드 없이 도는 일이라 작게)
MAX_MAKES_PER_FIX = 200

# 어림 토큰: 다시 쓰기 한 건 · 판정 한 건 (입력 + 출력)
REWRITE_TOKENS_EACH = 1_800
JUDGE_TOKENS_EACH = 250


@dataclass(frozen=True)
class FixSpec:
    """검사 하나를 AI로 고치는 방법."""

    # 묶음: ai · direct · blocked
    group: str

    # 고치는 방법: drop · mask · cut (규칙) · judge (Jev → LLM 판정) · rewrite (LLM 다시 쓰기) ·
    # answer (응답 만들기) · compare (비교 만들기) · none. 쌍 검사의 drop · judge는 쌍에 한다.
    how: str

    # 화면에 보일 하는 일 (blocked면 사람이 할 일)
    text: str


# 검사(와 비교 만들기)마다 고치는 방법. 순서가 AI로 고치기의 순서다(도우미 흐름과 같다: 지시 → 응답 → 선호).
FIX_SPECS: dict[str, FixSpec] = {
    CheckKey.STRUCTURE: FixSpec(LEFT_AI, "drop", "빼기 · 규칙"),
    CheckKey.EMPTY_PROMPT: FixSpec(LEFT_AI, "drop", "빼기 · 규칙"),
    CheckKey.DUPLICATE: FixSpec(LEFT_AI, "drop", "여분 빼기 · 규칙"),
    CheckKey.PII: FixSpec(LEFT_AI, "mask", "가리기 · 규칙"),
    CheckKey.CONTAMINATION: FixSpec(LEFT_AI, "drop", "빼기 · 규칙"),
    CheckKey.NEAR_DUPLICATE: FixSpec(LEFT_AI, "drop", "여분 빼기 · 규칙"),
    CheckKey.NO_ANSWER: FixSpec(LEFT_AI, "answer", "LLM으로 응답 만들기"),
    CheckKey.TOOL_CALL: FixSpec(LEFT_AI, "drop", "빼기 · 규칙"),
    CheckKey.EMPTY_ANSWER: FixSpec(LEFT_AI, "rewrite", "LLM으로 다시 쓰기"),
    CheckKey.LOOP: FixSpec(LEFT_AI, "cut", "루프 앞에서 자르기 · 규칙"),
    CheckKey.REFUSAL: FixSpec(LEFT_AI, "judge", "Jev 괜찮음 그대로 두기 · 나머지 LLM 판정"),
    CheckKey.SHORT: FixSpec(LEFT_AI, "judge", "Jev 괜찮음 그대로 두기 · 나머지 LLM 판정"),
    CheckKey.UNSAFE: FixSpec(LEFT_AI, "judge", "LLM 판정 · 해로우면 빼기"),
    CheckKey.TRUNCATED: FixSpec(LEFT_AI, "rewrite", "LLM으로 끝까지 다시 쓰기"),
    CheckKey.CODE_TRANSLATED: FixSpec(LEFT_AI, "rewrite", "LLM으로 코드 되돌리기"),
    CheckKey.LANG_MIX: FixSpec(LEFT_AI, "rewrite", "LLM으로 한국어로 다시 쓰기"),
    CheckKey.MISMATCH: FixSpec(LEFT_AI, "rewrite", "LLM으로 지시에 맞게 다시 쓰기"),
    CheckKey.REPEAT: FixSpec(LEFT_DIRECT, "judge", "LLM 판정 · 인용 · 절차면 그대로"),
    CheckKey.BROKEN: FixSpec(LEFT_DIRECT, "judge", "LLM 판정 · 예시 단어면 그대로"),
    CheckKey.LONG: FixSpec(LEFT_DIRECT, "drop", "빼기 · 규칙 · 내용을 잃음"),
    PAIRS_KEY: FixSpec(LEFT_AI, "compare", "LLM 응답 하나 더 · Jev가 고르기"),
    CheckKey.SAME_PAIR: FixSpec(LEFT_AI, "drop", "쌍 빼기 · 규칙"),
    CheckKey.JUDGE_REVERSED: FixSpec(
        LEFT_AI, "judge", "LLM이 순서를 바꿔 두 번 견줌 · 뒤집기 · 쌍 빼기"
    ),
    CheckKey.SMALL_MARGIN: FixSpec(LEFT_DIRECT, "drop", "쌍 빼기 · 규칙 · 신호가 약함"),
    CheckKey.TASK_SKEW: FixSpec(LEFT_BLOCKED, "none", "모자란 과제의 데이터 더하기"),
    CheckKey.SAFETY_EXAMPLES: FixSpec(LEFT_BLOCKED, "none", "해로운 요청에 거절한 예 더하기"),
    CheckKey.LENGTH_BIAS: FixSpec(LEFT_BLOCKED, "none", "고른 응답이 더 짧은 쌍 더하기"),
}

# AI로 고치기의 순서
FIX_ORDER = list(FIX_SPECS)


def ordered(keys: list[str]) -> list[str]:
    """고른 검사들을 흐름 순서로."""
    order = {str(key): index for index, key in enumerate(FIX_ORDER)}
    return sorted(keys, key=lambda key: order.get(key, len(order)))


def check_value(check: CheckRead) -> str:
    """검사 값 글 (보고 · 남은 것): 과제 쏠림은 몫, 안전 예시는 확인 · 거절 수, 나머지는 건수."""
    if check.kind == "share":
        return f"{round(check.rate * 100)}%"
    if check.kind == "none":
        return f"확인 {check.kept:,} · 거절 {check.count:,}"
    return f"{check.count:,}건"


async def left_items(db: AsyncSession, *, dataset_id: int) -> list[LeftItem]:
    """지금 진단에서 통과하지 못한 검사마다 한 줄 (흐름 순서). 비교 만들기의 할 일도 한 줄."""
    overview = await overview_service.get_overview(db, dataset_id=dataset_id)
    by_key = {check.key: check for check in overview.checks}
    items = []
    for key in FIX_ORDER:
        if key == PAIRS_KEY:
            if await wants_pairs(db, overview):
                by_target = (
                    await connections.get_connection(db, role=ConnectionRole.TARGET) is not None
                )
                items.append(_pairs_item(overview.unpaired, by_target=by_target))
            continue
        check = by_key.get(CheckKey(key))
        # none 검사(안전 예시)는 0건이 문제다
        is_clear = (
            check is None or check.grade == "good" or (check.count == 0 and check.kind != "none")
        )
        if is_clear:
            continue
        items.append(_item(check, FIX_SPECS[key]))
    return items


async def wants_pairs(db: AsyncSession, overview: OverviewRead) -> bool:
    """비교 만들기의 할 일이 있는지: 만들 데이터가 선호, 쌍 없는 지시가 있고, Jev가 연결됐다."""
    if overview.goal != Goal.PREFERENCE or overview.unpaired == 0:
        return False
    return await connections.get_connection(db, role=ConnectionRole.JEV) is not None


# 학습할 모델이 연결돼 있을 때 비교 만들기의 하는 일 (그 모델이 응답을 쓴다)
PAIRS_BY_TARGET_TEXT = "학습할 모델 응답 하나 더 · Jev가 고르기"


def _pairs_item(count: int, *, by_target: bool) -> LeftItem:
    spec = FIX_SPECS[PAIRS_KEY]
    return LeftItem(
        key=PAIRS_KEY,
        name=PAIRS_NAME,
        value=f"{count:,}",
        unit="건",
        sub=f"한 번에 {MAX_MAKES_PER_FIX}건" if count > MAX_MAKES_PER_FIX else "",
        grade="warn",
        group=spec.group,
        how=PAIRS_BY_TARGET_TEXT if by_target else spec.text,
        tokens=min(count, MAX_MAKES_PER_FIX) * making.TOKENS_EACH_ESTIMATE,
        action="",
        view_problem="",
    )


def _item(check: CheckRead, spec: FixSpec) -> LeftItem:
    count = check.count
    is_share = check.kind == "share"
    tokens = 0
    limit = 0
    if spec.how == "rewrite":
        limit = MAX_REWRITES_PER_FIX
        tokens = min(count, limit) * REWRITE_TOKENS_EACH
    elif spec.how == "answer":
        limit = MAX_MAKES_PER_FIX
        tokens = min(count, limit) * making.TOKENS_EACH_ESTIMATE
    elif spec.how == "judge":
        tokens = count * JUDGE_TOKENS_EACH
    sub = f"한 번에 {limit}건" if limit and count > limit else ""
    return LeftItem(
        key=check.key.value,
        name=CHECK_NAMES[check.key],
        value=str(round(check.rate * 100)) if is_share else f"{count:,}",
        unit="%" if is_share else "건",
        sub=sub,
        grade=check.grade,
        group=spec.group,
        how="" if spec.group == LEFT_BLOCKED else spec.text,
        tokens=tokens,
        action=spec.text if spec.group == LEFT_BLOCKED else "",
        view_problem=check.key.value,
    )
