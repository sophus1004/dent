"""대화 검사 규칙: 글의 모양만 보고 문제를 찾아 표시(marks)로 남기고, 규칙으로 되는 고치기(가리기 · 자르기)를 한다.

글을 넣거나 바꿀 때 measure로 한 번 재 지시 줄의 marks에 둔다. 진단 · 검토는 이 표시만 센다(요청마다 글을 읽지 않게).
표시 한 개 = {"turn": 메시지 순서, "start": 글 안 시작, "end": 끝}. 대화 전체에 걸친 것(구조)은 turn · start · end가 없다.
같은 지시(중복)는 대화 하나로는 알 수 없어 표시하지 않고 진단이 해시로 센다.

규칙은 모양만 본다(분야를 가정하지 않는다). 모양만으로 가를 수 없는 것은 판정하는 쪽을 둔다(CHECKS의 who):
짧은 응답('0.'처럼 맞는 답일 수 있음) · 거절(정당한 거절일 수 있음)은 Jev가, 반복 문장 · 깨진 글자(인용 · 예시 단어일 수 있음)는 사람이 가른다.

뜻을 봐야 하는 검사(근접 중복 · 과제 쏠림 · 지시 불일치 · 안전)도 이름과 등급 규칙은 여기 CHECKS에 둔다(needs가 embedding · jev).
재는 일은 뜻 분석(semantic.py)이 하고, 분석이 없으면 진단에서 '기다림'이다. 오염(needs eval)은 평가셋과 대조하는 작업이 잰다.

3 선호의 검사는 대화가 아니라 선호 쌍 하나가 단위다(unit pair). 같은 쌍 · 동점은 넣을 때 쌍 표시로(measure_pair),
판정 반대 · 작은 마진은 Jev가 두 응답을 따로 본 판정(지시를 따르나)으로, 길이 편향은 데이터셋의 쌍 전체로 잰다.
두 응답을 한 번에 보이고 고르게 하면 laya가 위치에 끌려(늘 뒤의 응답) 쓸 수 없었다.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from dent.modules.generation.models import Role


class CheckKey(StrEnum):
    """검사 이름. 진단의 줄 · 검토 큐 · 데이터 탭의 문제 거르기가 같이 쓴다."""

    STRUCTURE = "structure"
    EMPTY_PROMPT = "empty_prompt"
    DUPLICATE = "duplicate"
    PII = "pii"
    EMPTY_ANSWER = "empty_answer"
    TRUNCATED = "truncated"
    CODE_TRANSLATED = "code_translated"
    LOOP = "loop"
    REFUSAL = "refusal"
    SHORT = "short"
    REPEAT = "repeat"
    LANG_MIX = "lang_mix"
    BROKEN = "broken"
    LONG = "long"
    # 응답이 없는 지시(지시만) · 도구 호출 형식
    NO_ANSWER = "no_answer"
    TOOL_CALL = "tool_call"
    # 뜻 분석이 재는 검사
    NEAR_DUPLICATE = "near_duplicate"
    TASK_SKEW = "task_skew"
    MISMATCH = "mismatch"
    UNSAFE = "unsafe"
    # 평가셋과 대조하는 검사
    CONTAMINATION = "contamination"
    # 3 선호: 쌍 하나가 단위
    SAME_PAIR = "same_pair"
    JUDGE_REVERSED = "judge_reversed"
    SMALL_MARGIN = "small_margin"
    LENGTH_BIAS = "length_bias"


# 등급 매기는 방식: any = 하나라도 있으면 심각, warn_any = 하나라도 있으면 주의, rate = 대화 수에 대한 비율,
# share = 가장 큰 무리의 몫(good_max 이상이면 주의, 심각은 없다)
GradeKind = Literal["any", "warn_any", "rate", "share"]

# 고치는 쪽: rule(규칙) · jev · llm · human(사람). 화면이 '찾기 → 고치기'로 보인다.
Who = Literal["rule", "jev", "llm", "human"]

# 재는 데 필요한 것: rule(글의 모양, 가져올 때 잼) · embedding(뜻 분석) · jev(뜻 분석 + Jev 판정) · eval(평가셋 대조)
Needs = Literal["rule", "embedding", "jev", "eval"]

# 검사의 단위: conversation(대화 하나 = 지시 줄) · pair(선호 쌍 하나)
Unit = Literal["conversation", "pair"]


@dataclass(frozen=True)
class CheckSpec:
    """검사 하나의 단계 · 등급 규칙 · 고칠 수 있는 결정."""

    # 흐름 단계: 1 지시 · 2 응답 · 3 선호
    stage: int

    # 등급 매기는 방식
    kind: GradeKind

    # rate일 때 통과 · 주의의 상한 (비율). 그 위는 심각.
    good_max: float = 0.0
    warn_max: float = 0.0

    # 고치는 쪽
    who: tuple[Who, ...] = ("rule",)

    # 검토에서 고를 수 있는 결정 (models.Action). 앞의 것이 규칙이 권하는 것이다.
    # rewrite(LLM 다시 쓰기)는 LLM이 연결됐을 때만 된다.
    actions: tuple[str, ...] = ("drop", "keep")

    # 재는 데 필요한 것
    needs: Needs = "rule"

    # 단위 (비율의 분모도 이것: 대화 수 · 쌍 수)
    unit: Unit = "conversation"


# 검사마다의 규칙. 비율 경계는 분류 · 검색의 비슷한 검사와 맞췄다(중복 2% · 10%, 나머지 1% · 5%).
CHECKS: dict[CheckKey, CheckSpec] = {
    CheckKey.STRUCTURE: CheckSpec(stage=1, kind="any"),
    CheckKey.EMPTY_PROMPT: CheckSpec(stage=1, kind="any"),
    CheckKey.DUPLICATE: CheckSpec(stage=1, kind="rate", good_max=0.02, warn_max=0.10),
    CheckKey.NEAR_DUPLICATE: CheckSpec(
        stage=1, kind="rate", good_max=0.02, warn_max=0.10, needs="embedding"
    ),
    CheckKey.PII: CheckSpec(stage=1, kind="warn_any", actions=("mask", "keep", "drop")),
    # 평가셋 문항이 학습에 들어가면 점수가 거짓이 된다. 평가셋을 가져와 표시했을 때만 잰다.
    CheckKey.CONTAMINATION: CheckSpec(stage=1, kind="any", actions=("drop", "keep"), needs="eval"),
    # 한 대화의 문제가 아니라 데이터셋의 모양이라 검토 결정이 없다(데이터를 더해 채운다).
    CheckKey.TASK_SKEW: CheckSpec(
        stage=1, kind="share", good_max=0.40, who=("human",), actions=(), needs="embedding"
    ),
    # 지시만 있는 데이터의 할 일: 응답 만들기(LLM)로 채운다. 문제라기보다 다음 할 일이라 주의다.
    CheckKey.NO_ANSWER: CheckSpec(
        stage=2, kind="warn_any", who=("llm",), actions=("answer", "drop")
    ),
    CheckKey.EMPTY_ANSWER: CheckSpec(
        stage=2, kind="any", who=("rule", "llm"), actions=("drop", "rewrite")
    ),
    # 인자 JSON 깨짐 · 정의에 없는 도구 · 스키마 불일치 · 호출과 결과의 짝. 트레이너가 멈추거나 틀리게 배운다.
    CheckKey.TOOL_CALL: CheckSpec(stage=2, kind="any", actions=("drop", "keep")),
    CheckKey.TRUNCATED: CheckSpec(
        stage=2, kind="any", who=("rule", "llm"), actions=("drop", "rewrite", "keep")
    ),
    CheckKey.CODE_TRANSLATED: CheckSpec(
        stage=2,
        kind="rate",
        good_max=0.01,
        warn_max=0.05,
        who=("rule", "llm"),
        actions=("drop", "rewrite", "keep"),
    ),
    CheckKey.LOOP: CheckSpec(stage=2, kind="warn_any", actions=("cut", "drop", "keep")),
    CheckKey.REFUSAL: CheckSpec(
        stage=2,
        kind="rate",
        good_max=0.01,
        warn_max=0.05,
        who=("rule", "jev", "llm"),
        actions=("keep", "drop", "rewrite"),
    ),
    CheckKey.SHORT: CheckSpec(
        stage=2, kind="warn_any", who=("rule", "jev"), actions=("keep", "drop")
    ),
    CheckKey.REPEAT: CheckSpec(
        stage=2,
        kind="rate",
        good_max=0.01,
        warn_max=0.05,
        who=("rule", "human"),
        actions=("keep", "drop"),
    ),
    CheckKey.LANG_MIX: CheckSpec(
        stage=2,
        kind="rate",
        good_max=0.01,
        warn_max=0.05,
        who=("rule", "llm"),
        actions=("drop", "rewrite", "keep"),
    ),
    CheckKey.BROKEN: CheckSpec(
        stage=2, kind="warn_any", who=("rule", "human"), actions=("keep", "drop")
    ),
    CheckKey.LONG: CheckSpec(stage=2, kind="rate", good_max=0.01, warn_max=0.05),
    # Jev가 '따르지 않음'으로 확인한 것만 센다. 경계는 분류의 오라벨 의심과 같다(1% · 3%).
    CheckKey.MISMATCH: CheckSpec(
        stage=2,
        kind="rate",
        good_max=0.01,
        warn_max=0.03,
        who=("jev", "llm"),
        actions=("drop", "rewrite", "keep"),
        needs="jev",
    ),
    # Jev가 해롭다고 본 응답(≥ 0.8). 분야를 가정하지 않는 판정 모델의 몫이고, 빼기는 사람이 고른다.
    CheckKey.UNSAFE: CheckSpec(
        stage=2, kind="warn_any", who=("jev", "human"), actions=("drop", "keep"), needs="jev"
    ),
    # 3 선호 (쌍 하나가 단위)
    # 고른 응답과 버린 응답이 글자까지 같거나 원본 점수가 같다(배울 것이 없다).
    CheckKey.SAME_PAIR: CheckSpec(stage=3, kind="warn_any", unit="pair", actions=("drop", "keep")),
    # Jev가 버린 응답이 지시를 더 잘 따른다고 본다(라벨이 뒤집혔을 수 있다).
    CheckKey.JUDGE_REVERSED: CheckSpec(
        stage=3,
        kind="warn_any",
        who=("jev", "llm"),
        actions=("flip", "drop", "keep"),
        needs="jev",
        unit="pair",
    ),
    # 두 응답이 거의 비슷하다(배울 신호가 약하다). 제안일 뿐 심각은 없다.
    CheckKey.SMALL_MARGIN: CheckSpec(
        stage=3,
        kind="rate",
        good_max=0.20,
        warn_max=1.01,
        who=("jev", "human"),
        actions=("keep", "drop"),
        needs="jev",
        unit="pair",
    ),
    # 고른 응답이 더 긴 쌍의 몫. 길이만 배우게 된다. 데이터셋의 모양이라 결정이 없다.
    CheckKey.LENGTH_BIAS: CheckSpec(
        stage=3, kind="share", good_max=0.70, who=("human",), actions=(), unit="pair"
    ),
}

# 글의 모양으로 재는 검사 (가져올 때 · 고칠 때 marks에 남는다)
RULE_CHECKS = [key for key, spec in CHECKS.items() if spec.needs == "rule"]

# 뜻 분석이 재는 검사
SEMANTIC_CHECKS = [key for key, spec in CHECKS.items() if spec.needs in ("embedding", "jev")]

# 대화의 글(marks)로 재는 검사 · 쌍의 표시로 재는 검사
MARK_CHECKS = [key for key in RULE_CHECKS if CHECKS[key].unit == "conversation"]
PAIR_CHECKS = [key for key, spec in CHECKS.items() if spec.unit == "pair"]

# 짧은 응답의 글자 수 상한 (미만이면 짧다). '0.' · '예' 같은 한두 글자 답을 잡는다.
SHORT_ANSWER_LENGTH = 5

# 긴 응답의 글자 수 (넘으면 길다). 한국어 약 4천 토큰으로, 흔한 최대 길이(4,096)에서 뒤가 잘린다.
# 학습할 모델의 토크나이저로 세는 것은 다음 판이다.
LONG_ANSWER_LENGTH = 6_000

# 잘린 응답으로 보지 않는 마지막 줄의 글자 수 (서명 · 번호 · 짧은 목록 끝은 문장 부호가 없어도 된다)
SHORT_LAST_LINE = 20

# 잘린 응답의 표시 자리: 끝에서 이만큼 칠한다
TRUNCATED_MARK_LENGTH = 40

# 반복 문장: 이 글자 수보다 긴 문장 · 줄이 한 응답에 이 횟수 넘게 나오면
REPEAT_MIN_LENGTH = 25
REPEAT_MIN_COUNT = 3

# 언어 섞임: 코드 밖 글에서 로마자가 이보다 많고 한글 비율이 이보다 낮으면(영어 답), 한자 · 가나가 이보다 많으면
LANG_MIX_MIN_LATIN = 60
LANG_MIX_MAX_HANGUL_SHARE = 0.4
LANG_MIX_MAX_CJK = 3

# 문장이 끝났다고 보는 끝: 문장 부호 · 닫는 괄호 · 한국어 종결 글자 · 해시태그
END_OK = re.compile(r"([.!?。…」』\"'\)\]\}`~:*]|[다요죠까네음함임됨]|#\w+)\s*$")
CODE_FENCE = re.compile(r"```[^\n]*\n(.*?)(```|$)", re.DOTALL)

# 개인정보 · 비밀값 모양과 가릴 때 쓸 이름 (주민번호 · 휴대전화 · 카드 · 이메일 · API 키).
# 한국어는 숫자 바로 뒤에 붙어 쓰므로('5678입니다') 단어 경계(\b) 대신 앞뒤가 숫자 · 글자가 아닌지만 본다.
PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[주민번호]", re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")),
    ("[전화번호]", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("[카드번호]", re.compile(r"(?<!\d)\d{4}-\d{4}-\d{4}-\d{4}(?!\d)")),
    ("[이메일]", re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)*\.[A-Za-z]{2,}")),
    (
        "[비밀값]",
        re.compile(r"(?<![A-Za-z0-9])(sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"),
    ),
)

# 거절 · 상투구 · 다른 모델 이름 (사용자가 그 모델을 물은 대화는 뺀다)
REFUSAL = re.compile(
    r"(AI 언어 ?모델|인공지능 언어 ?모델|언어 모델로서|AI로서|저는 AI|as an ai|죄송하지만|죄송합니다만|"
    r"이행할 수 없습니다|도와드릴 수 없습니다|I cannot assist|I'm sorry, but)",
    re.IGNORECASE,
)
IDENTITY = re.compile(
    r"(OpenAI|ChatGPT|GPT-?[34]|Anthropic|지식 (컷오프|기준일)|2021년 9월)", re.IGNORECASE
)

# 번역기가 코드 안의 이름까지 한국어로 바꾼 것: 따옴표 밖의 한국어 인자 · 한국어 변수 · 한국어가 된 문법.
# 문자열 안의 한국어('시계방향')나 설명 글은 잡지 않는다.
CODE_HANGUL = re.compile(
    r"[A-Za-z_]\w*\(\s*[가-힣][가-힣_\w]*\s*[,)]|^\s*[가-힣][가-힣_\w]*\s*=\s*[\w\[({]|"
    r"class \w+\([가-힣]+\)|[가-힣A-Za-z]+(가 아니라면|의 작업에 대해)",
    re.MULTILINE,
)
CODE_HINT = re.compile(r"```|'''|\bdef |\bimport |\w+\([^)]*\)\s*[:{]")

# 같은 조각(4~30자)이 다섯 번 넘게 잇달아 나옴. 글자가 든 조각만 센다(들여쓰기 공백 · 표 구분선은 뺀다).
LOOP = re.compile(r"([^\n|]{4,30}?)\1{4,}")
HAS_LETTER = re.compile(r"[가-힣A-Za-z]")

# 대체 문자 · 아랍 · 키릴 · 타이 글자 (번역 · 인코딩이 깨진 흔적. 예시 단어일 수도 있어 사람이 본다)
ODD = re.compile(r"[�؀-ۿЀ-ӿ฀-๿]")
HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")
CJK = re.compile(r"[一-鿿぀-ヿ]")
LIST_ITEM = re.compile(r"^(\d+[.)]|[-*•])")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?다요])\s+")

# 표시 한 개: 메시지 순서와 글 안 자리 (대화 전체에 걸친 것이면 모두 None)
Mark = dict[str, int | None]


def measure(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    token_count: int | None = None,
    max_tokens: int | None = None,
) -> dict[str, list[Mark]]:
    """대화 전체(맥락 + 응답)를 재 문제 표시를 돌려준다. {검사: [표시, …]} (문제가 없는 검사는 싣지 않는다).

    응답이 없는 대화(지시만)도 잰다(구조는 마지막이 지시인지 본다). tools는 대화의 도구 정의다.
    token_count · max_tokens를 주면(학습할 모델의 토크나이저로 센 대화 길이) 긴 대화를 토큰으로 재고,
    없으면 응답의 글자 수로 어림한다.
    """
    marks: dict[str, list[Mark]] = {}

    def add(
        key: CheckKey, turn: int | None = None, start: int | None = None, end: int | None = None
    ) -> None:
        marks.setdefault(key.value, []).append({"turn": turn, "start": start, "end": end})

    if not is_well_formed(messages):
        add(CheckKey.STRUCTURE)
    user_text = " ".join(m["content"] for m in messages if m["role"] == Role.USER)
    by_tokens = token_count is not None and max_tokens is not None
    for turn, message in enumerate(messages):
        content = message["content"]
        for _name, pattern in PII_PATTERNS:
            if found := pattern.search(content):
                add(CheckKey.PII, turn, *found.span())
                break
        if message["role"] == Role.USER and not content.strip():
            add(CheckKey.EMPTY_PROMPT, turn)
        if message["role"] == Role.ASSISTANT:
            _measure_answer(
                content,
                turn=turn,
                user_text=user_text,
                add=add,
                calls_tool=bool(message.get("tool_calls")),
                by_tokens=by_tokens,
            )
    for turn in tool_problems(messages, tools):
        add(CheckKey.TOOL_CALL, turn)
    if by_tokens and token_count > max_tokens:  # type: ignore[operator]
        last = len(messages) - 1
        add(CheckKey.LONG, last if last >= 0 else None)
    return marks


def _measure_answer(
    content: str, *, turn: int, user_text: str, add: Any, calls_tool: bool, by_tokens: bool
) -> None:
    """응답 하나를 잰다. 도구를 부른 응답은 글이 비어도 된다. 토큰으로 재면 긴 응답은 대화 전체로 따로 잰다."""
    text = content.strip()
    if not text:
        if not calls_tool:
            add(CheckKey.EMPTY_ANSWER, turn)
        return
    if len(text) < SHORT_ANSWER_LENGTH and not calls_tool:
        add(CheckKey.SHORT, turn, 0, len(content))
    elif _looks_truncated(text) and not calls_tool:
        end = len(content.rstrip())
        add(CheckKey.TRUNCATED, turn, max(0, end - TRUNCATED_MARK_LENGTH), end)
    # 다른 모델 이름은 사용자가 그 모델을 물은 대화면 문제가 아니다.
    identity = None if IDENTITY.search(user_text) else IDENTITY.search(content)
    if found := REFUSAL.search(content) or identity:
        add(CheckKey.REFUSAL, turn, *found.span())
    if found := ODD.search(content):
        add(CheckKey.BROKEN, turn, *found.span())
    if CODE_HINT.search(content) and (found := CODE_HANGUL.search(content)):
        add(CheckKey.CODE_TRANSLATED, turn, *found.span())
    if loop := find_loop(content):
        add(CheckKey.LOOP, turn, *loop)
    if repeat := _find_repeat(content):
        add(CheckKey.REPEAT, turn, *repeat)
    if not CODE_HINT.search(content) and _is_other_language(content):
        add(CheckKey.LANG_MIX, turn)
    if not by_tokens and len(content) > LONG_ANSWER_LENGTH:
        add(CheckKey.LONG, turn, LONG_ANSWER_LENGTH, len(content))


def is_well_formed(messages: list[dict[str, Any]]) -> bool:
    """역할 순서가 맞는지: system은 맨 앞에 하나만, 그 뒤는 user로 시작해 user · assistant가 번갈아 끝은 assistant.

    응답이 없는 대화(지시만)는 끝이 user여도 된다. 도구 대화는 tool_calls가 있는 assistant 뒤에 tool 결과가
    하나 이상 오고 다시 assistant가 이어진다(tool은 그 자리에만 온다).
    """
    body = messages[1:] if messages and messages[0]["role"] == Role.SYSTEM else messages
    if not body or body[0]["role"] != Role.USER:
        return False
    previous = None
    for message in body:
        role = message["role"]
        if role == Role.TOOL:
            # tool은 도구를 부른 응답이나 다른 tool 결과 뒤에만 온다.
            after_call = previous is not None and (
                previous["role"] == Role.TOOL
                or (previous["role"] == Role.ASSISTANT and previous.get("tool_calls"))
            )
            if not after_call:
                return False
        elif role == Role.USER:
            if previous is not None and previous["role"] == Role.USER:
                return False
            if (
                previous is not None
                and previous["role"] == Role.ASSISTANT
                and previous.get("tool_calls")
            ):
                return False
        elif role == Role.ASSISTANT:
            if previous is not None and previous["role"] == Role.ASSISTANT:
                return False
            if previous is not None and previous["role"] == Role.SYSTEM:
                return False
        else:
            return False
        previous = message
    ends_ok = body[-1]["role"] in (Role.ASSISTANT, Role.USER, Role.TOOL)
    return ends_ok


# ---------- 도구 호출 ----------

# 도구 인자의 JSON Schema 기본 형 → 파이썬 형 (스키마 검사는 필요한 만큼만: 꼭 있어야 할 칸 · 모르는 칸 · 기본 형)
SCHEMA_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def tool_problems(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> list[int]:
    """도구 호출이 잘못된 메시지 자리들(차례대로, 겹치지 않게). 도구 대화가 아니면 빈 목록.

    인자 JSON이 깨짐 · 정의에 없는 도구(정의가 있을 때) · 꼭 있어야 할 인자가 없거나 형이 다름 ·
    결과(tool)가 가리키는 호출이 없음 · 마지막 응답이 아닌데 결과가 없는 호출을 잡는다.
    """
    defined = {_tool_name(tool): tool for tool in tools or [] if _tool_name(tool)}
    bad: list[int] = []
    open_calls: dict[str, int] = {}
    last = len(messages) - 1
    for turn, message in enumerate(messages):
        if message["role"] == Role.ASSISTANT:
            for call in message.get("tool_calls") or []:
                if _call_problem(call, defined):
                    bad.append(turn)
                elif call.get("id") and turn != last:
                    open_calls[str(call["id"])] = turn
        elif message["role"] == Role.TOOL:
            call_id = message.get("tool_call_id")
            if call_id is not None and str(call_id) in open_calls:
                open_calls.pop(str(call_id))
            elif call_id is not None:
                bad.append(turn)
    bad.extend(open_calls.values())
    return sorted(set(bad))


def tool_problem_text(call: dict[str, Any], tools: list[dict[str, Any]] | None) -> str | None:
    """호출 하나의 문제를 낱말로 (검토 · 대화 패널에 보인다). 문제가 없으면 None."""
    defined = {_tool_name(tool): tool for tool in tools or [] if _tool_name(tool)}
    return _call_problem(call, defined)


def call_arguments(call: dict[str, Any]) -> dict[str, Any] | None:
    """호출의 인자(사전). JSON 글이면 읽는다. 읽을 수 없거나 사전이 아니면 None."""
    function = call.get("function") or call
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return None
    return arguments if isinstance(arguments, dict) else None


def _tool_name(tool: dict[str, Any]) -> str:
    """도구 정의의 이름 ({"type": "function", "function": {...}} · {"name": …} 둘 다)."""
    body = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    return str(body.get("name") or "")


def _tool_parameters(tool: dict[str, Any]) -> dict[str, Any]:
    body = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    parameters = body.get("parameters")
    return parameters if isinstance(parameters, dict) else {}


def _call_problem(call: dict[str, Any], defined: dict[str, dict[str, Any]]) -> str | None:
    """호출 하나의 문제 낱말: 인자 JSON 깨짐 · 정의에 없는 도구 · 인자 빠짐 · 모르는 인자 · 인자 형."""
    function = call.get("function") or call
    name = str(function.get("name") or "")
    arguments = call_arguments(call)
    if arguments is None:
        return "인자 JSON 깨짐"
    if not defined:
        return None
    if name not in defined:
        return "정의에 없는 도구"
    schema = _tool_parameters(defined[name])
    properties = schema.get("properties") or {}
    for required in schema.get("required") or []:
        if required not in arguments:
            return "인자 빠짐"
    for key, value in arguments.items():
        if key not in properties:
            if schema.get("additionalProperties") is False:
                return "모르는 인자"
            continue
        expected = SCHEMA_TYPES.get(str(properties[key].get("type", "")))
        is_bool_as_number = isinstance(value, bool) and bool not in (expected or ())
        if expected and (not isinstance(value, expected) or is_bool_as_number):
            return "인자 형"
    return None


def find_loop(content: str) -> tuple[int, int] | None:
    """같은 조각이 잇달아 되풀이되는 구간 (시작, 끝). 없으면 None."""
    for found in LOOP.finditer(content):
        if HAS_LETTER.search(found.group(1)):
            return found.span()
    return None


def _looks_truncated(text: str) -> bool:
    """문장이 끝나지 않고 멈췄는지. 짧은 마지막 줄(서명 · 목록 끝) · 코드 울타리 끝은 잘림으로 보지 않는다."""
    last_line = text.split("\n")[-1].strip()
    return not END_OK.search(text) and not text.endswith("```") and len(last_line) > SHORT_LAST_LINE


def _find_repeat(content: str) -> tuple[int, int] | None:
    """한 응답 안에 같은 긴 문장 · 줄이 여러 번 나오면 그 둘째 자리. 번호 · 글머리 목록은 뺀다."""
    prose = CODE_FENCE.sub("", content)
    pieces = [line.strip() for line in prose.split("\n")]
    pieces += [part.strip() for part in SENTENCE_SPLIT.split(prose)]
    candidates = [p for p in pieces if len(p) > REPEAT_MIN_LENGTH and not LIST_ITEM.match(p)]
    repeated = [text for text, count in Counter(candidates).items() if count >= REPEAT_MIN_COUNT]
    if not repeated:
        return None
    first = content.find(repeated[0])
    second = content.find(repeated[0], first + 1)
    start = second if second >= 0 else first
    return (start, start + len(repeated[0])) if start >= 0 else None


def _is_other_language(content: str) -> bool:
    """코드 밖 글이 목표 언어(한국어)가 아닌지: 영어 답이거나 한자 · 가나가 섞였다."""
    prose = CODE_FENCE.sub("", content)
    hangul, latin = len(HANGUL.findall(prose)), len(LATIN.findall(prose))
    is_latin = (
        latin > LANG_MIX_MIN_LATIN and hangul / max(1, hangul + latin) < LANG_MIX_MAX_HANGUL_SHARE
    )
    return is_latin or len(CJK.findall(prose)) > LANG_MIX_MAX_CJK


# ---------- 선호 쌍 ----------


def measure_pair(
    chosen: str, rejected: str, *, chosen_score: float | None, rejected_score: float | None
) -> dict[str, list[Mark]]:
    """선호 쌍 하나를 잰다: 고른 응답과 버린 응답이 글자까지 같거나(공백 무시) 원본 점수가 같으면 같은 쌍 · 동점."""
    same_text = " ".join(chosen.split()) == " ".join(rejected.split())
    tie = chosen_score is not None and rejected_score is not None and chosen_score == rejected_score
    if same_text or tie:
        return {CheckKey.SAME_PAIR.value: [{"turn": None, "start": None, "end": None}]}
    return {}


# ---------- 규칙으로 되는 고치기 ----------


def mask_pii(text: str) -> str:
    """개인정보 · 비밀값을 [이메일]처럼 가린다."""
    for name, pattern in PII_PATTERNS:
        text = pattern.sub(name, text)
    return text


def cut_loop(text: str) -> str:
    """반복 루프를 첫 조각 하나만 남기고 자른다. 루프가 없으면 그대로."""
    span = find_loop(text)
    if span is None:
        return text
    start, _end = span
    unit = LOOP.match(text, start)
    kept = text[: start + len(unit.group(1))] if unit else text[:start]
    return kept.rstrip()


# ---------- 등급 ----------


def grade_of(key: CheckKey, *, count: int, total: int) -> str:
    """검사의 등급 (good · warn · bad). total은 비율의 분모(휴지통 밖 대화 수)."""
    spec = CHECKS[key]
    if spec.kind == "any":
        return "bad" if count else "good"
    if spec.kind == "warn_any":
        return "warn" if count else "good"
    rate = count / total if total else 0.0
    if spec.kind == "share":
        return "warn" if rate >= spec.good_max else "good"
    if rate < spec.good_max or count == 0:
        return "good"
    return "warn" if rate < spec.warn_max else "bad"
