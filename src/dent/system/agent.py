"""LLM 도우미 엔진의 바탕 (모든 모듈이 같이 쓴다): 단계 줄 · 말 · 사건 · 멈추기 · 토큰 상한 · 도구 되풀이.

도우미는 채팅이 아니다. 진단을 읽고, 스스로 묻고 답하며, 규칙 · Jev · LLM으로 데이터를 고친다.
무엇을 어떤 순서로 고칠지(단계 · 도구 · 바꾼 데이터의 전 · 후)는 모듈이 Agent를 이어 받아 정하고,
여기에는 모든 모듈이 같은 것만 둔다:
- LLM에 묻기(쓴 토큰을 실행에 더하고 상한에 닿으면 멈춤)
- 말하기(도구마다 받은 say를 줄마다 사건으로, '?'로 끝나는 줄은 스스로 묻는 말)
- 사건 적기 · 알림 · [멈추기] 확인 · 단계 줄 바꾸기
- 한 단계의 도구 되풀이: LLM이 finish_step을 부를 때까지(또는 상한까지) 도구를 부르게 한다
- 문(gate): 허락을 묻거나 다음 흐름 단계로 가기 전에 앞 단계에 심각이 남았으면 막는다(block, 허락 카드에는 남은 주의)
- 보고: 실행(또는 AI로 고치기) 처음과 끝의 진단(모듈의 snapshot)을 견줘 고친 검사 · 바꿈 · 비용과 LLM 두 줄을 싣는다
- AI로 고치기: 도우미가 끝난 뒤 '남은 것'(모듈의 LeftItem) 가운데 사람이 고른 것을 같은 실행에 단계로 붙여 고친다
- 실행을 멈춤 · 실패로 끝내기

모든 모듈의 도우미가 지키는 흐름(docs/principles.md '도우미가 지킬 일곱 가지'):
규칙 먼저(판단이 필요 없는 고치기는 LLM이 고르지 않고 단계 처음에 한다) → 닿을 때 잰다(단계는 그 단계에 닿은 때의 값으로
돌지 말지 정한다) → 문(심각이 남으면 허락 · 다음 흐름으로 가지 않는다) → 보고로 끝난다(허락을 기다리며 붙잡지 않는다)
→ 남은 것은 지금 진단으로 다시 세어 보이고, 사람이 고른 것만 AI로 고친다.

실행 한 줄 · 사건 기록은 system/helper.py가 다룬다. 이 엔진은 그것을 부를 뿐이다.
"""

import json
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.system import helper as helper_service
from dent.system import jobs, llm
from dent.system.db import flush_or_conflict
from dent.system.exceptions import ConflictError, ExternalServiceError
from dent.system.llm import ChatMessage, ToolSpec
from dent.system.models import Connection, HelperEvent, HelperEventKind, HelperRun, HelperRunStatus

# 단계 상태 (화면의 단계 줄)
STEP_TODO = "todo"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_SKIPPED = "skipped"
STEP_ASK = "ask"
STEP_LOCKED = "locked"
# 문에 걸림: 앞 단계에 심각이 남아 이 단계로 가지 않았다
STEP_BLOCKED = "blocked"

# 단계 줄의 한 줄 글 (허락을 거절해 건너뛴 단계 · 문에 걸린 단계)
PLAN_DECLINED = "건너뜀"
PLAN_BLOCKED = "막힘"

# 문에 걸렸을 때 실행에 남기는 까닭 (화면의 상태 풍선)
BLOCKED_MESSAGE = "앞 단계에 심각이 남아 멈춤 · 고친 뒤 다시 시작"

# 등급 (진단과 같은 낱말)
GRADE_GOOD = "good"
GRADE_WARN = "warn"
GRADE_BAD = "bad"

# 종합 상태 낱말 (진단과 같다): 심각이 있으면 · 주의만 있으면 · 없으면
OVERALL_WORDS = {GRADE_BAD: "준비 안 됨", GRADE_WARN: "확인 필요", GRADE_GOOD: "준비됨"}

# 보고 단계 · AI로 고치기
REPORT_KEY = "report"
REPORT_TITLE = "보고"
FIX_TITLE = "AI로 고치기"
FIX_REPORT_TITLE = f"{REPORT_TITLE} · {FIX_TITLE}"
# AI로 고치기 무리 번호의 바탕(모듈의 흐름 단계 1~3과 겹치지 않게) · 무리 머리의 아이콘
FIX_STAGE_BASE = 100
FIX_STAGE_ICON = "sparkles"
FIX_BUSY_MESSAGE = "도우미가 일하는 중입니다. 끝난 뒤 고쳐 주세요."

# 보고에 보이는 고친 검사 줄 수 · LLM 두 줄 요약의 최대 토큰
REPORT_MAX_ROWS = 8
REPORT_SUMMARY_TOKENS = 300

# 남은 것의 묶음: AI로 고칠 수 있음 · 직접 권장(고를 수는 있다) · AI로 못 고침
LEFT_AI = "ai"
LEFT_DIRECT = "direct"
LEFT_BLOCKED = "blocked"

# 한 단계의 LLM 답에 도구가 없을 때 한 번 재촉하는 말
NUDGE_MESSAGE = "도구로 이어 가거나 finish_step을 불러라."

# 도구가 없는 답이 이만큼 이어지면 단계를 끝낸다(같은 말을 되풀이하는 것을 막는다).
MAX_NUDGES = 1

# 단계를 끝내는 도구 이름
FINISH_STEP_TOOL = "finish_step"

# 도구 처리 함수: (LLM이 준 인자, 그 답에 쓴 토큰) → LLM에 돌려줄 결과
ToolHandler = Callable[[dict[str, Any], int], Awaitable[dict[str, Any]]]

# 단계 하나의 도구들: 이름 → (알림, 처리 함수)
Tools = dict[str, tuple[ToolSpec, ToolHandler]]


class AgentStopped(Exception):
    """사람이 [멈추기]를 눌렀다."""


class AgentBudgetExceeded(Exception):
    """토큰 상한에 닿았다."""


def agent_tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> ToolSpec:
    """도구 알림. 모든 도구는 say(스스로 묻고 답한 짧은 말)를 받는다."""
    return ToolSpec(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "say": {
                    "type": "string",
                    "description": "스스로 묻고('?'로 끝나는 한 줄) 답한 짧은 한국어",
                },
                **properties,
            },
            "required": ["say", *required],
        },
    )


def seconds(started: float) -> str:
    """time.monotonic()으로 잰 시작에서 지금까지의 초 (화면의 걸린 시간)."""
    return f"{time.monotonic() - started:.1f}초"


def with_step(steps: list[dict[str, Any]], key: str, **fields: Any) -> list[dict[str, Any]]:
    """단계 줄에서 key 단계의 칸만 바꾼 새 목록."""
    return [{**row, **fields} if row.get("key") == key else dict(row) for row in steps]


def more(total: int, shown: int) -> str:
    """보인 것 밖의 수. 예: '+ 12'. 다 보였으면 빈 문자열."""
    return f"+ {total - shown}" if total > shown else ""


@dataclass(frozen=True)
class OpenCheck:
    """통과하지 못하고 남은 검사 하나. 문 · 허락 카드 · 끝 알림에 같은 모양으로 싣는다."""

    # 검사 이름 (진단과 같은 낱말)
    name: str

    # 지금 값 (단위까지. 예: '2건')
    value: str

    # 등급 (warn · bad)
    grade: str

    def payload(self) -> dict[str, str]:
        """사건에 싣는 모양 {name, value, grade}."""
        return {"name": self.name, "value": self.value, "grade": self.grade}


@dataclass(frozen=True)
class LeftAdd:
    """남은 것 줄에서 사람이 고칠 수 있는 '더할 수' 한 칸. 예: 라벨마다 새 문장 수(분류의 라벨 균형)."""

    # 칸 열쇠 (AI로 고치기에 보낸다. 예: 라벨 번호)
    key: str

    # 칸 이름 · 지금 수 · 계획한 더할 수 · 더할 수의 상한
    name: str
    now: int
    add: int
    max: int


@dataclass(frozen=True)
class LeftItem:
    """도우미가 끝난 뒤 남은 것 한 줄. 모듈이 지금 진단으로 검사마다 만든다(저장하지 않는다)."""

    # 검사 열쇠 (AI로 고치기의 단계 열쇠가 된다)
    key: str

    # 검사 이름 · 값 · 단위 · 작은 글 (진단과 같은 낱말)
    name: str
    value: str
    unit: str
    sub: str

    # 등급 (warn · bad)
    grade: str

    # 묶음 (ai · direct · blocked)
    group: str

    # AI로 고치면 하는 일 (blocked면 빈 글)
    how: str = ""

    # 어림 비용: LLM 토큰 · Jev 호출
    tokens: int = 0
    jev: int = 0

    # AI로 못 고칠 때 사람이 할 일 (blocked만)
    action: str = ""

    # → 로 가는 곳을 모듈 화면이 정할 때 쓰는 대상 · 문제 거르기 (없으면 빈 글)
    view_target: str = ""
    view_problem: str = ""

    # 사람이 고칠 수 있는 더할 수 칸들과 그 표의 작은 글 (없으면 빈 목록)
    adds: list[LeftAdd] = field(default_factory=list)
    adds_note: str = ""


# AI로 고치기의 고치는 함수: 그 단계의 사실(changed · llm · jev · rule)을 돌려준다
Fixer = Callable[[], Awaitable[dict[str, Any]]]


def parse_json_object(content: str) -> Any:
    """LLM 답에서 첫 { … 마지막 }을 JSON으로 읽는다(앞뒤 글 · 코드 울타리가 있어도). 모양이 틀리면 None."""
    start, end = (content or "").find("{"), (content or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(content[start : end + 1])
    except ValueError:
        return None


def overall_of(checks: list[dict[str, Any]]) -> str:
    """검사들의 가장 나쁜 등급 (bad · warn · good)."""
    grades = {check.get("grade") for check in checks}
    if GRADE_BAD in grades:
        return GRADE_BAD
    return GRADE_WARN if GRADE_WARN in grades else GRADE_GOOD


def fix_step_key(round_no: int, key: str) -> str:
    return f"fix:{round_no}:{key}"


def fix_report_key(round_no: int) -> str:
    return f"{REPORT_KEY}:{round_no}"


def report_step_row(no: int) -> dict[str, Any]:
    """실행의 마지막 단계 줄: 보고."""
    return {"key": REPORT_KEY, "no": no, "title": REPORT_TITLE, "status": STEP_TODO}


def append_fix_steps(
    steps: list[dict[str, Any]], items: list[tuple[str, str]]
) -> tuple[list[dict[str, Any]], int]:
    """단계 줄 끝에 AI로 고치기 한 번(고른 검사마다 한 단계 + 보고)을 붙인다. (새 단계 줄, 몇 번째)."""
    round_no = 1 + max((int(row.get("fix_round", 0)) for row in steps), default=0)
    no = 1 + max((int(row["no"]) for row in steps), default=-1)
    stage = {
        "stage": FIX_STAGE_BASE + round_no,
        "stage_title": FIX_TITLE if round_no == 1 else f"{FIX_TITLE} · {round_no}",
        "stage_icon": FIX_STAGE_ICON,
        "fix_round": round_no,
    }
    rows = []
    for index, (key, title) in enumerate(items):
        rows.append(
            {
                "key": fix_step_key(round_no, key),
                "no": no + index,
                "title": title,
                "status": STEP_TODO,
                "check": key,
                **stage,
            }
        )
    rows.append(
        {
            "key": fix_report_key(round_no),
            "no": no + len(items),
            "title": REPORT_TITLE,
            "status": STEP_TODO,
            **stage,
        }
    )
    return [*[dict(row) for row in steps], *rows], round_no


async def start_fix(
    db: AsyncSession,
    *,
    module: str,
    dataset_id: int,
    job_kind: str,
    model: str | None,
    items: list[tuple[str, str]],
    adds: dict[str, dict[str, int]] | None = None,
) -> HelperRun:
    """AI로 고치기를 시작한다: 가장 최근 실행에 단계를 붙이고(없으면 새 실행) 작업을 넣는다. 커밋한다.

    adds는 사람이 고친 더할 수({검사: {칸: 수}})로, 작업에 실어 모듈의 고치는 함수가 읽는다.
    고르는 검사 · 연결은 모듈이 먼저 확인한다. 도는 중이거나 허락을 기다리는 실행이 있으면 ConflictError.
    """
    latest = await helper_service.latest_run(db, module=module, dataset_id=dataset_id)
    is_busy = latest is not None and latest.status in (
        HelperRunStatus.RUNNING,
        HelperRunStatus.ASKING,
    )
    if is_busy:
        raise ConflictError(FIX_BUSY_MESSAGE)
    run = latest
    if run is None:
        run = helper_service.new_run(module=module, dataset_id=dataset_id, model=model, steps=[])
        db.add(run)
        await flush_or_conflict(db, message=FIX_BUSY_MESSAGE)
    steps, round_no = append_fix_steps(list(run.steps), items)
    first_no = next(row["no"] for row in steps if row.get("fix_round") == round_no)
    job = await jobs.enqueue_job(
        db,
        module=module,
        kind=job_kind,
        params={
            "run_id": run.id,
            "fix": [key for key, _ in items],
            "round": round_no,
            "adds": adds or {},
        },
        dataset_id=dataset_id,
    )
    await helper_service.set_steps(db, run_id=run.id, steps=steps, step_now=first_no)
    await helper_service.resume_run(db, run_id=run.id, job_id=job.id)
    await db.commit()
    return await helper_service.get_run(db, run_id=run.id)


def blocking(checks: list[OpenCheck]) -> list[OpenCheck]:
    """문을 막는 검사(심각)만."""
    return [check for check in checks if check.grade == GRADE_BAD]


def open_payload(checks: list[OpenCheck]) -> list[dict[str, str]]:
    """남은 검사들을 사건에 싣는 모양으로. 심각을 앞에 둔다."""
    ordered = sorted(checks, key=lambda check: check.grade != GRADE_BAD)
    return [check.payload() for check in ordered]


class Agent:
    """도우미 실행 한 번의 바탕. 작업 실행기의 세션 하나로 돌고, 사건마다 커밋해 화면이 바로 보게 한다.

    모듈은 이것을 이어 받아 단계 · 도구를 더하고, system_prompt · max_run_tokens 등을 정한다.
    """

    # 모든 단계의 시스템 지시 (모듈이 정한다)
    system_prompt: str = ""

    # 실행 한 번에 쓰는 LLM 토큰 상한 (입력 + 출력). 닿으면 그 자리에서 멈춘다.
    max_run_tokens: int = 80_000

    # 한 단계에서 LLM과 주고받는 최대 횟수. 넘으면 그 단계를 끝낸다.
    max_turns_per_step: int = 8

    # LLM 답 한 번의 최대 토큰
    max_reply_tokens: int = 1_500

    def __init__(
        self,
        db: AsyncSession,
        *,
        run: HelperRun,
        llm_connection: Connection,
        jev_connection: Connection | None,
        transport: httpx.AsyncBaseTransport | None,
        jev_transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self.db = db
        self.run_id = run.id
        self.dataset_id = run.dataset_id
        self.steps: list[dict[str, Any]] = [dict(step) for step in run.steps]
        self.llm_connection = llm_connection
        self.jev_connection = jev_connection
        self.transport = transport
        self.jev_transport = jev_transport
        self.tokens = 0
        self.step_no = 0

    # ----- LLM -----

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        *,
        max_tokens: int | None = None,
    ) -> llm.ChatReply:
        """LLM에 한 번 묻고 쓴 토큰을 실행에 더한다. 상한에 닿으면 AgentBudgetExceeded."""
        if self.tokens >= self.max_run_tokens:
            raise AgentBudgetExceeded
        reply = await llm.chat(
            self.llm_connection,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens or self.max_reply_tokens,
            transport=self.transport,
        )
        used = reply.input_tokens + reply.output_tokens
        self.tokens += used
        await helper_service.add_usage(self.db, run_id=self.run_id, llm_tokens=used)
        await self.db.commit()
        return reply

    async def tool_loop(self, *, system: str, brief: str, tools: Tools) -> int:
        """한 단계의 도구 되풀이: LLM이 finish_step을 부를 때까지 도구를 부르게 한다. 쓴 토큰을 돌려준다.

        도구가 없는 답이 오면 한 번 재촉하고, 또 없으면 끝낸다. 도구를 부르기 전마다 [멈추기]를 본다.
        """
        specs = [spec for spec, _ in tools.values()]
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=brief),
        ]
        used = 0
        nudges = 0
        for _turn in range(self.max_turns_per_step):
            await self.check_stop()
            reply = await self.chat(messages, specs)
            turn_tokens = reply.input_tokens + reply.output_tokens
            used += turn_tokens
            messages.append(
                ChatMessage(
                    role="assistant", content=reply.content, tool_calls=tuple(reply.tool_calls)
                )
            )
            said: set[str] = set()
            await self.say_once(reply.content, said=said)
            if not reply.tool_calls:
                nudges += 1
                if nudges > MAX_NUDGES:
                    break
                messages.append(ChatMessage(role="user", content=NUDGE_MESSAGE))
                continue
            finished = False
            for call in reply.tool_calls:
                await self.check_stop()
                await self.say_once(str(call.arguments.get("say") or ""), said=said)
                entry = tools.get(call.name)
                if entry is None:
                    result: dict[str, Any] = {"오류": f"모르는 도구 {call.name}"}
                else:
                    result = await entry[1](call.arguments, turn_tokens)
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=call.id,
                    )
                )
                finished = finished or call.name == FINISH_STEP_TOOL
            if finished:
                break
        return used

    # ----- 말 · 사건 -----

    async def say(self, text: str) -> None:
        """에이전트의 말: 줄마다 사건 하나. '?'로 끝나는 줄은 스스로 묻는 말이다."""
        for line in (part.strip() for part in re.split(r"\n+", text or "")):
            if line:
                await self.emit(
                    HelperEventKind.SAY, {"text": line, "ask": line.endswith(("?", "？"))}
                )

    async def say_once(self, text: str | None, *, said: set[str]) -> None:
        """한 응답 안에서 아직 하지 않은 말이면 말한다. LLM이 답 본문과 도구의 say에, 또는 한 번에 부른
        여러 도구에 같은 말을 넣곤 해서 창에 같은 말이 두 번씩 적혔다."""
        line = (text or "").strip()
        if line and line not in said:
            said.add(line)
            await self.say(line)

    async def emit(self, kind: HelperEventKind, payload: dict[str, Any]) -> None:
        """사건 하나를 적고 바로 커밋한다(화면이 1초마다 이어 읽는다)."""
        await helper_service.add_event(
            self.db, run_id=self.run_id, step=self.step_no, kind=kind, payload=payload
        )
        await self.db.commit()

    async def notice(self, text: str, *, tone: str = "info") -> None:
        """알림 한 줄 (tone: info · warn · bad)."""
        await self.emit(HelperEventKind.NOTICE, {"text": text, "tone": tone})

    async def check_stop(self) -> None:
        """사람이 [멈추기]를 눌렀으면 AgentStopped."""
        if await helper_service.is_stop_requested(self.db, run_id=self.run_id):
            raise AgentStopped

    # ----- 문 · 남은 문제 -----

    async def block(self, key: str, checks: list[OpenCheck]) -> None:
        """문: 앞 단계에 심각이 남아 key 단계로 가지 않는다. 단계를 막힘으로 두고, 남은 문제를 알리고,
        보고를 쓰고 실행을 끝낸다.

        허락을 묻지 않는다. 남은 것(심각 포함)은 사람이 고치거나 골라서 AI로 고친다. 커밋한다.
        """
        self.step_no = next(row["no"] for row in self.steps if row["key"] == key)
        self.update_step(key, status=STEP_BLOCKED, plan=PLAN_BLOCKED)
        await self.save_steps()
        await self.emit(
            HelperEventKind.NOTICE,
            {
                "text": f"{PLAN_BLOCKED} · 심각 {len(blocking(checks))}",
                "tone": GRADE_BAD,
                "checks": open_payload(checks),
            },
        )
        await self.finish(error=BLOCKED_MESSAGE)

    # ----- 진단 · 보고 -----

    async def snapshot(self) -> dict[str, Any]:
        """지금 진단: {checks: [{key, name, value, grade}], kpi: [{name, value}]}. 모듈이 정한다."""
        raise NotImplementedError

    async def ask_json(self, *, system: str, user: str, max_tokens: int | None = None) -> Any:
        """LLM에 도구 없이 묻고 답의 JSON 하나를 읽는다. 모양이 틀리면 None. 쓴 토큰은 실행에 더한다."""
        reply = await self.chat(
            [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            [],
            max_tokens=max_tokens,
        )
        return parse_json_object(reply.content)

    def row(self, key: str) -> dict[str, Any] | None:
        """단계 줄의 key 단계."""
        return next((row for row in self.steps if row.get("key") == key), None)

    async def usage(self) -> dict[str, int]:
        """실행이 지금까지 쓴 것: LLM 토큰 · Jev 호출 · 바꾼 수."""
        run = await helper_service.get_run(self.db, run_id=self.run_id)
        return {"llm": run.llm_tokens, "jev": run.jev_calls, "changed": run.changed}

    async def start_report(self, key: str, *, from_step: int) -> None:
        """보고의 '처음'을 적는다(지금 진단 · 사용량 · 시각). 이미 적었으면 두 번 적지 않는다(이어 도는 작업)."""
        found = self.row(key)
        if found is None or found.get("start"):
            return
        start = await self.snapshot()
        start.update(
            from_step=from_step, at=datetime.now(UTC).isoformat(), usage=await self.usage()
        )
        self.update_step(key, start=start)
        await self.save_steps()

    async def write_report(self, key: str, *, title: str) -> None:
        """보고: 처음과 지금 진단을 견줘 고친 검사 · 바꿈 · 비용을 싣고, LLM이 두 줄로 스스로 묻고 답한다."""
        found = self.row(key)
        if found is None:
            return
        self.step_no = int(found["no"])
        self.update_step(key, status=STEP_RUNNING)
        await self.save_steps()
        start = found.get("start") or {}
        end = await self.snapshot()
        before = {check["key"]: check for check in start.get("checks", [])}
        after = {check["key"]: check for check in end.get("checks", [])}
        rows = []
        for check in end.get("checks", []):
            old = before.get(check["key"])
            is_same = old is not None and (old["value"], old["grade"]) == (
                check["value"],
                check["grade"],
            )
            if not is_same:
                rows.append(
                    {
                        "name": check["name"],
                        "from": old["value"] if old else "—",
                        "to": check["value"],
                        "grade": check["grade"],
                    }
                )
        rows += [
            {"name": check["name"], "from": check["value"], "to": "—", "grade": GRADE_GOOD}
            for key_, check in before.items()
            if key_ not in after
        ]
        start_kpi = {item["name"]: item["value"] for item in start.get("kpi", [])}
        kpi = [
            {"name": item["name"], "from": start_kpi.get(item["name"], "—"), "to": item["value"]}
            for item in end.get("kpi", [])
        ]
        usage = await self.usage()
        start_usage = start.get("usage") or {"llm": 0, "jev": 0}
        from_grade = overall_of(start.get("checks", [])) if start else ""
        to_grade = overall_of(end.get("checks", []))
        payload = {
            "title": title,
            "overall": [OVERALL_WORDS.get(from_grade, "—"), OVERALL_WORDS[to_grade]],
            "overall_grade": [from_grade or GRADE_GOOD, to_grade],
            "counts": [
                _grade_counts(start.get("checks", [])),
                _grade_counts(end.get("checks", [])),
            ],
            "kpi": kpi,
            "rows": rows[:REPORT_MAX_ROWS],
            "more": max(len(rows) - REPORT_MAX_ROWS, 0),
            "changed": await self._changed_summary(int(start.get("from_step", 0))),
            "cost": {
                "llm": usage["llm"] - int(start_usage.get("llm", 0)),
                "jev": usage["jev"] - int(start_usage.get("jev", 0)),
                "time": _elapsed(start.get("at")),
            },
        }
        await self._report_summary(payload)
        await self.emit(HelperEventKind.REPORT, payload)
        self.update_step(key, status=STEP_DONE)
        await self.save_steps()

    async def finish(self, *, error: str | None = None) -> None:
        """실행의 보고를 쓰고 끝낸다(done). 커밋한다."""
        await self.write_report(REPORT_KEY, title=REPORT_TITLE)
        await helper_service.finish_run(
            self.db, run_id=self.run_id, status=HelperRunStatus.DONE, error=error
        )
        await self.db.commit()

    async def _changed_summary(self, from_step: int) -> str:
        """from_step 단계부터 바꾼 카드의 동작마다 수. 예: '학습에서 빼기 412 · 라벨 바꾸기 20'."""
        payloads = await self.db.scalars(
            select(HelperEvent.payload).where(
                HelperEvent.run_id == self.run_id,
                HelperEvent.kind == HelperEventKind.CHANGE.value,
                HelperEvent.step >= from_step,
            )
        )
        counts: Counter[str] = Counter()
        for payload in payloads:
            counts[str(payload.get("action") or "")] += int(payload.get("count") or 0)
        return " · ".join(f"{action} {count:,}" for action, count in counts.items() if count)

    async def _report_summary(self, payload: dict[str, Any]) -> None:
        """보고를 LLM이 두 줄(스스로 묻는 한 줄 · 답 한 줄)로 말한다. LLM이 안 되면 말 없이 보고만 싣는다."""
        facts = {
            "종합": payload["overall"],
            "심각 · 주의": payload["counts"],
            "고친 검사": [f"{row['name']} {row['from']} → {row['to']}" for row in payload["rows"]],
            "바꿈": payload["changed"],
        }
        try:
            reply = await self.chat(
                [
                    ChatMessage(role="system", content=REPORT_SUMMARY_PROMPT),
                    ChatMessage(role="user", content=json.dumps(facts, ensure_ascii=False)),
                ],
                [],
                max_tokens=REPORT_SUMMARY_TOKENS,
            )
        except (ExternalServiceError, AgentBudgetExceeded):
            return
        lines = [line.strip() for line in (reply.content or "").splitlines() if line.strip()]
        await self.say("\n".join(lines[:2]))

    # ----- AI로 고치기 -----

    async def run_fix(self, *, round_no: int, keys: list[str], fixers: dict[str, Fixer]) -> None:
        """사람이 고른 검사마다 한 단계씩 고치고 보고로 끝낸다. 닿은 때 재서 이미 풀렸으면 건너뛴다.

        이어 도는 작업(뜻 분석을 기다린 뒤)이면 이미 끝낸 단계는 다시 하지 않는다. 커밋한다.
        """
        report_key = fix_report_key(round_no)
        first = next((int(row["no"]) for row in self.steps if row.get("fix_round") == round_no), 0)
        await self.start_report(report_key, from_step=first)
        for key in keys:
            step_key = fix_step_key(round_no, key)
            found = self.row(step_key)
            if found is None or found.get("status") in (STEP_DONE, STEP_SKIPPED):
                continue
            self.step_no = int(found["no"])
            await self.check_stop()
            before = _check_of(await self.snapshot(), key)
            if before is None or before["grade"] == GRADE_GOOD:
                self.update_step(step_key, status=STEP_SKIPPED, plan="문제 없음")
                await self.save_steps()
                continue
            self.update_step(step_key, status=STEP_RUNNING, plan=before["value"])
            await self.save_steps()
            started = time.monotonic()
            fixer = fixers.get(key)
            facts = await fixer() if fixer else {}
            after = _check_of(await self.snapshot(), key) or {"value": "0", "grade": GRADE_GOOD}
            await self.emit(
                HelperEventKind.RESULT,
                {
                    "check": found["title"],
                    "from": before["value"],
                    "to": after["value"],
                    "grade": after["grade"],
                },
            )
            kept = {name: value for name, value in facts.items() if value}
            kept["time"] = seconds(started)
            self.update_step(
                step_key,
                status=STEP_DONE,
                facts=kept,
                delta=[before["value"], after["value"], after["grade"]],
            )
            await self.save_steps()
        await self.write_report(report_key, title=FIX_REPORT_TITLE)
        await helper_service.finish_run(self.db, run_id=self.run_id, status=HelperRunStatus.DONE)
        await self.db.commit()

    # ----- 단계 줄 -----

    def update_step(self, key: str, **fields: Any) -> None:
        """단계 줄에서 key 단계의 칸을 바꾼다(저장은 save_steps)."""
        for row in self.steps:
            if row["key"] == key:
                row.update(fields)

    async def save_steps(self) -> None:
        """단계 줄과 지금 단계를 저장하고 커밋한다."""
        await helper_service.set_steps(
            self.db, run_id=self.run_id, steps=self.steps, step_now=self.step_no
        )
        await self.db.commit()


# 보고를 두 줄로 말하게 하는 지시
REPORT_SUMMARY_PROMPT = (
    "너는 학습 데이터 도구 DENT의 도우미다. 방금 끝낸 일의 보고를 두 줄로 쓴다. "
    "첫 줄은 스스로 묻는 물음 한 줄('?'로 끝), 둘째 줄은 그 답 한 줄. 존댓말 없이 짧게, 수를 넣는다. "
    "다른 말은 쓰지 않는다."
)


def _grade_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        grade: sum(1 for check in checks if check.get("grade") == grade)
        for grade in (GRADE_BAD, GRADE_WARN)
    }


def _check_of(snapshot: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((check for check in snapshot.get("checks", []) if check["key"] == key), None)


def _elapsed(started_at: str | None) -> str:
    """처음 시각부터 지금까지 '분:초'. 모르면 빈 글."""
    if not started_at:
        return ""
    total = int((datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds())
    return f"{total // 60}:{total % 60:02d}"


async def end_run(db: AsyncSession, *, run_id: int, status: HelperRunStatus, message: str) -> None:
    """실행을 멈춤 · 실패로 끝내고 알림을 적는다. 도는 단계는 그 자리에서 멈춘 것으로 둔다."""
    await db.rollback()
    run = await helper_service.get_run(db, run_id=run_id)
    steps = [
        {**row, "status": STEP_TODO if row.get("status") == STEP_RUNNING else row.get("status")}
        for row in run.steps
    ]
    await helper_service.set_steps(db, run_id=run_id, steps=steps, step_now=run.step_now)
    await helper_service.add_event(
        db,
        run_id=run_id,
        step=run.step_now,
        kind=HelperEventKind.NOTICE,
        payload={"text": message, "tone": "bad" if status == HelperRunStatus.FAILED else "warn"},
    )
    await helper_service.finish_run(db, run_id=run_id, status=status, error=message)
    await db.commit()
