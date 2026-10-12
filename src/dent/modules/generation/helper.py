"""LLM 도우미 (생성): 진단을 읽고, 스스로 묻고 답하며, 규칙 · Jev · LLM으로 대화 데이터를 고친다. 채팅이 아니다.

한 번 실행 (작업 ("generation", "helper"))
  0 계획        진단 요약을 LLM에 보이고 무엇이 급한지 말하게 한다(도구 plan). 순서는 흐름을 따른다.
  1 지시        대화 구조 → 빈 지시 → 같은 지시 → 개인정보 → 오염 → 근접 중복. 모두 규칙이다(빼기 · 가리기).
  (문)          응답 단계에 들어가기 전에 앞 단계의 검사를 다시 잰다. 심각이 남으면 막고 보고로 끝낸다.
  2 응답        응답 만들기(허락) → 도구 호출 형식(빼기) → 빈 응답(빼기) → 반복 루프(자르기) → 거절 · 상투구
                → 짧은 응답 → 지시 불일치 → 안전.
                응답 만들기는 응답이 없는 지시(지시만)에 LLM이 응답을 쓴다. 시험 몇 건과 비용 어림을 보이고 허락을 받는다.
                거절 · 짧은 응답은 Jev가 괜찮다고 본 것을 규칙으로 그대로 두고, 남은 것만 LLM이 본다.
                지시 불일치는 Jev가 확인한 것(따름 ≤ 0.2)과 의심(0.2~0.5 · 여러 턴 · 코드 응답)을 LLM이 대화마다
                '마지막 말에 맞게 답했나'로 판정한다(Jev 점수를 보이지 않는다): 맞음 그대로 · 다름 빼기 · 모름 보류.
                안전은 Jev가 해롭다고 본 응답을 LLM이 한 번 더 본다.
  (문)          선호 단계 앞.
  3 선호        비교 만들기(허락) → 같은 쌍 · 동점(쌍 빼기) → 판정 반대(LLM이 뒤집기 · 그대로 두기 · 쌍 빼기 · 보류).
                만들 데이터가 선호이거나 쌍이 있는 데이터셋만 이 단계가 있다. 비교 만들기는 LLM이 다른 응답을 쓰고
                Jev가 두 응답을 따로 본 점수로 고른 응답을 정한다(Jev가 없으면 건너뛴다).
  보고          처음과 끝의 진단을 견줘 고친 검사 · 바꿈 · 비용을 싣고 LLM이 두 줄로 말한다. 실행은 여기서 끝난다.
단계는 닿은 때 잰다(앞 단계의 고치기가 만든 문제도 잡는다. 예: 루프를 자르면 잘린 응답이 생긴다 → 남은 것).
뜻 분석이 없으면 뜻 검사는 '뜻 분석 전'으로 건너뛴다. 시작할 때 분석이 없거나 오래됐고 임베딩이 연결돼 있으면
분석 작업을 먼저 넣는다(작업 실행기가 넣은 순서대로 처리한다). 응답 만들기로 응답이 늘면, 뜻 분석에 기대는 단계
(지시 불일치 · 안전)에 닿았을 때 분석을 다시 만든 뒤 그 단계부터 이어 도는 작업을 넣는다(이어 도는 작업은 다시 기다리지 않는다).
허락에 답하면 같은 실행이 새 작업으로 그 단계부터 이어 돈다. 허락을 받은 일의 LLM 토큰은 실행의 비용에 더하지만
도우미 판단의 토큰 상한(MAX_RUN_TOKENS)에는 세지 않는다(허락 카드에서 비용을 보고 허락했다).
남은 것 · AI로 고치기(helper_fixes): 끝난 뒤 남은 것은 화면이 지금 진단으로 다시 센다. 사람이 고른 것만 같은 실행에
  단계로 붙여 고친다. LLM 다시 쓰기는 토큰이 드는 일이라 실행 안에서 하지 않고 여기서만 한다.
고치기는 바로 반영하고 결정 기록(generation_changes)에 실행 · 카드 번호와 함께 남긴다. 되돌리기는 카드 하나 또는
실행 전체이고, 사람의 검토 결정 기록에서도 한 건씩 되돌릴 수 있다. 영구 삭제는 없다.
"""

import json
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import conversations, helper_fixes, making, problems, rules, semantic
from dent.modules.generation import overview as overview_service
from dent.modules.generation import service as generation_service
from dent.modules.generation.helper_fixes import CHECK_NAMES, PAIRS_KEY, PAIRS_NAME, check_value
from dent.modules.generation.models import Action, Change, Goal, Pair, Prompt, Response, Role
from dent.modules.generation.rewriting import REWRITE_MAX_TOKENS, read_rewrite, rewrite_messages
from dent.modules.generation.rules import CHECKS, CheckKey
from dent.system import agent as agent_service
from dent.system import connections, jobs
from dent.system import datasets as datasets_service
from dent.system import helper as helper_service
from dent.system.agent import (
    GRADE_GOOD,
    LEFT_BLOCKED,
    PLAN_DECLINED,
    REPORT_KEY,
    STEP_ASK,
    STEP_DONE,
    STEP_RUNNING,
    STEP_SKIPPED,
    STEP_TODO,
    Agent,
    AgentBudgetExceeded,
    AgentStopped,
    OpenCheck,
    Tools,
    agent_tool,
    blocking,
    end_run,
    open_payload,
    report_step_row,
    seconds,
)
from dent.system.db import flush_or_conflict
from dent.system.exceptions import ConflictError, ExternalServiceError, InvalidInputError
from dent.system.llm import ChatMessage
from dent.system.models import (
    Connection,
    ConnectionRole,
    HelperEvent,
    HelperEventKind,
    HelperRun,
    HelperRunStatus,
)

HELPER_JOB_KIND = "helper"

LLM_NOT_CONNECTED_MESSAGE = "LLM이 연결되지 않았습니다. 연결 설정에서 LLM을 저장해 주세요."
ALREADY_RUNNING_MESSAGE = "도우미가 이미 일하는 중입니다."
NOT_FIXABLE_MESSAGE = "AI로 고칠 수 없는 것을 골랐습니다. 남은 것을 다시 읽어 주세요."
UNDO_WHILE_RUNNING_MESSAGE = "도우미가 일하는 중에는 되돌릴 수 없습니다. 멈춘 뒤 되돌려 주세요."
NOT_THIS_MODULE_MESSAGE = "생성 데이터셋의 도우미가 아닙니다."
NOT_ASKING_MESSAGE = "허락을 기다리는 도우미가 아닙니다."

# 실행 한 번에 쓰는 LLM 토큰 상한 (입력 + 출력). 닿으면 그 자리에서 멈춘다. 허락을 받은 만들기의 토큰은 세지 않는다.
MAX_RUN_TOKENS = 80_000

# 한 단계에서 LLM과 주고받는 최대 횟수 · LLM 답 한 번의 최대 토큰
MAX_TURNS_PER_STEP = 8
MAX_REPLY_TOKENS = 1_500

# LLM에 한 번에 보이는 대화 · 쌍 수 (규칙 · Jev 뒤에 남은 애매한 것만 본다)
REVIEW_LIMIT = 10

# LLM에 보이는 지시 · 응답의 앞부분 글자 수 (토큰 아끼기)
INSTRUCTION_PREVIEW = 200
RESPONSE_PREVIEW = 400

# 여러 턴 대화에서 마지막 지시 앞에 함께 보이는 턴 수와 턴마다 글자 수
EARLIER_TURNS = 4
EARLIER_PREVIEW = 160

# 앞 턴의 역할 낱말
ROLE_WORDS = {Role.USER: "사용자", Role.ASSISTANT: "응답"}

# 바꾼 카드에 보이는 보기 수 · 보기 글자 수
CHANGE_SAMPLE_COUNT = 3
SAMPLE_LENGTH = 60

# 다시 쓰기를 이만큼 할 때마다 카드 하나로 반영한다(멈춰도 앞의 것은 남게)
REWRITE_CHUNK = 5

# 응답 · 비교 만들기를 이만큼 할 때마다 카드 하나로 반영한다(LLM 요청은 making.MAKE_CONCURRENCY건씩 함께)
MAKE_CHUNK = 8

# 지시 불일치를 LLM이 대화마다 판정할 때 한 번에 보이는 수 · 한 번 실행에 보는 최대 수 · 답의 최대 토큰
VERDICT_BATCH = 12
MAX_VERDICTS = 60
VERDICT_MAX_TOKENS = 2_000

# 판정할 때 보이는 앞 턴 수 · 턴마다 글자 수 · 마지막 응답 글자 수
VERDICT_TURNS = 5
VERDICT_TURN_CHARS = 200
VERDICT_ANSWER_CHARS = 400

# 지시 불일치 판정 지시. Jev 점수와 '의심'이라는 말을 보이지 않는다: 보이면 LLM이 그쪽으로 끌려 맞는 여러 턴 답을
# 빼곤 했다(상담 대화 11건 중 6~8건). 중립적으로 물으면 같은 모델이 예시의 맞는 답 · 바꿔 단 답을 모두 맞혔다(19 / 19).
VERDICT_SYSTEM = """아래는 학습 데이터의 대화들이다. 대화마다 마지막 응답이 마지막 사용자 말에 알맞은 답인지 판단한다.
앞 턴의 흐름을 함께 읽는다(짧은 후속 말 · 번호만 준 말도 앞 턴을 이어 받는 말이다).
답은 JSON 목록만: [{"ref": "r1", "fits": "yes|no|unsure", "why": "한 줄"}]
- yes: 마지막 말에 맞게 답했다(완벽하지 않아도 대화로 자연스러우면 yes)
- no: 마지막 말과 다른 것을 답했거나 무시했다
- unsure: 판단할 근거가 모자라다"""

# 판정의 역할 낱말
VERDICT_ROLES = {Role.USER: "사용자", Role.ASSISTANT: "응답", Role.TOOL: "도구 결과"}

# 비교 만들기 한 건에 Jev에 묻는 수 (두 응답을 따로)
JEV_CALLS_PER_PAIR = 2

PLAN_KEY = "plan"

# 응답 만들기 단계의 열쇠 (응답 없음 검사)
ANSWER_KEY = CheckKey.NO_ANSWER.value

# 단계 줄의 '뜻 분석 기다림' · Jev가 없어 건너뜀 · 뜻 분석이 없어 건너뜀
PLAN_WAIT_ANALYSIS = "뜻 분석 기다림"
PLAN_NO_JEV = "Jev 미연결"
PLAN_NO_ANALYSIS = "뜻 분석 전"

# 흐름 단계: 1 지시 · 2 응답 · 3 선호
STAGE_TITLES = {1: "지시", 2: "응답", 3: "선호"}

# 바꾼 카드의 동작 이름 (보고의 '바꿈'이 동작마다 센다)
ACTION_NAMES = {
    Action.DROP: "대화 빼기",
    Action.KEEP: "그대로 두기",
    Action.MASK: "가리기",
    Action.CUT: "루프 자르기",
    Action.REWRITE: "다시 쓰기",
    Action.ANSWER: "응답 만들기",
    Action.PAIR: "비교 만들기",
}
PAIR_ACTION_NAMES = {
    Action.DROP: "쌍 빼기",
    Action.KEEP: "쌍 그대로 두기",
    Action.FLIP: "쌍 뒤집기",
}

SYSTEM_BASE = """너는 LLM 학습 데이터(대화) 도구 DENT의 도우미다. 진단에 나온 문제를 단계마다 고친다. 사람과 대화하지 않는다.
말하기: 도구를 부를 때마다 say에 스스로 묻고 답한 짧은 한국어를 쓴다. 물음은 한 줄로 '?'로 끝내고, 답은 한두 줄.
  존댓말 없이 짧게. 예: "이 거절은 마땅한가?\\n폭발물 제조를 묻는 요청이라 거절이 맞다. 그대로 둔다."
원칙:
- 규칙으로 되는 일은 규칙이 이미 했다. 너는 남은 애매한 것만 본다.
- 할 수 있는 일은 그 단계의 도구뿐이다(keep 문제 아님 · drop 학습에서 빼기(휴지통) · hold 사람에게 넘기기,
  선호 쌍은 flip 고른 응답과 버린 응답 바꾸기도). 영구 삭제는 없다. 응답을 다시 쓰지 않는다.
- 빼는 것은 학습 신호가 없거나 해로울 때만. 판단할 근거가 모자라면 hold로 넘긴다.
- 대화 · 쌍은 ref(r1, r2 …)로 가리킨다. 같은 도구를 되풀이하지 않는다. 단계를 다 하면 finish_step을 부른다."""


# ---------- 단계 ----------


@dataclass(frozen=True)
class StepDef:
    """고치는 단계 하나."""

    # 단계 열쇠 (검사 이름, 비교 만들기는 PAIRS_KEY)
    key: str

    # 흐름 단계: 1 지시 · 2 응답 · 3 선호
    stage: int

    # 규칙으로 하는 결정: drop · mask · cut · jev_keep(Jev가 괜찮다고 본 것 그대로 두기). 없으면 빈 글
    rule: str = ""

    # 규칙을 하기 전에 스스로 묻고 답하는 말
    rule_say: str = ""

    # 규칙 뒤 남은 것에 대한 LLM 지시. 빈 글이면 규칙만 하는 단계다(LLM을 부르지 않는다).
    goal: str = ""

    # 허락 단계인지 (응답 만들기 · 비교 만들기: 시험 · 비용을 보이고 기다린다)
    permission: bool = False

    # 단계 이름 (없으면 검사 이름)
    title: str = ""

    # 뜻 분석에 기대는 단계인지 (분석에 없는 새 응답이 있으면 분석을 다시 만든 뒤 돈다)
    needs_analysis: bool = False

    # LLM이 도구 대신 대화마다 '맞게 답했나'를 판정하는 단계인지 (지시 불일치)
    verdict: bool = False

    @property
    def check(self) -> CheckKey | None:
        """단계가 고치는 검사 (비교 만들기는 None)."""
        return CheckKey(self.key) if self.key in CheckKey._value2member_map_ else None

    @property
    def name(self) -> str:
        """단계 줄에 보일 이름."""
        return self.title or CHECK_NAMES[CheckKey(self.key)]


STEPS: tuple[StepDef, ...] = (
    StepDef(
        CheckKey.STRUCTURE, 1, "drop", "역할 순서가 틀린 대화가 있나?\n학습이 멈추는 모양이라 뺀다."
    ),
    StepDef(
        CheckKey.EMPTY_PROMPT, 1, "drop", "사용자 말이 빈 대화가 있나?\n학습 신호가 없어 뺀다."
    ),
    StepDef(
        CheckKey.DUPLICATE, 1, "drop", "같은 지시가 여럿인가?\n번호가 가장 작은 것만 남기고 뺀다."
    ),
    StepDef(CheckKey.PII, 1, "mask", "개인정보가 남았나?\n[이메일] · [전화번호]처럼 가린다."),
    StepDef(
        CheckKey.CONTAMINATION,
        1,
        "drop",
        "평가셋과 겹친 지시가 있나?\n학습에 들어가면 평가 점수가 거짓이 되므로 뺀다.",
    ),
    StepDef(
        CheckKey.NEAR_DUPLICATE,
        1,
        "drop",
        "뜻이 거의 같은 지시가 여럿인가?\n번호가 작은 것을 남기고 여분을 뺀다.",
    ),
    StepDef(ANSWER_KEY, 2, permission=True, title="응답 만들기"),
    StepDef(
        CheckKey.TOOL_CALL,
        2,
        "drop",
        "도구 호출이 깨진 대화가 있나?\n트레이너가 멈추거나 틀린 호출을 배우므로 뺀다.",
    ),
    StepDef(CheckKey.EMPTY_ANSWER, 2, "drop", "응답이 빈 대화가 있나?\n학습 신호가 없어 뺀다."),
    StepDef(
        CheckKey.LOOP,
        2,
        "cut",
        "같은 조각을 되풀이하는 응답이 있나?\n루프 앞에서 자른다. 잘려서 끝맺지 못한 것은 남은 것에서 다시 쓴다.",
    ),
    StepDef(
        CheckKey.REFUSAL,
        2,
        "jev_keep",
        "이 거절 · 상투구가 문제인가?\nJev가 지시를 따른 답으로 확인한 것은 그대로 둔다.",
        "규칙이 거절 · 상투구로 잡은 대화 가운데 Jev가 지시를 따른다고 확인하지 않은 것이 남았다. 해롭거나 할 수 없는 "
        "요청을 거절한 것이면 keep, 멀쩡한 요청을 거절했거나 'AI 언어 모델로서' 같은 상투구만 있으면 drop. "
        "Jev의 '마땅한 거절' 점수는 참고만 한다(멀쩡한 요청의 거절에도 높게 줄 때가 있다).",
    ),
    StepDef(
        CheckKey.SHORT,
        2,
        "jev_keep",
        "짧은 응답이 맞는 답인가?\nJev가 지시를 따른 답으로 본 것(분류 과제의 0 · 1 등)은 그대로 둔다.",
        "짧은 응답 가운데 Jev도 괜찮다고 하지 않은 것이 남았다. 지시가 짧은 답(예 · 아니오 · 번호 · 한 단어)을 "
        "바라면 keep, 답이 모자라거나 엉뚱하면 drop.",
    ),
    StepDef(
        CheckKey.MISMATCH,
        2,
        "",
        "응답이 지시를 따르지 않는다고 Jev가 본 대화가 맞게 답했나?\n"
        "Jev 점수를 보이지 않고 LLM이 대화마다 판정한다. 다르면 빼고, 맞으면 두고, 모르면 사람에게.",
        needs_analysis=True,
        verdict=True,
    ),
    StepDef(
        CheckKey.UNSAFE,
        2,
        goal="Jev가 응답이 해롭다고 본 대화다. 응답이 실제로 해로운 내용(폭력 · 무기 · 자해 · 범죄의 방법, 혐오, "
        "성적인 내용 등)을 담았으면 drop, 거절했거나 해롭지 않은 내용이면 keep(Jev 오판), 판단이 갈리면 hold.",
        needs_analysis=True,
    ),
    StepDef(PAIRS_KEY, 3, permission=True, title="비교 만들기"),
    StepDef(
        CheckKey.SAME_PAIR,
        3,
        "drop",
        "고른 응답과 버린 응답이 같거나 점수가 같은 쌍이 있나?\n배울 것이 없어 쌍을 뺀다.",
    ),
    StepDef(
        CheckKey.JUDGE_REVERSED,
        3,
        goal="Jev가 버린 응답이 지시를 더 잘 따른다고 본 쌍이다. 버린 응답이 실제로 더 나으면 flip(고른 · 버린 응답을 "
        "바꿈), 고른 응답이 맞으면 keep, 두 응답이 모두 나쁘거나 가를 수 없으면 drop(쌍 빼기), 판단이 갈리면 hold.",
    ),
)

STEP_BY_KEY = {step.key: step for step in STEPS}

# LLM이 판정하는 검사의 지시 (AI로 고치기의 판정 · 실행 안의 단계가 같이 쓴다)
JUDGE_GOALS: dict[CheckKey, str] = {
    **{CheckKey(step.key): step.goal for step in STEPS if step.goal},
    CheckKey.REPEAT: "같은 문장이 여러 번 나오는 응답이다. 인용 · 절차 · 표처럼 되풀이가 뜻이 있으면 keep, "
    "같은 말을 쓸데없이 되풀이하면 drop.",
    CheckKey.BROKEN: "깨진 글자(대체 문자 · 다른 문자)가 든 대화다. 예시 단어 · 외국어 인용이면 keep, "
    "글자가 깨져 읽을 수 없으면 drop.",
}


def initial_steps(*, with_pairs: bool) -> list[dict[str, Any]]:
    """실행을 시작할 때의 단계 줄: 계획 · 흐름 단계들(선호가 아니면 3 선호를 뺀다, 허락 단계는 permission 표시) · 보고."""
    rows: list[dict[str, Any]] = [{"key": PLAN_KEY, "no": 0, "title": "계획", "status": STEP_TODO}]
    steps = [step for step in STEPS if with_pairs or step.stage < 3]
    rows += [
        {
            "key": step.key,
            "no": index,
            "title": step.name + (" · 허락" if step.permission else ""),
            "status": STEP_TODO,
            "stage": step.stage,
            "stage_title": STAGE_TITLES[step.stage],
            "permission": step.permission,
        }
        for index, step in enumerate(steps, start=1)
    ]
    rows.append(report_step_row(len(steps) + 1))
    return rows


# ---------- 시작 · 허락 · 남은 것 · AI로 고치기 · 되돌리기 (API) ----------


async def start_helper(db: AsyncSession, *, dataset_id: int) -> HelperRun:
    """도우미를 시작한다. 뜻 분석이 없거나 오래됐고 임베딩이 연결돼 있으면 뜻 분석을 먼저 대기열에 넣는다.

    데이터셋이 없으면 NotFoundError, LLM이 없으면 InvalidInputError, 이미 도는 중 · 허락을 기다리는 중이면 ConflictError.
    """
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None or connection.model is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    latest = await helper_service.latest_run(
        db, module=generation_service.MODULE_NAME, dataset_id=dataset_id
    )
    if latest is not None and latest.status in (HelperRunStatus.RUNNING, HelperRunStatus.ASKING):
        raise ConflictError(ALREADY_RUNNING_MESSAGE)
    await _refresh_analysis_if_needed(db, dataset_id=dataset_id)
    run = helper_service.new_run(
        module=generation_service.MODULE_NAME,
        dataset_id=dataset_id,
        model=connection.model,
        steps=initial_steps(with_pairs=await _with_pairs(db, dataset_id=dataset_id)),
    )
    db.add(run)
    await flush_or_conflict(db, message=ALREADY_RUNNING_MESSAGE)
    job = await jobs.enqueue_job(
        db,
        module=generation_service.MODULE_NAME,
        kind=HELPER_JOB_KIND,
        params={"run_id": run.id},
        dataset_id=dataset_id,
    )
    run.job_id = job.id
    await db.commit()
    return await helper_service.get_run(db, run_id=run.id)


async def answer_permission(db: AsyncSession, *, run_id: int, approve: bool) -> HelperRun:
    """허락에 답한다. 같은 실행을 새 작업으로 그 단계부터 이어 돈다(허락이면 그 일을 하고, 아니면 건너뛴다).

    실행이 없으면 NotFoundError, 다른 모듈의 실행이거나 허락을 기다리는 중이 아니면 ConflictError.
    """
    run = await helper_service.get_run(db, run_id=run_id)
    if run.module != generation_service.MODULE_NAME:
        raise ConflictError(NOT_THIS_MODULE_MESSAGE)
    if run.status != HelperRunStatus.ASKING or not run.permission:
        raise ConflictError(NOT_ASKING_MESSAGE)
    step_key = str(run.permission.get("step"))
    job = await jobs.enqueue_job(
        db,
        module=generation_service.MODULE_NAME,
        kind=HELPER_JOB_KIND,
        params={"run_id": run_id, "resume": step_key, "approved": approve},
        dataset_id=run.dataset_id,
    )
    await helper_service.add_event(
        db,
        run_id=run_id,
        step=run.step_now,
        kind=HelperEventKind.NOTICE,
        payload={"text": ("허락 · " if approve else "건너뜀 · ") + STEP_BY_KEY[step_key].name},
    )
    await helper_service.resume_run(db, run_id=run_id, job_id=job.id)
    await db.commit()
    return await helper_service.get_run(db, run_id=run_id)


async def left_items(db: AsyncSession, *, dataset_id: int) -> list[agent_service.LeftItem]:
    """남은 것(지금 진단으로 센다). 도우미 실행이 없으면 빈 목록. 데이터셋이 없으면 NotFoundError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    latest = await helper_service.latest_run(
        db, module=generation_service.MODULE_NAME, dataset_id=dataset_id
    )
    if latest is None:
        return []
    return await helper_fixes.left_items(db, dataset_id=dataset_id)


async def start_fix(db: AsyncSession, *, dataset_id: int, keys: list[str]) -> HelperRun:
    """AI로 고치기를 시작한다(고른 검사를 흐름 순서로). LLM이 없거나 지금 남은 것 가운데 AI로 고칠 수 없는 것을
    고르면 InvalidInputError, 도는 중이면 ConflictError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None or connection.model is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    items = {item.key: item for item in await helper_fixes.left_items(db, dataset_id=dataset_id)}
    is_fixable = all(key in items and items[key].group != LEFT_BLOCKED for key in keys)
    if not is_fixable:
        raise InvalidInputError(NOT_FIXABLE_MESSAGE)
    return await agent_service.start_fix(
        db,
        module=generation_service.MODULE_NAME,
        dataset_id=dataset_id,
        job_kind=HELPER_JOB_KIND,
        model=connection.model,
        items=[(key, items[key].name) for key in helper_fixes.ordered(keys)],
    )


@dataclass(frozen=True)
class UndoResult:
    """되돌린 결과: 전 값으로 돌린 수 · 그 사이 바뀌어 건너뛴 수."""

    reverted: int
    skipped: int


async def undo_helper(db: AsyncSession, *, run_id: int, event_id: int | None) -> UndoResult:
    """도우미가 바꾼 것을 되돌린다(카드 하나 또는 실행 전체). 커밋한다.

    실행이 없으면 NotFoundError, 다른 모듈의 실행이거나 도는 중이면 ConflictError.
    """
    run = await helper_service.get_run(db, run_id=run_id)
    if run.module != generation_service.MODULE_NAME:
        raise ConflictError(NOT_THIS_MODULE_MESSAGE)
    if run.status == HelperRunStatus.RUNNING:
        raise ConflictError(UNDO_WHILE_RUNNING_MESSAGE)
    query = select(Change).where(Change.run_id == run_id, Change.undone_at.is_(None))
    if event_id is not None:
        query = query.where(Change.event_id == event_id)
    changes = list((await db.scalars(query)).all())
    reverted, skipped = await conversations.undo_many(db, changes=changes)
    if reverted:
        await helper_service.add_usage(db, run_id=run_id, changed=-reverted)
        await datasets_service.touch_dataset(db, dataset_id=run.dataset_id)
    await helper_service.add_event(
        db,
        run_id=run_id,
        step=run.step_now,
        kind=HelperEventKind.NOTICE,
        payload={
            "text": f"되돌림 {reverted}건" + (f" · 건너뜀 {skipped}건" if skipped else ""),
            "undo_of": event_id,
        },
    )
    await db.commit()
    return UndoResult(reverted=reverted, skipped=skipped)


async def undone_event_ids(db: AsyncSession, *, run_id: int) -> list[int]:
    """모든 줄을 되돌린 카드(사건) 번호들. 화면이 그 카드의 되돌리기를 끈다."""
    rows = await db.execute(
        select(Change.event_id)
        .where(Change.run_id == run_id, Change.event_id.is_not(None))
        .group_by(Change.event_id)
        .having(func.bool_and(Change.undone_at.is_not(None)))
    )
    return [row[0] for row in rows]


async def _with_pairs(db: AsyncSession, *, dataset_id: int) -> bool:
    """3 선호 단계를 둘지: 만들 데이터가 선호이거나 쌍이 있다."""
    settings = await generation_service.find_settings(db, dataset_id=dataset_id)
    if settings.goal == Goal.PREFERENCE.value:
        return True
    return await _count_query(db, problems.active_pairs(dataset_id)) > 0


async def _refresh_analysis_if_needed(db: AsyncSession, *, dataset_id: int) -> None:
    """뜻 분석이 없거나 오래됐으면 다시 만들게 한다(임베딩이 연결돼 있고 만드는 중이 아닐 때만).

    작업 실행기는 작업을 넣은 순서대로 하나씩 처리하므로, 도우미 작업은 뜻 분석이 끝난 뒤에 돈다.
    """
    if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
        return
    state = await semantic.get_analysis_state(db, dataset_id=dataset_id)
    is_building = state.run is not None and state.run.status in ("queued", "running")
    is_stale = state.done is None or state.outdated
    if is_stale and not is_building:
        try:
            await semantic.start_analysis(db, dataset_id=dataset_id)
        except (InvalidInputError, ConflictError):
            # 대화가 없거나 그 사이 누가 먼저 눌렀다. 도우미는 있는 분석으로 한다.
            await db.rollback()


# ---------- 실행 (작업 실행기) ----------


@dataclass
class StepState:
    """단계 하나를 도는 동안의 것: LLM에 보인 대화 · 쌍(ref → 번호)과 사실(바꿈 · 보류 · LLM)."""

    key: CheckKey
    refs: dict[str, int] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=lambda: defaultdict(int))


@dataclass(frozen=True)
class Trial:
    """허락 전 시험의 결과."""

    # 시험한 지시 수 · 넣을 수 있었던 수(모양 · Jev를 지남)
    tried: int
    passed: int

    # 쓴 LLM 토큰 · Jev 요청 수
    tokens: int
    jev_calls: int

    # 비교 만들기: 만든 응답이 고른 응답이 된 수 · 두 점수 차이가 작은 수
    other_won: int = 0
    small_margin: int = 0


class Helper(Agent):
    """생성 도우미 실행 한 번. 말 · 사건 · 멈추기 · 토큰 상한 · 도구 되풀이는 시스템의 Agent가 한다."""

    system_prompt = SYSTEM_BASE
    max_turns_per_step = MAX_TURNS_PER_STEP
    max_reply_tokens = MAX_REPLY_TOKENS

    @property
    def max_run_tokens(self) -> int:  # type: ignore[override]
        """토큰 상한. 모듈 상수를 읽을 때마다 본다(시험이 상한을 바꿔 볼 수 있게)."""
        return MAX_RUN_TOKENS

    def __init__(
        self,
        db: AsyncSession,
        *,
        run: HelperRun,
        llm_connection: Connection,
        jev_connection: Connection | None,
        transport: httpx.AsyncBaseTransport | None,
        jev_transport: httpx.AsyncBaseTransport | None,
        total: int,
    ) -> None:
        super().__init__(
            db,
            run=run,
            llm_connection=llm_connection,
            jev_connection=jev_connection,
            transport=transport,
            jev_transport=jev_transport,
        )
        # 휴지통 밖 대화 수 (단계 결과 등급의 분모, 실행을 시작할 때 잰다)
        self.total = total
        # 이 실행의 작업 번호 (만들기의 진행률을 적는다)
        self.job_id = run.job_id

    # ----- 한 번 실행 -----

    def _steps(self) -> list[StepDef]:
        """이 실행의 단계들 (단계 줄에 있는 것만, 흐름 순서)."""
        keys = {row["key"] for row in self.steps}
        return [step for step in STEPS if step.key in keys]

    def _row_no(self, key: str) -> int:
        return next(int(row["no"]) for row in self.steps if row["key"] == key)

    async def run(
        self,
        *,
        resume: str | None = None,
        approved: bool = False,
        continue_from: str | None = None,
        fix: list[str] | None = None,
        round_no: int = 0,
    ) -> None:
        """계획 → 단계들 → 보고. 허락 단계에 닿으면 asking으로 멈춘다. resume이면 허락에 답한 그 단계부터,
        continue_from이면 뜻 분석을 새로 만든 뒤 그 단계부터 이어 돈다. fix면 AI로 고치기 한 번(round_no)."""
        if fix is not None:
            fixers = {key: self._fixer(key) for key in fix if key in helper_fixes.FIX_SPECS}
            await self.run_fix(round_no=round_no, keys=fix, fixers=fixers)
            return
        steps = self._steps()
        # 뜻 분석을 기다린 뒤 이어 도는 작업이면 다시 기다리지 않는다(분석이 실패해도 되풀이하지 않게).
        waited = continue_from is not None
        if continue_from is not None:
            start = next(index for index, step in enumerate(steps) if step.key == continue_from)
        elif resume is None:
            await self._plan()
            start = 0
        else:
            start = next(index for index, step in enumerate(steps) if step.key == resume)
            step = steps[start]
            self.step_no = self._row_no(step.key)
            if approved:
                # 허락을 물은 뒤 데이터가 바뀌었을 수 있어(되돌리기 · 사람 고치기) 문을 다시 본다.
                earlier = await self._open_checks(before=step.stage)
                if blocking(earlier):
                    await self.block(step.key, earlier)
                    return
                await self._do_permitted(step)
            else:
                self.update_step(step.key, status=STEP_SKIPPED, plan=PLAN_DECLINED)
                await self.save_steps()
            start += 1
        for index in range(start, len(steps)):
            step = steps[index]
            is_stage_start = step.stage > 1 and (index == 0 or steps[index - 1].stage < step.stage)
            if is_stage_start:
                earlier = await self._open_checks(before=step.stage)
                if blocking(earlier):
                    # 문: 앞 단계에 심각이 남은 채 허락을 묻거나 다음 흐름으로 가지 않는다.
                    await self.block(step.key, earlier)
                    return
            # 뜻 분석에 기대는 단계(지시 불일치 · 안전)는 Jev 판정까지 있어야 재므로 Jev가 없으면 기다리지 않는다.
            if step.needs_analysis and not waited and self.jev_connection is not None:
                missing = await self._missing_from_analysis()
                if missing:
                    await self._wait_for_analysis(step, missing)
                    return
            if await self._run_step(step) == STEP_ASK:
                return
        # 보고로 끝낸다. 남은 것은 화면이 지금 진단으로 다시 세어 보이고, 사람이 고르면 AI로 고친다.
        await self.finish()

    # ----- 0 계획 -----

    async def _plan(self) -> None:
        """진단을 보이고 LLM이 무엇이 급한지 말하게 한다. 순서는 흐름(STEPS)을 따른다."""
        self.step_no = 0
        self.update_step(PLAN_KEY, status=STEP_RUNNING)
        await self.save_steps()
        started = time.monotonic()
        overview = await overview_service.get_overview(self.db, dataset_id=self.dataset_id)
        steps = self._steps()
        keys = {step.key for step in steps}
        lines = []
        for check in overview.checks:
            if check.grade == GRADE_GOOD:
                continue
            lines.append(
                f"- {CHECK_NAMES[check.key]} ({check.key}): {check_value(check)} · {check.grade}"
            )
            if check.key.value in keys:
                self.update_step(check.key.value, plan=check_value(check))
        if PAIRS_KEY in keys and overview.unpaired:
            lines.append(f"- {PAIRS_NAME}: {overview.unpaired:,}건 · 비교 만들기")
            self.update_step(PAIRS_KEY, plan=f"{overview.unpaired:,}건")
        waiting = [f"{CHECK_NAMES[item.key]}({item.reason})" for item in overview.waiting]
        await self.save_steps()
        await self.start_report(REPORT_KEY, from_step=0)
        order = " → ".join(
            f"{STAGE_TITLES[stage]}({' · '.join(s.name for s in steps if s.stage == stage)})"
            for stage in sorted({step.stage for step in steps})
        )
        brief = (
            f"대화 {overview.conversations:,}\n진단:\n"
            + ("\n".join(lines) if lines else "- 고칠 문제 없음")
            + (f"\n기다림: {', '.join(waiting)}" if waiting else "")
            + f"\nJev: {'연결됨' if self.jev_connection else '미연결'}"
            + f"\n흐름 순서대로 고친다: {order}."
            + "\n판단이 필요 없는 고치기는 규칙이 먼저 하고, 너는 남은 애매한 것만 본다. 응답 만들기 · 비교 만들기는 "
            "시험과 비용을 보이고 사람의 허락을 받는다. 응답 다시 쓰기는 끝난 뒤 사람이 골라서 한다."
            + "\nplan 도구로 한 번, 무엇이 급한지 say로 말한다."
        )
        tool = agent_tool("plan", "고칠 계획을 말한다.", {}, [])
        reply = await self.chat(
            [
                ChatMessage(role="system", content=SYSTEM_BASE),
                ChatMessage(role="user", content=brief),
            ],
            [tool],
        )
        said: set[str] = set()
        await self.say_once(reply.content, said=said)
        for call in reply.tool_calls:
            await self.say_once(str(call.arguments.get("say") or ""), said=said)
        self.update_step(
            PLAN_KEY,
            status=STEP_DONE,
            facts={"llm": reply.input_tokens + reply.output_tokens, "time": seconds(started)},
        )
        await self.save_steps()

    # ----- 단계 하나 -----

    async def _run_step(self, step: StepDef) -> str:
        """단계 하나. 닿은 때 재서 문제가 없으면 건너뛴다. 허락 단계면 시험 · 비용을 보이고 STEP_ASK를 돌려준다.
        규칙을 먼저 하고, 남은 것이 있고 LLM 몫이 있으면 LLM이 finish_step을 부를 때까지 판단 도구를 부르게 한다."""
        key = step.key
        self.step_no = self._row_no(key)
        await self.check_stop()
        skip_reason = await self._skip_reason(step)
        if skip_reason:
            self.update_step(key, status=STEP_SKIPPED, plan=skip_reason)
            await self.save_steps()
            return STEP_SKIPPED
        before = await self._step_count(step)
        if before == 0:
            self.update_step(key, status=STEP_SKIPPED, plan="문제 없음")
            await self.save_steps()
            return STEP_SKIPPED
        if step.permission:
            await self._ask_permission(step, before)
            return STEP_ASK
        check = CheckKey(step.key)
        state = StepState(key=check)
        self.update_step(key, status=STEP_RUNNING, plan=f"{before:,}건")
        await self.save_steps()
        started = time.monotonic()
        if step.rule:
            await self.say(step.rule_say)
            state.facts["rule"] += await self._apply_rule(
                check, step.rule, tool=f"rule:{step.rule}"
            )
        if step.goal and await self._step_count(step) > 0:
            state.facts["llm"] += await self._judge(state, goal=step.goal)
        if step.verdict:
            if not step.rule:
                await self.say(step.rule_say)
            state.facts["llm"] += await self._judge_fits(state)
        await self._finish_step(step, state, before, started)
        return STEP_DONE

    async def _skip_reason(self, step: StepDef) -> str:
        """단계를 잴 수 없는 까닭(비교 만들기에 Jev가 없음 · 뜻 분석 전). 잴 수 있으면 빈 글."""
        if step.key == PAIRS_KEY and self.jev_connection is None:
            return PLAN_NO_JEV
        if step.check is not None and not await self._can_measure(step.check):
            return PLAN_NO_ANALYSIS
        return ""

    async def _finish_step(
        self, step: StepDef, state: StepState, before: int, started: float
    ) -> None:
        now = await self._step_count(step)
        # 지시 불일치는 의심(진단에 세지 않음)까지 보이고, 등급은 진단처럼 확인된 것으로 매긴다.
        graded = await self._count(CheckKey.MISMATCH) if step.key == CheckKey.MISMATCH else now
        grade_to = self._grade(step, graded)
        await self.emit(
            HelperEventKind.RESULT,
            {"check": step.name, "from": f"{before:,}건", "to": f"{now:,}건", "grade": grade_to},
        )
        facts = {name: value for name, value in state.facts.items() if value}
        facts["time"] = seconds(started)
        self.update_step(
            step.key,
            status=STEP_DONE,
            facts=facts,
            delta=[f"{before:,}건", f"{now:,}건", grade_to],
        )
        await self.save_steps()

    async def _apply_rule(self, check: CheckKey, rule: str, *, tool: str) -> int:
        """규칙 하나: 빼기 · 가리기 · 자르기 · Jev가 괜찮다고 본 것 그대로 두기. 쌍 검사면 쌍에. 바꾼 수."""
        if rule == "jev_keep":
            analysis = await problems.done_analysis(self.db, dataset_id=self.dataset_id)
            if analysis is None or self.jev_connection is None:
                return 0
            query = problems.jev_fine_ids(self.dataset_id, analysis.id, check)
            ids = list((await self.db.scalars(query)).all())
            return await self._record(check, Action.KEEP, ids, tool=tool)
        ids = await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=check)
        if CHECKS[check].unit == "pair":
            return await self._record_pairs(check, Action(rule), ids, tool=tool)
        return await self._record(check, Action(rule), ids, tool=tool)

    # ----- LLM 판정 -----

    async def _judge(self, state: StepState, *, goal: str) -> int:
        """규칙 뒤 남은 대화(쌍 검사면 쌍)를 LLM이 판정한다(한 번에 REVIEW_LIMIT건). 쓴 토큰."""
        if CHECKS[state.key].unit == "pair":
            brief = await self._pair_brief(state)
        else:
            brief = await self._conversation_brief(state)
        return await self.tool_loop(
            system=f"{SYSTEM_BASE}\n\n지금 단계: {CHECK_NAMES[state.key]} ({state.key})\n할 일: {goal}",
            brief=brief,
            tools=self._judge_tools(state),
        )

    async def _judge_fits(self, state: StepState) -> int:
        """지시 불일치(확인 · 의심)를 LLM이 대화마다 '마지막 말에 맞게 답했나'로 판정한다(VERDICT_BATCH건씩).

        맞음은 그대로 두고, 다름은 빼고, 모름 · 답을 읽지 못한 것은 사람에게 넘긴다. 쓴 토큰.
        """
        ids = (await self._candidates(state.key))[:MAX_VERDICTS]
        used = 0
        for start in range(0, len(ids), VERDICT_BATCH):
            await self.check_stop()
            items = await conversations.items_of(
                self.db, dataset_id=self.dataset_id, ids=ids[start : start + VERDICT_BATCH]
            )
            items = [item for item in items if item.answer is not None]
            if not items:
                continue
            refs = {f"r{index}": item for index, item in enumerate(items, start=1)}
            reply = await self.chat(
                [
                    ChatMessage(role="system", content=VERDICT_SYSTEM),
                    ChatMessage(
                        role="user",
                        content="\n\n".join(_verdict_line(ref, item) for ref, item in refs.items()),
                    ),
                ],
                [],
                max_tokens=VERDICT_MAX_TOKENS,
            )
            used += reply.input_tokens + reply.output_tokens
            verdicts = _read_verdicts(reply.content)
            fits = [
                refs[ref].prompt.id
                for ref, (fit, _why) in verdicts.items()
                if ref in refs and fit == "yes"
            ]
            off = {ref: why for ref, (fit, why) in verdicts.items() if ref in refs and fit == "no"}
            held = len(items) - len(fits) - len(off)
            await self.say(
                f"대화 {len(items)}건이 마지막 말에 맞게 답했나?\n"
                f"맞음 {len(fits)} · 다름 {len(off)} · 모름 {held}"
            )
            state.facts["changed"] += await self._record(
                state.key, Action.KEEP, fits, tool="llm:fits"
            )
            state.facts["changed"] += await self._record(
                state.key,
                Action.DROP,
                [refs[ref].prompt.id for ref in off],
                tool="llm:fits",
                samples=[f"{_sample(refs[ref])} · {why}" for ref, why in off.items()],
            )
            if held:
                await helper_service.add_usage(self.db, run_id=self.run_id, held=held)
                await self.db.commit()
                state.facts["held"] += held
        return used

    async def _conversation_brief(self, state: StepState) -> str:
        """LLM에 보일 남은 대화들 (ref를 매긴다)."""
        ids = await self._candidates(state.key)
        items = await conversations.items_of(
            self.db, dataset_id=self.dataset_id, ids=ids[:REVIEW_LIMIT]
        )
        confirmed = set(
            await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=state.key)
        )
        lines = [f"남은 문제: {CHECK_NAMES[state.key]} · {len(ids):,}건", "보기:"]
        for index, item in enumerate(items, start=1):
            ref = f"r{index}"
            state.refs[ref] = item.prompt.id
            lines.append(_conversation_line(ref, item, suspect=item.prompt.id not in confirmed))
        if len(ids) > len(items):
            lines.append(f"(보이지 않은 {len(ids) - len(items):,}건은 사람이 본다)")
        return "\n".join(lines)

    async def _pair_brief(self, state: StepState) -> str:
        """LLM에 보일 남은 쌍들: 지시 · 고른 응답 · 버린 응답 · Jev 따름 점수 (ref를 매긴다)."""
        ids = await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=state.key)
        shown = ids[:REVIEW_LIMIT]
        pairs = list(
            (await self.db.scalars(select(Pair).where(Pair.id.in_(shown)).order_by(Pair.id))).all()
        )
        answer_ids = [pair.chosen_id for pair in pairs] + [pair.rejected_id for pair in pairs]
        answers = {
            answer.id: answer.content
            for answer in (
                await self.db.scalars(select(Response).where(Response.id.in_(answer_ids)))
            ).all()
        }
        contexts = await making.contexts(self.db, ids=[pair.prompt_id for pair in pairs])
        lines = [f"남은 문제: {CHECK_NAMES[state.key]} · {len(ids):,}쌍", "보기:"]
        for index, pair in enumerate(pairs, start=1):
            ref = f"r{index}"
            state.refs[ref] = pair.id
            instruction = _last_user(contexts.get(pair.prompt_id, []))[:INSTRUCTION_PREVIEW]
            chosen = answers.get(pair.chosen_id, "")[:RESPONSE_PREVIEW]
            rejected = answers.get(pair.rejected_id, "")[:RESPONSE_PREVIEW]
            lines.append(
                f'{ref} 지시 "{instruction}"\n   고른 응답 "{chosen}"\n   버린 응답 "{rejected}"'
                f"\n   Jev 따름 고른 {pair.judge_chosen or 0:.2f} · 버린 {pair.judge_rejected or 0:.2f}"
            )
        if len(ids) > len(pairs):
            lines.append(f"(보이지 않은 {len(ids) - len(pairs):,}쌍은 사람이 본다)")
        return "\n".join(lines)

    async def _candidates(self, check: CheckKey) -> list[int]:
        """LLM이 볼 대화: 남은 문제, 지시 불일치면 Jev가 애매하게 본 의심도(확인된 것이 앞)."""
        ids = await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=check)
        if check != CheckKey.MISMATCH:
            return ids
        analysis = await problems.done_analysis(self.db, dataset_id=self.dataset_id)
        if analysis is None:
            return ids
        confirmed = set(ids)
        suspects = (
            await self.db.scalars(problems.mismatch_suspect_ids(self.dataset_id, analysis.id))
        ).all()
        return ids + [i for i in suspects if i not in confirmed]

    def _judge_tools(self, state: StepState) -> Tools:
        """판단 도구: keep · drop · hold (쌍이면 flip도) · finish_step."""
        is_pair = CHECKS[state.key].unit == "pair"
        refs = {
            "refs": {"type": "array", "items": {"type": "string"}, "description": "r1 같은 ref들"}
        }
        # 빼기는 데이터를 바꾸는 판단이라 한 건씩 까닭과 함께 부르게 한다(여러 건을 한 까닭으로 묶어 빼던 일을 막는다).
        one_ref = {
            "ref": {"type": "string", "description": "r1 같은 ref 하나"},
            "reason": {"type": "string", "description": "이 대화를 빼는 까닭 한 줄"},
        }

        def handler(action: Action | None) -> agent_service.ToolHandler:
            async def handle(arguments: dict[str, Any], _tokens: int) -> dict[str, Any]:
                # drop은 한 번에 하나(ref)라 refs가 없으면 ref를 쓴다.
                asked = arguments.get("refs") or [arguments.get("ref")]
                ids = [state.refs[ref] for ref in asked if ref in state.refs]
                if action is None:
                    await helper_service.add_usage(self.db, run_id=self.run_id, held=len(ids))
                    await self.db.commit()
                    state.facts["held"] += len(ids)
                    return {"보류": len(ids)}
                if is_pair:
                    allowed = set(
                        await conversations.open_ids(
                            self.db, dataset_id=self.dataset_id, check=state.key
                        )
                    )
                    changed = await self._record_pairs(
                        state.key,
                        action,
                        [i for i in ids if i in allowed],
                        tool=f"llm:{action.value}",
                    )
                else:
                    allowed = set(await self._candidates(state.key))
                    changed = await self._record(
                        state.key,
                        action,
                        [i for i in ids if i in allowed],
                        tool=f"llm:{action.value}",
                    )
                state.facts["changed"] += changed
                return {"바꿈": changed, "남은 수": await self._count(state.key)}

            return handle

        async def finish(_arguments: dict[str, Any], _tokens: int) -> dict[str, Any]:
            return {"끝": True}

        what = "쌍을" if is_pair else "대화를"
        tools: Tools = {
            "keep": (
                agent_tool(
                    "keep", "문제 아님: 그대로 둔다(이 검사로 다시 묻지 않는다).", refs, ["refs"]
                ),
                handler(Action.KEEP),
            ),
            "drop": (
                agent_tool(
                    "drop",
                    f"{what} 학습에서 뺀다(되돌릴 수 있다). 한 번에 하나만, 왜 빼는지 그 대화에 맞는 까닭과 함께.",
                    one_ref,
                    ["ref", "reason"],
                ),
                handler(Action.DROP),
            ),
            "hold": (
                agent_tool("hold", "판단이 갈려 사람에게 넘긴다(바꾸지 않는다).", refs, ["refs"]),
                handler(None),
            ),
            agent_service.FINISH_STEP_TOOL: (
                agent_tool(agent_service.FINISH_STEP_TOOL, "이 단계를 끝낸다.", {}, []),
                finish,
            ),
        }
        if is_pair:
            tools["flip"] = (
                agent_tool(
                    "flip", "고른 응답과 버린 응답을 바꾼다(버린 응답이 더 낫다).", refs, ["refs"]
                ),
                handler(Action.FLIP),
            )
        return tools

    # ----- 허락 -----

    async def _ask_permission(self, step: StepDef, count: int) -> None:
        """허락 단계: 시험으로 몇 건 만들어 보고(넣지 않음) 계획 · 비용을 보인 뒤 실행을 asking으로 둔다."""
        self.update_step(step.key, status=STEP_ASK, plan=f"{count:,}건")
        await self.save_steps()
        model = self.llm_connection.model
        trial = await self._trial(step)
        each = trial.tokens / trial.tried if trial.tried else making.TOKENS_EACH_ESTIMATE
        doing = min(count, making.MAX_PROMPTS_PER_RUN)
        estimate = round(each * doing)
        this_run = f" · 이번에 {doing:,}" if count > doing else ""
        if step.key == ANSWER_KEY:
            with_tools = await self._count(CheckKey.NO_ANSWER) - count
            await self.say(
                f"응답이 없는 지시에 응답을 만들까?\n지시 {count:,}개. LLM이 쓰고 모양이 걸리면 한 번 더 쓴다. "
                "출처는 합성으로 가려 둔다."
            )
            facts = [
                [
                    "대상",
                    f"응답 없는 지시 {count:,}{this_run}"
                    + (f" · 도구 대화 {with_tools:,} 뺌" if with_tools > 0 else ""),
                ],
                ["만들기", "지시마다 응답 하나 · 출처 합성"],
                ["거르기", "빈 · 잘린 응답 · 루프 · 언어 섞임 · 번역된 코드 → 한 번 더"],
                ["시험", f"지시 {trial.tried} → 통과 {trial.passed} / {trial.tried}"],
                ["비용", f"약 {estimate:,}토큰 · {model}"],
                ["되돌리기", "카드마다 · 실행 전체"],
            ]
        else:
            await self.say(
                f"쌍이 없는 지시에 비교를 만들까?\n지시 {count:,}개. LLM이 다른 응답을 하나 더 쓰고, "
                "Jev가 두 응답을 따로 본 점수로 고른 응답을 정한다."
            )
            facts = [
                ["대상", f"쌍 없는 지시 {count:,}{this_run}"],
                ["만들기", "다른 구성의 응답 하나 · 출처 합성"],
                ["고르기", "Jev 따름이 높은 쪽이 고른 응답 · 응답마다 따로 물음"],
                [
                    "시험",
                    (
                        f"지시 {trial.tried} → 쌍 {trial.passed} · 만든 응답이 고름 {trial.other_won}"
                        f" · 작은 마진 {trial.small_margin}"
                    ),
                ],
                [
                    "비용",
                    f"약 {estimate:,}토큰 · Jev {doing * JEV_CALLS_PER_PAIR:,}번 · {model}",
                ],
                ["되돌리기", "카드마다 · 실행 전체"],
            ]
        settings = await generation_service.find_settings(self.db, dataset_id=self.dataset_id)
        summary = (
            f"{'선호' if settings.goal == Goal.PREFERENCE.value else 'SFT'} · "
            f"{settings.tokenizer_model or '글자 수 어림'} · 최대 {settings.max_tokens:,}토큰"
        )
        # 앞 단계에 남은 주의(심각은 문이 이미 막았다)를 함께 싣는다.
        checks = open_payload(await self._open_checks(before=step.stage))
        permission: dict[str, Any] = {
            "step": step.key,
            "title": step.name,
            "facts": facts,
            "checks": checks,
            "settings": summary,
        }
        await self.emit(HelperEventKind.PERMISSION, permission)
        await helper_service.finish_run(
            self.db, run_id=self.run_id, status=HelperRunStatus.ASKING, permission=permission
        )
        await self.db.commit()

    async def _trial(self, step: StepDef) -> Trial:
        """허락 전 시험: 앞 TRIAL_PROMPTS건으로 만들어 본다(넣지 않는다). 쓴 토큰은 실행의 비용에 더한다."""
        if step.key == ANSWER_KEY:
            ids = await making.answer_target_ids(
                self.db, dataset_id=self.dataset_id, limit=making.TRIAL_PROMPTS
            )
            made = await self._write(ids)
            trial = Trial(
                tried=len(made),
                passed=sum(1 for item in made if item.text is not None),
                tokens=sum(item.tokens for item in made),
                jev_calls=0,
            )
        else:
            ids = await making.pair_target_ids(
                self.db, dataset_id=self.dataset_id, limit=making.TRIAL_PROMPTS
            )
            made = await self._compare(ids)
            judged = [item for item in made if item.text is not None]
            trial = Trial(
                tried=len(made),
                passed=len(judged),
                tokens=sum(item.tokens for item in made),
                jev_calls=_jev_calls(made),
                other_won=sum(1 for item in judged if _other_wins(item)),
                small_margin=sum(1 for item in judged if _is_small_margin(item)),
            )
        await helper_service.add_usage(
            self.db, run_id=self.run_id, llm_tokens=trial.tokens, jev_calls=trial.jev_calls
        )
        await self.db.commit()
        return trial

    async def _do_permitted(self, step: StepDef) -> None:
        """허락받은 일: 응답 만들기 또는 비교 만들기."""
        self.update_step(step.key, status=STEP_RUNNING)
        await self.save_steps()
        before = await self._step_count(step)
        started = time.monotonic()
        state = StepState(key=step.check or CheckKey.NO_ANSWER)
        if step.key == ANSWER_KEY:
            outcome = await self._make_answers(limit=making.MAX_PROMPTS_PER_RUN)
        else:
            outcome = await self._make_pairs(limit=making.MAX_PROMPTS_PER_RUN)
        state.facts.update(_outcome_facts(outcome))
        await self._finish_step(step, state, before, started)

    # ----- 만들기 (허락 단계 · AI로 고치기) -----

    async def _write(self, ids: list[int]) -> list[making.Made]:
        """지시들에 응답을 쓴다(넣지 않는다)."""
        found = await making.contexts(self.db, ids=ids)
        return await making.write_answers(
            self.llm_connection,
            [(i, found[i], None) for i in ids if i in found],
            transport=self.transport,
        )

    async def _compare(self, ids: list[int]) -> list[making.Made]:
        """지시들에 견줄 응답을 쓰고 Jev가 두 응답을 따로 본 점수를 붙인다(넣지 않는다)."""
        if self.jev_connection is None:
            return []
        found = await making.contexts(self.db, ids=ids)
        mains = await making.main_answers(self.db, ids=ids)
        targets = [i for i in ids if i in found and i in mains]
        made = await making.write_answers(
            self.llm_connection,
            [(i, found[i], mains[i].content) for i in targets],
            transport=self.transport,
        )
        return await making.judge_pairs(
            self.jev_connection,
            [(found[item.prompt_id], mains[item.prompt_id].content, item) for item in made],
            transport=self.jev_transport,
        )

    async def _make_answers(self, *, limit: int) -> making.Outcome:
        """응답 없는 지시에 응답을 만들어 넣는다(MAKE_CHUNK건마다 카드 하나). 멈추면 앞의 것은 남는다."""
        ids = await making.answer_target_ids(self.db, dataset_id=self.dataset_id, limit=limit)
        return await self._make(ids, self._write, self._record_answers)

    async def _make_pairs(self, *, limit: int) -> making.Outcome:
        """쌍 없는 지시에 비교를 만들어 쌍을 넣는다(MAKE_CHUNK건마다 카드 하나). Jev가 없으면 하지 않는다."""
        if self.jev_connection is None:
            return making.Outcome()
        ids = await making.pair_target_ids(self.db, dataset_id=self.dataset_id, limit=limit)
        return await self._make(ids, self._compare, self._record_made_pairs)

    async def _make(
        self,
        ids: list[int],
        produce: Callable[[list[int]], Awaitable[list[making.Made]]],
        record: Callable[[list[making.Made], making.Outcome], Awaitable[None]],
    ) -> making.Outcome:
        """MAKE_CHUNK건씩 만들고(produce) 넣는다(record). 쓴 토큰 · Jev 수를 실행에 더하고 진행률을 적는다."""
        outcome = making.Outcome()
        for start in range(0, len(ids), MAKE_CHUNK):
            await self.check_stop()
            made = await produce(ids[start : start + MAKE_CHUNK])
            tokens = sum(item.tokens for item in made)
            jev_calls = _jev_calls(made) if record == self._record_made_pairs else 0
            outcome.tokens += tokens
            outcome.jev_calls += jev_calls
            outcome.short += sum(1 for item in made if item.reason == making.SHAPE_FAILED)
            outcome.failed += sum(1 for item in made if item.reason == making.JEV_FAILED)
            await record(made, outcome)
            await helper_service.add_usage(
                self.db, run_id=self.run_id, llm_tokens=tokens, jev_calls=jev_calls
            )
            if self.job_id is not None:
                done = min(start + MAKE_CHUNK, len(ids))
                await jobs.set_progress(self.db, job_id=self.job_id, done=done, total=len(ids))
            await self.db.commit()
        if outcome.short or outcome.failed:
            await self.notice(
                f"만듦 {outcome.made:,}"
                + (f" · 모양이 걸려 뺌 {outcome.short:,}" if outcome.short else "")
                + (f" · Jev 실패 {outcome.failed:,}" if outcome.failed else ""),
                tone="warn",
            )
        return outcome

    async def _record_answers(self, made: list[making.Made], outcome: making.Outcome) -> None:
        """만든 응답들을 넣고 카드 하나를 적는다. 커밋한다."""
        done = [item for item in made if item.text is not None]
        if not done:
            return
        items = {
            item.prompt.id: item
            for item in await conversations.items_of(
                self.db, dataset_id=self.dataset_id, ids=[i.prompt_id for i in done]
            )
        }
        event = await self._change_event(
            Action.ANSWER, len(done), tool="llm:answer", samples=[i.text or "" for i in done]
        )
        added = 0
        for item in done:
            found = items.get(item.prompt_id)
            # 만드는 사이에 사람이 응답을 넣었거나 대화를 뺐으면 넣지 않는다.
            if found is None or found.answer is not None or found.prompt.trashed_at is not None:
                continue
            await conversations.apply_text(
                self.db,
                item=found,
                check=CheckKey.NO_ANSWER,
                action=Action.ANSWER,
                rewritten=item.text,
                run_id=self.run_id,
                event_id=event.id,
            )
            added += 1
            outcome.samples.append((item.text or "")[:SAMPLE_LENGTH])
        outcome.made += added
        await self._finish_change(added)

    async def _record_made_pairs(self, made: list[making.Made], outcome: making.Outcome) -> None:
        """만든 비교들로 쌍을 넣고 카드 하나를 적는다. 커밋한다."""
        done = [item for item in made if item.text is not None]
        if not done:
            return
        ids = [item.prompt_id for item in done]
        prompts = {
            prompt.id: prompt
            for prompt in (await self.db.scalars(select(Prompt).where(Prompt.id.in_(ids)))).all()
        }
        mains = await making.main_answers(self.db, ids=ids)
        event = await self._change_event(
            Action.PAIR, len(done), tool="llm+jev:pair", samples=[i.text or "" for i in done]
        )
        added = 0
        for item in done:
            prompt, main = prompts.get(item.prompt_id), mains.get(item.prompt_id)
            if prompt is None or main is None or prompt.trashed_at is not None:
                continue
            await conversations.add_pair(
                self.db,
                prompt=prompt,
                main=main,
                content=item.text or "",
                main_follows=item.main_follows or 0.0,
                other_follows=item.other_follows or 0.0,
                run_id=self.run_id,
                event_id=event.id,
            )
            added += 1
            outcome.other_won += _other_wins(item)
            outcome.small_margin += _is_small_margin(item)
        outcome.made += added
        await self._finish_change(added)

    # ----- 뜻 분석 새로 만들기 -----

    async def _missing_from_analysis(self) -> int:
        """다 만든 뜻 분석보다 뒤에 만든 주 응답 수 (응답 만들기로 늘어난 것). 임베딩이 없거나 분석이 없으면 0."""
        if await connections.get_connection(self.db, role=ConnectionRole.EMBEDDING) is None:
            return 0
        analysis = await problems.done_analysis(self.db, dataset_id=self.dataset_id)
        if analysis is None:
            return 0
        return await _count_query(
            self.db,
            select(Response.id)
            .join(Prompt, Prompt.id == Response.prompt_id)
            .where(
                Response.dataset_id == self.dataset_id,
                Response.is_main,
                generation_service.IS_ACTIVE,
                Response.created_at > analysis.created_at,
            ),
        )

    async def _wait_for_analysis(self, step: StepDef, missing: int) -> None:
        """뜻 분석 작업을 넣고, 그 뒤에 step부터 이어 도는 작업을 넣는다. 실행은 도는 중으로 둔다."""
        self.step_no = self._row_no(step.key)
        self.update_step(step.key, status=STEP_RUNNING, plan=PLAN_WAIT_ANALYSIS)
        await self.save_steps()
        await self.say(
            f"새 응답 {missing:,}개가 뜻 분석에 없나?\n분석을 다시 만든 뒤 {step.name}부터 이어 간다."
        )
        try:
            await semantic.start_analysis(self.db, dataset_id=self.dataset_id)
        except (ConflictError, InvalidInputError):
            # 이미 만드는 중이다. 그 작업이 먼저 들어가 있으므로 이어 도는 작업보다 먼저 끝난다.
            await self.db.rollback()
        job = await jobs.enqueue_job(
            self.db,
            module=generation_service.MODULE_NAME,
            kind=HELPER_JOB_KIND,
            params={"run_id": self.run_id, "continue_from": step.key},
            dataset_id=self.dataset_id,
        )
        await helper_service.resume_run(self.db, run_id=self.run_id, job_id=job.id)
        await self.db.commit()

    # ----- AI로 고치기 -----

    def _fixer(self, key: str) -> agent_service.Fixer:
        spec = helper_fixes.FIX_SPECS[key]

        async def fix() -> dict[str, Any]:
            if spec.how == "answer":
                outcome = await self._make_answers(limit=helper_fixes.MAX_MAKES_PER_FIX)
                return _outcome_facts(outcome)
            if spec.how == "compare":
                outcome = await self._make_pairs(limit=helper_fixes.MAX_MAKES_PER_FIX)
                return _outcome_facts(outcome)
            check = CheckKey(key)
            if spec.how in ("drop", "mask", "cut"):
                return {"rule": await self._apply_rule(check, spec.how, tool=f"fix:{spec.how}")}
            if spec.how == "judge":
                facts: dict[str, Any] = defaultdict(int)
                if check in (CheckKey.REFUSAL, CheckKey.SHORT):
                    facts["rule"] += await self._apply_rule(check, "jev_keep", tool="fix:jev_keep")
                if await self._count(check):
                    state = StepState(key=check)
                    facts["llm"] += await self._judge(state, goal=JUDGE_GOALS[check])
                    facts.update({k: v for k, v in state.facts.items() if v})
                return dict(facts)
            if spec.how == "rewrite":
                return await self._rewrite(check)
            return {}

        return fix

    async def _rewrite(self, check: CheckKey) -> dict[str, Any]:
        """LLM으로 응답을 다시 쓴다(최대 MAX_REWRITES_PER_FIX건, REWRITE_CHUNK건마다 카드 하나)."""
        await self.say(
            f"{CHECK_NAMES[check]} 응답을 어떻게 고칠까?\n"
            f"LLM이 다시 쓰고 출처를 합성으로 둔다. 한 번에 {helper_fixes.MAX_REWRITES_PER_FIX}건까지."
        )
        ids = await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=check)
        candidates = await conversations.items_of(self.db, dataset_id=self.dataset_id, ids=ids)
        # 다시 쓰기는 마지막 응답만 바꾼다. 앞 턴의 응답에 문제가 있는 대화는 풀지 못해 건너뛴다.
        targets = [
            item.prompt.id for item in candidates if conversations.is_rewritable(item, check)
        ]
        earlier = len(ids) - len(targets)
        if earlier:
            await self.say(
                f"앞 턴의 응답에 문제가 있는 대화는?\n{earlier}건은 다시 써도 풀리지 않아 남긴다."
            )
        targets = targets[: helper_fixes.MAX_REWRITES_PER_FIX]
        used = 0
        rewritten = 0
        for start in range(0, len(targets), REWRITE_CHUNK):
            await self.check_stop()
            items = await conversations.items_of(
                self.db, dataset_id=self.dataset_id, ids=targets[start : start + REWRITE_CHUNK]
            )
            texts: dict[int, str] = {}
            for item in items:
                if item.answer is None:
                    continue
                reply = await self.chat(
                    rewrite_messages(item.prompt.messages, item.answer.content, check),
                    [],
                    max_tokens=REWRITE_MAX_TOKENS,
                )
                used += reply.input_tokens + reply.output_tokens
                try:
                    texts[item.prompt.id] = read_rewrite(reply.content)
                except ExternalServiceError:
                    continue
            rewritten += await self._record_rewrites(check, items, texts)
        return {"changed": rewritten, "llm": used, "earlier": earlier}

    # ----- 바꾼 카드 -----

    async def _change_event(
        self, action: Action, count: int, *, tool: str, samples: list[str], is_pair: bool = False
    ) -> HelperEvent:
        """바꾼 카드 하나를 적는다(커밋은 부른 쪽이 한다)."""
        names = PAIR_ACTION_NAMES if is_pair else ACTION_NAMES
        return await helper_service.add_event(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            kind=HelperEventKind.CHANGE,
            payload={
                "action": names[action],
                "count": count,
                "tool": tool,
                "samples": [sample[:SAMPLE_LENGTH] for sample in samples[:CHANGE_SAMPLE_COUNT]],
            },
        )

    async def _record(
        self,
        check: CheckKey,
        action: Action,
        ids: list[int],
        *,
        tool: str,
        samples: list[str] | None = None,
    ) -> int:
        """빼기 · 그대로 두기 · 가리기 · 자르기를 반영하고 바꾼 카드 하나를 적는다(보기는 samples, 없으면 지시 앞부분).
        바꾼 수. 커밋한다."""
        if not ids:
            return 0
        items = await conversations.items_of(self.db, dataset_id=self.dataset_id, ids=ids)
        event = await self._change_event(
            action,
            len(ids),
            tool=tool,
            samples=samples if samples is not None else [_sample(item) for item in items],
        )
        if action in (Action.DROP, Action.KEEP):
            await conversations.apply_bulk(
                self.db,
                dataset_id=self.dataset_id,
                check=check,
                action=action,
                ids=ids,
                run_id=self.run_id,
                event_id=event.id,
            )
        else:
            for item in items:
                await conversations.apply_text(
                    self.db,
                    item=item,
                    check=check,
                    action=action,
                    run_id=self.run_id,
                    event_id=event.id,
                )
        return await self._finish_change(len(ids))

    async def _record_pairs(
        self, check: CheckKey, action: Action, ids: list[int], *, tool: str
    ) -> int:
        """쌍들에 빼기 · 뒤집기 · 그대로 두기를 반영하고 바꾼 카드 하나를 적는다. 바꾼 수. 커밋한다."""
        if not ids:
            return 0
        shown = await self.db.scalars(
            select(Prompt.messages)
            .join(Pair, Pair.prompt_id == Prompt.id)
            .where(Pair.id.in_(ids[:CHANGE_SAMPLE_COUNT]))
        )
        event = await self._change_event(
            action,
            len(ids),
            tool=tool,
            samples=[_last_user(messages) or "(빈 지시)" for messages in shown],
            is_pair=True,
        )
        await conversations.apply_pair_bulk(
            self.db,
            dataset_id=self.dataset_id,
            check=check,
            action=action,
            ids=ids,
            run_id=self.run_id,
            event_id=event.id,
        )
        return await self._finish_change(len(ids))

    async def _record_rewrites(
        self, check: CheckKey, items: list[conversations.ConversationItem], texts: dict[int, str]
    ) -> int:
        """다시 쓴 응답들을 반영하고 카드 하나를 적는다. 바꾼 수. 커밋한다."""
        done = [item for item in items if item.prompt.id in texts]
        if not done:
            return 0
        event = await self._change_event(
            Action.REWRITE,
            len(done),
            tool="llm:rewrite",
            samples=[texts[item.prompt.id] for item in done],
        )
        for item in done:
            await conversations.apply_text(
                self.db,
                item=item,
                check=check,
                action=Action.REWRITE,
                rewritten=texts[item.prompt.id],
                run_id=self.run_id,
                event_id=event.id,
            )
        return await self._finish_change(len(done))

    async def _finish_change(self, changed: int) -> int:
        """바꾼 수를 실행에 더하고 데이터셋 수정 시각을 바꾼 뒤 커밋한다(화면이 바로 보게)."""
        await helper_service.add_usage(self.db, run_id=self.run_id, changed=changed)
        await datasets_service.touch_dataset(self.db, dataset_id=self.dataset_id)
        await self.db.commit()
        return changed

    # ----- 재기 -----

    async def snapshot(self) -> dict[str, Any]:
        """지금 진단: 잰 검사마다 {key, name, value, grade}. 선호면 쌍 없음(비교 만들기의 할 일)도."""
        overview = await overview_service.get_overview(self.db, dataset_id=self.dataset_id)
        checks = [
            {
                "key": check.key.value,
                "name": CHECK_NAMES[check.key],
                "value": check_value(check),
                "grade": check.grade,
            }
            for check in overview.checks
        ]
        if overview.goal == Goal.PREFERENCE:
            checks.append(
                {
                    "key": PAIRS_KEY,
                    "name": PAIRS_NAME,
                    "value": f"{overview.unpaired:,}건",
                    "grade": "warn" if overview.unpaired else GRADE_GOOD,
                }
            )
        kpi = [{"name": "대화", "value": f"{overview.conversations:,}"}]
        if overview.pairs or overview.goal == Goal.PREFERENCE:
            kpi.append({"name": "쌍", "value": f"{overview.pairs:,}"})
        return {"checks": checks, "kpi": kpi}

    async def _open_checks(self, *, before: int) -> list[OpenCheck]:
        """before 단계보다 앞 흐름 단계들에서 통과하지 못한 검사 (문 · 막힘 · 허락 카드).

        사람이 허락을 거절해 건너뛴 단계의 검사(응답 만들기를 건너뛰면 응답 없음)는 세지 않는다.
        """
        declined = {
            row["key"]
            for row in self.steps
            if row.get("status") == STEP_SKIPPED and row.get("plan") == PLAN_DECLINED
        }
        overview = await overview_service.get_overview(self.db, dataset_id=self.dataset_id)
        return [
            OpenCheck(name=CHECK_NAMES[check.key], value=check_value(check), grade=check.grade)
            for check in overview.checks
            if check.stage < before
            and check.grade != GRADE_GOOD
            and check.key.value not in declined
        ]

    async def _can_measure(self, check: CheckKey) -> bool:
        """뜻 검사면 다 만든 뜻 분석이 있어야 잰다(Jev 검사는 Jev 판정도, 안전은 안전 판정도).

        쌍 검사(판정 반대)는 쌍에 적힌 Jev 판정으로 잰다(가져온 쌍은 뜻 분석이, 만든 쌍은 비교 만들기가 적는다).
        """
        needs = CHECKS[check].needs
        if needs in ("rule", "eval") or CHECKS[check].unit == "pair":
            return True
        analysis = await problems.done_analysis(self.db, dataset_id=self.dataset_id)
        if analysis is None:
            return False
        if needs == "embedding":
            return True
        if (analysis.checks.get("jev") or {}).get("status") != "judged":
            return False
        if check == CheckKey.UNSAFE:
            return bool((analysis.checks.get("safety") or {}).get("judged"))
        return True

    async def _step_count(self, step: StepDef) -> int:
        """단계에 닿았을 때 할 일의 수: 응답 · 비교 만들기는 대상 수, 지시 불일치는 의심까지, 나머지는 남은 문제 수."""
        if step.key == ANSWER_KEY:
            return await _count_query(self.db, problems.answer_targets(self.dataset_id))
        if step.key == PAIRS_KEY:
            return await _count_query(self.db, problems.pair_targets(self.dataset_id))
        check = CheckKey(step.key)
        if check == CheckKey.MISMATCH:
            return len(await self._candidates(check))
        return await self._count(check)

    async def _count(self, check: CheckKey) -> int:
        return len(await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=check))

    def _grade(self, step: StepDef, count: int) -> str:
        if step.permission:
            return "warn" if count else GRADE_GOOD
        check = CheckKey(step.key)
        return rules.grade_of(check, count=count, total=max(count, self.total))


async def _count_query(db: AsyncSession, query: Any) -> int:
    """번호 쿼리의 줄 수."""
    return int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)


def _jev_calls(made: list[making.Made]) -> int:
    """비교 만들기에서 Jev에 물은 수 (모양이 걸려 묻지 않은 것은 뺀다)."""
    return JEV_CALLS_PER_PAIR * sum(1 for item in made if item.reason != making.SHAPE_FAILED)


def _other_wins(item: making.Made) -> bool:
    """만든 응답이 고른 응답이 되는지 (Jev 따름이 주 응답보다 높다)."""
    return (item.other_follows or 0) > (item.main_follows or 0)


def _is_small_margin(item: making.Made) -> bool:
    """두 응답의 Jev 따름 점수 차이가 작은지 (진단의 '작은 마진'과 같은 경계)."""
    return abs((item.other_follows or 0) - (item.main_follows or 0)) < problems.SMALL_MARGIN


def _outcome_facts(outcome: making.Outcome) -> dict[str, Any]:
    """단계 줄의 사실: 넣은 수 · LLM 토큰 · Jev 수 · 넣지 못한 수."""
    return {
        "changed": outcome.made,
        "llm": outcome.tokens,
        "jev": outcome.jev_calls,
        "short": outcome.short + outcome.failed,
    }


def _last_user(messages: list[dict[str, Any]]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == Role.USER), "")


def _sample(item: conversations.ConversationItem) -> str:
    """바꾼 카드의 보기: 마지막 사용자 말 앞부분."""
    return _last_user(item.prompt.messages)[:SAMPLE_LENGTH] or "(빈 지시)"


def _verdict_line(ref: str, item: conversations.ConversationItem) -> str:
    """판정에 보일 대화 한 건: 최근 턴들(역할: 글)과 마지막 응답. Jev 점수는 보이지 않는다."""
    turns = [m for m in item.prompt.messages if m["role"] in VERDICT_ROLES][-VERDICT_TURNS:]
    body = "\n".join(
        f"   {VERDICT_ROLES[m['role']]}: {m['content'][:VERDICT_TURN_CHARS]}" for m in turns
    )
    answer = item.answer.content if item.answer else ""
    return f"{ref}\n{body}\n   마지막 응답: {answer[:VERDICT_ANSWER_CHARS]}"


def _read_verdicts(reply: str) -> dict[str, tuple[str, str]]:
    """LLM 판정 답(JSON 목록)을 {ref: (fits, why)}로. 읽을 수 없으면 빈 사전(모두 사람에게)."""
    start, end = reply.find("["), reply.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        rows = json.loads(reply[start : end + 1])
    except json.JSONDecodeError:
        return {}
    verdicts: dict[str, tuple[str, str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get("ref"), str):
            verdicts[row["ref"]] = (str(row.get("fits", "")).lower(), str(row.get("why", "")))
    return verdicts


def _conversation_line(ref: str, item: conversations.ConversationItem, *, suspect: bool) -> str:
    """LLM에 보일 대화 한 건: 앞 턴(여러 턴이면 최근 몇 개를 순서대로) · 마지막 지시 · 응답 앞부분 · Jev 점수 · 의심 표시."""
    judgment = item.judgment
    jev = ""
    if judgment is not None:
        if judgment.follows is not None:
            jev += f" · Jev 따름 {judgment.follows:.2f}"
        if judgment.refusal_ok is not None:
            jev += f" · Jev 마땅한 거절 {judgment.refusal_ok:.2f}"
        if judgment.unsafe is not None:
            jev += f" · Jev 해로움 {judgment.unsafe:.2f}"
    context = [m for m in item.prompt.messages if m["role"] in (Role.USER, Role.ASSISTANT)]
    last_user = next((m["content"] for m in reversed(context) if m["role"] == Role.USER), "")
    # 마지막 지시 앞의 턴들을 순서대로 (앞 턴을 잇는 말을 LLM이 읽을 수 있게)
    before = context[:-1][-EARLIER_TURNS:] if context else []
    earlier = "".join(
        f'\n   앞 {ROLE_WORDS[m["role"]]} "{m["content"][:EARLIER_PREVIEW]}"' for m in before
    )
    instruction = last_user[:INSTRUCTION_PREVIEW]
    answer = (item.answer.content if item.answer else "(응답 없음)")[:RESPONSE_PREVIEW]
    tag = " · 의심" if suspect else ""
    head = f"{ref} 대화" + (f" · 앞 턴 {len(before)}" if before else "")
    return f'{head}{earlier}\n   지시 "{instruction}"\n   응답 "{answer}"{jev}{tag}'


async def run_helper(
    db: AsyncSession,
    *,
    run_id: int,
    resume: str | None = None,
    approved: bool = False,
    continue_from: str | None = None,
    fix: list[str] | None = None,
    round_no: int = 0,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """실행 한 번(또는 허락 뒤 · 분석 뒤 이어 돌기 · AI로 고치기 한 번)을 끝까지 돈다.

    멈춤 · 상한 · LLM 실패는 실행 상태와 알림으로 남긴다.
    """
    run = await helper_service.get_run(db, run_id=run_id)
    if run.status != HelperRunStatus.RUNNING:
        return
    try:
        llm_connection = await connections.get_connection(db, role=ConnectionRole.LLM)
        if llm_connection is None or llm_connection.model is None:
            raise ExternalServiceError(LLM_NOT_CONNECTED_MESSAGE)
        jev_connection: Connection | None = await connections.get_connection(
            db, role=ConnectionRole.JEV
        )
        helper = Helper(
            db,
            run=run,
            llm_connection=llm_connection,
            jev_connection=jev_connection,
            transport=transport,
            jev_transport=jev_transport,
            total=await _active_count(db, dataset_id=run.dataset_id),
        )
        await helper.run(
            resume=resume,
            approved=approved,
            continue_from=continue_from,
            fix=fix,
            round_no=round_no,
        )
    except AgentStopped:
        await end_run(db, run_id=run_id, status=HelperRunStatus.STOPPED, message="멈춤")
    except AgentBudgetExceeded:
        await end_run(
            db,
            run_id=run_id,
            status=HelperRunStatus.STOPPED,
            message=f"LLM 토큰 상한 {MAX_RUN_TOKENS:,}에 닿아 멈춤",
        )
    except ExternalServiceError as error:
        await end_run(db, run_id=run_id, status=HelperRunStatus.FAILED, message=error.message)


async def fail_run(db: AsyncSession, *, run_id: int, message: str) -> None:
    """실행을 실패로 끝낸다(뜻밖의 오류 · 너무 여러 번 끊김)."""
    await end_run(db, run_id=run_id, status=HelperRunStatus.FAILED, message=message)


async def _active_count(db: AsyncSession, *, dataset_id: int) -> int:
    """휴지통 밖 대화 수 (등급의 분모)."""
    overview = await overview_service.get_overview(db, dataset_id=dataset_id)
    return overview.conversations
