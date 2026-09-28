"""도우미가 바꾼 것의 기록과 되돌리기 (retrieval_helper_changes).

도우미의 도구는 edits · mining · generation이 모은 ChangeLog를 여기에 넘긴다. 바꾼 카드(사건) 하나와
줄마다 전 · 후를 적는다. 되돌리기는 카드 하나 또는 실행 전체이고, 지금 값이 바꾼 뒤 값과 같을 때만 전 값으로 돌린다
(그 사이 사람이 고친 것은 건드리지 않는다). 새로 만든 질의는 휴지통으로, 나눈 문서는 나누기 전으로 돌린다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import edits, repeats
from dent.modules.retrieval.models import (
    Document,
    HelperChange,
    HelperField,
    HelperTarget,
    Judgment,
    JudgmentSource,
    Query,
)
from dent.system import datasets as datasets_service
from dent.system import helper as helper_service
from dent.system.models import HelperEventKind
from dent.system.text import make_text_hash

# 바꾼 카드에 보일 보기 글 수 · 글자 수
SAMPLE_COUNT = 5
SAMPLE_LENGTH = 120


@dataclass(frozen=True)
class UndoResult:
    """되돌린 결과."""

    # 전 값으로 돌린 줄 수
    reverted: int

    # 그 사이 다른 곳에서 고쳐 두고 건너뛴 수
    skipped: int


async def record(
    db: AsyncSession,
    *,
    run_id: int,
    step: int,
    dataset_id: int,
    log: edits.ChangeLog,
    action: str,
    tool: str,
    samples: Sequence[str] = (),
) -> int:
    """바꾼 카드 하나와 변경 기록을 적고 실행의 바꾼 수를 더한다. 바꾼 줄 수를 돌려준다. 커밋한다."""
    if not log.entries:
        return 0
    event = await helper_service.add_event(
        db,
        run_id=run_id,
        step=step,
        kind=HelperEventKind.CHANGE,
        payload={
            "action": action,
            "count": len(log.entries),
            "tool": tool,
            "samples": [sample[:SAMPLE_LENGTH] for sample in samples[:SAMPLE_COUNT]],
        },
    )
    db.add_all(HelperChange(run_id=run_id, event_id=event.id, **entry) for entry in log.entries)
    await helper_service.add_usage(db, run_id=run_id, changed=len(log.entries))
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return len(log.entries)


async def undo(db: AsyncSession, *, run_id: int, event_id: int | None = None) -> UndoResult:
    """도우미가 바꾼 것을 되돌린다(event_id가 있으면 그 카드만). 커밋한다."""
    run = await helper_service.get_run(db, run_id=run_id)
    query = (
        select(HelperChange)
        .where(HelperChange.run_id == run_id, HelperChange.undone_at.is_(None))
        # 나중에 바꾼 것부터 되돌린다(같은 줄을 두 번 바꿨으면 차례로 풀린다).
        .order_by(HelperChange.id.desc())
    )
    if event_id is not None:
        query = query.where(HelperChange.event_id == event_id)
    changes = list(await db.scalars(query))
    now = datetime.now(UTC)
    reverted = 0
    for change in changes:
        if await _revert(db, change, dataset_id=run.dataset_id, now=now):
            change.undone_at = now
            reverted += 1
    skipped = len(changes) - reverted
    if reverted:
        await helper_service.add_usage(db, run_id=run_id, changed=-reverted)
        await datasets_service.touch_dataset(db, dataset_id=run.dataset_id)
    await helper_service.add_event(
        db,
        run_id=run_id,
        step=run.step_now,
        kind=HelperEventKind.NOTICE,
        payload={
            "text": f"되돌림 {reverted}" + (f" · 건너뜀 {skipped}" if skipped else ""),
            "tone": "info",
        },
    )
    await db.commit()
    return UndoResult(reverted=reverted, skipped=skipped)


async def undone_event_ids(db: AsyncSession, *, run_id: int) -> list[int]:
    """모든 줄을 되돌린 카드 번호들 (화면이 그 카드의 ↶를 끈다)."""
    rows = await db.execute(
        select(HelperChange.event_id)
        .where(HelperChange.run_id == run_id)
        .group_by(HelperChange.event_id)
        .having(func.bool_and(HelperChange.undone_at.is_not(None)))
    )
    return [row[0] for row in rows]


# ---------- 안쪽 ----------


async def _revert(
    db: AsyncSession, change: HelperChange, *, dataset_id: int, now: datetime
) -> bool:
    """줄 하나를 전 값으로 돌린다. 지금 값이 바꾼 뒤 값과 다르면(그 사이 고침) 돌리지 않고 False."""
    target = HelperTarget(change.target)
    field = HelperField(change.field)
    if target == HelperTarget.JUDGMENT:
        return await _revert_judgment(db, change, dataset_id=dataset_id)
    if target == HelperTarget.QUERY:
        query = await db.get(Query, change.query_id, populate_existing=True)
        if query is None:
            return False
        return _revert_row(query, field, change, now=now)
    document = await db.get(Document, change.document_id, populate_existing=True)
    if document is None:
        return False
    if field == HelperField.CHUNKED:
        if document.replaced_at is None:
            return False
        await edits.unsplit_document(db, document_id=document.id)
        return True
    reverted = _revert_row(document, field, change, now=now)
    if reverted and field == HelperField.TEXT:
        # 본문이 돌아왔으니 학습 글 · 표시 · 든 반복 구간도 다시 만든다.
        await repeats.refresh_documents(db, dataset_id=dataset_id, documents=[document])
    return reverted


def _revert_row(
    row: Query | Document, field: HelperField, change: HelperChange, *, now: datetime
) -> bool:
    """질의 · 문서 한 줄의 칸을 전 값으로. 지금 값이 바꾼 뒤 값이 아니면 False."""
    current: Any
    if field == HelperField.EXCLUDE and isinstance(row, Query):
        current = row.exclude_reason
        if current != change.after:
            return False
        row.exclude_reason = change.before
    elif field in (HelperField.TRASH, HelperField.CREATED):
        # 만든 질의는 휴지통으로, 휴지통으로 보낸 것은 되살린다.
        is_trashed = row.trashed_at is not None
        wanted_after = field == HelperField.TRASH
        if is_trashed != wanted_after:
            return False
        row.trashed_at = None if field == HelperField.TRASH else now
    elif field == HelperField.TEXT:
        if row.text != change.after:
            return False
        row.text = str(change.before)
        row.text_hash = make_text_hash(row.text)
    elif field == HelperField.SKIP_GENERATION and isinstance(row, Document):
        if row.skip_generation != change.after:
            return False
        row.skip_generation = bool(change.before)
    else:
        return False
    row.row_version += 1
    return True


async def _revert_judgment(db: AsyncSession, change: HelperChange, *, dataset_id: int) -> bool:
    """판정 하나를 전 값으로(전이 없으면 뗀다). 지금 등급이 바꾼 뒤 등급이 아니면 False.

    전 값은 판정의 값(등급 · 출처 · 충돌 · 교사 점수)이다. 예전 기록은 등급 숫자만 있다.
    """
    judgment = await db.get(Judgment, (change.query_id, change.document_id), populate_existing=True)
    current = judgment.grade if judgment is not None else None
    if current != change.after:
        return False
    state = change.before
    if state is not None and not isinstance(state, dict):
        state = {"grade": state}
    if state is None:
        await db.execute(
            delete(Judgment).where(
                Judgment.query_id == change.query_id, Judgment.document_id == change.document_id
            )
        )
        return True
    values = {
        "grade": int(state["grade"]),
        "source": state.get("source") or JudgmentSource.HUMAN.value,
        "conflict": bool(state.get("conflict", False)),
    }
    for name in ("teacher_score", "overlap"):
        if name in state:
            values[name] = state[name]
    if judgment is not None:
        await db.execute(
            update(Judgment)
            .where(Judgment.query_id == change.query_id, Judgment.document_id == change.document_id)
            .values(**values)
        )
    else:
        db.add(
            Judgment(
                query_id=change.query_id,
                document_id=change.document_id,
                dataset_id=dataset_id,
                **values,
            )
        )
    return True
