"""생성 내보내기: SFT · 선호 쌍(DPO) · 지시만 jsonl을 만든다.

파일 자리 · 기록 · 내려받기 · 보관은 시스템(system/exports.py)이 한다.

파일 모양 (모두 TRL이 읽는 대화 모양이고 Axolotl chat_template · Unsloth도 그대로 읽는다)
- sft_jsonl: 한 줄에 {"messages": [{"role", "content"}, …], "source": 출처}. 맥락과 주 응답을 이어 대화 전체를 넣는다.
  도구 대화면 메시지의 tool_calls · tool_call_id와 도구 정의 "tools"도 싣는다(SFTTrainer의 도구 호출 모양).
  응답이 없는 대화(지시만)는 넣지 않는다.
- dpo_jsonl: 한 줄에 {"prompt": [맥락 메시지], "chosen": [응답], "rejected": [응답], "source": 쌍의 출처}.
  DPOTrainer의 대화 선호 모양이다. 휴지통 밖 쌍만 넣는다.
- prompts_jsonl: 한 줄에 {"prompt": [맥락 메시지], "source": 출처}. GRPO처럼 응답을 학습 중에 만드는 방식이 읽는다.
train · valid · test로 나누지 않는다. 휴지통 밖을 모두 넣고, 출처 칸(original · synthetic · human)으로 만든 것을 가를 수 있게 한다.
모든 줄에 묶음 칸 "group"(같은 맥락이거나 가장 최근 뜻 분석의 근접 중복 지시로 이어진 대화들의 대표 번호,
가장 작은 지시 번호)을 싣는다. 받은 쪽이 묶음 단위로 나누면 거의 같은 지시가 학습 · 평가 양쪽에 들어가지 않는다.
심각이 남으면 막는다(409): 대화 구조가 깨지거나 빈 응답이 든 파일은 트레이너가 멈추거나 조용히 틀리게 배운다.
지시만 파일은 응답을 넣지 않으므로 지시 단계(1)의 심각만 본다.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from dent.modules.generation import importing, overview, problems
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import NearDuplicate, Pair, Prompt, Response, Source
from dent.modules.generation.schemas import (
    CheckRead,
    ExportCheckRead,
    ExportCreate,
    ExportFileRead,
    ExportPreviewRead,
)
from dent.modules.generation.service import IS_ACTIVE, PAIR_IS_ACTIVE
from dent.system import exports as exports_service
from dent.system import jobs
from dent.system.exceptions import ConflictError, InvalidInputError
from dent.system.groups import Groups
from dent.system.models import Export

# 파일 모양별 파일 이름 끝
FILE_SUFFIX = {
    "sft_jsonl": "sft.jsonl",
    "dpo_jsonl": "dpo.jsonl",
    "prompts_jsonl": "prompts.jsonl",
}

# 파일 모양별 줄의 단위
FILE_UNIT = {"sft_jsonl": "conversation", "dpo_jsonl": "pair", "prompts_jsonl": "conversation"}

# 지시만 파일이 보는 흐름 단계 (응답을 넣지 않으므로 지시 단계의 심각만 막는다)
PROMPTS_STAGES = (1,)

# 출처 칸 이름
SOURCE_COLUMN = "source"

# 묶음 칸 이름 (같은 맥락 · 근접 중복 지시로 이어진 대화들의 대표 번호)
GROUP_COLUMN = "group"

# 파일을 쓸 때 DB에서 한 번에 받는 줄 수 (수백만 건을 한꺼번에 메모리에 올리지 않게)
STREAM_BATCH = 2_000

# 미리 보기 한 줄에서 메시지 글을 줄이는 글자 수
PREVIEW_TEXT_LENGTH = 40

# 작업 단계 이름
PHASE_WRITING = "writing"

NO_ROWS_MESSAGES = {
    "sft_jsonl": "내보낼 대화가 없습니다(응답이 있는 대화만 넣습니다).",
    "dpo_jsonl": "내보낼 선호 쌍이 없습니다.",
    "prompts_jsonl": "내보낼 지시가 없습니다.",
}
BLOCKED_MESSAGE = "심각한 문제가 남아 있어 내보낼 수 없습니다. 진단에서 먼저 고쳐 주세요."


async def preview(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> ExportPreviewRead:
    """내보내기 미리 보기: 첫 줄(처음 고른 모양) · 파일마다 줄 수 · 남은 심각 · 주의 · 막힘."""
    dataset = await generation_service.get_dataset(db, dataset_id=dataset_id)
    result = await overview.get_overview(db, dataset_id=dataset_id)
    open_checks = [check for check in result.checks if check.grade != "good"]
    checks = [
        ExportCheckRead(key=check.key, count=check.count, grade=check.grade)
        for check in open_checks
    ]
    # 심각 먼저 (같으면 검사 순서)
    checks.sort(key=lambda check: check.grade != "bad")
    files = [
        ExportFileRead(
            format=fmt,
            file_name=_file_name(dataset.name, fmt),
            conversations=await _count(db, dataset_id=dataset_id, fmt=fmt),
            unit=FILE_UNIT[fmt],  # type: ignore[arg-type]
            blocked=_is_blocked(fmt, open_checks),
        )
        for fmt in data.formats
    ]
    return ExportPreviewRead(
        line=await _preview_line(db, dataset_id=dataset_id, fmt=data.formats[0]),
        files=files,
        checks=checks,
        blocked=any(file.blocked for file in files),
    )


async def create_exports(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> list[Export]:
    """고른 파일 모양마다 내보내기 기록과 작업을 넣는다.

    고른 파일을 막는 심각이 남으면 ConflictError, 넣을 줄이 없는 파일이 있으면 InvalidInputError.
    """
    dataset = await generation_service.get_dataset(db, dataset_id=dataset_id)
    bad = await overview.blocking_checks(db, dataset_id=dataset_id)
    if any(_is_blocked(fmt, bad) for fmt in data.formats):
        raise ConflictError(BLOCKED_MESSAGE)
    for fmt in data.formats:
        if not await _count(db, dataset_id=dataset_id, fmt=fmt):
            raise InvalidInputError(NO_ROWS_MESSAGES[fmt])
    made = []
    for fmt in data.formats:
        export = await exports_service.create_export(
            db,
            module=generation_service.MODULE_NAME,
            dataset_id=dataset_id,
            format=fmt,
            file_name=_file_name(dataset.name, fmt),
            options=data.model_dump(mode="json"),
        )
        made.append(export)
    await db.commit()
    return [await exports_service.get_export(db, export_id=export.id) for export in made]


async def build_export(db: AsyncSession, *, export_id: int, job_id: int) -> None:
    """내보낼 파일 하나를 만든다(작업 실행기). 실패하면 도메인 예외를 낸다(부른 쪽이 fail_export)."""
    export = await exports_service.get_export(db, export_id=export_id)
    exports_service.start_export(export)
    await jobs.start_phase(db, job_id=job_id, phase=PHASE_WRITING, total=None)
    await db.commit()
    path = exports_service.file_path(export)
    fmt = export.format
    count = await _write_jsonl(_lines(db, dataset_id=export.dataset_id, fmt=fmt), path=path)
    if not count:
        raise InvalidInputError(NO_ROWS_MESSAGES.get(fmt, NO_ROWS_MESSAGES["sft_jsonl"]))
    result_key = "pairs" if FILE_UNIT.get(fmt) == "pair" else "conversations"
    await exports_service.finish_export(db, export, job_id=job_id, result={result_key: count})
    await db.commit()


# ---------- 모으기 · 쓰기 ----------


def _is_blocked(fmt: str, checks: list[CheckRead]) -> bool:
    """이 파일을 막는 심각이 있는지. 지시만 파일은 지시 단계의 심각만 본다."""
    stages = PROMPTS_STAGES if fmt == "prompts_jsonl" else None
    return any(
        check.grade == "bad" and (stages is None or check.stage in stages) for check in checks
    )


async def _count(db: AsyncSession, *, dataset_id: int, fmt: str) -> int:
    """파일에 넣을 줄 수."""
    query = _query(dataset_id, fmt).order_by(None)
    return int(await db.scalar(select(func.count()).select_from(query.subquery())) or 0)


def _query(dataset_id: int, fmt: str) -> Any:
    """파일 모양마다 넣을 줄을 고르는 쿼리. 번호 순으로 정렬해 파일이 늘 같게 한다."""
    if fmt == "dpo_jsonl":
        chosen = aliased(Response)
        rejected = aliased(Response)
        return (
            select(
                Prompt.id,
                Prompt.messages,
                Prompt.tools,
                chosen.content,
                chosen.tool_calls,
                rejected.content,
                rejected.tool_calls,
                Pair.source,
            )
            .join(Pair, and_(Pair.prompt_id == Prompt.id, PAIR_IS_ACTIVE))
            .join(chosen, chosen.id == Pair.chosen_id)
            .join(rejected, rejected.id == Pair.rejected_id)
            .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
            .order_by(Pair.id)
        )
    if fmt == "prompts_jsonl":
        return (
            select(Prompt.id, Prompt.messages, Prompt.tools, Prompt.source)
            .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
            .order_by(Prompt.id)
        )
    # 출처는 주 응답을 LLM이 쓰거나 다시 썼으면 응답의 출처(synthetic), 아니면 지시의 출처다.
    source = case((Response.source != Source.ORIGINAL.value, Response.source), else_=Prompt.source)
    return (
        select(
            Prompt.id, Prompt.messages, Prompt.tools, Response.content, Response.tool_calls, source
        )
        .join(Response, and_(Response.prompt_id == Prompt.id, Response.is_main))
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .order_by(Prompt.id)
    )


def _line(fmt: str, row: Any, *, group: int) -> dict[str, Any]:
    """DB 줄 하나(맨 앞은 지시 번호)를 파일의 한 줄로."""
    if fmt == "dpo_jsonl":
        _prompt_id, messages, tools, chosen, chosen_calls, rejected, rejected_calls, source = row
        line: dict[str, Any] = {
            "prompt": _messages(messages),
            "chosen": _messages(importing.conversation([], chosen, chosen_calls)),
            "rejected": _messages(importing.conversation([], rejected, rejected_calls)),
        }
    elif fmt == "prompts_jsonl":
        _prompt_id, messages, tools, source = row
        line = {"prompt": _messages(messages)}
    else:
        _prompt_id, messages, tools, content, tool_calls, source = row
        line = {"messages": _messages(importing.conversation(messages, content, tool_calls))}
    if tools:
        line["tools"] = tools
    line[SOURCE_COLUMN] = source
    line[GROUP_COLUMN] = group
    return line


async def group_numbers(db: AsyncSession, *, dataset_id: int) -> dict[int, int]:
    """묶음에 들 수 있는 휴지통 밖 지시마다 {지시 번호: 묶음 대표 번호}. 싣지 않은 지시는 혼자다.

    맥락이 같거나(해시) 가장 최근 뜻 분석의 근접 중복 쌍이면 한 묶음이다(이어진 쌍도). 수백만 건이어도
    묶음에 드는 지시만 읽는다.
    """
    grouped = Groups[int]()
    repeated = (
        select(Prompt.text_hash)
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .group_by(Prompt.text_hash)
        .having(func.count() > 1)
    )
    first_of: dict[str, int] = {}
    for prompt_id, text_hash in await db.execute(
        select(Prompt.id, Prompt.text_hash)
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE, Prompt.text_hash.in_(repeated))
        .order_by(Prompt.id)
    ):
        grouped.union(first_of.setdefault(text_hash, prompt_id), prompt_id)
    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    if analysis is not None:
        active = select(Prompt.id).where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        for prompt_a, prompt_b in await db.execute(
            select(NearDuplicate.prompt_a, NearDuplicate.prompt_b).where(
                NearDuplicate.analysis_id == analysis.id,
                NearDuplicate.prompt_a.in_(active),
                NearDuplicate.prompt_b.in_(active),
            )
        ):
            grouped.union(prompt_a, prompt_b)
    return grouped.representatives()


def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """파일에 쓸 메시지들: 역할 · 글, 도구 호출이 있으면 tool_calls · tool_call_id까지만."""
    kept = []
    for message in messages:
        out: dict[str, Any] = {"role": message["role"], "content": message["content"]}
        if message.get("tool_calls"):
            out["tool_calls"] = message["tool_calls"]
        if message.get("tool_call_id"):
            out["tool_call_id"] = message["tool_call_id"]
        kept.append(out)
    return kept


async def _lines(db: AsyncSession, *, dataset_id: int, fmt: str) -> AsyncIterator[dict[str, Any]]:
    """파일의 줄들을 번호 순으로 묶음마다 받아 하나씩 낸다."""
    groups = await group_numbers(db, dataset_id=dataset_id)
    result = await db.stream(_query(dataset_id, fmt).execution_options(yield_per=STREAM_BATCH))
    async for row in result:
        yield _line(fmt, row, group=groups.get(row[0], row[0]))


async def _write_jsonl(lines: AsyncIterator[dict[str, Any]], *, path: Path) -> int:
    count = 0
    # 줄바꿈은 OS와 상관없이 \n이다(Windows에서 \r\n이 되지 않게: 같은 데이터면 어느 PC에서나 같은 파일).
    with path.open("w", encoding="utf-8", newline="\n") as file:
        async for line in lines:
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
            count += 1
    return count


def _file_name(dataset_name: str, fmt: str) -> str:
    return exports_service.safe_file_name(f"{dataset_name}-{FILE_SUFFIX[fmt]}")


async def _preview_line(db: AsyncSession, *, dataset_id: int, fmt: str) -> str:
    """파일의 첫 줄(메시지 글은 줄여서)."""
    first = (await db.execute(_query(dataset_id, fmt).limit(1))).first()
    if first is None:
        return "—"
    # 미리 보기는 묶음을 세지 않는다: 첫 줄은 번호가 가장 작아 묶음이 있어도 대개 그 대표다.
    line = _line(fmt, first, group=first[0])
    for key in ("messages", "prompt", "chosen", "rejected"):
        for message in line.get(key) or []:
            text = message["content"]
            message["content"] = (
                text if len(text) <= PREVIEW_TEXT_LENGTH else text[:PREVIEW_TEXT_LENGTH] + "…"
            )
    return json.dumps(line, ensure_ascii=False)
