"""LLM 도우미 (생성): 진단을 읽고, 스스로 묻고 답하며, 규칙 · Jev · LLM으로 대화 데이터를 고친다. 채팅이 아니다.

한 번 실행 (작업 ("generation", "helper"))
  0 계획        진단 요약을 LLM에 보이고 무엇이 급한지 말하게 한다(도구 plan). 순서는 흐름을 따른다.
  1 지시        대화 구조 → 빈 지시 → 같은 지시 → 개인정보 → 근접 중복. 모두 규칙이다(빼기 · 가리기).
  (문)          응답 단계에 들어가기 전에 지시 단계의 검사를 다시 잰다. 심각이 남으면 막고 보고로 끝낸다.
  2 응답        빈 응답(빼기) → 반복 루프(자르기) → 거절 · 상투구 → 짧은 응답 → 지시 불일치.
                거절 · 짧은 응답은 Jev가 괜찮다고 본 것을 규칙으로 그대로 두고, 남은 것만 LLM이 본다.
                지시 불일치는 Jev가 확인한 것이라 LLM이 빼기 · 그대로 두기 · 보류를 고른다.
  3 보고        처음과 끝의 진단을 견줘 고친 검사 · 바꿈 · 비용을 싣고 LLM이 두 줄로 말한다. 실행은 여기서 끝난다.
단계는 닿은 때 잰다(앞 단계의 고치기가 만든 문제도 잡는다. 예: 루프를 자르면 잘린 응답이 생긴다 → 남은 것).
뜻 분석이 없으면 근접 중복 · 지시 불일치는 '뜻 분석 전'으로 건너뛴다. 시작할 때 분석이 없거나 오래됐고 임베딩이
연결돼 있으면 분석 작업을 먼저 넣는다(작업 실행기가 넣은 순서대로 처리한다). 이 도우미는 대화를 새로 만들지 않으므로
분석을 다시 기다리는 일(이어 돌기)이 없다.
남은 것 · AI로 고치기(helper_fixes): 끝난 뒤 남은 것은 화면이 지금 진단으로 다시 센다. 사람이 고른 것만 같은 실행에
  단계로 붙여 고친다. LLM 다시 쓰기(잘린 · 빈 응답 · 번역된 코드 · 언어 섞임 · 지시 불일치)는 토큰이 드는 일이라
  실행 안에서 하지 않고 여기서만 한다. 다시 쓴 응답은 출처가 synthetic이다.
고치기는 바로 반영하고 결정 기록(generation_changes)에 실행 · 카드 번호와 함께 남긴다. 되돌리기는 카드 하나 또는
실행 전체이고, 사람의 검토 결정 기록에서도 한 건씩 되돌릴 수 있다. 영구 삭제는 없다.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import conversations, helper_fixes, problems, rules, semantic
from dent.modules.generation import overview as overview_service
from dent.modules.generation import service as generation_service
from dent.modules.generation.helper_fixes import CHECK_NAMES, check_value
from dent.modules.generation.models import Action, Change, Role
from dent.modules.generation.rewriting import REWRITE_MAX_TOKENS, read_rewrite, rewrite_messages
from dent.modules.generation.rules import CHECKS, CheckKey
from dent.system import agent as agent_service
from dent.system import connections, jobs
from dent.system import datasets as datasets_service
from dent.system import helper as helper_service
from dent.system.agent import (
    GRADE_GOOD,
    LEFT_BLOCKED,
    REPORT_KEY,
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
    end_run,
    report_step_row,
    seconds,
)
from dent.system.db import flush_or_conflict
from dent.system.exceptions import ConflictError, ExternalServiceError, InvalidInputError
from dent.system.llm import ChatMessage
from dent.system.models import (
    Connection,
    ConnectionRole,
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

# 실행 한 번에 쓰는 LLM 토큰 상한 (입력 + 출력). 닿으면 그 자리에서 멈춘다.
MAX_RUN_TOKENS = 80_000

# 한 단계에서 LLM과 주고받는 최대 횟수 · LLM 답 한 번의 최대 토큰
MAX_TURNS_PER_STEP = 8
MAX_REPLY_TOKENS = 1_500

# LLM에 한 번에 보이는 대화 수 (규칙 · Jev 뒤에 남은 애매한 것만 본다)
REVIEW_LIMIT = 10

# LLM에 보이는 지시 · 응답의 앞부분 글자 수 (토큰 아끼기)
INSTRUCTION_PREVIEW = 200
RESPONSE_PREVIEW = 400

# 바꾼 카드에 보이는 보기 수 · 보기 글자 수
CHANGE_SAMPLE_COUNT = 3
SAMPLE_LENGTH = 60

# 다시 쓰기를 이만큼 할 때마다 카드 하나로 반영한다(멈춰도 앞의 것은 남게)
REWRITE_CHUNK = 5

PLAN_KEY = "plan"

# 흐름 단계: 1 지시 · 2 응답
STAGE_TITLES = {1: "지시", 2: "응답"}

# 바꾼 카드의 동작 이름 (보고의 '바꿈'이 동작마다 센다)
ACTION_NAMES = {
    Action.DROP: "대화 빼기",
    Action.KEEP: "그대로 두기",
    Action.MASK: "가리기",
    Action.CUT: "루프 자르기",
    Action.REWRITE: "다시 쓰기",
}

SYSTEM_BASE = """너는 LLM 학습 데이터(대화) 도구 DENT의 도우미다. 진단에 나온 문제를 단계마다 고친다. 사람과 대화하지 않는다.
말하기: 도구를 부를 때마다 say에 스스로 묻고 답한 짧은 한국어를 쓴다. 물음은 한 줄로 '?'로 끝내고, 답은 한두 줄.
  존댓말 없이 짧게. 예: "이 거절은 마땅한가?\\n폭발물 제조를 묻는 요청이라 거절이 맞다. 그대로 둔다."
원칙:
- 규칙으로 되는 일은 규칙이 이미 했다. 너는 남은 애매한 대화만 본다.
- 할 수 있는 일은 keep(문제 아님, 그대로 둔다) · drop(대화를 학습에서 뺀다, 휴지통) · hold(사람에게 넘긴다)뿐이다.
  영구 삭제는 없다. 응답을 다시 쓰지 않는다.
- 대화를 빼는 것은 학습 신호가 없거나 해로울 때만. 판단할 근거가 모자라면 hold로 넘긴다.
- 대화는 ref(r1, r2 …)로 가리킨다. 같은 도구를 되풀이하지 않는다. 단계를 다 하면 finish_step을 부른다."""


# ---------- 단계 ----------


@dataclass(frozen=True)
class StepDef:
    """고치는 단계 하나."""

    # 단계 열쇠 (검사 이름과 같다)
    key: CheckKey

    # 흐름 단계: 1 지시 · 2 응답
    stage: int

    # 규칙으로 하는 결정: drop · mask · cut · jev_keep(Jev가 괜찮다고 본 것 그대로 두기). 없으면 빈 글
    rule: str

    # 규칙을 하기 전에 스스로 묻고 답하는 말
    rule_say: str = ""

    # 규칙 뒤 남은 것에 대한 LLM 지시. 빈 글이면 규칙만 하는 단계다(LLM을 부르지 않는다).
    goal: str = ""


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
        CheckKey.NEAR_DUPLICATE,
        1,
        "drop",
        "뜻이 거의 같은 지시가 여럿인가?\n번호가 작은 것을 남기고 여분을 뺀다.",
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
        "이 거절 · 상투구가 문제인가?\nJev가 마땅한 거절이나 지시를 따른 답으로 본 것은 그대로 둔다.",
        "규칙이 거절 · 상투구로 잡은 대화 가운데 Jev도 괜찮다고 하지 않은 것이 남았다. 해롭거나 할 수 없는 요청을 "
        "거절한 것이면 keep, 멀쩡한 요청을 거절했거나 'AI 언어 모델로서' 같은 상투구만 있으면 drop.",
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
        "",
        "Jev가 지시를 따르지 않는다고 확인한 대화다. Jev가 틀렸으면 keep, 지시와 다른 것을 답했으면 drop. "
        "앞 턴을 이어 묻는 질문처럼 판단이 갈리면 hold.",
    ),
)

STEP_BY_KEY = {step.key: step for step in STEPS}

# LLM이 판정하는 검사의 지시 (AI로 고치기의 판정 · 실행 안의 단계가 같이 쓴다)
JUDGE_GOALS: dict[CheckKey, str] = {
    **{step.key: step.goal for step in STEPS if step.goal},
    CheckKey.REPEAT: "같은 문장이 여러 번 나오는 응답이다. 인용 · 절차 · 표처럼 되풀이가 뜻이 있으면 keep, "
    "같은 말을 쓸데없이 되풀이하면 drop.",
    CheckKey.BROKEN: "깨진 글자(대체 문자 · 다른 문자)가 든 대화다. 예시 단어 · 외국어 인용이면 keep, "
    "글자가 깨져 읽을 수 없으면 drop.",
}


def initial_steps() -> list[dict[str, Any]]:
    """실행을 시작할 때의 단계 줄: 계획 · 흐름 두 단계의 열 단계 · 보고."""
    rows: list[dict[str, Any]] = [{"key": PLAN_KEY, "no": 0, "title": "계획", "status": STEP_TODO}]
    rows += [
        {
            "key": step.key.value,
            "no": index,
            "title": CHECK_NAMES[step.key],
            "status": STEP_TODO,
            "stage": step.stage,
            "stage_title": STAGE_TITLES[step.stage],
        }
        for index, step in enumerate(STEPS, start=1)
    ]
    rows.append(report_step_row(len(STEPS) + 1))
    return rows


# ---------- 시작 · 남은 것 · AI로 고치기 · 되돌리기 (API) ----------


async def start_helper(db: AsyncSession, *, dataset_id: int) -> HelperRun:
    """도우미를 시작한다. 뜻 분석이 없거나 오래됐고 임베딩이 연결돼 있으면 뜻 분석을 먼저 대기열에 넣는다.

    데이터셋이 없으면 NotFoundError, LLM이 없으면 InvalidInputError, 이미 도는 중이면 ConflictError.
    """
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None or connection.model is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    latest = await helper_service.latest_run(
        db, module=generation_service.MODULE_NAME, dataset_id=dataset_id
    )
    if latest is not None and latest.status == HelperRunStatus.RUNNING:
        raise ConflictError(ALREADY_RUNNING_MESSAGE)
    await _refresh_analysis_if_needed(db, dataset_id=dataset_id)
    run = helper_service.new_run(
        module=generation_service.MODULE_NAME,
        dataset_id=dataset_id,
        model=connection.model,
        steps=initial_steps(),
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
    """되돌린 결과: 전 값으로 돌린 대화 수 · 그 사이 바뀌어 건너뛴 수."""

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
    """단계 하나를 도는 동안의 것: LLM에 보인 대화(ref → 번호)와 사실(바꿈 · 보류 · LLM)."""

    key: CheckKey
    refs: dict[str, int] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=lambda: defaultdict(int))


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

    # ----- 한 번 실행 -----

    async def run(self, *, fix: list[str] | None = None, round_no: int = 0) -> None:
        """계획 → 지시 단계 → 문 → 응답 단계 → 보고. fix면 AI로 고치기 한 번(round_no)."""
        if fix is not None:
            fixers = {
                key: self._fixer(CheckKey(key))
                for key in fix
                if key in CheckKey and CheckKey(key) in helper_fixes.FIX_SPECS
            }
            await self.run_fix(round_no=round_no, keys=fix, fixers=fixers)
            return
        await self._plan()
        for step in STEPS:
            is_stage_start = step.stage > 1 and step is next(
                s for s in STEPS if s.stage == step.stage
            )
            if is_stage_start:
                open_checks = await self._open_checks(stage=step.stage - 1)
                if agent_service.blocking(open_checks):
                    await self.block(step.key.value, open_checks)
                    return
            await self._run_step(step)
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
        lines = []
        for check in overview.checks:
            if check.grade == GRADE_GOOD:
                continue
            lines.append(
                f"- {CHECK_NAMES[check.key]} ({check.key}): {check_value(check)} · {check.grade}"
            )
            if check.key in STEP_BY_KEY:
                self.update_step(check.key.value, plan=check_value(check))
        waiting = [f"{CHECK_NAMES[item.key]}({item.reason})" for item in overview.waiting]
        await self.save_steps()
        await self.start_report(REPORT_KEY, from_step=0)
        brief = (
            f"대화 {overview.conversations:,}\n진단:\n"
            + ("\n".join(lines) if lines else "- 고칠 문제 없음")
            + (f"\n기다림: {', '.join(waiting)}" if waiting else "")
            + f"\nJev: {'연결됨' if self.jev_connection else '미연결'}"
            + "\n흐름 순서대로 고친다: 지시(구조 · 빈 지시 · 같은 지시 · 개인정보 · 근접 중복) → "
            "응답(빈 응답 · 루프 · 거절 · 짧은 응답 · 지시 불일치)."
            + "\n판단이 필요 없는 고치기는 규칙이 먼저 하고, 너는 남은 애매한 것만 본다. "
            "응답 다시 쓰기는 끝난 뒤 사람이 골라서 한다."
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

    # ----- 1 · 2 단계 -----

    async def _run_step(self, step: StepDef) -> None:
        """단계 하나. 닿은 때 재서 문제가 없으면 건너뛴다. 규칙을 먼저 하고, 남은 것이 있고 LLM 몫이 있으면
        LLM이 finish_step을 부를 때까지 판단 도구를 부르게 한다."""
        key = step.key.value
        self.step_no = next(row["no"] for row in self.steps if row["key"] == key)
        await self.check_stop()
        if not await self._can_measure(step.key):
            self.update_step(key, status=STEP_SKIPPED, plan="뜻 분석 전")
            await self.save_steps()
            return
        before = await self._count(step.key)
        if before == 0:
            self.update_step(key, status=STEP_SKIPPED, plan="문제 없음")
            await self.save_steps()
            return
        state = StepState(key=step.key)
        self.update_step(key, status=STEP_RUNNING, plan=f"{before:,}건")
        await self.save_steps()
        started = time.monotonic()
        if step.rule:
            await self.say(step.rule_say)
            state.facts["rule"] += await self._apply_rule(
                step.key, step.rule, tool=f"rule:{step.rule}"
            )
        now = await self._count(step.key)
        if now > 0 and step.goal:
            state.facts["llm"] += await self._judge(state, goal=step.goal)
            now = await self._count(step.key)
        grade_to = self._grade(step.key, now)
        await self.emit(
            HelperEventKind.RESULT,
            {
                "check": CHECK_NAMES[step.key],
                "from": f"{before:,}건",
                "to": f"{now:,}건",
                "grade": grade_to,
            },
        )
        facts = {name: value for name, value in state.facts.items() if value}
        facts["time"] = seconds(started)
        self.update_step(
            key,
            status=STEP_DONE,
            facts=facts,
            delta=[f"{before:,}건", f"{now:,}건", grade_to],
        )
        await self.save_steps()

    async def _apply_rule(self, check: CheckKey, rule: str, *, tool: str) -> int:
        """규칙 하나: 빼기 · 가리기 · 자르기 · Jev가 괜찮다고 본 것 그대로 두기. 바꾼 수."""
        if rule == "jev_keep":
            analysis = await problems.done_analysis(self.db, dataset_id=self.dataset_id)
            if analysis is None or self.jev_connection is None:
                return 0
            query = problems.jev_fine_ids(self.dataset_id, analysis.id, check)
            ids = list((await self.db.scalars(query)).all())
            return await self._record(check, Action.KEEP, ids, tool=tool)
        ids = await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=check)
        return await self._record(check, Action(rule), ids, tool=tool)

    async def _judge(self, state: StepState, *, goal: str) -> int:
        """규칙 뒤 남은 대화를 LLM이 keep · drop · hold로 판정한다(한 번에 REVIEW_LIMIT건). 쓴 토큰."""
        ids = await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=state.key)
        items = await conversations.items_of(
            self.db, dataset_id=self.dataset_id, ids=ids[:REVIEW_LIMIT]
        )
        lines = [f"남은 문제: {CHECK_NAMES[state.key]} · {len(ids):,}건", "보기:"]
        for index, item in enumerate(items, start=1):
            ref = f"r{index}"
            state.refs[ref] = item.prompt.id
            follows = item.judgment.follows if item.judgment else None
            jev = f" · Jev 따름 {follows:.2f}" if follows is not None else ""
            instruction = _last_user(item.prompt.messages)[:INSTRUCTION_PREVIEW]
            answer = item.answer.content[:RESPONSE_PREVIEW]
            lines.append(f'{ref} 지시 "{instruction}"\n   응답 "{answer}"{jev}')
        if len(ids) > len(items):
            lines.append(f"(보이지 않은 {len(ids) - len(items):,}건은 사람이 본다)")
        return await self.tool_loop(
            system=f"{SYSTEM_BASE}\n\n지금 단계: {CHECK_NAMES[state.key]} ({state.key})\n할 일: {goal}",
            brief="\n".join(lines),
            tools=self._judge_tools(state),
        )

    def _judge_tools(self, state: StepState) -> Tools:
        """판단 도구: keep · drop · hold · finish_step."""
        refs = {
            "refs": {"type": "array", "items": {"type": "string"}, "description": "r1 같은 ref들"}
        }

        def handler(action: Action | None) -> agent_service.ToolHandler:
            async def handle(arguments: dict[str, Any], _tokens: int) -> dict[str, Any]:
                ids = [state.refs[ref] for ref in arguments.get("refs", []) if ref in state.refs]
                if action is None:
                    await helper_service.add_usage(self.db, run_id=self.run_id, held=len(ids))
                    await self.db.commit()
                    state.facts["held"] += len(ids)
                    return {"보류": len(ids)}
                open_now = set(
                    await conversations.open_ids(
                        self.db, dataset_id=self.dataset_id, check=state.key
                    )
                )
                changed = await self._record(
                    state.key, action, [i for i in ids if i in open_now], tool=f"llm:{action.value}"
                )
                state.facts["changed"] += changed
                return {"바꿈": changed, "남은 수": await self._count(state.key)}

            return handle

        async def finish(_arguments: dict[str, Any], _tokens: int) -> dict[str, Any]:
            return {"끝": True}

        return {
            "keep": (
                agent_tool(
                    "keep", "문제 아님: 그대로 둔다(이 검사로 다시 묻지 않는다).", refs, ["refs"]
                ),
                handler(Action.KEEP),
            ),
            "drop": (
                agent_tool("drop", "대화를 학습에서 뺀다(휴지통, 되돌릴 수 있다).", refs, ["refs"]),
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

    # ----- AI로 고치기 -----

    def _fixer(self, check: CheckKey) -> agent_service.Fixer:
        spec = helper_fixes.FIX_SPECS[check]

        async def fix() -> dict[str, Any]:
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

    async def _record(self, check: CheckKey, action: Action, ids: list[int], *, tool: str) -> int:
        """빼기 · 그대로 두기 · 가리기 · 자르기를 반영하고 바꾼 카드 하나를 적는다. 바꾼 수. 커밋한다."""
        if not ids:
            return 0
        items = await conversations.items_of(self.db, dataset_id=self.dataset_id, ids=ids)
        event = await helper_service.add_event(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            kind=HelperEventKind.CHANGE,
            payload={
                "action": ACTION_NAMES[action],
                "count": len(ids),
                "tool": tool,
                "samples": [_sample(item) for item in items[:CHANGE_SAMPLE_COUNT]],
            },
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

    async def _record_rewrites(
        self, check: CheckKey, items: list[conversations.ConversationItem], texts: dict[int, str]
    ) -> int:
        """다시 쓴 응답들을 반영하고 카드 하나를 적는다. 바꾼 수. 커밋한다."""
        done = [item for item in items if item.prompt.id in texts]
        if not done:
            return 0
        event = await helper_service.add_event(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            kind=HelperEventKind.CHANGE,
            payload={
                "action": ACTION_NAMES[Action.REWRITE],
                "count": len(done),
                "tool": "llm:rewrite",
                "samples": [
                    texts[item.prompt.id][:SAMPLE_LENGTH] for item in done[:CHANGE_SAMPLE_COUNT]
                ],
            },
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
        """지금 진단: 잰 검사마다 {key, name, value, grade}."""
        overview = await overview_service.get_overview(self.db, dataset_id=self.dataset_id)
        return {
            "checks": [
                {
                    "key": check.key.value,
                    "name": CHECK_NAMES[check.key],
                    "value": check_value(check),
                    "grade": check.grade,
                }
                for check in overview.checks
            ],
            "kpi": [{"name": "대화", "value": f"{overview.conversations:,}"}],
        }

    async def _open_checks(self, *, stage: int) -> list[OpenCheck]:
        """한 흐름 단계에서 통과하지 못한 검사 (문 · 막힘 카드)."""
        overview = await overview_service.get_overview(self.db, dataset_id=self.dataset_id)
        return [
            OpenCheck(name=CHECK_NAMES[check.key], value=check_value(check), grade=check.grade)
            for check in overview.checks
            if check.stage == stage and check.grade != GRADE_GOOD
        ]

    async def _can_measure(self, check: CheckKey) -> bool:
        """뜻 검사면 다 만든 뜻 분석이 있어야 잰다(지시 불일치는 Jev 판정도)."""
        needs = CHECKS[check].needs
        if needs == "rule":
            return True
        analysis = await problems.done_analysis(self.db, dataset_id=self.dataset_id)
        if analysis is None:
            return False
        return needs != "jev" or (analysis.checks.get("jev") or {}).get("status") == "judged"

    async def _count(self, check: CheckKey) -> int:
        return len(await conversations.open_ids(self.db, dataset_id=self.dataset_id, check=check))

    def _grade(self, check: CheckKey, count: int) -> str:
        return rules.grade_of(check, count=count, total=max(count, self.total))


def _last_user(messages: list[dict[str, Any]]) -> str:
    return next((m["content"] for m in reversed(messages) if m["role"] == Role.USER), "")


def _sample(item: conversations.ConversationItem) -> str:
    """바꾼 카드의 보기: 마지막 사용자 말 앞부분."""
    return _last_user(item.prompt.messages)[:SAMPLE_LENGTH] or "(빈 지시)"


async def run_helper(
    db: AsyncSession,
    *,
    run_id: int,
    fix: list[str] | None = None,
    round_no: int = 0,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """실행 한 번(또는 AI로 고치기 한 번)을 끝까지 돈다. 멈춤 · 상한 · LLM 실패는 실행 상태와 알림으로 남긴다."""
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
        await helper.run(fix=fix, round_no=round_no)
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
