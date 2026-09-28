"""LLM 도우미 (분류): 진단을 읽고, 스스로 묻고 답하며, 규칙 · Jev · LLM으로 데이터를 고친다. 채팅이 아니다.

한 번 실행 (작업 ("classification", "helper"))
  0 계획      진단 요약을 LLM에 보이고 무엇이 급한지 말하게 한다(도구 plan). 순서는 흐름을 따른다.
  1~5 단계    라벨 충돌 → 오라벨 의심 → 짧은 문장 → 중복 여분 → 근접 중복. train · valid · test는 나누지 않는다.
              단계에 닿은 때 재서 문제가 없으면 건너뛴다(앞 단계의 고치기가 만든 문제도 잡힌다. 예: 충돌 통일 → 중복 여분).
              규칙 먼저: 판단이 필요 없는 고치기(Jev 확신 통일 · 분류기 = Jev · 여분 빼기 · 같은 라벨의 근접 중복
              하나 남기기)는 LLM이 고르지 않고 단계 처음에 한다. 남은 것이 있을 때만 LLM이
              그 단계의 판단 도구로 finish_step까지 돈다.
  6 보고      처음과 끝의 진단을 견줘 고친 검사 · 바꿈 · 비용을 싣고 LLM이 두 줄로 말한다. 실행은 여기서 끝난다.
남은 것 · AI로 고치기 (helper_fixes): 끝난 뒤 남은 것은 화면이 지금 진단으로 다시 센다. 사람이 고른 것만
  같은 실행에 단계로 붙여 고친다(작업 ("classification", "helper"), 줄마다 규칙 → Jev → LLM).
  새 문장은 '라벨 균형' 줄이다: 가장 많은 라벨의 1/1.5까지 모자란 라벨을 채우고, 심각이 남으면 만들지 않는다.
  이미 있는 문장 · 보기와 뜻이 거의 같은 새 문장은 거른다(임베딩이 있을 때).
말하기: 도구마다 say 인자(스스로 묻고 답한 짧은 한국어)를 받아 사건으로 적는다. '?'로 끝나는 줄이 스스로 묻는 말이다.
  규칙으로 한 일도 스스로 묻고 답하는 말을 남긴다.
토큰 아끼기: 단계마다 대화를 새로 시작하고(앞 단계 대화를 싣지 않는다), 도구 결과는 수와 몇 줄만 보인다.
  문장 하나하나 판정은 Jev가 하고, LLM은 Jev도 애매한 문장만 본다. 실행마다 토큰 상한(MAX_RUN_TOKENS)이 있다.
고치기는 바로 반영하고 모두 변경 기록에 남긴다(helper_tools). 영구 삭제는 없다.
멈춤: 사람이 [멈추기]를 누르면 다음 도구 전에, 토큰 상한에 닿으면 그 자리에서 멈춘다.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.classification import helper_fixes, helper_tools, semantic_checks, semantic_map
from dent.modules.classification import records as records_service
from dent.modules.classification import service as classification_service
from dent.modules.classification.helper_tools import CHECK_TITLES, Measure
from dent.modules.classification.models import Label, SuspectDecision
from dent.system import agent as agent_service
from dent.system import connections, jev, jobs
from dent.system import helper as helper_service
from dent.system.agent import (
    GRADE_BAD,
    LEFT_BLOCKED,
    REPORT_KEY,
    STEP_DONE,
    STEP_RUNNING,
    STEP_SKIPPED,
    STEP_TODO,
    Agent,
    AgentBudgetExceeded,
    AgentStopped,
    LeftItem,
    Tools,
    agent_tool,
    end_run,
    more,
    report_step_row,
    seconds,
)
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
BALANCE_BLOCKED_MESSAGE = "막힘 · 심각이 남아 새 문장을 만들지 않음"
UNDO_WHILE_RUNNING_MESSAGE = "도우미가 일하는 중에는 되돌릴 수 없습니다. 멈춘 뒤 되돌려 주세요."
NOT_THIS_MODULE_MESSAGE = "분류 데이터셋의 도우미가 아닙니다."

# 실행 한 번에 쓰는 LLM 토큰 상한 (입력 + 출력). 닿으면 그 자리에서 멈춘다.
MAX_RUN_TOKENS = 80_000

# 한 단계에서 LLM과 주고받는 최대 횟수. 넘으면 그 단계를 끝낸다(같은 도구를 되풀이하는 것을 막는다).
MAX_TURNS_PER_STEP = 8

# LLM 답 한 번의 최대 토큰
MAX_REPLY_TOKENS = 1_500

# LLM에 한 번에 보이는 문장 수 (애매한 것만 본다)
REVIEW_LIMIT = 12

# Jev 카드에 보일 줄 수
JEV_TABLE_ROWS = 8

# 라벨 충돌: Jev 확신이 이 값 이상이면 규칙으로 Jev 라벨로 통일한다(아래는 LLM이 본다).
DEFAULT_JEV_MIN = 0.8

# 한 단계에서 Jev에 묻는 최대 수
MAX_JEV_PER_STEP = 3_000

PLAN_KEY = "plan"

SYSTEM_BASE = """너는 분류 학습 데이터 도구 DENT의 도우미다. 진단에 나온 문제를 단계마다 고친다. 사람과 대화하지 않는다.
말하기: 도구를 부를 때마다 say에 스스로 묻고 답한 짧은 한국어를 쓴다. 물음은 한 줄로 '?'로 끝내고, 답은 한두 줄.
  존댓말 없이 짧게. 예: "어느 라벨이 맞을까?\\n보기 문장이 모두 환불 요청이다. 환불로 통일한다."
원칙:
- 규칙으로 되는 일은 규칙 도구로 한 번에 한다. 문장 하나하나의 판정은 Jev에 맡긴다.
- 네가 문장을 직접 보는 것은 Jev도 애매할 때뿐이다. Jev가 애매하다는 것은 근거가 없다는 뜻이 아니다:
  문장의 뜻과 라벨 목록을 보고 한 라벨이 분명하면 그 라벨로 고친다. 두 라벨 모두 맞을 만큼 문장이 애매할 때만
  hold로 사람에게 넘긴다(Jev 점수가 낮다는 것만으로는 넘기지 않는다).
- 영구 삭제는 없다. 할 수 있는 일은 학습에서 빼기 · 라벨 바꾸기 · 유지 · 보류뿐이다.
- 도구 결과의 수를 보고 판단하고, 같은 도구를 되풀이하지 않는다. 단계를 다 하면 finish_step을 부른다.
- 라벨 이름은 주어진 목록의 이름을 그대로 쓴다."""


# ---------- 단계 ----------


@dataclass(frozen=True)
class StepDef:
    """고치는 단계 하나."""

    # 단계 열쇠 (검사 이름과 같다)
    key: str

    # 화면의 단계 이름
    title: str

    # 규칙을 한 뒤 남은 것에 대한 LLM 지시. 빈 글이면 규칙만 하는 단계다(LLM을 부르지 않는다).
    goal: str = ""


STEPS: tuple[StepDef, ...] = (
    StepDef(
        "conflict",
        CHECK_TITLES["conflict"],
        "같은 문장에 라벨이 둘 이상인 무리 가운데 Jev도 애매한 것(또는 Jev 미연결)이 남았다. "
        "보기(ref)의 문장 뜻으로 맞는 라벨을 골라 relabel한다. 두 라벨 모두 맞을 만큼 애매한 문장만 hold한다.",
    ),
    StepDef(
        "suspect",
        CHECK_TITLES["suspect"],
        "분류기와 Jev가 같은 답인 의심은 규칙으로 바꾸거나 유지했다. 둘이 다르거나 약한 것이 남았다. "
        "review_suspects로 보고 relabel · keep · hold.",
    ),
    StepDef(
        "short",
        CHECK_TITLES["short"],
        "라벨을 가를 근거가 없을 만큼 짧은 문장이다. 뜻이 없으면 exclude_all_short로 뺀다. "
        "뜻이 분명한 것만 남기고 싶으면 보기를 보고 exclude로 골라 뺀다.",
    ),
    # 규칙: 문장 · 라벨이 모두 같은 무리마다 하나만 남긴다.
    StepDef("duplicate", CHECK_TITLES["duplicate"]),
    StepDef(
        "near_duplicate",
        CHECK_TITLES["near_duplicate"],
        "같은 라벨 쌍은 규칙으로 하나만 남겼다. 라벨이 다른 쌍이 남았다. "
        "보기(ref)의 문장을 보고 relabel · exclude · hold.",
    ),
)


STEP_BY_KEY = {step.key: step for step in STEPS}


def initial_steps() -> list[dict[str, Any]]:
    """실행을 시작할 때의 단계 줄: 계획 · 다섯 단계 · 보고."""
    rows: list[dict[str, Any]] = [{"key": PLAN_KEY, "no": 0, "title": "계획", "status": STEP_TODO}]
    rows += [
        {"key": step.key, "no": index, "title": step.title, "status": STEP_TODO}
        for index, step in enumerate(STEPS, start=1)
    ]
    rows.append(report_step_row(len(STEPS) + 1))
    return rows


# ---------- 시작 · 남은 것 · AI로 고치기 · 되돌리기 (API) ----------


async def start_helper(db: AsyncSession, *, dataset_id: int) -> HelperRun:
    """도우미를 시작한다. 뜻 분석이 없거나 오래됐고 임베딩이 연결돼 있으면 뜻 분석을 먼저 대기열에 넣는다.

    데이터셋이 없으면 NotFoundError, LLM이 없으면 InvalidInputError, 이미 도는 중이면 ConflictError.
    """
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None or connection.model is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    latest = await helper_service.latest_run(
        db, module=classification_service.MODULE_NAME, dataset_id=dataset_id
    )
    if latest is not None and latest.status == HelperRunStatus.RUNNING:
        raise ConflictError(ALREADY_RUNNING_MESSAGE)
    await _refresh_analysis_if_needed(db, dataset_id=dataset_id)

    run = helper_service.new_run(
        module=classification_service.MODULE_NAME,
        dataset_id=dataset_id,
        model=connection.model,
        steps=initial_steps(),
    )
    db.add(run)
    await _flush_or_running(db)
    job = await jobs.enqueue_job(
        db,
        module=classification_service.MODULE_NAME,
        kind=HELPER_JOB_KIND,
        params={"run_id": run.id},
        dataset_id=dataset_id,
    )
    run.job_id = job.id
    await db.commit()
    return await helper_service.get_run(db, run_id=run.id)


async def left_items(db: AsyncSession, *, dataset_id: int) -> list[LeftItem]:
    """남은 것(지금 진단으로 센다). 도우미 실행이 없으면 빈 목록. 데이터셋이 없으면 NotFoundError."""
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    latest = await helper_service.latest_run(
        db, module=classification_service.MODULE_NAME, dataset_id=dataset_id
    )
    if latest is None:
        return []
    return await helper_fixes.left_items(db, dataset_id=dataset_id)


async def start_fix(
    db: AsyncSession,
    *,
    dataset_id: int,
    keys: list[str],
    adds: dict[str, dict[str, int]] | None = None,
) -> HelperRun:
    """AI로 고치기를 시작한다(고른 검사를 도우미 단계 순서로). adds는 사람이 고친 더할 수
    (라벨 균형: {라벨 번호: 새 문장 수}). LLM이 없으면 InvalidInputError,
    지금 남은 것 가운데 AI로 고칠 수 없는 것을 고르면 InvalidInputError, 도는 중이면 ConflictError."""
    await classification_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None or connection.model is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    items = {item.key: item for item in await helper_fixes.left_items(db, dataset_id=dataset_id)}
    is_fixable = all(key in items and items[key].group != LEFT_BLOCKED for key in keys)
    if not is_fixable:
        raise InvalidInputError(NOT_FIXABLE_MESSAGE)
    return await agent_service.start_fix(
        db,
        module=classification_service.MODULE_NAME,
        dataset_id=dataset_id,
        job_kind=HELPER_JOB_KIND,
        model=connection.model,
        items=[(key, items[key].name) for key in helper_fixes.ordered(keys)],
        adds={key: cells for key, cells in (adds or {}).items() if key in keys},
    )


async def undo_helper(
    db: AsyncSession, *, run_id: int, event_id: int | None
) -> helper_tools.UndoResult:
    """도우미가 바꾼 것을 되돌린다(카드 하나 또는 실행 전체).

    실행이 없으면 NotFoundError, 도는 중이면 ConflictError(멈춘 뒤 되돌린다).
    """
    run = await helper_service.get_run(db, run_id=run_id)
    if run.module != classification_service.MODULE_NAME:
        raise ConflictError(NOT_THIS_MODULE_MESSAGE)
    if run.status == HelperRunStatus.RUNNING:
        raise ConflictError(UNDO_WHILE_RUNNING_MESSAGE)
    return await helper_tools.undo(db, run_id=run_id, event_id=event_id)


async def _refresh_analysis_if_needed(db: AsyncSession, *, dataset_id: int) -> None:
    """뜻 분석이 없거나 오래됐으면 다시 만들게 한다(임베딩이 연결돼 있고 만드는 중이 아닐 때만).

    작업 실행기는 작업을 넣은 순서대로 하나씩 처리하므로, 도우미 작업은 뜻 분석이 끝난 뒤에 돈다.
    """
    if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
        return
    state = await semantic_map.get_map_state(db, dataset_id=dataset_id)
    is_building = state.run is not None and state.run.status in ("queued", "running")
    is_stale = state.map is None or state.checks is None or state.outdated
    if is_stale and not is_building:
        try:
            await semantic_map.start_map(db, dataset_id=dataset_id)
        except (InvalidInputError, ConflictError):
            # 문장이 없거나 그 사이 누가 먼저 눌렀다. 도우미는 있는 분석으로 한다.
            await db.rollback()


async def _flush_or_running(db: AsyncSession) -> None:
    """실행 줄을 DB에 보낸다. 같은 데이터셋에 도는 도우미가 있으면(부분 UNIQUE) ConflictError."""
    from dent.system.db import flush_or_conflict

    await flush_or_conflict(db, message=ALREADY_RUNNING_MESSAGE)


# ---------- 실행 (작업 실행기) ----------


@dataclass
class Ref:
    """LLM에 보인 대상 하나 (r1, r2 …). 도구가 ref로 가리킨다."""

    ref: str
    text: str
    # 지금 라벨 같은 사실 (LLM과 카드에 보인다)
    facts: str
    record_ids: list[int]
    # 오라벨 의심이면 그 의심 (라벨을 바꾸거나 유지하면 판단을 적는다)
    suspect: helper_tools.SuspectInfo | None = None


@dataclass
class StepState:
    """단계 하나를 도는 동안의 것: 보인 대상, Jev 판정, 사실(바꿈 · 보류 · 유지 · Jev · LLM)."""

    def_: StepDef
    no: int
    refs: dict[str, Ref] = field(default_factory=dict)
    verdicts: dict[str, tuple[str, float]] = field(default_factory=dict)
    groups: list[helper_tools.TextGroup] = field(default_factory=list)
    suspects: list[helper_tools.SuspectInfo] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=lambda: defaultdict(int))


class Helper(Agent):
    """분류 도우미 실행 한 번. 말 · 사건 · 멈추기 · 토큰 상한 · 도구 되풀이는 시스템의 Agent가 한다."""

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
        labels: list[Label],
        transport: httpx.AsyncBaseTransport | None,
        jev_transport: httpx.AsyncBaseTransport | None,
        embedding_transport: httpx.AsyncBaseTransport | None = None,
        adds: dict[str, dict[str, int]] | None = None,
    ) -> None:
        super().__init__(
            db,
            run=run,
            llm_connection=llm_connection,
            jev_connection=jev_connection,
            transport=transport,
            jev_transport=jev_transport,
        )
        self.embedding_transport = embedding_transport
        # AI로 고치기에 사람이 고친 더할 수 {검사: {칸: 수}} (라벨 균형: 라벨 번호 → 새 문장 수)
        self.adds = adds or {}
        self.labels = labels
        self.label_ids = {label.name: label.id for label in labels}
        self.label_names = {label.id: label.name for label in labels}

    # ----- 한 번 실행 -----

    async def run(self, *, fix: list[str] | None = None, round_no: int = 0) -> None:
        """계획 → 단계들(흐름 순서, 닿을 때 잰다) → 보고. fix면 AI로 고치기 한 번(round_no)."""
        if fix is not None:
            fixers = {
                "duplicate": self._fix_duplicate,
                "conflict": self._fix_conflict,
                "suspect": self._fix_suspect,
                "short": self._fix_short,
                "near_duplicate": self._fix_near,
                "balance": self._fix_balance,
            }
            await self.run_fix(
                round_no=round_no,
                keys=fix,
                fixers={key: fixers[key] for key in fix if key in fixers},
            )
            return
        await self._plan()
        for step in STEPS:
            await self._run_step(step)
        # 보고로 끝낸다. 남은 것은 화면이 지금 진단으로 다시 세어 보이고, 사람이 고르면 AI로 고친다.
        await self.finish()

    # ----- 0 계획 -----

    async def _plan(self) -> None:
        """진단을 보이고 LLM이 무엇이 급한지 말하게 한다. 순서는 흐름(STEPS)을 따른다.

        순서를 LLM이 정하지 않는 까닭: 뒤 단계가 앞 단계의 문제를 새로 만들지 않도록 흐름을 짰다
        (예: 라벨 충돌을 통일하면 중복 여분이 생기므로 중복 여분은 그 뒤에 온다).
        """
        self.step_no = 0
        self.update_step(PLAN_KEY, status=STEP_RUNNING)
        await self.save_steps()
        started = time.monotonic()
        measures = await self._measure_all()
        lines = []
        for step in STEPS:
            measure = measures[step.key]
            if measure.value > 0:
                self.update_step(step.key, plan=measure.text)
                lines.append(f"- {step.title} ({step.key}): {measure.text} · {measure.grade}")
        await self.save_steps()
        await self.start_report(REPORT_KEY, from_step=0)
        brief = (
            "진단:\n"
            + ("\n".join(lines) if lines else "- 고칠 문제 없음")
            + f"\nJev: {'연결됨' if self.jev_connection else '미연결'}"
            + f"\n라벨: {', '.join(self.label_ids)}"
            + "\n흐름 순서대로 고친다: 라벨 충돌 → 오라벨 의심 → 짧은 문장 → 중복 여분 → 근접 중복."
            + "\n판단이 필요 없는 고치기는 규칙이 먼저 하고, 너는 남은 애매한 것만 본다."
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

    # ----- 1~6 단계 -----

    async def _run_step(self, step: StepDef) -> None:
        """단계 하나. 닿은 때 재서 문제가 없으면 건너뛴다. 규칙을 먼저 하고, 남은 것이 있고 LLM 몫이 있으면
        LLM이 finish_step을 부를 때까지 그 단계의 판단 도구를 부르게 한다."""
        self.step_no = next(row["no"] for row in self.steps if row["key"] == step.key)
        await self.check_stop()
        before = (await self._measure_all())[step.key]
        if before.value == 0:
            self.update_step(step.key, status=STEP_SKIPPED, plan="문제 없음")
            await self.save_steps()
            return
        state = StepState(def_=step, no=self.step_no)
        self.update_step(step.key, status=STEP_RUNNING, plan=before.text)
        await self.save_steps()
        started = time.monotonic()
        await self._rules(state)
        now = (await self._measure_all())[step.key]
        needs_llm = now.value > 0 and bool(step.goal)
        if needs_llm:
            state.facts["llm"] += await self.tool_loop(
                system=f"{SYSTEM_BASE}\n\n지금 단계: {step.title} ({step.key})\n할 일: {step.goal}",
                brief=await self._brief(state, now),
                tools=self._tools_for(state),
            )
            now = (await self._measure_all())[step.key]
        await self.emit(
            HelperEventKind.RESULT,
            {"check": step.title, "from": before.text, "to": now.text, "grade": now.grade},
        )
        facts = {name: value for name, value in state.facts.items() if value}
        facts["time"] = seconds(started)
        self.update_step(
            step.key, status=STEP_DONE, facts=facts, delta=[before.text, now.text, now.grade]
        )
        await self.save_steps()

    async def _rules(self, state: StepState) -> None:
        """규칙 먼저: 판단이 필요 없는 고치기는 LLM이 고르지 않고 단계 처음에 한다(스스로 묻고 답하는 말과 함께)."""
        key = state.def_.key
        if key == "conflict":
            if self.jev_connection is None:
                await self.say("Jev가 없나?\n무리를 직접 보고, 근거가 없으면 사람에게 넘긴다.")
                return
            await self.say(
                "어느 라벨이 맞을까?\n"
                f"Jev에 묻고, 무리의 라벨 가운데 하나를 {DEFAULT_JEV_MIN} 이상으로 고르면 통일한다. "
                "셋째 라벨은 직접 본다."
            )
            state.groups = await helper_tools.problem_groups(
                db=self.db, dataset_id=self.dataset_id, problem="conflict"
            )
            await self._jev_judge_conflicts(state)
            await self._apply_jev_labels(state, minimum=DEFAULT_JEV_MIN)
        elif key == "suspect":
            state.suspects = await helper_tools.open_suspects(self.db, dataset_id=self.dataset_id)
            buckets = _suspect_buckets(state.suspects)
            if buckets["agree"] or buckets["keeps"]:
                await self.say(
                    "분류기와 Jev가 같은 답인가?\n"
                    "둘이 같은 라벨을 고른 것은 바꾸고, Jev가 지금 라벨을 고른 것은 둔다."
                )
                await self._accept_agreeing(state)
                await self._keep_where_jev_keeps(state)
        elif key == "duplicate":
            await self.say("같은 문장 · 라벨이 여럿인가?\n무리마다 하나만 남기고 뺀다.")
            await self._exclude_duplicates(state)
        elif key == "near_duplicate":
            await self.say(
                "뜻이 거의 같은 쌍을 어떻게 할까?\n"
                "같은 라벨이면 하나만 남긴다. 라벨이 다르면 직접 본다."
            )
            await self._resolve_near(state)

    async def _brief(self, state: StepState, now: Measure) -> str:
        """규칙을 한 뒤 LLM에 보일 요약(남은 수와 몇 줄). 보기 문장은 ref로 가리킬 수 있게 등록한다."""
        key = state.def_.key
        head = f"남은 문제: {state.def_.title} · {now.text} ({now.grade})\n라벨: {', '.join(self.label_ids)}"
        if key == "conflict":
            groups = await helper_tools.problem_groups(
                db=self.db, dataset_id=self.dataset_id, problem="conflict"
            )
            jev_line = (
                f"Jev: 무리의 라벨을 {DEFAULT_JEV_MIN} 이상으로 고른 것은 통일함 · "
                "남은 것은 애매하거나 Jev가 셋째 라벨을 고름"
                if self.jev_connection
                else "Jev: 미연결 (네가 보거나 hold)"
            )
            lines = [head, f"무리 {len(groups)}", jev_line, "보기:"]
            for shown in self._refs_for_groups(state, groups[:REVIEW_LIMIT]):
                lines.append(f'{shown["ref"]} "{shown["문장"]}" · {shown["사실"]}')
            return "\n".join(lines)
        if key == "suspect":
            buckets = _suspect_buckets(state.suspects)
            return "\n".join(
                [
                    head,
                    f"대기 {len(state.suspects)}",
                    f"Jev가 셋째 라벨 {len(buckets['third'])} · Jev 없음/약함 {len(buckets['weak'])}",
                ]
            )
        if key == "short":
            records = await helper_tools.problem_records(
                self.db, dataset_id=self.dataset_id, problem="short"
            )
            lines = [
                head,
                f"문장 {len(records)} · 기준 {records_service.SHORT_TEXT_LENGTH}자 미만",
                "보기:",
            ]
            for record_id, text in records[:REVIEW_LIMIT]:
                ref = self._add_ref(state, text, f"{len(text)}자", [record_id])
                lines.append(f'{ref.ref} "{text}"')
            return "\n".join(lines)
        # near_duplicate: 규칙이 뺀 뒤 남은 것은 라벨이 다른 쌍이다.
        mismatched = [
            pair
            for pair in await helper_tools.near_pairs(self.db, dataset_id=self.dataset_id)
            if helper_tools.pair_kind(pair) == "label"
        ]
        lines = [head, f"라벨 다른 쌍 {len(mismatched)}", "보기:"]
        for pair in mismatched[: REVIEW_LIMIT // 2]:
            for group in (pair.a, pair.b):
                ref = self._add_ref(
                    state, group.text, _group_facts(group), [record.id for record in group.records]
                )
                lines.append(
                    f'{ref.ref} "{group.text}" · {ref.facts} · 유사도 {pair.similarity:.3f}'
                )
        return "\n".join(lines)

    def _tools_for(self, state: StepState) -> Tools:
        """LLM에 주는 그 단계의 판단 도구들 (이름 → (알림, 처리 함수)). 공통: relabel · hold · finish_step.

        규칙(Jev 통일 · 분류기 = Jev · 여분 빼기 · 근접 중복 규칙)은 단계 처음에 이미 했으므로 주지 않는다.
        """
        key = state.def_.key
        items = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "label": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["ref", "reason"],
            },
        }
        tools: Tools = {
            "finish_step": (
                agent_tool("finish_step", "이 단계를 끝낸다.", {}, []),
                self._finish_step,
            ),
            "hold": (
                agent_tool(
                    "hold",
                    "근거가 없어 사람에게 넘긴다(ref마다 까닭).",
                    {"items": items},
                    ["items"],
                ),
                lambda args, tokens: self._hold(state, args, tokens),
            ),
            "relabel": (
                agent_tool(
                    "relabel",
                    "ref 대상의 라벨을 바꾼다(label은 라벨 목록의 이름, 까닭 짧게).",
                    {"items": items},
                    ["items"],
                ),
                lambda args, tokens: self._relabel(state, args, tokens),
            ),
        }
        if key == "suspect":
            tools["review_suspects"] = (
                agent_tool(
                    "review_suspects",
                    "애매한 의심을 몇 개씩 본다. bucket=third(Jev가 셋째 라벨) · weak(Jev 없음/약함).",
                    {"bucket": {"type": "string", "enum": ["third", "weak"]}},
                    ["bucket"],
                ),
                lambda args, tokens: self._review_suspects(state, args),
            )
            tools["keep"] = (
                agent_tool(
                    "keep",
                    "ref 의심의 지금 라벨을 유지한다(까닭 짧게).",
                    {"items": items},
                    ["items"],
                ),
                lambda args, tokens: self._keep(state, args, tokens),
            )
        elif key == "short":
            tools["exclude_all_short"] = (
                agent_tool("exclude_all_short", "짧은 문장을 모두 학습에서 뺀다.", {}, []),
                lambda args, tokens: self._exclude_problem(state, "short"),
            )
            tools["exclude"] = (
                agent_tool(
                    "exclude", "ref 대상을 학습에서 뺀다(까닭 짧게).", {"items": items}, ["items"]
                ),
                lambda args, tokens: self._exclude_refs(state, args, tokens),
            )
        elif key == "near_duplicate":
            tools["exclude"] = (
                agent_tool(
                    "exclude", "ref 대상을 학습에서 뺀다(까닭 짧게).", {"items": items}, ["items"]
                ),
                lambda args, tokens: self._exclude_refs(state, args, tokens),
            )
        return tools

    # ----- 도구: 공통 -----

    async def _finish_step(self, _args: dict[str, Any], _tokens: int) -> dict[str, Any]:
        return {"끝": True}

    async def _hold(self, state: StepState, args: dict[str, Any], _tokens: int) -> dict[str, Any]:
        """사람에게 넘긴다. 보류 카드 하나를 적는다."""
        picked = self._picked(state, args)
        if not picked:
            return {"오류": "ref 없음"}
        await self.emit(
            HelperEventKind.HOLD,
            {
                "items": [
                    {"text": ref.text, "facts": ref.facts, "why": reason}
                    for ref, _, reason in picked
                ]
            },
        )
        await helper_service.add_usage(self.db, run_id=self.run_id, held=len(picked))
        await self.db.commit()
        state.facts["held"] += len(picked)
        return {"보류": len(picked)}

    async def _relabel(self, state: StepState, args: dict[str, Any], tokens: int) -> dict[str, Any]:
        """LLM이 본 대상의 라벨을 바꾼다. LLM 카드(판단)와 바꾼 카드를 적는다."""
        picked = self._picked(state, args)
        unknown = [label for _, label, _ in picked if label not in self.label_ids]
        if unknown:
            return {"오류": f"모르는 라벨: {', '.join(sorted(set(unknown)))}"}
        if not picked:
            return {"오류": "ref 없음"}
        await self._emit_llm_card(picked, tokens)
        targets = {
            record_id: self.label_ids[label]
            for ref, label, _ in picked
            for record_id in ref.record_ids
        }
        changed = await helper_tools.relabel(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            targets=targets,
            tool="LLM",
        )
        await self._decide_suspects([ref for ref, _, _ in picked], SuspectDecision.ACCEPTED)
        state.facts["changed"] += changed
        return {"바꾼 문장": changed}

    # ----- 규칙: 라벨 충돌 (Jev) -----

    async def _jev_judge_conflicts(self, state: StepState) -> None:
        """무리마다 Jev에 라벨을 묻고 Jev 카드를 적는다(판정은 state.verdicts에)."""
        if self.jev_connection is None:
            return
        questions = semantic_checks.jev_questions(self.labels)
        asked = 0
        for group in state.groups[:MAX_JEV_PER_STEP]:
            try:
                decision = await jev.decide(
                    self.jev_connection,
                    state={"text": group.text},
                    questions=questions,
                    transport=self.jev_transport,
                )
            except ExternalServiceError as error:
                await self.notice(error.message, tone="warn")
                break
            asked += 1
            answer = decision.answers.get(semantic_checks.JEV_QUESTION)
            if answer is None or answer.choice not in self.label_ids:
                continue
            state.verdicts[group.text_hash] = (
                answer.choice,
                float(answer.probabilities.get(answer.choice, 0.0)),
            )
        await helper_service.add_usage(self.db, run_id=self.run_id, jev_calls=asked)
        state.facts["jev"] += asked
        confident = sum(
            1
            for group in state.groups
            if (verdict := state.verdicts.get(group.text_hash)) and _is_sure_pick(group, verdict)
        )
        rows = []
        for group in sorted(
            state.groups, key=lambda item: -state.verdicts.get(item.text_hash, ("", 0.0))[1]
        )[:JEV_TABLE_ROWS]:
            verdict = state.verdicts.get(group.text_hash)
            if verdict is None:
                continue
            rows.append(
                {
                    "text": group.text,
                    "from": " · ".join(group.label_counts()),
                    "choice": verdict[0],
                    "prob": round(verdict[1], 3),
                    "decision": "apply" if _is_sure_pick(group, verdict) else "llm",
                }
            )
        await self.emit(
            HelperEventKind.JEV,
            {
                "meta": f"{asked}문장 · 라벨 {len(self.labels)} · {DEFAULT_JEV_MIN} 이상 {confident}",
                "rows": rows,
                "more": more(asked, len(rows)),
            },
        )

    async def _apply_jev_labels(self, state: StepState, *, minimum: float) -> None:
        """Jev가 무리의 라벨 가운데 하나를 기준 이상으로 고른 무리를 그 라벨로 통일한다.

        셋째 라벨(무리의 어느 라벨도 아닌 것)은 두 사람이 모두 틀렸다는 판단이라 규칙으로 하지 않고 LLM에 넘긴다.
        """
        targets: dict[int, int] = {}
        applied = 0
        for group in state.groups:
            verdict = state.verdicts.get(group.text_hash)
            if verdict is None or not _is_sure_pick(group, verdict, minimum=minimum):
                continue
            applied += 1
            for record in group.records:
                targets[record.id] = self.label_ids[verdict[0]]
        changed = await helper_tools.relabel(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            targets=targets,
            tool=f"Jev ≥ {minimum:g} · 무리 {applied}",
        )
        state.facts["changed"] += changed

    # ----- 규칙: 오라벨 의심 (분류기 · Jev) -----

    async def _accept_agreeing(self, state: StepState) -> None:
        """분류기와 Jev가 같은 라벨을 고른 의심을 그 라벨로 바꾼다. Jev 카드(뜻 분석 때 판정)와 바꾼 카드."""
        agree = _suspect_buckets(state.suspects)["agree"]
        if not agree:
            return
        await self.emit(
            HelperEventKind.JEV,
            {
                "meta": "뜻 분석 때 판정 · 다시 묻지 않음",
                "rows": [
                    {
                        "text": suspect.text,
                        "from": suspect.label_name,
                        "choice": suspect.jev_name,
                        "prob": round(suspect.jev_confidence or 0.0, 3),
                        "decision": "apply",
                    }
                    for suspect in agree[:JEV_TABLE_ROWS]
                ],
                "more": more(len(agree), JEV_TABLE_ROWS),
            },
        )
        targets = {
            record_id: suspect.suggested_label_id
            for suspect in agree
            for record_id in suspect.record_ids
        }
        changed = await helper_tools.relabel(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            targets=targets,
            tool="분류기 = Jev",
        )
        await helper_tools.decide_suspects(
            self.db,
            dataset_id=self.dataset_id,
            keys=[(suspect.text_hash, suspect.label_id) for suspect in agree],
            decision=SuspectDecision.ACCEPTED,
        )
        await self.db.commit()
        state.suspects = [suspect for suspect in state.suspects if suspect not in agree]
        state.facts["changed"] += changed

    async def _keep_where_jev_keeps(self, state: StepState) -> None:
        """Jev가 지금 라벨이 맞다고 본 의심을 유지한다(데이터는 그대로, 판단만 적는다)."""
        keeps = _suspect_buckets(state.suspects)["keeps"]
        if not keeps:
            return
        await helper_tools.decide_suspects(
            self.db,
            dataset_id=self.dataset_id,
            keys=[(suspect.text_hash, suspect.label_id) for suspect in keeps],
            decision=SuspectDecision.KEPT,
        )
        await self.emit(
            HelperEventKind.CHANGE,
            {
                "action": "유지",
                "count": len(keeps),
                "tool": "Jev = 지금 라벨",
                "keep": True,
                "samples": [suspect.text for suspect in keeps[:3]],
            },
        )
        state.suspects = [suspect for suspect in state.suspects if suspect not in keeps]
        state.facts["kept"] += len(keeps)

    # ----- 도구: 오라벨 의심 (LLM) -----

    async def _review_suspects(self, state: StepState, args: dict[str, Any]) -> dict[str, Any]:
        """애매한 의심 몇 개를 ref로 보인다."""
        bucket = _suspect_buckets(state.suspects).get(str(args.get("bucket") or "third"), [])
        shown = []
        for suspect in bucket[:REVIEW_LIMIT]:
            jev_text = (
                f"Jev {suspect.jev_name} {suspect.jev_confidence:.2f}"
                if suspect.jev_name
                else "Jev 없음"
            )
            facts = f"지금 {suspect.label_name} · 분류기 {suspect.suggested_name} {suspect.suggested_probability:.2f} · {jev_text}"
            ref = self._add_ref(state, suspect.text, facts, suspect.record_ids, suspect=suspect)
            shown.append({"ref": ref.ref, "문장": suspect.text, "사실": facts})
        return {"보기": shown, "이 묶음 전체": len(bucket)}

    async def _keep(self, state: StepState, args: dict[str, Any], tokens: int) -> dict[str, Any]:
        """LLM이 본 의심의 지금 라벨을 유지한다."""
        picked = [
            (ref, "유지", reason)
            for ref, _, reason in self._picked(state, args)
            if ref.suspect is not None
        ]
        if not picked:
            return {"오류": "의심 ref 없음"}
        await self._emit_llm_card(picked, tokens)
        await self._decide_suspects([ref for ref, _, _ in picked], SuspectDecision.KEPT)
        await self.db.commit()
        state.facts["kept"] += len(picked)
        return {"유지": len(picked)}

    # ----- 도구: 짧은 문장 · 중복 · 근접 중복 -----

    async def _exclude_problem(self, state: StepState, problem: str) -> dict[str, Any]:
        records = await helper_tools.problem_records(
            self.db, dataset_id=self.dataset_id, problem=problem
        )  # type: ignore[arg-type]
        changed = await helper_tools.exclude(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            record_ids=[record_id for record_id, _ in records],
            tool="규칙",
        )
        state.facts["changed"] += changed
        state.facts["rule"] = 1
        return {"뺀 문장": changed}

    async def _exclude_duplicates(self, state: StepState) -> None:
        """규칙: 문장 · 라벨이 모두 같은 무리마다 하나만 남기고 뺀다."""
        extras = await helper_tools.duplicate_extra_ids(self.db, dataset_id=self.dataset_id)
        changed = await helper_tools.exclude(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            record_ids=extras,
            tool="규칙",
        )
        state.facts["changed"] += changed
        state.facts["rule"] = 1

    async def _exclude_refs(
        self, state: StepState, args: dict[str, Any], tokens: int
    ) -> dict[str, Any]:
        picked = [(ref, "빼기", reason) for ref, _, reason in self._picked(state, args)]
        if not picked:
            return {"오류": "ref 없음"}
        await self._emit_llm_card(picked, tokens)
        changed = await helper_tools.exclude(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            record_ids=[record_id for ref, _, _ in picked for record_id in ref.record_ids],
            tool="LLM",
        )
        state.facts["changed"] += changed
        return {"뺀 문장": changed}

    async def _resolve_near(self, state: StepState) -> None:
        """규칙: 같은 라벨 쌍은 번호가 큰 쪽을 뺀다. 라벨이 다른 쌍은 LLM 몫이다."""
        pairs = await helper_tools.near_pairs(self.db, dataset_id=self.dataset_id)
        drop: list[int] = []
        for pair in pairs:
            if helper_tools.pair_kind(pair) == "same":
                later = pair.a if pair.a.records[0].id > pair.b.records[0].id else pair.b
                drop += [record.id for record in later.records]
        changed = await helper_tools.exclude(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            record_ids=drop,
            tool="규칙",
        )
        state.facts["changed"] += changed
        state.facts["rule"] = 1

    # ----- 진단 · AI로 고치기 -----

    async def snapshot(self) -> dict[str, Any]:
        """지금 진단: 다섯 단계 · 라벨 균형 · 의미 쏠림의 이름 · 값 · 등급 (보고의 처음 · 끝)."""
        measures = await self._measure_all()
        return {
            "checks": [
                {
                    "key": key,
                    "name": CHECK_TITLES[key],
                    "value": measure.text,
                    "grade": measure.grade,
                }
                for key, measure in measures.items()
            ],
            "kpi": [],
        }

    def _fix_state(self, key: str) -> StepState:
        return StepState(def_=STEP_BY_KEY[key], no=self.step_no)

    async def _fix_duplicate(self) -> dict[str, Any]:
        state = self._fix_state("duplicate")
        await self._rules(state)
        return dict(state.facts)

    async def _fix_conflict(self) -> dict[str, Any]:
        """라벨 충돌: Jev가 무리의 라벨을 확신하면 통일(규칙), 셋째 라벨 · 남은 무리는 LLM이 라벨을 고르고, 그래도 애매하면 학습에서 뺀다."""
        state = self._fix_state("conflict")
        await self._rules(state)
        groups = await helper_tools.problem_groups(
            db=self.db, dataset_id=self.dataset_id, problem="conflict"
        )
        groups = groups[: helper_fixes.FIX_MAX_ITEMS]
        chosen = await self._choose_labels(
            state, [(index, group.text, _group_facts(group)) for index, group in enumerate(groups)]
        )
        targets: dict[int, int] = {}
        unsure: list[int] = []
        for index, group in enumerate(groups):
            label = chosen.get(index)
            if label is None:
                unsure += [record.id for record in group.records]
            else:
                targets.update({record.id: self.label_ids[label] for record in group.records})
        state.facts["changed"] += await helper_tools.relabel(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            targets=targets,
            tool="LLM",
        )
        state.facts["changed"] += await helper_tools.exclude(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            record_ids=unsure,
            tool="애매 · 학습에서 빼기",
        )
        return dict(state.facts)

    async def _fix_suspect(self) -> dict[str, Any]:
        """오라벨 의심: 분류기 = Jev는 규칙, 남은 것은 LLM이 라벨을 고른다. 지금 라벨이거나 애매하면 유지."""
        state = self._fix_state("suspect")
        state.suspects = await helper_tools.open_suspects(self.db, dataset_id=self.dataset_id)
        await self._rules(state)
        suspects = state.suspects[: helper_fixes.FIX_MAX_ITEMS]
        chosen = await self._choose_labels(
            state,
            [
                (
                    index,
                    suspect.text,
                    f"지금 {suspect.label_name} · 분류기 {suspect.suggested_name}",
                )
                for index, suspect in enumerate(suspects)
            ],
        )
        targets: dict[int, int] = {}
        accepted, kept = [], []
        for index, suspect in enumerate(suspects):
            label = chosen.get(index)
            key = (suspect.text_hash, suspect.label_id)
            if label is None or self.label_ids[label] == suspect.label_id:
                kept.append(key)
            else:
                targets.update(
                    {record_id: self.label_ids[label] for record_id in suspect.record_ids}
                )
                accepted.append(key)
        state.facts["changed"] += await helper_tools.relabel(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            targets=targets,
            tool="LLM",
        )
        for keys, decision in ((accepted, SuspectDecision.ACCEPTED), (kept, SuspectDecision.KEPT)):
            await helper_tools.decide_suspects(
                self.db, dataset_id=self.dataset_id, keys=keys, decision=decision
            )
        await self.db.commit()
        state.facts["kept"] += len(kept)
        return dict(state.facts)

    async def _fix_short(self) -> dict[str, Any]:
        """짧은 문장: LLM이 뜻이 없다고 본 문장만 뺀다."""
        state = self._fix_state("short")
        records = await helper_tools.problem_records(
            self.db, dataset_id=self.dataset_id, problem="short"
        )
        records = records[: helper_fixes.FIX_MAX_ITEMS]
        shown = {record_id for record_id, _ in records}
        dropped: list[int] = []
        for batch in helper_fixes.batched(records):
            user = "\n".join(f"[{record_id}] {text}" for record_id, text in batch)
            data = await self.ask_json(system=helper_fixes.SHORT_PROMPT, user=user)
            for item in helper_fixes.parse_json_items(data):
                record_id = item.get("id")
                # LLM이 보이지 않은 번호를 주면 빼지 않는다
                if item.get("answer") == helper_fixes.DROP and record_id in shown:
                    dropped.append(int(record_id))
        state.facts["llm"] += len(records)
        state.facts["changed"] += await helper_tools.exclude(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            record_ids=dropped,
            tool="LLM · 뜻 없음",
        )
        return dict(state.facts)

    async def _fix_near(self) -> dict[str, Any]:
        """근접 중복: 같은 라벨 쌍은 규칙, 라벨이 다른 쌍은 LLM이 문장마다 라벨을 고른 뒤 규칙을 한 번 더."""
        state = self._fix_state("near_duplicate")
        await self._rules(state)
        pairs = [
            pair
            for pair in await helper_tools.near_pairs(self.db, dataset_id=self.dataset_id)
            if helper_tools.pair_kind(pair) == "label"
        ][: helper_fixes.FIX_MAX_ITEMS // 2]
        groups = {group.text_hash: group for pair in pairs for group in (pair.a, pair.b)}
        ordered_groups = list(groups.values())
        chosen = await self._choose_labels(
            state,
            [
                (index, group.text, _group_facts(group))
                for index, group in enumerate(ordered_groups)
            ],
        )
        targets = {
            record.id: self.label_ids[chosen[index]]
            for index, group in enumerate(ordered_groups)
            if chosen.get(index) is not None
            for record in group.records
        }
        state.facts["changed"] += await helper_tools.relabel(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            targets=targets,
            tool="LLM",
        )
        # 라벨을 맞춘 쌍은 이제 같은 라벨이라 규칙이 한쪽을 뺀다.
        await self._resolve_near(state)
        return dict(state.facts)

    async def _fix_balance(self) -> dict[str, Any]:
        """라벨 균형: 새 문장(계획은 helper_fixes.balance_plan). 심각이 남았으면 만들지 않는다(문)."""
        measures = await self._measure_all()
        # 라벨 균형 자체가 심각일 수 있으므로 그것은 세지 않는다.
        has_bad = any(
            measure.grade == GRADE_BAD for key, measure in measures.items() if key != "balance"
        )
        if has_bad:
            await self.notice(BALANCE_BLOCKED_MESSAGE, tone="bad")
            return {}
        cells = self.adds.get("balance")
        chosen = None if cells is None else {int(key): count for key, count in cells.items()}
        plan = await helper_fixes.balance_plan(self.db, dataset_id=self.dataset_id, adds=chosen)
        started_tokens = self.tokens
        added, near_rejected = await helper_fixes.generate_sentences(
            self, labels=self.labels, plan=plan, embedding_transport=self.embedding_transport
        )
        if near_rejected:
            await self.notice(f"근접 {near_rejected} 버림")
        return {"changed": added, "llm": self.tokens - started_tokens}

    async def _choose_labels(
        self, state: StepState, items: list[tuple[int, str, str]]
    ) -> dict[int, str | None]:
        """(번호, 문장, 사실)마다 LLM이 고른 라벨 이름. 모르거나 목록 밖이면 None. LLM 카드를 적는다."""
        chosen: dict[int, str | None] = {}
        for batch in helper_fixes.batched(items):
            user = f"라벨: {', '.join(self.label_ids)}\n" + "\n".join(
                f"[{index}] {text} ({facts})" for index, text, facts in batch
            )
            data = await self.ask_json(system=helper_fixes.LABEL_PROMPT, user=user)
            for item in helper_fixes.parse_json_items(data):
                label = str(item.get("label") or "")
                try:
                    index = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                chosen[index] = label if label in self.label_ids else None
        state.facts["llm"] += len(items)
        texts = {index: (text, facts) for index, text, facts in items}
        await self.emit(
            HelperEventKind.LLM,
            {
                "meta": f"LLM 라벨 {sum(1 for label in chosen.values() if label)} / {len(items)}",
                "rows": [
                    {"text": texts[index][0], "from": texts[index][1], "to": label or "", "why": ""}
                    for index, label in list(chosen.items())[:JEV_TABLE_ROWS]
                    if index in texts
                ],
            },
        )
        return chosen

    # ----- 도움 -----

    async def _emit_llm_card(self, picked: list[tuple[Ref, str, str]], tokens: int) -> None:
        """LLM이 직접 본 대상과 판단 (문장 · 지금 · → · 까닭)."""
        await self.emit(
            HelperEventKind.LLM,
            {
                "meta": f"문장 {len(picked)} · {tokens:,}토큰",
                "rows": [
                    {"text": ref.text, "from": ref.facts, "to": to, "why": reason}
                    for ref, to, reason in picked
                ],
            },
        )

    async def _decide_suspects(self, refs: list[Ref], decision: SuspectDecision) -> None:
        keys = [
            (ref.suspect.text_hash, ref.suspect.label_id) for ref in refs if ref.suspect is not None
        ]
        await helper_tools.decide_suspects(
            self.db, dataset_id=self.dataset_id, keys=keys, decision=decision
        )
        await self.db.commit()

    def _add_ref(
        self,
        state: StepState,
        text: str,
        facts: str,
        record_ids: list[int],
        *,
        suspect: helper_tools.SuspectInfo | None = None,
    ) -> Ref:
        ref = Ref(
            ref=f"r{len(state.refs) + 1}",
            text=text,
            facts=facts,
            record_ids=record_ids,
            suspect=suspect,
        )
        state.refs[ref.ref] = ref
        return ref

    def _refs_for_groups(
        self, state: StepState, groups: list[helper_tools.TextGroup]
    ) -> list[dict[str, str]]:
        shown = []
        for group in groups:
            verdict = state.verdicts.get(group.text_hash)
            facts = _group_facts(group) + (
                f" · Jev {verdict[0]} {verdict[1]:.2f}" if verdict else ""
            )
            ref = self._add_ref(state, group.text, facts, [record.id for record in group.records])
            shown.append({"ref": ref.ref, "문장": group.text, "사실": facts})
        return shown

    def _picked(self, state: StepState, args: dict[str, Any]) -> list[tuple[Ref, str, str]]:
        """도구 인자 items의 (ref, 라벨, 까닭). 모르는 ref는 버린다."""
        picked = []
        for item in args.get("items") or []:
            ref = state.refs.get(str(item.get("ref") or ""))
            if ref is not None:
                picked.append((ref, str(item.get("label") or ""), str(item.get("reason") or "")))
        return picked

    async def _measure_all(self) -> dict[str, Measure]:
        """검사마다 지금 값 (helper_tools.measure_all)."""
        return await helper_tools.measure_all(self.db, dataset_id=self.dataset_id)


# ---------- 작업 처리 (jobs.py가 부른다) ----------


async def run_helper(
    db: AsyncSession,
    *,
    run_id: int,
    fix: list[str] | None = None,
    round_no: int = 0,
    adds: dict[str, dict[str, int]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
    embedding_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """실행 한 번(또는 AI로 고치기 한 번)을 끝까지 돈다. 멈춤 · 상한 · LLM 실패는 실행 상태와 알림으로 남긴다."""
    run = await helper_service.get_run(db, run_id=run_id)
    if run.status != HelperRunStatus.RUNNING:
        return
    try:
        llm_connection = await connections.get_connection(db, role=ConnectionRole.LLM)
        if llm_connection is None or llm_connection.model is None:
            raise ExternalServiceError(LLM_NOT_CONNECTED_MESSAGE)
        jev_connection = await connections.get_connection(db, role=ConnectionRole.JEV)
        labels = list(
            await db.scalars(
                select(Label).where(Label.dataset_id == run.dataset_id).order_by(Label.name)
            )
        )
        helper = Helper(
            db,
            run=run,
            llm_connection=llm_connection,
            jev_connection=jev_connection,
            labels=labels,
            transport=transport,
            jev_transport=jev_transport,
            embedding_transport=embedding_transport,
            adds=adds,
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
    """너무 여러 번 끊긴 실행을 실패로 둔다(작업 실행기의 뒷정리)."""
    await end_run(db, run_id=run_id, status=HelperRunStatus.FAILED, message=message)


# ---------- 작은 도움 ----------


def _group_facts(group: helper_tools.TextGroup) -> str:
    return " · ".join(f"{name} {count}" for name, count in group.label_counts().items())


def _is_sure_pick(
    group: helper_tools.TextGroup, verdict: tuple[str, float], *, minimum: float = DEFAULT_JEV_MIN
) -> bool:
    """충돌 무리의 Jev 판정이 규칙으로 통일할 만한지: 무리의 라벨 가운데 하나를 기준 이상으로 골랐다."""
    choice, probability = verdict
    return choice in group.label_counts() and probability >= minimum


def _suspect_buckets(
    suspects: list[helper_tools.SuspectInfo],
) -> dict[str, list[helper_tools.SuspectInfo]]:
    """오라벨 의심 나눔: agree(분류기 = Jev 확인) · keeps(Jev = 지금 라벨) · third(Jev가 셋째 라벨) · weak(그 밖)."""
    buckets: dict[str, list[helper_tools.SuspectInfo]] = {
        "agree": [],
        "keeps": [],
        "third": [],
        "weak": [],
    }
    for suspect in suspects:
        if suspect.jev_label_id is None:
            buckets["weak"].append(suspect)
        elif suspect.jev_label_id == suspect.label_id:
            buckets["keeps"].append(suspect)
        elif suspect.jev_label_id == suspect.suggested_label_id:
            buckets["agree" if suspect.is_confirmed else "weak"].append(suspect)
        else:
            buckets["third"].append(suspect)
    return buckets
