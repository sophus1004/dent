"""도우미가 끝난 뒤 남은 것 (생성): 지금 진단으로 검사마다 한 줄(LeftItem)을 만든다. 저장하지 않는다.

사람이 직접 고치면(검토 결정) 줄이 줄거나 사라진다. 묶음과 AI로 고치면 하는 일:
- ai: 규칙으로 되는 것(빼기 · 가리기 · 자르기), Jev → LLM 판정(거절 · 짧은 응답), LLM 다시 쓰기
  (잘린 · 빈 응답 · 번역된 코드 · 언어 섞임 · 지시 불일치, 한 번에 MAX_REWRITES_PER_FIX건까지)
- direct: 뜻을 봐야 하는 것(반복 문장 · 깨진 글자: 인용 · 예시 단어일 수 있음, 긴 응답: 빼면 내용을 잃음).
  고를 수는 있다(LLM 판정 · 규칙 빼기).
- blocked: AI로 못 고침(과제 쏠림: 데이터를 더한다).
어림 비용은 대화 수 × 한 건 어림(REWRITE_TOKENS_EACH · JUDGE_TOKENS_EACH).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import overview as overview_service
from dent.modules.generation.rules import CheckKey
from dent.modules.generation.schemas import CheckRead
from dent.system.agent import LEFT_AI, LEFT_BLOCKED, LEFT_DIRECT, LeftItem

# 검사 이름 (진단 · 검토 화면과 같은 낱말)
CHECK_NAMES: dict[CheckKey, str] = {
    CheckKey.STRUCTURE: "대화 구조",
    CheckKey.EMPTY_PROMPT: "빈 지시",
    CheckKey.DUPLICATE: "같은 지시",
    CheckKey.NEAR_DUPLICATE: "근접 중복",
    CheckKey.PII: "개인정보",
    CheckKey.TASK_SKEW: "과제 쏠림",
    CheckKey.EMPTY_ANSWER: "빈 응답",
    CheckKey.TRUNCATED: "잘린 응답",
    CheckKey.CODE_TRANSLATED: "번역된 코드",
    CheckKey.LOOP: "반복 루프",
    CheckKey.REFUSAL: "거절 · 상투구",
    CheckKey.SHORT: "짧은 응답",
    CheckKey.REPEAT: "반복 문장",
    CheckKey.LANG_MIX: "언어 섞임",
    CheckKey.BROKEN: "깨진 글자",
    CheckKey.LONG: "긴 응답",
    CheckKey.MISMATCH: "지시 불일치",
}

# AI로 고치기 한 번에 LLM으로 다시 쓰는 최대 대화 수 (토큰 상한 안에서)
MAX_REWRITES_PER_FIX = 40

# 어림 토큰: 다시 쓰기 한 건 · 판정 한 건 (입력 + 출력)
REWRITE_TOKENS_EACH = 1_800
JUDGE_TOKENS_EACH = 250


@dataclass(frozen=True)
class FixSpec:
    """검사 하나를 AI로 고치는 방법."""

    # 묶음: ai · direct · blocked
    group: str

    # 고치는 방법: drop · mask · cut (규칙) · judge (Jev → LLM 판정) · rewrite (LLM 다시 쓰기) · none
    how: str

    # 화면에 보일 하는 일 (blocked면 사람이 할 일)
    text: str


FIX_SPECS: dict[CheckKey, FixSpec] = {
    CheckKey.STRUCTURE: FixSpec(LEFT_AI, "drop", "빼기 · 규칙"),
    CheckKey.EMPTY_PROMPT: FixSpec(LEFT_AI, "drop", "빼기 · 규칙"),
    CheckKey.DUPLICATE: FixSpec(LEFT_AI, "drop", "여분 빼기 · 규칙"),
    CheckKey.NEAR_DUPLICATE: FixSpec(LEFT_AI, "drop", "여분 빼기 · 규칙"),
    CheckKey.PII: FixSpec(LEFT_AI, "mask", "가리기 · 규칙"),
    CheckKey.LOOP: FixSpec(LEFT_AI, "cut", "루프 앞에서 자르기 · 규칙"),
    CheckKey.EMPTY_ANSWER: FixSpec(LEFT_AI, "rewrite", "LLM으로 다시 쓰기"),
    CheckKey.TRUNCATED: FixSpec(LEFT_AI, "rewrite", "LLM으로 끝까지 다시 쓰기"),
    CheckKey.CODE_TRANSLATED: FixSpec(LEFT_AI, "rewrite", "LLM으로 코드 되돌리기"),
    CheckKey.LANG_MIX: FixSpec(LEFT_AI, "rewrite", "LLM으로 한국어로 다시 쓰기"),
    CheckKey.MISMATCH: FixSpec(LEFT_AI, "rewrite", "LLM으로 지시에 맞게 다시 쓰기"),
    CheckKey.REFUSAL: FixSpec(LEFT_AI, "judge", "Jev 괜찮음 그대로 두기 · 나머지 LLM 판정"),
    CheckKey.SHORT: FixSpec(LEFT_AI, "judge", "Jev 괜찮음 그대로 두기 · 나머지 LLM 판정"),
    CheckKey.REPEAT: FixSpec(LEFT_DIRECT, "judge", "LLM 판정 · 인용 · 절차면 그대로"),
    CheckKey.BROKEN: FixSpec(LEFT_DIRECT, "judge", "LLM 판정 · 예시 단어면 그대로"),
    CheckKey.LONG: FixSpec(LEFT_DIRECT, "drop", "빼기 · 규칙 · 내용을 잃음"),
    CheckKey.TASK_SKEW: FixSpec(LEFT_BLOCKED, "none", "모자란 과제의 데이터 더하기"),
}

# AI로 고치기의 순서 (도우미 흐름과 같다: 지시 → 응답, 규칙 → 판정 → 다시 쓰기)
FIX_ORDER = list(FIX_SPECS)


def ordered(keys: list[str]) -> list[str]:
    """고른 검사들을 흐름 순서로."""
    order = {key.value: index for index, key in enumerate(FIX_ORDER)}
    return sorted(keys, key=lambda key: order.get(key, len(order)))


def check_value(check: CheckRead) -> str:
    """검사 값 글 (보고 · 남은 것): 과제 쏠림은 몫, 나머지는 건수."""
    if check.kind == "share":
        return f"{round(check.rate * 100)}%"
    return f"{check.count:,}건"


async def left_items(db: AsyncSession, *, dataset_id: int) -> list[LeftItem]:
    """지금 진단에서 통과하지 못한 검사마다 한 줄 (흐름 순서)."""
    overview = await overview_service.get_overview(db, dataset_id=dataset_id)
    by_key = {check.key: check for check in overview.checks}
    items = []
    for key in FIX_ORDER:
        check = by_key.get(key)
        if check is None or check.grade == "good" or check.count == 0:
            continue
        spec = FIX_SPECS[key]
        items.append(_item(check, spec))
    return items


def _item(check: CheckRead, spec: FixSpec) -> LeftItem:
    count = check.count
    is_share = check.kind == "share"
    tokens = 0
    if spec.how == "rewrite":
        tokens = min(count, MAX_REWRITES_PER_FIX) * REWRITE_TOKENS_EACH
    elif spec.how == "judge":
        tokens = count * JUDGE_TOKENS_EACH
    sub = ""
    if spec.how == "rewrite" and count > MAX_REWRITES_PER_FIX:
        sub = f"한 번에 {MAX_REWRITES_PER_FIX}건"
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
