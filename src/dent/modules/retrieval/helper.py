"""LLM 도우미 (검색): 진단을 읽고, 스스로 묻고 답하며, 규칙 · Jev · LLM으로 흐름 세 단계를 차례로 고친다. 채팅이 아니다.

한 번 실행 (작업 ("retrieval", "helper"))
  0 계획
  1 문서       문서 정리(글자 · 같은 문서 · 반복 구간 제안) → 문서 나누기(허락) → 청크 고르기(질의 안 만듦)
  2 질의       질의 만들기(허락, 허락 전 청크 5개 시험) → 질의 정리(짧은 · 긴 · 중복 · 문맥 의존)
               → 정답 확인(정답 없음 · 판정 충돌 · 정답 의심 · 빠진 정답)
  3 하드 네거티브  오답 찾기(규칙 · 학습할 모델) → 거짓 오답 확인(Jev 확인 · 정답과 같은 오답)
단계는 닿은 때 재서 문제가 없으면 건너뛴다. 허락이 필요한 단계에 닿으면 계획을 보이고 멈춰 기다린다(asking).
사람이 답하면 같은 실행이 새 작업으로 그 단계부터 이어 돈다(허락이면 그 일을 하고, 아니면 건너뛴다).
train · valid · test는 나누지 않는다(한 덩어리).
규칙 먼저: 판단이 필요 없는 고치기(StepDef.rules: 글자 정리 · 합치기 · 질의 안 만들 청크 표시 · Jev 확인 받기 …)는
LLM이 고르지 않고 단계 처음에 한다. 남은 것이 있고 LLM 몫(goal)이 있을 때만 LLM이 판단 도구로 돈다.
문: 허락 단계와 다음 흐름 단계에 들어가기 전에 앞 단계들의 검사를 다시 잰다. 심각이 남았으면 막고(남은 문제 카드),
주의는 허락 카드에 싣는다. 사람이 허락을 거절해 건너뛴 단계의 검사는 세지 않는다(사람이 정한 것이다).
뜻 분석 새로 만들기: 뜻 분석에 기대는 단계(StepDef.needs_analysis)에 닿았는데 분석에 없는 질의 · 문서가 있으면
(질의 만들기 · 문서 나누기 뒤) 분석 작업을 넣고, 그 뒤에 이 단계부터 이어 도는 작업을 넣고 이번 작업을 끝낸다.
작업 실행기는 넣은 순서대로 하나씩 처리하므로 이어 도는 작업은 새 분석을 본다. 이어 도는 작업에서는 다시 기다리지 않는다.
말하기 · 토큰 상한 · [멈추기]는 시스템의 Agent가 한다. 고치기는 바로 반영하고 모두 변경 기록에 남긴다(되돌리기).
반복 구간(떼기 · 남김)은 데이터마다 뜻이 달라 사람이 고른다. 도우미는 보기를 읽고 제안과 까닭만 적는다.
"""

import functools
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import (
    edits,
    generation,
    helper_changes,
    helper_fixes,
    judging,
    mining,
    problems,
    repeats,
    semantic,
)
from dent.modules.retrieval import overview as overview_service
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_DOC_MAX_TOKENS,
    POSITIVE_MIN_GRADE,
    AnalysisStatus,
    Document,
    HelperField,
    HelperTarget,
    Judgment,
    MapPoint,
    Query,
    Ranking,
    Repeat,
    RepeatDecision,
    Shape,
    Suggestion,
    SuggestionDecision,
    SuggestionKind,
)
from dent.modules.retrieval.schemas import CheckRead
from dent.modules.retrieval.service import (
    DOCUMENT_ACTIVE,
    DOCUMENT_INPUT,
    QUERY_ACTIVE,
    QUERY_INCLUDED,
)
from dent.system import agent as agent_service
from dent.system import connections, jobs
from dent.system import helper as helper_service
from dent.system.agent import (
    FINISH_STEP_TOOL,
    GRADE_BAD,
    GRADE_GOOD,
    GRADE_WARN,
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
    LeftItem,
    OpenCheck,
    Tools,
    agent_tool,
    blocking,
    end_run,
    fix_step_key,
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
    HelperEventKind,
    HelperRun,
    HelperRunStatus,
)
from dent.system.text import make_text_hash

HELPER_JOB_KIND = "helper"

LLM_NOT_CONNECTED_MESSAGE = "LLM이 연결되지 않았습니다. 연결 설정에서 LLM을 저장해 주세요."
ALREADY_RUNNING_MESSAGE = "도우미가 이미 일하는 중입니다."
NOT_ASKING_MESSAGE = "허락을 기다리는 도우미가 아닙니다."
NO_CHUNK_FIRST_MESSAGE = "먼저 나눌 긴 문서가 없는 허락입니다."

# 질의 만들기 허락 카드의 [먼저 나누기]: 건너뛴 문서 나누기를 허락받은 것으로 먼저 돈다
ACTION_CHUNK_FIRST = "chunk_first"

# 허락에 답한 알림의 앞말: (허락, 동작) → 글
ANSWER_WORDS: dict[tuple[bool, str | None], str] = {
    (True, None): "허락 · ",
    (False, None): "건너뜀 · ",
    (True, ACTION_CHUNK_FIRST): "먼저 나누기 · ",
}
UNDO_WHILE_RUNNING_MESSAGE = "도우미가 일하는 중에는 되돌릴 수 없습니다. 멈춘 뒤 되돌려 주세요."
NOT_THIS_MODULE_MESSAGE = "검색 데이터셋의 도우미가 아닙니다."
NO_ANALYSIS_TOOL_MESSAGE = "뜻 분석 없음"
NOT_FIXABLE_MESSAGE = "AI로 고칠 수 없는 것을 골랐습니다. 남은 것을 다시 읽어 주세요."

# 실행 한 번에 쓰는 LLM 토큰 상한 · 단계마다 LLM과 주고받는 최대 횟수 · 답 한 번의 최대 토큰
MAX_RUN_TOKENS = 80_000
MAX_TURNS_PER_STEP = 8
MAX_REPLY_TOKENS = 1_500

# 단계 요약에 보이는 보기 수 · Jev 카드의 줄 수 · 바꾼 카드의 보기 수
BRIEF_SAMPLE_COUNT = 5
JEV_TABLE_ROWS = 8

# 정답 찾기: 정답 없는 질의마다 기준 검색 상위 몇 개를 Jev에 묻나 · 한 단계에서 Jev에 묻는 최대 수
FIND_POSITIVE_TOP = 3
MAX_JEV_PER_STEP = 2_000

# 거짓 오답: Jev 예가 이 값 이상이고 확인(0.8) 아래면 답의 일부만 담은 것으로 보고 오답에서 뺀다(정답으로 올리지 않음).
# 이 값 아래는 진짜 하드 오답으로 남긴다.
PARTIAL_ANSWER_PROBABILITY = 0.5

# 반복 구간 요약에 보이는 틀 수 · 틀마다 보기 수
BRIEF_REPEATS = 12
BRIEF_REPEAT_SAMPLES = 2

# 흐름 단계 이름 (화면의 무리 머리)
STAGE_TITLES = {1: "문서", 2: "질의", 3: "하드 네거티브"}

# 뜻 분석을 새로 만드는 동안 단계 줄에 보이는 글
PLAN_WAIT_ANALYSIS = "뜻 분석 기다림"

PLAN_KEY = "plan"

SYSTEM_BASE = """너는 검색(임베딩) 학습 데이터 도구 DENT의 도우미다. 진단에 나온 문제를 흐름 단계마다 고친다. 사람과 대화하지 않는다.
데이터: 질의 · 문서 · 판정(정답 · 오답). 흐름: 1 문서 → 2 질의 → 3 하드 네거티브.
말하기: 도구를 부를 때마다 say에 스스로 묻고 답한 짧은 한국어를 쓴다. 물음은 한 줄로 '?'로 끝내고, 답은 한두 줄.
  존댓말 없이 짧게. 예: "정답이 맞을까?\\n문서가 질의의 답을 담았다. 정답으로 둔다."
원칙:
- 규칙으로 되는 일은 규칙 도구로 한 번에 한다. (질의, 문서) 판정은 Jev에 맡긴다.
- Jev가 애매하다는 것은 근거가 없다는 뜻이 아니다: 질의 · 문서의 내용으로 판단이 분명하면 네가 정한다.
  둘 다 말이 될 만큼 애매할 때만 hold로 사람에게 넘긴다(Jev 점수가 낮다는 것만으로는 넘기지 않는다).
- 영구 삭제는 없다. 할 수 있는 일은 학습에서 빼기 · 휴지통 · 판정 바꾸기 · 글 고치기 · 보류뿐이다.
- 도구 결과의 수를 보고 판단하고, 같은 도구를 되풀이하지 않는다. 단계를 다 하면 finish_step을 부른다."""


@dataclass(frozen=True)
class StepDef:
    """고치는 단계 하나."""

    # 단계 열쇠
    key: str

    # 화면의 단계 이름
    title: str

    # 흐름 단계 (1 문서 · 2 질의 · 3 하드 네거티브)
    stage: int

    # 이 단계가 고치는 진단 검사 이름들
    checks: tuple[str, ...]

    # 규칙을 한 뒤 남은 것에 대한 LLM 지시. 빈 글이면 LLM을 부르지 않는다(허락 · 규칙만 하는 단계).
    goal: str = ""

    # 허락을 받아야 하는 단계인지
    permission: bool = False

    # 규칙 먼저: 단계 처음에 LLM 없이 차례로 하는 도구 이름들 (LLM에는 주지 않는다)
    rules: tuple[str, ...] = ()

    # 규칙을 하기 전에 남기는 스스로 묻고 답하는 말
    rule_say: str = ""

    # 뜻 분석(근접 중복 · 기준 검색 순위 · 제안)에 기대는 단계인지. 분석에 없는 질의 · 문서가 있으면 분석을 새로 만든 뒤 돈다.
    needs_analysis: bool = False


STEPS: tuple[StepDef, ...] = (
    StepDef(
        "documents",
        "문서 정리",
        1,
        ("broken", "repeat", "duplicate_document"),
        "반복 구간(여러 문서에 되풀이되는 문장 · 메타 모양)은 보기(p1 …)를 읽고 suggest_repeats로 "
        "remove · keep 제안과 까닭만 적는다. 결정은 사람이 한다. "
        "remove: 내용과 상관없는 서명 · 연락처 · 저작권 · 틀, 그리고 많은 문서의 같은 자리(앞 · 뒤)에 똑같이 붙은 "
        "안내 문장(정보가 있어도 문서를 서로 가르지 못하고, 그 문장으로 질의를 만들면 정답이 여러 문서로 흩어져 "
        "거짓 오답이 생긴다). keep: 문서마다 그 자리에서 뜻을 이루는 본문(본문 가운데에 나오거나 문서마다 뜻이 다른 것). "
        "고를 반복 구간이 없으면 finish_step.",
        rules=("clean_documents", "merge_duplicates"),
        rule_say="글자와 같은 문서부터 볼까?\n깨진 글자를 고치고, 학습 글이 같은 문서는 하나로 합친다.",
    ),
    StepDef("chunk", "문서 나누기", 1, ("long",), permission=True),
    StepDef(
        "pieces",
        "청크 고르기",
        1,
        # 나누면 같은 청크(예: 여러 부칙의 같은 시행일 조문)이 새로 생기므로 중복 문서를 여기서 다시 본다.
        ("pick", "duplicate_document"),
        rules=("merge_duplicates", "mark_skip_generation"),
        # 뜻이 거의 같은 청크(근접 중복)도 한 묶음으로 보려면 나눈 청크도 분석에 있어야 한다.
        needs_analysis=True,
        rule_say=(
            "질의를 만들기 전에 무엇을 할까?\n나눠서 같아진 청크를 합치고, "
            "목차 · 표만인 청크는 질의 안 만듦으로 둔다."
        ),
    ),
    StepDef("generate", "질의 만들기", 2, ("no_queries",), permission=True),
    StepDef(
        "queries",
        "질의 정리",
        2,
        ("short_long", "duplicate_query", "context"),
        "짧은 · 긴 질의 · 같은 질의 여분은 규칙으로 뺐다. "
        "문맥 의존 질의가 남았으면 보기를 보고 rewrite로 고유 이름을 넣어 고쳐 쓰거나, 고칠 수 없으면 exclude_context로 뺀다.",
        rules=("exclude_short_long", "exclude_duplicates"),
        rule_say="쓸모없는 질의가 있나?\n짧은 · 긴 질의와 같은 질의 여분을 뺀다.",
    ),
    StepDef(
        "answers",
        "정답 확인",
        2,
        ("no_positive", "conflict", "suspect", "missing"),
        "Jev로 판정 충돌을 정하고 정답을 찾고 Jev가 확인한 제안을 받았다. "
        "정답을 끝내 못 찾은 질의는 exclude_no_positive로 빼거나, 근거가 없으면 hold한다.",
        rules=(
            "resolve_conflicts",
            "find_positives",
            "accept_missing_positives",
            "accept_suspect_positives",
        ),
        needs_analysis=True,
        rule_say=(
            "정답이 맞나?\n판정 충돌과 정답 없는 질의는 Jev에 묻고, 빠진 정답 · 정답 의심은 Jev가 확인한 것만 받는다. "
            "오답 찾기 전에 빠진 정답부터 붙인다."
        ),
    ),
    StepDef("mine", "오답 찾기", 3, ("no_negative",)),
    StepDef(
        "negatives",
        "거짓 오답 확인",
        3,
        ("false_negative", "same_negative"),
        rules=(
            "accept_false_negatives",
            "drop_false_negatives",
            "keep_hard_negatives",
            "remove_same_negatives",
        ),
        needs_analysis=True,
        rule_say=(
            "오답이 사실 정답은 아닌가?\nJev 예 ≥ 0.8은 정답으로, 0.5~0.8은 오답에서 떼고, 0.5 아래는 하드 오답으로 둔다. "
            "정답과 같은 묶음의 오답도 뗀다."
        ),
    ),
)

STEP_BY_KEY = {step.key: step for step in STEPS}


def initial_steps() -> list[dict[str, Any]]:
    """실행을 시작할 때의 단계 줄: 계획 · 흐름 세 단계의 여덟 단계(허락 단계는 permission 표시) · 보고."""
    rows: list[dict[str, Any]] = [{"key": PLAN_KEY, "no": 0, "title": "계획", "status": STEP_TODO}]
    rows += [
        {
            "key": step.key,
            "no": index,
            "title": step.title + (" · 허락" if step.permission else ""),
            "status": STEP_TODO,
            "stage": step.stage,
            "stage_title": STAGE_TITLES[step.stage],
            "permission": step.permission,
        }
        for index, step in enumerate(STEPS, start=1)
    ]
    rows.append(report_step_row(len(STEPS) + 1))
    return rows


# ---------- 시작 · 허락 · 되돌리기 (API) ----------


async def start_helper(db: AsyncSession, *, dataset_id: int) -> HelperRun:
    """도우미를 시작한다. 뜻 분석이 없거나 오래됐고 임베딩이 연결돼 있으면 뜻 분석을 먼저 대기열에 넣는다.

    데이터셋이 없으면 NotFoundError, LLM이 없으면 InvalidInputError, 이미 도는 중이면 ConflictError.
    """
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None or connection.model is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    latest = await helper_service.latest_run(
        db, module=retrieval_service.MODULE_NAME, dataset_id=dataset_id
    )
    if latest is not None and latest.status in (HelperRunStatus.RUNNING, HelperRunStatus.ASKING):
        raise ConflictError(ALREADY_RUNNING_MESSAGE)
    await _refresh_analysis_if_needed(db, dataset_id=dataset_id)
    run = helper_service.new_run(
        module=retrieval_service.MODULE_NAME,
        dataset_id=dataset_id,
        model=connection.model,
        steps=initial_steps(),
    )
    db.add(run)
    await flush_or_conflict(db, message=ALREADY_RUNNING_MESSAGE)
    job = await jobs.enqueue_job(
        db,
        module=retrieval_service.MODULE_NAME,
        kind=HELPER_JOB_KIND,
        params={"run_id": run.id},
        dataset_id=dataset_id,
    )
    run.job_id = job.id
    await db.commit()
    return await helper_service.get_run(db, run_id=run.id)


async def answer_permission(
    db: AsyncSession, *, run_id: int, approve: bool, action: str | None = None
) -> HelperRun:
    """허락에 답한다. 같은 실행을 새 작업으로 그 단계부터 이어 돈다(허락이면 그 일을 하고, 아니면 건너뛴다).

    action이 chunk_first면(질의 만들기 허락 카드의 [먼저 나누기]) 건너뛴 문서 나누기부터 허락받은 것으로 돈다.
    실행이 없으면 NotFoundError, 허락을 기다리는 중이 아니면 ConflictError,
    [먼저 나누기]를 할 수 없는 카드면 InvalidInputError.
    """
    run = await helper_service.get_run(db, run_id=run_id)
    if run.module != retrieval_service.MODULE_NAME:
        raise ConflictError(NOT_THIS_MODULE_MESSAGE)
    if run.status != HelperRunStatus.ASKING or not run.permission:
        raise ConflictError(NOT_ASKING_MESSAGE)
    step_key = str(run.permission.get("step"))
    if action == ACTION_CHUNK_FIRST:
        alert = run.permission.get("alert") or {}
        if alert.get("action") != ACTION_CHUNK_FIRST:
            raise InvalidInputError(NO_CHUNK_FIRST_MESSAGE)
        step_key = "chunk"
        approve = True
    job = await jobs.enqueue_job(
        db,
        module=retrieval_service.MODULE_NAME,
        kind=HELPER_JOB_KIND,
        params={"run_id": run_id, "resume": step_key, "approved": approve},
        dataset_id=run.dataset_id,
    )
    await helper_service.add_event(
        db,
        run_id=run_id,
        step=run.step_now,
        kind=HelperEventKind.NOTICE,
        payload={"text": ANSWER_WORDS[(approve, action)] + STEP_BY_KEY[step_key].title},
    )
    await helper_service.resume_run(db, run_id=run_id, job_id=job.id)
    await db.commit()
    return await helper_service.get_run(db, run_id=run_id)


async def undo_helper(
    db: AsyncSession, *, run_id: int, event_id: int | None
) -> helper_changes.UndoResult:
    """도우미가 바꾼 것을 되돌린다. 도는 중이면 ConflictError."""
    run = await helper_service.get_run(db, run_id=run_id)
    if run.module != retrieval_service.MODULE_NAME:
        raise ConflictError(NOT_THIS_MODULE_MESSAGE)
    if run.status == HelperRunStatus.RUNNING:
        raise ConflictError(UNDO_WHILE_RUNNING_MESSAGE)
    return await helper_changes.undo(db, run_id=run_id, event_id=event_id)


async def left_items(db: AsyncSession, *, dataset_id: int) -> list[LeftItem]:
    """남은 것(지금 진단으로 센다). 도우미 실행이 없으면 빈 목록. 데이터셋이 없으면 NotFoundError."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    latest = await helper_service.latest_run(
        db, module=retrieval_service.MODULE_NAME, dataset_id=dataset_id
    )
    if latest is None:
        return []
    return await helper_fixes.left_items(db, dataset_id=dataset_id)


async def start_fix(db: AsyncSession, *, dataset_id: int, keys: list[str]) -> HelperRun:
    """AI로 고치기를 시작한다(고른 검사를 흐름 순서로). LLM이 없으면 InvalidInputError,
    지금 남은 것 가운데 AI로 고칠 수 없는 것을 고르면 InvalidInputError, 도는 중이면 ConflictError."""
    await retrieval_service.get_dataset(db, dataset_id=dataset_id)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None or connection.model is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    items = {item.key: item for item in await helper_fixes.left_items(db, dataset_id=dataset_id)}
    is_fixable = all(key in items and items[key].group != LEFT_BLOCKED for key in keys)
    if not is_fixable:
        raise InvalidInputError(NOT_FIXABLE_MESSAGE)
    return await agent_service.start_fix(
        db,
        module=retrieval_service.MODULE_NAME,
        dataset_id=dataset_id,
        job_kind=HELPER_JOB_KIND,
        model=connection.model,
        items=[(key, items[key].name) for key in helper_fixes.ordered(keys)],
    )


async def _refresh_analysis_if_needed(db: AsyncSession, *, dataset_id: int) -> None:
    """뜻 분석이 없거나 오래됐으면 다시 만들게 한다(임베딩이 연결돼 있고 만드는 중이 아닐 때만)."""
    if await connections.get_connection(db, role=ConnectionRole.EMBEDDING) is None:
        return
    state = await semantic.get_analysis_state(db, dataset_id=dataset_id)
    is_building = state.run is not None and state.run.status in (
        AnalysisStatus.QUEUED,
        AnalysisStatus.RUNNING,
    )
    if (state.done is None or state.outdated) and not is_building:
        try:
            await semantic.start_analysis(db, dataset_id=dataset_id)
        except (InvalidInputError, ConflictError):
            await db.rollback()


# ---------- 실행 ----------


@dataclass(frozen=True)
class Measure:
    """단계 하나의 지금 값: 남은 문제 수 · 글 · 등급."""

    value: int
    text: str
    grade: str


@dataclass
class Sample:
    """LLM에 보인 보기 하나 (r1, r2 …): 질의 번호 · 글 · 사실."""

    query_id: int
    text: str
    facts: str


@dataclass(frozen=True)
class Trial:
    """허락 전 시험의 결과: 시험한 청크 수 · 만든 후보 수 · 결과(통과 · 다시 만듦 · 모자람)."""

    documents: int
    made: int
    outcome: generation.Outcome


def _overlap_word(overlap: int) -> str:
    """오버랩 토큰을 화면 낱말로 (0이면 없음)."""
    return f"{overlap}토큰" if overlap else "없음"


@dataclass
class StepState:
    """단계 하나를 도는 동안의 것: 보인 보기 · 사실(바꿈 · 보류 · Jev · LLM)."""

    def_: StepDef
    samples: dict[str, Sample] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=lambda: defaultdict(int))

    # 보인 반복 구간 (p1, p2 … → 반복 구간 번호)
    repeats: dict[str, int] = field(default_factory=dict)


class Helper(Agent):
    """검색 도우미 실행 한 번."""

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
        embedding_transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        super().__init__(
            db,
            run=run,
            llm_connection=llm_connection,
            jev_connection=jev_connection,
            transport=transport,
            jev_transport=jev_transport,
        )
        self.job_id = run.job_id
        self.embedding_transport = embedding_transport
        self.shape: Shape | None = None

    async def run(
        self,
        *,
        resume: str | None,
        approved: bool,
        continue_from: str | None = None,
        fix: list[str] | None = None,
        round_no: int = 0,
        waited_fix: bool = False,
    ) -> None:
        """계획 → 단계들 → 보고. 허락 단계에 닿으면 asking으로 멈춘다. resume이면 허락에 답한 그 단계부터,
        continue_from이면 뜻 분석을 새로 만든 뒤 그 단계부터 이어 돈다. fix면 AI로 고치기 한 번(round_no)."""
        self.shape = await edits.dataset_shape(self.db, dataset_id=self.dataset_id)
        if fix is not None:
            await self._fix(fix, round_no=round_no, waited=waited_fix)
            return
        # 뜻 분석을 기다린 뒤 이어 도는 작업이면 다시 기다리지 않는다(분석이 실패해도 되풀이하지 않게).
        waited = continue_from is not None
        if continue_from is not None:
            start = next(index for index, step in enumerate(STEPS) if step.key == continue_from)
            self.step_no = start + 1
            missing = await self._missing_from_analysis()
            if missing is not None and any(missing):
                await self.notice(
                    f"뜻 분석 없이 이어 감 · 분석에 없는 질의 {missing[0]:,} · 문서 {missing[1]:,}",
                    tone="warn",
                )
        elif resume is None:
            await self._plan()
            start = 0
        else:
            start = next(index for index, step in enumerate(STEPS) if step.key == resume)
            step = STEPS[start]
            self.step_no = start + 1
            if approved:
                # 허락을 물은 뒤 데이터가 바뀌었거나(되돌리기 · 사람 고치기) 문이 없던 때 물은 허락일 수 있어 다시 본다.
                earlier = await self._earlier_open_checks(start)
                if blocking(earlier):
                    await self.block(step.key, earlier)
                    return
                await self._do_permitted(step)
            else:
                self.update_step(step.key, status=STEP_SKIPPED, plan=PLAN_DECLINED)
                await self.save_steps()
            start += 1
        for index in range(start, len(STEPS)):
            step = STEPS[index]
            if step.needs_analysis and not waited:
                missing = await self._missing_from_analysis()
                if missing is not None and any(missing):
                    await self._wait_for_analysis(
                        step.key,
                        step.title,
                        missing,
                        params={"run_id": self.run_id, "continue_from": step.key},
                    )
                    return
            if _is_gate(index):
                earlier = await self._earlier_open_checks(index)
                if blocking(earlier):
                    # 문: 앞 단계에 심각이 남은 채 허락을 묻거나 다음 흐름으로 가지 않는다
                    # (예: 긴 문서가 남은 채 질의를 만들면 뒤가 잘린 글로 질의를 만든다).
                    await self.block(step.key, earlier)
                    return
            if await self._run_step(step) == STEP_ASK:
                return
        # 보고로 끝낸다. 남은 것(뒤 단계의 고치기가 앞 단계 문제를 새로 만들 수도 있다. 예: 거짓 오답 → 정답)은
        # 화면이 지금 진단으로 다시 세어 보이고, 사람이 고르면 AI로 고친다.
        await self.finish()

    # ----- 진단 · AI로 고치기 -----

    async def snapshot(self) -> dict[str, Any]:
        """지금 진단: 검사마다 이름 · 값 · 등급과 기준 검색 점수 (보고의 처음 · 끝)."""
        overview = await overview_service.get_overview(self.db, dataset_id=self.dataset_id)
        checks = []
        for check in overview.checks:
            shown = _open_check(check)
            checks.append(
                {"key": check.key, "name": shown.name, "value": shown.value, "grade": check.grade}
            )
        kpi = []
        if overview.kpi is not None:
            kpi = [
                {"name": "R@10", "value": f"{overview.kpi.recall_at_10 * 100:.1f}%"},
                {"name": "MRR@10", "value": f"{overview.kpi.mrr_at_10:.3f}"},
            ]
        return {"checks": checks, "kpi": kpi}

    async def _fix(self, keys: list[str], *, round_no: int, waited: bool) -> None:
        """AI로 고치기 한 번. 뜻 분석에 기대는 검사가 있고 분석에 없는 질의 · 문서가 있으면 분석부터 새로 만든다."""
        needs_analysis = any(key in helper_fixes.NEEDS_ANALYSIS for key in keys)
        if needs_analysis and not waited:
            missing = await self._missing_from_analysis()
            if missing is not None and any(missing):
                first = fix_step_key(round_no, keys[0])
                found = self.row(first)
                await self._wait_for_analysis(
                    first,
                    str(found["title"]) if found else "",
                    missing,
                    params={"run_id": self.run_id, "fix": keys, "round": round_no, "waited": True},
                )
                return
        fixing = helper_fixes.Fixing(
            agent=self,
            llm_connection=self.llm_connection,
            jev_connection=self.jev_connection,
            transport=self.transport,
            jev_transport=self.jev_transport,
            embedding_transport=self.embedding_transport,
            job_id=self.job_id,
        )
        fixers = {
            key: functools.partial(helper_fixes.FIXERS[key], fixing)
            for key in keys
            if key in helper_fixes.FIXERS
        }
        await self.run_fix(round_no=round_no, keys=keys, fixers=fixers)

    # ----- 뜻 분석 새로 만들기 -----

    async def _missing_from_analysis(self) -> tuple[int, int] | None:
        """가장 최근 뜻 분석에 없는 (질의 수, 문서 수). 임베딩이 없으면 None(분석을 만들 수 없다)."""
        if await connections.get_connection(self.db, role=ConnectionRole.EMBEDDING) is None:
            return None
        analysis = await retrieval_service.latest_done_analysis(self.db, dataset_id=self.dataset_id)
        analysis_id = analysis.id if analysis else None
        query_in_analysis = exists(
            select(1).where(MapPoint.analysis_id == analysis_id, MapPoint.query_id == Query.id)
        )
        document_in_analysis = exists(
            select(1).where(
                MapPoint.analysis_id == analysis_id, MapPoint.document_id == Document.id
            )
        )
        queries = await self.db.scalar(
            select(func.count())
            .select_from(Query)
            .where(Query.dataset_id == self.dataset_id, QUERY_ACTIVE, ~query_in_analysis)
        )
        documents = await self.db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.dataset_id == self.dataset_id, DOCUMENT_ACTIVE, ~document_in_analysis)
        )
        return int(queries or 0), int(documents or 0)

    async def _wait_for_analysis(
        self, key: str, title: str, missing: tuple[int, int], *, params: dict[str, Any]
    ) -> None:
        """뜻 분석 작업을 넣고, 그 뒤에 key 단계부터 이어 도는 작업(params)을 넣는다. 실행은 도는 중으로 둔다."""
        self.step_no = next(row["no"] for row in self.steps if row["key"] == key)
        self.update_step(key, status=STEP_RUNNING, plan=PLAN_WAIT_ANALYSIS)
        await self.save_steps()
        await self.say(
            f"새 질의 {missing[0]:,} · 문서 {missing[1]:,}이 뜻 분석에 없나?\n"
            f"분석을 다시 만든 뒤 {title}부터 이어 간다."
        )
        try:
            await semantic.start_analysis(self.db, dataset_id=self.dataset_id)
        except ConflictError:
            # 이미 만드는 중이다. 그 작업이 먼저 들어가 있으므로 이어 도는 작업보다 먼저 끝난다.
            await self.db.rollback()
        job = await jobs.enqueue_job(
            self.db,
            module=retrieval_service.MODULE_NAME,
            kind=HELPER_JOB_KIND,
            params=params,
            dataset_id=self.dataset_id,
        )
        await helper_service.resume_run(self.db, run_id=self.run_id, job_id=job.id)
        await self.db.commit()

    # ----- 재기 · 문 -----

    async def _checks(self) -> list[CheckRead]:
        overview = await overview_service.get_overview(self.db, dataset_id=self.dataset_id)
        return overview.checks

    async def _earlier_open_checks(self, index: int) -> list[OpenCheck]:
        """index 단계보다 앞 단계들의 검사 가운데 남은 것. 사람이 허락을 거절해 건너뛴 단계의 검사는 세지 않는다."""
        declined = {
            row["key"]
            for row in self.steps
            if row.get("status") == STEP_SKIPPED and row.get("plan") == PLAN_DECLINED
        }
        keys = {
            check for step in STEPS[:index] if step.key not in declined for check in step.checks
        }
        return [
            _open_check(check)
            for check in await self._checks()
            if check.key in keys and check.grade != GRADE_GOOD
        ]

    async def _measure(self, step: StepDef, checks: list[CheckRead] | None = None) -> Measure:
        """단계의 남은 문제: 검사마다 통과하지 못한 것의 수와 글."""
        found = [check for check in (checks or await self._checks()) if check.key in step.checks]
        open_checks = [check for check in found if check.grade != GRADE_GOOD]
        value = sum(max(check.view_count, 1) for check in open_checks)
        if step.key == "generate":
            # 입구와 상관없이 질의가 필요한 문서(문서만 모양의 문서 · 더한 문서 · 나눈 지문의 답 없는 청크)에 만든다.
            targets = len(await generation.target_documents(self.db, dataset_id=self.dataset_id))
            text = f"청크 {targets:,}" if targets else "없음"
            return Measure(value=targets, text=text, grade="bad" if targets else "good")
        text = " · ".join(_check_text(check) for check in open_checks)
        worst = (
            GRADE_BAD
            if any(check.grade == GRADE_BAD for check in open_checks)
            else (GRADE_WARN if open_checks else GRADE_GOOD)
        )
        if step.key == "mine":
            # 오답을 찾은 뒤 문서 풀이 늘었으면 모든 질의를 다시 찾는다(모자란 질의가 없어도).
            new_documents = await retrieval_service.count_unmined_documents(
                self.db, dataset_id=self.dataset_id
            )
            if new_documents:
                grown = f"새 문서 {new_documents:,} · 다시 찾기"
                return Measure(
                    value=value + new_documents,
                    text=f"{grown} · {text}" if text else grown,
                    grade=worst if open_checks else GRADE_WARN,
                )
        return Measure(value=value, text=text or "없음", grade=worst)

    # ----- 0 계획 -----

    async def _plan(self) -> None:
        """진단을 보이고 LLM이 계획을 말하게 한다(순서는 흐름을 따른다: 문서 → 질의 → 하드 네거티브)."""
        self.step_no = 0
        self.update_step(PLAN_KEY, status=STEP_RUNNING)
        await self.save_steps()
        started = time.monotonic()
        checks = await self._checks()
        lines = []
        for step in STEPS:
            measure = await self._measure(step, checks)
            if measure.value:
                self.update_step(step.key, plan=measure.text)
                lines.append(
                    f"- {STAGE_TITLES[step.stage]} · {step.title}: {measure.text} ({measure.grade})"
                )
        await self.save_steps()
        await self.start_report(REPORT_KEY, from_step=0)
        brief = (
            "진단:\n"
            + ("\n".join(lines) if lines else "- 고칠 문제 없음")
            + f"\n모양: {self.shape.value if self.shape else '—'}"
            + f"\nJev: {'연결됨' if self.jev_connection else '미연결'}"
            + "\n흐름 순서대로 고친다. 허락이 필요한 단계(문서 나누기 · 질의 만들기)는 계획을 보이고 기다린다."
            + "\nplan 도구로 한 번, 무엇부터 왜 고칠지 say로 말한다."
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
        """단계 하나를 돈다. 닿은 때 재서 문제가 없으면 건너뛴다. 규칙을 먼저 하고, 남은 것이 있고 LLM 몫이 있으면
        LLM이 판단 도구로 돈다. 허락을 기다리게 됐으면 STEP_ASK를 돌려준다."""
        self.step_no = next(row["no"] for row in self.steps if row["key"] == step.key)
        await self.check_stop()
        before = await self._measure(step)
        if before.value == 0:
            self.update_step(step.key, status=STEP_SKIPPED, plan="문제 없음")
            await self.save_steps()
            return STEP_SKIPPED
        if step.permission:
            await self._ask_permission(step, before)
            return STEP_ASK
        self.update_step(step.key, status=STEP_RUNNING)
        await self.save_steps()
        started = time.monotonic()
        state = StepState(def_=step)
        if step.key == "mine":
            await self._mine(state)
        else:
            tools = self._tools_for(state)
            if step.rules:
                await self.say(step.rule_say)
                for name in step.rules:
                    # 데이터 모양에 따라 없는 규칙도 있다.
                    if name in tools:
                        await self.check_stop()
                        await tools[name][1]({}, 0)
            now = await self._measure(step)
            needs_llm = now.value > 0 and bool(step.goal)
            if needs_llm:
                state.facts["llm"] += await self.tool_loop(
                    system=f"{SYSTEM_BASE}\n\n지금 단계: {step.title}\n할 일: {step.goal}",
                    brief=await self._brief(state, now),
                    tools={name: entry for name, entry in tools.items() if name not in step.rules},
                )
        await self._finish_step(step, state, before, started)
        return STEP_DONE

    async def _finish_step(
        self, step: StepDef, state: StepState, before: Measure, started: float
    ) -> None:
        after = await self._measure(step)
        await self.emit(
            HelperEventKind.RESULT,
            {"check": step.title, "from": before.text, "to": after.text, "grade": after.grade},
        )
        facts = {name: value for name, value in state.facts.items() if value}
        facts["time"] = seconds(started)
        self.update_step(
            step.key, status=STEP_DONE, facts=facts, delta=[before.text, after.text, after.grade]
        )
        await self.save_steps()

    async def _record(
        self, state: StepState, log: edits.ChangeLog, *, action: str, tool: str, samples: list[str]
    ) -> int:
        changed = await helper_changes.record(
            self.db,
            run_id=self.run_id,
            step=self.step_no,
            dataset_id=self.dataset_id,
            log=log,
            action=action,
            tool=tool,
            samples=samples,
        )
        state.facts["changed"] += changed
        return changed

    # ----- 허락 -----

    async def _ask_permission(self, step: StepDef, before: Measure) -> None:
        """허락 단계: 계획(시험 포함)을 보이고 실행을 asking으로 둔다."""
        self.update_step(step.key, status=STEP_ASK, plan=before.text)
        await self.save_steps()
        settings = await retrieval_service.find_settings(self.db, dataset_id=self.dataset_id)
        limit = settings.doc_max_tokens if settings else DEFAULT_DOC_MAX_TOKENS
        overlap = settings.chunk_overlap if settings else DEFAULT_CHUNK_OVERLAP
        has_header = settings.section_header if settings else True
        alert: dict[str, str] | None = None
        if step.key == "chunk":
            long_count = before.value
            # 구획(번호 단위)으로 나뉘는 문서는 길이로만 세면 크게 틀려서, 실제 규칙으로 나눠 본 평균으로 센다.
            sample = await edits.split_sample(
                self.db, dataset_id=self.dataset_id, limit=limit, overlap=overlap
            )
            long_documents = int(
                await self.db.scalar(
                    select(func.count()).where(
                        Document.dataset_id == self.dataset_id,
                        DOCUMENT_ACTIVE,
                        Document.token_count > limit,
                    )
                )
                or 0
            )
            pieces = round(sample.chunks_per_document * long_documents)
            await self.say(
                f"긴 문서를 나눌까?\n{limit}토큰을 넘는 문서 {long_count:,}개가 학습 때 뒤가 잘린다."
            )
            facts = [
                ["대상", f"{limit}토큰 넘는 문서 {long_count:,}"],
                [
                    "자르기",
                    (
                        f"구획(제목 줄 · 번호 단위) → 줄 → 문장 · 최대 {limit}토큰 · "
                        f"오버랩 {_overlap_word(overlap)}"
                    ),
                ],
                ["머리말", "청크마다 구획 경로" if has_header else "끔"],
                ["결과", f"청크 약 {pieces:,}"],
                ["판정", "답이 든 청크로 옮김"],
                ["되돌리기", "카드마다 · 실행 전체"],
            ]
            summary = (
                f"{limit}토큰 · 오버랩 {_overlap_word(overlap)} · "
                f"머리말 {'켬' if has_header else '끔'}"
            )
        else:
            plan = await generation.plan(self.db, dataset_id=self.dataset_id)
            trial = await self._trial_generation()
            await self.say(
                f"질의를 만들까?\n질의가 없는 청크 {plan.documents:,}개. 청크마다 후보 "
                f"{plan.per_chunk * generation.CANDIDATE_FACTOR}개를 만들고 걸러 "
                f"{plan.per_chunk}개를 남긴다. 모자라면 한 번 더 만든다."
            )
            facts = [
                [
                    "대상",
                    (
                        f"청크 {plan.documents:,} · 질의 안 만들 청크 {plan.skipped:,} · "
                        f"중복 묶음 {plan.duplicates:,} 뺌"
                    ),
                ],
                [
                    "만들기",
                    (
                        f"청크마다 {plan.per_chunk} · 후보 "
                        f"{plan.per_chunk * generation.CANDIDATE_FACTOR} · "
                        f"질문형 {plan.question_share}% · 검색어형 {100 - plan.question_share}%"
                    ),
                ],
                ["거르기", "되찾기 10위 · Jev ≥ 0.8 · 같은 질의 · 문맥 의존 · 쉬운 쌍 상한"],
                ["정답 더하기", "원 청크보다 위 · Jev ≥ 0.8"],
                [
                    "시험",
                    f"청크 {trial.documents} → 통과 {trial.outcome.added} / {trial.made}"
                    + (
                        f" · 다시 만듦 {trial.outcome.retried} · 모자람 {trial.outcome.short}"
                        if trial.outcome.retried
                        else ""
                    ),
                ],
                ["비용", f"약 {plan.tokens:,}토큰 · {self.llm_connection.model}"],
            ]
            summary = f"청크마다 {plan.per_chunk} · 질문형 {plan.question_share}%"
            # 문서 나누기를 건너뛰어 긴 문서가 남았으면: LLM은 앞부분만 보고 질의를 만든다.
            long_left = await self._measure(STEP_BY_KEY["chunk"])
            if long_left.value:
                alert = {
                    "text": f"긴 문서 {long_left.value:,} · 나누지 않음",
                    "sub": f"앞 {generation.DOCUMENT_PROMPT_CHARS:,}자만",
                    "action": ACTION_CHUNK_FIRST,
                    "label": "먼저 나누기",
                }
        # 앞 단계에 남은 주의(심각은 문이 이미 막았다)를 함께 싣는다. 예: 고를 반복 구간이 남으면
        # 떼기 전 글로 질의를 만들게 되므로, 사람이 알고 허락하게 한다.
        index = next(position for position, item in enumerate(STEPS) if item.key == step.key)
        checks = open_payload(await self._earlier_open_checks(index))
        permission: dict[str, Any] = {
            "step": step.key,
            "title": step.title,
            "facts": facts,
            "checks": checks,
            "settings": summary,
        }
        if alert is not None:
            permission["alert"] = alert
        await self.emit(HelperEventKind.PERMISSION, permission)
        await helper_service.finish_run(
            self.db, run_id=self.run_id, status=HelperRunStatus.ASKING, permission=permission
        )
        await self.db.commit()

    async def _trial_generation(self) -> "Trial":
        """허락 전 시험: 청크 몇 개로 만들어 거르기 · 한 번 더 만들기까지 해 본다. 넣지는 않는다."""
        documents = await generation.target_documents(
            self.db, dataset_id=self.dataset_id, limit=generation.TRIAL_DOCUMENTS
        )
        if not documents:
            return Trial(documents=0, made=0, outcome=generation.Outcome())
        outcome = await generation.generate(
            self.db,
            dataset_id=self.dataset_id,
            documents=documents,
            llm_connection=self.llm_connection,
            dry_run=True,
            transport=self.transport,
            embedding_transport=self.embedding_transport,
            jev_transport=self.jev_transport,
        )
        await helper_service.add_usage(
            self.db, run_id=self.run_id, llm_tokens=outcome.tokens, jev_calls=outcome.jev_calls
        )
        self.tokens += outcome.tokens
        await self._emit_generated(outcome, meta="시험")
        made = outcome.added + sum(outcome.rejected.values())
        return Trial(documents=len(documents), made=made, outcome=outcome)

    async def _emit_generated(self, outcome: generation.Outcome, *, meta: str) -> None:
        rejected = " · ".join(f"{reason} {count}" for reason, count in outcome.rejected.items())
        await self.emit(
            HelperEventKind.LLM,
            {
                "meta": f"{meta} · 통과 {outcome.added}"
                + (f" · 정답 더함 {outcome.relabeled}" if outcome.relabeled else "")
                + (f" · 다시 만듦 {outcome.retried}" if outcome.retried else "")
                + (f" · 모자람 {outcome.short}" if outcome.short else "")
                + (f" · 질의 안 만듦 {outcome.gave_up}" if outcome.gave_up else "")
                + (f" · 거름 {rejected}" if rejected else ""),
                "rows": [
                    {
                        "text": text,
                        "from": source,
                        "to": "통과" if passed else "거름",
                        "why": reason or "통과",
                    }
                    for text, source, passed, reason in outcome.samples[:JEV_TABLE_ROWS]
                ],
            },
        )

    async def _do_permitted(self, step: StepDef) -> None:
        """허락받은 일: 문서 나누기 또는 질의 만들기."""
        self.update_step(step.key, status=STEP_RUNNING)
        await self.save_steps()
        before = await self._measure(step)
        started = time.monotonic()
        state = StepState(def_=step)
        log = edits.ChangeLog()
        if step.key == "chunk":
            split_count, piece_count = await edits.split_long_documents(
                self.db, dataset_id=self.dataset_id, log=log
            )
            await self.db.flush()
            await self._record(
                state,
                log,
                action="문서 나누기",
                tool=f"문서 {split_count:,} → 청크 {piece_count:,}",
                samples=[],
            )
            state.facts["rule"] = 1
        else:
            documents = await generation.target_documents(
                self.db, dataset_id=self.dataset_id, limit=generation.MAX_DOCUMENTS_PER_RUN
            )

            async def progress(done: int, total: int) -> None:
                if self.job_id is not None:
                    await jobs.set_progress(self.db, job_id=self.job_id, done=done, total=total)
                    await self.db.commit()

            async def canceled() -> bool:
                return await helper_service.is_stop_requested(self.db, run_id=self.run_id)

            outcome = await generation.generate(
                self.db,
                dataset_id=self.dataset_id,
                documents=documents,
                llm_connection=self.llm_connection,
                log=log,
                on_progress=progress,
                is_canceled=canceled,
                transport=self.transport,
                embedding_transport=self.embedding_transport,
                jev_transport=self.jev_transport,
            )
            await helper_service.add_usage(
                self.db, run_id=self.run_id, llm_tokens=outcome.tokens, jev_calls=outcome.jev_calls
            )
            self.tokens += outcome.tokens
            await self._emit_generated(outcome, meta="질의 만들기")
            await self._record(
                state,
                log,
                action="새 질의 더하기",
                tool=f"청크 {len(documents):,}",
                samples=[text for text, _source, passed, _reason in outcome.samples if passed],
            )
            state.facts["llm"] += outcome.tokens
            state.facts["jev"] += outcome.jev_calls
        await self._finish_step(step, state, before, started)

    # ----- 3 오답 찾기 -----

    async def _mine(self, state: StepState) -> None:
        """규칙 단계: 학습할 모델로 오답을 찾아 붙인다(허락 없이, 되돌리기 가능)."""
        settings = await retrieval_service.find_settings(self.db, dataset_id=self.dataset_id)
        rank_from = settings.mine_rank_from if settings else 10
        rank_to = settings.mine_rank_to if settings else 200
        pool = settings.negative_pool if settings else 20
        new_documents = await retrieval_service.count_unmined_documents(
            self.db, dataset_id=self.dataset_id
        )
        if new_documents:
            await self.say(
                f"오답을 다시 찾아야 하나?\n오답을 찾은 뒤 문서가 {new_documents:,}개 늘었다. "
                "찾기로 붙인 오답을 떼고 모든 질의를 다시 찾는다(원본 오답 · 사람 판정은 그대로)."
            )
        await self.say(
            f"오답을 어디서 고를까?\n학습할 모델로 {rank_from}~{rank_to}위에서 가까운 것부터 질의마다 {pool}개까지 모으고, "
            "정답만큼 가까운 것 · 정답의 중복 묶음 · Jev가 답이라 한 것은 뺀다."
        )
        log = edits.ChangeLog()

        async def canceled() -> bool:
            return await helper_service.is_stop_requested(self.db, run_id=self.run_id)

        try:
            result = await mining.mine_negatives(
                self.db,
                dataset_id=self.dataset_id,
                job_id=self.job_id or 0,
                log=log,
                is_canceled=canceled,
                transport=self.embedding_transport,
                jev_transport=self.jev_transport,
            )
        except InvalidInputError as error:
            await self.notice(error.message, tone="warn")
            return
        if result is None:
            raise AgentStopped
        await self._record(
            state,
            log,
            action="오답 붙이기",
            tool=f"질의 {result.queries:,} · Jev가 뺀 {result.jev_rejected:,} · 모자람 {result.lacking:,}",
            samples=[],
        )
        state.facts["rule"] = 1
        state.facts["jev"] += result.jev_rejected
        if result.added == 0:
            await self.notice(
                f"오답 0 · {result.rank_from}~{result.rank_to}위에 고를 문서 없음 · 코퍼스를 늘리거나 범위를 바꿈",
                tone="warn",
            )

    # ----- 요약 · 도구 -----

    async def _brief(self, state: StepState, before: Measure) -> str:
        """단계를 시작할 때 LLM에 보일 요약(수와 몇 줄). 보기 질의는 r1 … 로 가리킬 수 있게 등록한다."""
        lines = [
            f"문제: {before.text} ({before.grade})",
            f"모양: {self.shape.value if self.shape else '—'}",
        ]
        key = state.def_.key
        settings = await retrieval_service.find_settings(self.db, dataset_id=self.dataset_id)
        analysis = await retrieval_service.latest_done_analysis(self.db, dataset_id=self.dataset_id)
        analysis_id = analysis.id if analysis else None
        if key == "queries":
            for problem in ("short_long", "context"):
                rows = (
                    await self.db.execute(
                        select(Query.id, Query.text, Query.source)
                        .where(
                            Query.dataset_id == self.dataset_id,
                            QUERY_INCLUDED,
                            problems.query_problem(
                                problem, settings=settings, analysis_id=analysis_id
                            ),
                        )
                        .order_by(Query.id)
                        .limit(BRIEF_SAMPLE_COUNT)
                    )
                ).all()
                if rows:
                    lines.append(f"{overview_service.CHECK_NAMES.get(problem, problem)} 보기:")
                    for query_id, text, source in rows:
                        ref = f"r{len(state.samples) + 1}"
                        state.samples[ref] = Sample(query_id=query_id, text=text, facts=source)
                        lines.append(f"- {ref} [{source}] {text}")
        elif key == "answers":
            lines.append(
                f"뜻 분석: {'있음' if analysis_id else '없음 (find_positives · accept_suggestions 못 씀)'}"
            )
        elif key == "documents":
            undecided = list(
                await self.db.scalars(
                    select(Repeat)
                    .where(Repeat.dataset_id == self.dataset_id, Repeat.decision.is_(None))
                    .order_by(Repeat.document_count.desc(), Repeat.id)
                    .limit(BRIEF_REPEATS)
                )
            )
            if undecided:
                total = await self.db.scalar(
                    select(func.count()).where(
                        Document.dataset_id == self.dataset_id, DOCUMENT_ACTIVE
                    )
                )
                lines.append(
                    f"고를 반복 구간 (문서 수 · 앞머리 · 꼬리 · 규칙 제안, 문서 전체 {total or 0:,}):"
                )
            for repeat in undecided:
                ref = f"p{len(state.repeats) + 1}"
                state.repeats[ref] = repeat.id
                samples = " / ".join(
                    sample[:80] for sample in repeat.samples[:BRIEF_REPEAT_SAMPLES]
                )
                lines.append(
                    f"- {ref} [{repeat.kind}] {repeat.text[:80]} · 문서 {repeat.document_count:,}"
                    f" · 앞 {repeat.head_count:,} · 뒤 {repeat.tail_count:,} · 제안 {repeat.suggestion}"
                    f" · 보기: {samples}"
                )
        lines.append(f"Jev: {'연결됨' if self.jev_connection else '미연결 (Jev 도구는 hold로)'}")
        return "\n".join(lines)

    def _tools_for(self, state: StepState) -> Tools:
        """단계의 도구들. 모든 단계에 hold · finish_step이 있다."""
        key = state.def_.key
        tools: Tools = {}

        def rule(name: str, description: str, action: str, run: Any) -> None:
            async def handler(_args: dict[str, Any], _tokens: int) -> dict[str, Any]:
                log = edits.ChangeLog()
                count = await run(log)
                await self.db.flush()
                await self._record(state, log, action=action, tool=name, samples=[])
                state.facts["rule"] = 1
                return {"바꿈": count}

            tools[name] = (agent_tool(name, description, {}, []), handler)

        dataset_id = self.dataset_id
        db = self.db
        if key == "documents":
            rule(
                "clean_documents",
                "깨진 글자 · HTML 찌꺼기를 정리한다.",
                "글자 정리",
                lambda log: edits.clean_documents(db, dataset_id=dataset_id, log=log),
            )
            rule(
                "merge_duplicates",
                "학습 글이 같은 문서를 하나로 합친다(판정은 남기는 문서로).",
                "같은 문서 합치기",
                lambda log: edits.merge_duplicate_documents(db, dataset_id=dataset_id, log=log),
            )
            self._repeat_tools(state, tools)
        elif key == "pieces":
            rule(
                "mark_skip_generation",
                "표만 · 목차 · 짧은 청크를 질의 안 만듦으로 표시한다.",
                "질의 안 만듦 표시",
                lambda log: edits.mark_skip_generation(db, dataset_id=dataset_id, log=log),
            )
        elif key == "queries":
            self._query_tools(state, tools, rule)
        elif key == "answers":
            self._answer_tools(state, tools, rule)
        elif key == "negatives":
            self._negative_tools(state, tools, rule)
        self._common_tools(state, tools)
        return tools

    def _repeat_tools(self, state: StepState, tools: Tools) -> None:
        """반복 구간 제안 도구: 결정은 바꾸지 않고 제안과 까닭만 적는다(사람이 고른다)."""
        db = self.db

        async def suggest(args: dict[str, Any], _tokens: int) -> dict[str, Any]:
            count = 0
            for item in args.get("items") or []:
                repeat_id = state.repeats.get(str(item.get("ref")))
                decision = str(item.get("decision") or "")
                if repeat_id is None or decision not in (
                    RepeatDecision.REMOVE.value,
                    RepeatDecision.KEEP.value,
                ):
                    continue
                reason = f"도우미 · {str(item.get('why') or '').strip()[:200]}"
                await repeats.suggest_repeat(
                    db, repeat_id=repeat_id, suggestion=RepeatDecision(decision), reason=reason
                )
                count += 1
            await db.commit()
            if count:
                await self.notice(f"반복 구간 제안 {count} · 결정은 사람이", tone="info")
            return {"제안": count}

        item = {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "decision": {"type": "string", "enum": ["remove", "keep"]},
                "why": {"type": "string"},
            },
            "required": ["ref", "decision", "why"],
        }
        tools["suggest_repeats"] = (
            agent_tool(
                "suggest_repeats",
                "반복 구간 보기(p1 …)마다 remove(학습 글에서 뗌) · keep(내용) 제안과 까닭을 적는다.",
                {"items": {"type": "array", "items": item}},
                ["items"],
            ),
            suggest,
        )

    def _query_tools(self, state: StepState, tools: Tools, rule: Any) -> None:
        db = self.db
        dataset_id = self.dataset_id

        async def by_problem(problem: str, log: edits.ChangeLog) -> int:
            settings = await retrieval_service.find_settings(db, dataset_id=dataset_id)
            analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
            condition = problems.query_problem(
                problem, settings=settings, analysis_id=analysis.id if analysis else None
            )
            return await edits.exclude_queries(
                db, dataset_id=dataset_id, condition=condition, log=log
            )

        rule(
            "exclude_short_long",
            "짧은 · 긴 질의를 학습에서 뺀다.",
            "학습에서 빼기",
            lambda log: by_problem("short_long", log),
        )
        rule(
            "exclude_duplicates",
            "같은 질의 여분을 뺀다(하나만 남긴다).",
            "학습에서 빼기",
            lambda log: by_problem("duplicate", log),
        )
        rule(
            "exclude_context",
            "문맥 의존 질의를 모두 뺀다.",
            "학습에서 빼기",
            lambda log: by_problem("context", log),
        )

        async def rewrite(args: dict[str, Any], _tokens: int) -> dict[str, Any]:
            log = edits.ChangeLog()
            done = []
            for item in args.get("items") or []:
                sample = state.samples.get(str(item.get("ref")))
                text = str(item.get("text") or "").strip()
                query = await db.get(Query, sample.query_id) if sample else None
                if query is None or not text or text == query.text:
                    continue
                log.add(
                    target=HelperTarget.QUERY,
                    field_name=HelperField.TEXT,
                    query_id=query.id,
                    before=query.text,
                    after=text,
                )
                query.text = text
                query.text_hash = make_text_hash(text)
                query.row_version += 1
                done.append(text)
            await db.flush()
            await self._record(state, log, action="질의 고치기", tool="rewrite", samples=done)
            return {"고침": len(done)}

        item = {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "text": {"type": "string"}},
            "required": ["ref", "text"],
        }
        tools["rewrite"] = (
            agent_tool(
                "rewrite",
                "보기 질의(ref)를 고유 이름을 넣은 새 글로 고친다.",
                {"items": {"type": "array", "items": item}},
                ["items"],
            ),
            rewrite,
        )

    def _answer_tools(self, state: StepState, tools: Tools, rule: Any) -> None:
        db = self.db
        dataset_id = self.dataset_id

        def accept_confirmed(kind: SuggestionKind) -> Any:
            async def run(log: edits.ChangeLog) -> int:
                analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
                if analysis is None:
                    return 0
                return await edits.accept_suggestions(
                    db,
                    dataset_id=dataset_id,
                    analysis_id=analysis.id,
                    kind=kind,
                    confirmed_only=True,
                    log=log,
                )

            return run

        rule(
            "accept_missing_positives",
            "Jev가 확인한 빠진 정답 제안을 정답으로 붙인다.",
            "정답 붙이기",
            accept_confirmed(SuggestionKind.MISSING_POSITIVE),
        )
        rule(
            "accept_suspect_positives",
            "Jev가 확인한 정답 의심 제안의 정답을 뗀다.",
            "정답 떼기",
            accept_confirmed(SuggestionKind.SUSPECT_POSITIVE),
        )

        async def resolve_conflicts(_args: dict[str, Any], _tokens: int) -> dict[str, Any]:
            if self.jev_connection is None:
                return {"오류": "Jev 미연결"}
            conflicts = list(
                (
                    await db.execute(
                        select(Judgment, Query.text, DOCUMENT_INPUT)
                        .join(Query, Query.id == Judgment.query_id)
                        .join(Document, Document.id == Judgment.document_id)
                        .where(
                            Judgment.dataset_id == dataset_id,
                            Judgment.conflict.is_(True),
                            QUERY_ACTIVE,
                            DOCUMENT_ACTIVE,
                        )
                        .limit(MAX_JEV_PER_STEP)
                    )
                ).tuples()
            )
            log = edits.ChangeLog()
            rows = []
            held = 0
            # Jev가 확신한 것도 LLM이 같은 답일 때만 바꾼다(정답을 바꾸는 판단을 Jev 하나로 하지 않는다).
            sure: list[tuple[int, str, str]] = []
            verdicts: dict[int, tuple[Judgment, float, str]] = {}
            for index, (judgment, query_text, document_text) in enumerate(conflicts):
                probability = await self._ask_jev(state, query_text, document_text)
                if probability is None:
                    continue
                answer = helper_fixes.jev_answer(probability)
                if answer is None:
                    held += 1
                else:
                    sure.append((index, query_text, document_text))
                    verdicts[index] = (judgment, probability, answer)
            confirmed = await helper_fixes.judge_pairs(self, sure) if sure else {}
            decisions: dict[int, str] = {}
            for index, _query, _document in sure:
                judgment, probability, answer = verdicts[index]
                if confirmed.get(index) != answer:
                    held += 1
                    decisions[index] = "llm"
                    continue
                decisions[index] = "apply"
                await edits.set_grade(
                    db,
                    dataset_id=dataset_id,
                    query_id=judgment.query_id,
                    document_id=judgment.document_id,
                    grade=POSITIVE_MIN_GRADE if answer == helper_fixes.YES else 0,
                    teacher_score=probability,
                    log=log,
                )
            for index, (_judgment, query_text, document_text) in enumerate(conflicts):
                if len(rows) >= JEV_TABLE_ROWS or index not in verdicts:
                    continue
                probability = verdicts[index][1]
                rows.append(
                    {
                        "text": query_text,
                        "from": document_text[:80],
                        "choice": "예" if probability >= 0.5 else "아니오",
                        "prob": round(probability, 3),
                        "decision": decisions.get(index, "llm"),
                    }
                )
            await self.emit(
                HelperEventKind.JEV,
                {"meta": f"판정 충돌 {len(conflicts)} · 보류 {held}", "rows": rows, "more": ""},
            )
            await db.flush()
            await self._record(
                state, log, action="판정 바꾸기", tool="resolve_conflicts", samples=[]
            )
            if held:
                await helper_service.add_usage(db, run_id=self.run_id, held=held)
                state.facts["held"] += held
            return {"바꿈": len(log.entries), "보류": held}

        tools["resolve_conflicts"] = (
            agent_tool(
                "resolve_conflicts",
                "판정 충돌 쌍을 Jev로 정답 · 오답으로 정한다(LLM이 같은 답일 때만, 엇갈리면 보류).",
                {},
                [],
            ),
            resolve_conflicts,
        )

        async def find_positives(_args: dict[str, Any], _tokens: int) -> dict[str, Any]:
            analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
            if analysis is None:
                return {"오류": NO_ANALYSIS_TOOL_MESSAGE}
            if self.jev_connection is None:
                return {"오류": "Jev 미연결"}
            queries = list(
                (
                    await db.execute(
                        select(Query.id, Query.text)
                        .where(
                            Query.dataset_id == dataset_id,
                            QUERY_INCLUDED,
                            ~problems.positive_exists(),
                        )
                        .order_by(Query.id)
                        .limit(MAX_JEV_PER_STEP // FIND_POSITIVE_TOP)
                    )
                ).tuples()
            )
            log = edits.ChangeLog()
            rows = []
            found = 0
            # Jev가 고른 문서도 LLM이 예일 때만 정답으로 붙인다(Jev 하나로 정답을 새로 붙이지 않는다).
            picks: list[tuple[int, str, str]] = []
            picked: dict[int, tuple[int, int, float]] = {}
            for query_id, query_text in queries:
                ranked = (
                    await db.execute(
                        select(Document.id, DOCUMENT_INPUT)
                        .join(Ranking, Ranking.document_id == Document.id)
                        .where(
                            Ranking.analysis_id == analysis.id,
                            Ranking.query_id == query_id,
                            DOCUMENT_ACTIVE,
                        )
                        .order_by(Ranking.rank)
                        .limit(FIND_POSITIVE_TOP)
                    )
                ).tuples()
                for document_id, document_text in ranked:
                    probability = await self._ask_jev(state, query_text, document_text)
                    if probability is not None and probability >= judging.CONFIRM_PROBABILITY:
                        key = len(picks)
                        picks.append((key, query_text, document_text))
                        picked[key] = (query_id, document_id, probability)
                        break
            confirmed = await helper_fixes.judge_pairs(self, picks) if picks else {}
            for key, query_text, document_text in picks:
                query_id, document_id, probability = picked[key]
                is_agreed = confirmed.get(key) == helper_fixes.YES
                if is_agreed:
                    await edits.set_grade(
                        db,
                        dataset_id=dataset_id,
                        query_id=query_id,
                        document_id=document_id,
                        grade=POSITIVE_MIN_GRADE,
                        teacher_score=probability,
                        log=log,
                    )
                    found += 1
                if len(rows) < JEV_TABLE_ROWS:
                    rows.append(
                        {
                            "text": query_text,
                            "from": document_text[:80],
                            "choice": "예",
                            "prob": round(probability, 3),
                            "decision": "apply" if is_agreed else "llm",
                        }
                    )
            await self.emit(
                HelperEventKind.JEV,
                {"meta": f"정답 없는 질의 {len(queries)} · 찾음 {found}", "rows": rows, "more": ""},
            )
            await db.flush()
            await self._record(state, log, action="정답 붙이기", tool="find_positives", samples=[])
            return {"찾음": found, "못 찾음": len(queries) - found}

        tools["find_positives"] = (
            agent_tool(
                "find_positives",
                "정답 없는 질의의 기준 검색 상위 문서를 Jev에 물어 정답을 붙인다(LLM도 예일 때만).",
                {},
                [],
            ),
            find_positives,
        )
        rule(
            "exclude_no_positive",
            "정답이 없는 질의를 학습에서 뺀다.",
            "학습에서 빼기",
            lambda log: edits.exclude_queries(
                db, dataset_id=dataset_id, condition=~problems.positive_exists(), log=log
            ),
        )

    def _negative_tools(self, state: StepState, tools: Tools, rule: Any) -> None:
        db = self.db
        dataset_id = self.dataset_id

        async def accept_false_negatives(log: edits.ChangeLog) -> int:
            analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
            if analysis is None:
                return 0
            return await edits.accept_suggestions(
                db,
                dataset_id=dataset_id,
                analysis_id=analysis.id,
                kind=SuggestionKind.FALSE_NEGATIVE,
                confirmed_only=True,
                log=log,
            )

        async def unconfirmed(*, partial: bool) -> list[Suggestion]:
            """확인되지 않은 거짓 오답 제안: 답의 일부만(예 0.5~0.8) 또는 하드 오답(예 0.5 아래)."""
            analysis = await retrieval_service.latest_done_analysis(db, dataset_id=dataset_id)
            if analysis is None:
                return []
            band = (
                Suggestion.jev_probability >= PARTIAL_ANSWER_PROBABILITY
                if partial
                else Suggestion.jev_probability < PARTIAL_ANSWER_PROBABILITY
            )
            return list(
                await db.scalars(
                    select(Suggestion).where(
                        Suggestion.analysis_id == analysis.id,
                        Suggestion.kind == SuggestionKind.FALSE_NEGATIVE.value,
                        Suggestion.decision.is_(None),
                        Suggestion.is_confirmed.is_(False),
                        band,
                    )
                )
            )

        async def drop_false_negatives(log: edits.ChangeLog) -> int:
            count = 0
            for suggestion in await unconfirmed(partial=True):
                judgment = await db.get(Judgment, (suggestion.query_id, suggestion.document_id))
                if judgment is not None and judgment.grade < POSITIVE_MIN_GRADE:
                    await edits.remove_judgment(db, judgment, log=log)
                    count += 1
                suggestion.decision = SuggestionDecision.ACCEPTED.value
            return count

        async def keep_hard_negatives(_args: dict[str, Any], _tokens: int) -> dict[str, Any]:
            kept = await unconfirmed(partial=False)
            for suggestion in kept:
                suggestion.decision = SuggestionDecision.KEPT.value
            await db.commit()
            return {"유지": len(kept)}

        tools["keep_hard_negatives"] = (
            agent_tool(
                "keep_hard_negatives",
                "Jev 예 0.5 아래인 거짓 오답 제안을 하드 오답으로 남긴다(유지).",
                {},
                [],
            ),
            keep_hard_negatives,
        )

        rule(
            "accept_false_negatives",
            "Jev가 확인한 거짓 오답을 정답으로 바꾼다.",
            "정답 붙이기",
            accept_false_negatives,
        )
        rule(
            "drop_false_negatives",
            "답의 일부만 담은 거짓 오답(Jev 예 0.5~0.8)을 오답에서 뗀다.",
            "오답 떼기",
            drop_false_negatives,
        )
        rule(
            "remove_same_negatives",
            "정답과 같은 중복 묶음(본문이 같거나 근접 중복)의 오답을 뗀다.",
            "오답 떼기",
            lambda log: edits.remove_same_negatives(db, dataset_id=dataset_id, log=log),
        )

    def _common_tools(self, state: StepState, tools: Tools) -> None:
        async def hold(args: dict[str, Any], _tokens: int) -> dict[str, Any]:
            why = str(args.get("why") or "")
            items = []
            for ref in args.get("refs") or []:
                sample = state.samples.get(str(ref))
                if sample is not None:
                    items.append({"text": sample.text, "facts": sample.facts, "why": why})
            count = len(items) or int(args.get("count") or 0)
            if items:
                await self.emit(HelperEventKind.HOLD, {"items": items})
            if count:
                await helper_service.add_usage(self.db, run_id=self.run_id, held=count)
                state.facts["held"] += count
            return {"보류": count}

        tools["hold"] = (
            agent_tool(
                "hold",
                "근거가 없는 것을 사람에게 넘긴다(보기 ref 또는 수).",
                {
                    "refs": {"type": "array", "items": {"type": "string"}},
                    "count": {"type": "integer"},
                    "why": {"type": "string"},
                },
                ["why"],
            ),
            hold,
        )

        async def finish(_args: dict[str, Any], _tokens: int) -> dict[str, Any]:
            return {"끝": True}

        tools[FINISH_STEP_TOOL] = (
            agent_tool(FINISH_STEP_TOOL, "이 단계를 끝낸다.", {}, []),
            finish,
        )

    async def _ask_jev(self, state: StepState, query: str, document: str) -> float | None:
        """Jev에 한 번 묻고 쓴 수를 센다. 실패하면 Jev 없이 이어 간다."""
        if self.jev_connection is None:
            return None
        try:
            probability = await judging.ask_contains(
                self.jev_connection, query=query, document=document, transport=self.jev_transport
            )
        except ExternalServiceError as error:
            await self.notice(error.message, tone="warn")
            self.jev_connection = None
            return None
        await helper_service.add_usage(self.db, run_id=self.run_id, jev_calls=1)
        state.facts["jev"] += 1
        return probability


# ---------- 작은 도움 ----------


def _is_gate(index: int) -> bool:
    """문을 두는 단계인지: 허락 단계이거나 흐름 단계(문서 → 질의 → 하드 네거티브)의 첫 단계."""
    step = STEPS[index]
    starts_stage = index > 0 and STEPS[index - 1].stage != step.stage
    return step.permission or starts_stage


def _check_text(check: CheckRead) -> str:
    """검사 한 줄 글 (이름 · 값 · 단위)."""
    return f"{overview_service.CHECK_NAMES.get(check.key, check.key)} {check.value}{check.unit}"


def _open_check(check: CheckRead) -> OpenCheck:
    name = overview_service.CHECK_NAMES.get(check.key, check.key)
    value = _check_text(check).removeprefix(name).strip()
    return OpenCheck(name=name, value=value, grade=check.grade)


# ---------- 작업 처리 (jobs.py가 부른다) ----------


async def run_helper(
    db: AsyncSession,
    *,
    run_id: int,
    resume: str | None = None,
    approved: bool = False,
    continue_from: str | None = None,
    fix: list[str] | None = None,
    round_no: int = 0,
    waited_fix: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    jev_transport: httpx.AsyncBaseTransport | None = None,
    embedding_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """실행 한 번(또는 허락 뒤 · 분석 뒤 이어 돌기 · AI로 고치기)을 끝까지 돈다.

    멈춤 · 상한 · LLM 실패는 실행 상태와 알림으로 남긴다.
    """
    run = await helper_service.get_run(db, run_id=run_id)
    if run.status != HelperRunStatus.RUNNING:
        return
    try:
        llm_connection = await connections.get_connection(db, role=ConnectionRole.LLM)
        if llm_connection is None or llm_connection.model is None:
            raise ExternalServiceError(LLM_NOT_CONNECTED_MESSAGE)
        jev_connection = await connections.get_connection(db, role=ConnectionRole.JEV)
        helper = Helper(
            db,
            run=run,
            llm_connection=llm_connection,
            jev_connection=jev_connection,
            transport=transport,
            jev_transport=jev_transport,
            embedding_transport=embedding_transport,
        )
        await helper.run(
            resume=resume,
            approved=approved,
            continue_from=continue_from,
            fix=fix,
            round_no=round_no,
            waited_fix=waited_fix,
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
    """너무 여러 번 끊긴 실행을 실패로 둔다(작업 실행기의 뒷정리)."""
    await end_run(db, run_id=run_id, status=HelperRunStatus.FAILED, message=message)
