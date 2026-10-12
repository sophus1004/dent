"""생성 내보내기: SFT 학습 jsonl을 만든다.

파일 자리 · 기록 · 내려받기 · 보관은 시스템(system/exports.py)이 한다.

파일 모양
- sft_jsonl: 한 줄에 {"messages": [{"role", "content"}, …], "source": 출처}. TRL SFTTrainer의 대화 모양(conversational)이고
  Axolotl chat_template · Unsloth도 그대로 읽는다. 맥락과 마지막 응답을 이어 대화 전체를 넣는다.
train · valid · test로 나누지 않는다. 휴지통 밖 대화를 모두 넣고, 출처 칸(original · synthetic · human)으로 만든 것을 가를 수 있게 한다.
심각이 남으면 막는다(409): 대화 구조가 깨지거나 빈 응답이 든 파일은 트레이너가 멈추거나 조용히 틀리게 배운다.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import importing, overview
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import Prompt, Response, Source
from dent.modules.generation.schemas import (
    ExportCheckRead,
    ExportCreate,
    ExportFileRead,
    ExportPreviewRead,
)
from dent.modules.generation.service import IS_ACTIVE
from dent.system import exports as exports_service
from dent.system import jobs
from dent.system.exceptions import ConflictError, InvalidInputError
from dent.system.models import Export

# 파일 모양별 파일 이름 끝
FILE_SUFFIX = {"sft_jsonl": "sft.jsonl"}

# 출처 칸 이름
SOURCE_COLUMN = "source"

# 파일을 쓸 때 DB에서 한 번에 받는 줄 수 (수백만 건을 한꺼번에 메모리에 올리지 않게)
STREAM_BATCH = 2_000

# 미리 보기 한 줄에서 메시지 글을 줄이는 글자 수
PREVIEW_TEXT_LENGTH = 40

# 작업 단계 이름
PHASE_WRITING = "writing"

NO_ROWS_MESSAGE = "내보낼 대화가 없습니다."
BLOCKED_MESSAGE = "심각한 문제가 남아 있어 내보낼 수 없습니다. 진단에서 먼저 고쳐 주세요."


async def preview(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> ExportPreviewRead:
    """내보내기 미리 보기: 첫 줄 · 파일마다 대화 수 · 남은 심각 · 주의 · 막힘."""
    dataset = await generation_service.get_dataset(db, dataset_id=dataset_id)
    result = await overview.get_overview(db, dataset_id=dataset_id)
    checks = [
        ExportCheckRead(key=check.key, count=check.count, grade=check.grade)
        for check in result.checks
        if check.grade != "good"
    ]
    # 심각 먼저 (같으면 검사 순서)
    checks.sort(key=lambda check: check.grade != "bad")
    return ExportPreviewRead(
        line=await _preview_line(db, dataset_id=dataset_id),
        files=[
            ExportFileRead(
                format=fmt,
                file_name=_file_name(dataset.name, fmt),
                conversations=result.conversations,
            )
            for fmt in data.formats
        ],
        checks=checks,
        blocked=any(check.grade == "bad" for check in checks),
    )


async def create_exports(db: AsyncSession, *, dataset_id: int, data: ExportCreate) -> list[Export]:
    """고른 파일 모양마다 내보내기 기록과 작업을 넣는다.

    심각이 남으면 ConflictError, 넣을 대화가 없으면 InvalidInputError.
    """
    dataset = await generation_service.get_dataset(db, dataset_id=dataset_id)
    if await overview.blocking_checks(db, dataset_id=dataset_id):
        raise ConflictError(BLOCKED_MESSAGE)
    active = await db.scalar(
        select(func.count()).select_from(Prompt).where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
    )
    if not active:
        raise InvalidInputError(NO_ROWS_MESSAGE)
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
    result = await _write_jsonl(_rows(db, dataset_id=export.dataset_id), path=path)
    if not result["conversations"]:
        raise InvalidInputError(NO_ROWS_MESSAGE)
    await exports_service.finish_export(db, export, job_id=job_id, result=result)
    await db.commit()


# ---------- 모으기 · 쓰기 ----------


def _rows_query(dataset_id: int) -> Any:
    # 번호 순으로 정렬해 파일이 늘 같게 한다. 첫 판은 지시마다 응답이 하나다.
    # 출처는 응답을 LLM이 다시 썼으면 응답의 출처(synthetic), 아니면 지시의 출처다.
    source = case((Response.source != Source.ORIGINAL.value, Response.source), else_=Prompt.source)
    return (
        select(Prompt.messages, Response.content, source)
        .join(Response, Response.prompt_id == Prompt.id)
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE)
        .order_by(Prompt.id, Response.id)
    )


async def _rows(
    db: AsyncSession, *, dataset_id: int
) -> AsyncIterator[tuple[list[dict[str, Any]], str, str]]:
    """내보낼 대화 (맥락, 마지막 응답, 출처)를 번호 순으로 묶음마다 받아 하나씩 낸다."""
    result = await db.stream(_rows_query(dataset_id).execution_options(yield_per=STREAM_BATCH))
    async for messages, content, source in result:
        yield messages, content, source


def _line(messages: list[dict[str, Any]], content: str, source: str) -> dict[str, Any]:
    return {"messages": importing.conversation(messages, content), SOURCE_COLUMN: source}


async def _write_jsonl(
    rows: AsyncIterator[tuple[list[dict[str, Any]], str, str]], *, path: Path
) -> dict[str, Any]:
    count = 0
    with path.open("w", encoding="utf-8") as file:
        async for messages, content, source in rows:
            file.write(json.dumps(_line(messages, content, source), ensure_ascii=False) + "\n")
            count += 1
    return {"conversations": count}


def _file_name(dataset_name: str, fmt: str) -> str:
    return exports_service.safe_file_name(f"{dataset_name}-{FILE_SUFFIX[fmt]}")


async def _preview_line(db: AsyncSession, *, dataset_id: int) -> str:
    """학습 jsonl의 첫 줄(메시지 글은 줄여서)."""
    first = (await db.execute(_rows_query(dataset_id).limit(1))).first()
    if first is None:
        return "—"
    messages, content, source = first
    line = _line(messages, content, source)
    for message in line["messages"]:
        text = message["content"]
        message["content"] = (
            text if len(text) <= PREVIEW_TEXT_LENGTH else text[:PREVIEW_TEXT_LENGTH] + "…"
        )
    return json.dumps(line, ensure_ascii=False)
