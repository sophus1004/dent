"""대화 목록 · 한 건 · 검토 큐와 검토 결정(빼기 · 그대로 두기 · 가리기 · 자르기 · 다시 쓰기), 결정 기록 · 되돌리기, 휴지통.

결정은 바로 반영하고 전 · 후를 generation_changes에 남긴다. 되돌리기는 지금 값이 '후'와 같을 때만 '전'으로 돌린다
(그 사이에 다른 결정으로 글이 바뀌었으면 409). 글을 바꾸는 결정(가리기 · 자르기 · 다시 쓰기)은 규칙 검사를 다시 재고
그 대화의 Jev 판정을 지운다(옛 글의 판정이라서). 다시 쓰기는 LLM이 쓰고 응답의 출처를 synthetic으로 둔다.
문제가 남았는지는 problems.py의 조건으로 본다(뜻 검사는 다 만든 뜻 분석으로). 영구 삭제는 휴지통 비우기뿐이다.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, cast, delete, exists, func, insert, not_, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import importing, judging, problems, rewriting, rules, semantic
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import (
    Action,
    Change,
    Judgment,
    Prompt,
    Response,
    Role,
    Source,
)
from dent.modules.generation.rules import CHECKS, RULE_CHECKS, CheckKey
from dent.modules.generation.schemas import (
    ChangeRead,
    ConversationBulkUpdate,
    ConversationRead,
    ConversationStatusFilter,
    DecideAllCreate,
    DecisionCreate,
    JevRead,
    MessageRead,
    ProblemFilter,
    ProblemRead,
)
from dent.modules.generation.service import IS_ACTIVE, IS_TRASHED, has_open
from dent.system import connections
from dent.system import datasets as datasets_service
from dent.system.exceptions import ConflictError, InvalidInputError, NotFoundError
from dent.system.models import ConnectionRole

# 대화 목록 한 쪽의 최대 크기
MAX_PAGE_SIZE = 200

# 검토 큐에 싣는 대화 번호의 최대 수 (화면이 앞에서부터 한 건씩 넘긴다)
REVIEW_MAX = 5_000

# 결정 기록 목록의 기본 크기
CHANGE_PAGE_SIZE = 30

# 문제 조각의 최대 글자 수
SNIPPET_LENGTH = 80

# 자리가 없는 문제의 조각 글
SNIPPET_WITHOUT_SPAN = {
    CheckKey.EMPTY_PROMPT: "(빈 지시)",
    CheckKey.EMPTY_ANSWER: "(빈 응답)",
    CheckKey.DUPLICATE: "같은 지시가 앞에 있음",
    CheckKey.LANG_MIX: "목표 언어가 아닌 글",
    CheckKey.NEAR_DUPLICATE: "뜻이 거의 같은 지시가 앞에 있음",
}

# 글을 바꾸는 결정 (규칙 검사를 다시 재고 Jev 판정을 지운다)
TEXT_ACTIONS = (Action.MASK, Action.CUT, Action.REWRITE)

# 다시 쓰기의 LLM 요청을 보낼 곳. 테스트만 가짜 LLM 서버를 넣는다(평소에는 None = 실제 연결).
REWRITE_TRANSPORT: Any = None

NOT_FOUND_MESSAGE = "대화를 찾을 수 없습니다."
WRONG_ACTION_MESSAGE = "이 검사에서는 할 수 없는 결정입니다."
NO_PROBLEM_MESSAGE = "이 대화에는 그 문제가 남아 있지 않습니다. 새로 불러와 주세요."
TRASHED_MESSAGE = "휴지통에 있는 대화입니다. 먼저 되살려 주세요."
UNDONE_MESSAGE = "이미 되돌린 결정입니다."
STALE_UNDO_MESSAGE = "그 뒤에 대화가 바뀌어 이 결정을 되돌릴 수 없습니다."
LLM_NOT_CONNECTED_MESSAGE = "LLM 미연결: 연결 설정에서 LLM을 연결하세요."
EARLIER_TURN_MESSAGE = "앞 턴의 응답에 있는 문제는 다시 쓸 수 없습니다. 빼거나 그대로 두세요."
JEV_ONLY_KEEP_MESSAGE = "Jev가 괜찮다고 본 대화에는 '모두 그대로 두기'만 할 수 있습니다."

# 검색어 안의 LIKE 특수 글자를 글자 그대로 찾게 할 때 쓰는 탈출 글자
LIKE_ESCAPE = "\\"


@dataclass(frozen=True)
class ConversationItem:
    """대화 한 건: 지시 줄과 마지막 응답, 해시 · 뜻으로 센 문제, Jev 판정."""

    # 지시 (맥락 · 문제 표시 · 휴지통)
    prompt: Prompt

    # 마지막 응답
    answer: Response

    # 같은 지시의 여분인지 (진단의 '같은 지시'에 드는지)
    is_duplicate: bool = False

    # 근접 중복의 여분 · Jev가 확인한 지시 불일치인지 (다 만든 뜻 분석)
    is_near_duplicate: bool = False
    is_mismatch: bool = False

    # 뜻 분석의 Jev 판정. 묻지 않았으면 None
    judgment: Judgment | None = None


@dataclass(frozen=True)
class ConversationPage:
    """대화 목록 한 쪽."""

    # 대화들
    items: list[ConversationItem]

    # 거른 전체 수
    total: int


# ---------- 읽기 ----------


async def list_conversations(
    db: AsyncSession,
    *,
    dataset_id: int,
    status: ConversationStatusFilter,
    problem: ProblemFilter | None,
    q: str | None,
    limit: int,
    offset: int,
) -> ConversationPage:
    """대화 목록. 상태 · 문제 · 검색어로 거르고 번호 순. 데이터셋이 없으면 NotFoundError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    conditions = [Prompt.dataset_id == dataset_id, IS_ACTIVE if status == "active" else IS_TRASHED]
    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    analysis_id = analysis.id if analysis else None
    if problem == "any":
        conditions.append(problems.any_problem(dataset_id, analysis_id=analysis_id))
    elif problem == "none":
        conditions.append(not_(problems.any_problem(dataset_id, analysis_id=analysis_id)))
    elif problem is not None:
        conditions.append(_problem_condition(dataset_id, CheckKey(problem), analysis_id))
    if q:
        pattern = "%" + _escape_like(q) + "%"
        in_answer = exists().where(
            Response.prompt_id == Prompt.id, Response.content.ilike(pattern, escape=LIKE_ESCAPE)
        )
        conditions.append(
            or_(cast(Prompt.messages, Text).ilike(pattern, escape=LIKE_ESCAPE), in_answer)
        )

    total = await db.scalar(select(func.count()).select_from(Prompt).where(*conditions))
    ids = (
        await db.scalars(
            select(Prompt.id).where(*conditions).order_by(Prompt.id).limit(limit).offset(offset)
        )
    ).all()
    return ConversationPage(
        items=await _items(db, dataset_id=dataset_id, ids=list(ids)), total=int(total or 0)
    )


async def get_conversation(db: AsyncSession, *, prompt_id: int) -> ConversationItem:
    """대화 한 건. 없으면 NotFoundError."""
    prompt = await db.get(Prompt, prompt_id, populate_existing=True)
    if prompt is None:
        raise NotFoundError(NOT_FOUND_MESSAGE)
    [item] = await _items(db, dataset_id=prompt.dataset_id, ids=[prompt.id])
    return item


async def review_queue(
    db: AsyncSession, *, dataset_id: int, check: CheckKey
) -> tuple[list[int], int]:
    """한 검사의 문제가 남은 휴지통 밖 대화 번호들(번호 순, 최대 REVIEW_MAX)과 전체 수."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    query = await _open_ids(db, dataset_id=dataset_id, check=check)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    ids = (await db.scalars(query.limit(REVIEW_MAX))).all()
    return list(ids), int(total or 0)


async def list_changes(
    db: AsyncSession, *, dataset_id: int, limit: int = CHANGE_PAGE_SIZE
) -> list[Change]:
    """되돌리지 않은 결정 기록을 최근 것부터."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    query = (
        select(Change)
        .where(Change.dataset_id == dataset_id, Change.undone_at.is_(None))
        .order_by(Change.id.desc())
        .limit(limit)
    )
    return list((await db.scalars(query)).all())


def conversation_read(item: ConversationItem) -> ConversationRead:
    """대화 한 건을 응답 모양으로: 메시지 전체 · 남은 문제(조각 포함) · 그대로 둔 검사."""
    prompt = item.prompt
    messages = importing.conversation(prompt.messages, item.answer.content)
    problems = [
        _problem_read(CheckKey(key), mark, messages)
        for key, marks in prompt.marks.items()
        if key in prompt.open_checks and key in CheckKey.__members__.values()
        for mark in marks
    ]
    if item.is_duplicate:
        problems.append(_problem_read(CheckKey.DUPLICATE, {}, messages))
    if item.is_near_duplicate:
        problems.append(_problem_read(CheckKey.NEAR_DUPLICATE, {}, messages))
    judgment = item.judgment
    if item.is_mismatch and judgment is not None:
        # 응답 전체에 걸친 문제: 마지막 응답 자리에 칠하지 않고 줄로만 보인다.
        problems.append(
            ProblemRead(
                key=CheckKey.MISMATCH,
                turn=None,
                start=None,
                end=None,
                snippet=f"Jev 따름 {judgment.follows or 0:.2f}",
            )
        )
    # 대화 안의 자리 순으로(대화 전체에 걸친 것이 먼저). JSONB는 열쇠 순서를 지키지 않는다.
    problems.sort(
        key=lambda problem: (-1 if problem.turn is None else problem.turn, problem.start or 0)
    )
    is_rewritten = item.answer.source != Source.ORIGINAL
    return ConversationRead(
        id=prompt.id,
        dataset_id=prompt.dataset_id,
        messages=[MessageRead(role=m["role"], content=m["content"]) for m in messages],
        source=item.answer.source if is_rewritten else prompt.source,
        problems=problems,
        kept=[CheckKey(key) for key in prompt.kept if key in CheckKey.__members__.values()],
        trashed=prompt.trashed_at is not None,
        row_version=prompt.row_version,
        extra=prompt.extra,
        jev=_jev_read(judgment),
        import_id=prompt.import_id,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


# ---------- 결정 ----------


async def decide(
    db: AsyncSession, *, prompt_id: int, data: DecisionCreate
) -> tuple[ConversationItem, Change]:
    """검토 결정 하나를 바로 반영하고 기록한다.

    없으면 NotFoundError, 그 검사에서 할 수 없는 결정이면 InvalidInputError,
    휴지통에 있거나 그 문제가 이미 없으면 ConflictError.
    """
    if data.action not in CHECKS[data.check].actions:
        raise InvalidInputError(WRONG_ACTION_MESSAGE)
    item = await get_conversation(db, prompt_id=prompt_id)
    prompt, answer = item.prompt, item.answer
    if prompt.trashed_at is not None:
        raise ConflictError(TRASHED_MESSAGE)
    if not _is_open(item, data.check):
        raise ConflictError(NO_PROBLEM_MESSAGE)

    rewritten = None
    if data.action == Action.REWRITE:
        if not is_rewritable(item, data.check):
            raise InvalidInputError(EARLIER_TURN_MESSAGE)
        connection = await connections.get_connection(db, role=ConnectionRole.LLM)
        if connection is None:
            raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
        rewritten = await rewriting.rewrite_answer(
            connection,
            messages=prompt.messages,
            content=answer.content,
            check=data.check,
            transport=REWRITE_TRANSPORT,
        )
    before, after = _apply(
        prompt, answer, check=data.check, action=data.action, rewritten=rewritten
    )
    if data.action in TEXT_ACTIONS:
        await semantic.forget_judgments(db, prompt_id=prompt.id)
    change = Change(
        dataset_id=prompt.dataset_id,
        prompt_id=prompt.id,
        check_key=data.check.value,
        action=data.action.value,
        before=before,
        after=after,
    )
    db.add(change)
    prompt.row_version += 1
    await datasets_service.touch_dataset(db, dataset_id=prompt.dataset_id)
    await db.commit()
    # 커밋하면 속성이 만료되므로, 응답 모양으로 바꾸기 전에 다시 읽는다(비동기 세션은 게으른 읽기를 못 한다).
    await db.refresh(change)
    return await get_conversation(db, prompt_id=prompt_id), change


async def decide_all(db: AsyncSession, *, dataset_id: int, data: DecideAllCreate) -> int:
    """한 검사의 남은 대화 모두에 같은 결정(모두 빼기 · 모두 그대로 두기). 바꾼 수. 대화마다 결정 기록을 남긴다."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    if data.action not in CHECKS[data.check].actions:
        raise InvalidInputError(WRONG_ACTION_MESSAGE)
    if data.only_jev_ok:
        if data.action != Action.KEEP:
            raise InvalidInputError(JEV_ONLY_KEEP_MESSAGE)
        analysis = await problems.done_analysis(db, dataset_id=dataset_id)
        if analysis is None:
            return 0
        query = problems.jev_fine_ids(dataset_id, analysis.id, data.check)
    else:
        query = await _open_ids(db, dataset_id=dataset_id, check=data.check)
    ids = list((await db.scalars(query)).all())
    if not ids:
        return 0
    await apply_bulk(
        db, dataset_id=dataset_id, check=data.check, action=Action(data.action), ids=ids
    )
    await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return len(ids)


async def apply_bulk(
    db: AsyncSession,
    *,
    dataset_id: int,
    check: CheckKey,
    action: Action,
    ids: list[int],
    run_id: int | None = None,
    event_id: int | None = None,
) -> None:
    """대화들에 빼기 · 그대로 두기를 한 번에 반영하고 대화마다 결정 기록을 남긴다(도우미면 실행 · 카드 번호와 함께).

    확인은 부른 쪽이 한다(문제가 남은 휴지통 밖 대화들). 커밋은 부른 쪽이 한다.
    """
    if not ids:
        return
    key = check.value
    if action == Action.DROP:
        values: dict[str, Any] = {"trashed_at": datetime.now(UTC)}
        before, after = {"trashed": False}, {"trashed": True}
    else:
        values = {
            "kept": Prompt.kept.op("||")(cast([key], JSONB)),
            "open_checks": Prompt.open_checks.op("-")(key),
        }
        before, after = {"kept": False, "check": key}, {"kept": True, "check": key}
    await db.execute(
        update(Prompt)
        .where(Prompt.id.in_(ids))
        .values(**values, row_version=Prompt.row_version + 1)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        insert(Change),
        [
            {
                "dataset_id": dataset_id,
                "prompt_id": prompt_id,
                "check_key": key,
                "action": action.value,
                "before": before,
                "after": after,
                "run_id": run_id,
                "event_id": event_id,
            }
            for prompt_id in ids
        ],
    )


async def apply_text(
    db: AsyncSession,
    *,
    item: ConversationItem,
    check: CheckKey,
    action: Action,
    rewritten: str | None = None,
    run_id: int | None = None,
    event_id: int | None = None,
) -> None:
    """대화 하나의 글을 바꾸는 결정(가리기 · 자르기 · 다시 쓰기)을 반영하고 기록한다. 커밋은 부른 쪽이 한다."""
    prompt, answer = item.prompt, item.answer
    before, after = _apply(prompt, answer, check=check, action=action, rewritten=rewritten)
    await semantic.forget_judgments(db, prompt_id=prompt.id)
    db.add(
        Change(
            dataset_id=prompt.dataset_id,
            prompt_id=prompt.id,
            check_key=check.value,
            action=action.value,
            before=before,
            after=after,
            run_id=run_id,
            event_id=event_id,
        )
    )
    prompt.row_version += 1


async def items_of(db: AsyncSession, *, dataset_id: int, ids: list[int]) -> list[ConversationItem]:
    """번호들의 대화를 그 순서대로 (문제 · Jev 판정과 함께)."""
    return await _items(db, dataset_id=dataset_id, ids=ids)


async def open_ids(db: AsyncSession, *, dataset_id: int, check: CheckKey) -> list[int]:
    """한 검사의 문제가 남은 휴지통 밖 대화 번호들(번호 순)."""
    return list((await db.scalars(await _open_ids(db, dataset_id=dataset_id, check=check))).all())


async def undo_many(db: AsyncSession, *, changes: list[Change]) -> tuple[int, int]:
    """결정들을 되돌린다(최근 것부터). 그 사이 글이 바뀐 것은 건너뛴다. (되돌린 수, 건너뛴 수). 커밋은 부른 쪽이 한다."""
    reverted = skipped = 0
    for change in sorted(changes, key=lambda item: item.id, reverse=True):
        try:
            await _revert(db, change)
        except ConflictError:
            skipped += 1
            continue
        reverted += 1
    return reverted, skipped


async def undo_change(db: AsyncSession, *, change_id: int) -> ConversationItem:
    """결정 하나를 되돌린다. 없으면 NotFoundError, 이미 되돌렸거나 그 뒤에 글이 바뀌었으면 ConflictError."""
    change = await db.get(Change, change_id, populate_existing=True)
    if change is None:
        raise NotFoundError("결정 기록을 찾을 수 없습니다.")
    if change.undone_at is not None:
        raise ConflictError(UNDONE_MESSAGE)
    await _revert(db, change)
    await datasets_service.touch_dataset(db, dataset_id=change.dataset_id)
    await db.commit()
    return await get_conversation(db, prompt_id=change.prompt_id)


async def _revert(db: AsyncSession, change: Change) -> None:
    """결정 하나를 '전'으로 돌리고 되돌린 시각을 적는다. 그 사이 글이 바뀌었으면 ConflictError."""
    item = await get_conversation(db, prompt_id=change.prompt_id)
    prompt, answer = item.prompt, item.answer
    action = Action(change.action)
    if action == Action.DROP:
        prompt.trashed_at = None
    elif action == Action.KEEP:
        prompt.kept = [key for key in prompt.kept if key != change.check_key]
        prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)
    else:
        current = {"messages": prompt.messages, "content": answer.content}
        after = {"messages": change.after["messages"], "content": change.after["content"]}
        if current != after:
            raise ConflictError(STALE_UNDO_MESSAGE)
        _set_text(
            prompt, answer, messages=change.before["messages"], content=change.before["content"]
        )
        if "source" in change.before:
            answer.source = change.before["source"]
        await semantic.forget_judgments(db, prompt_id=prompt.id)
    change.undone_at = datetime.now(UTC)
    prompt.row_version += 1


def change_read(change: Change) -> ChangeRead:
    """결정 기록을 응답 모양으로."""
    return ChangeRead.model_validate(change)


# ---------- 휴지통 ----------


async def bulk_update(db: AsyncSession, *, dataset_id: int, data: ConversationBulkUpdate) -> int:
    """여러 대화를 휴지통에 넣거나 되살린다. 실제로 바뀐 수. 데이터셋이 없으면 NotFoundError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    is_trashing = data.action == "trash"
    result = await db.execute(
        update(Prompt)
        .where(
            Prompt.dataset_id == dataset_id,
            Prompt.id.in_(data.ids),
            IS_ACTIVE if is_trashing else IS_TRASHED,
        )
        .values(
            trashed_at=datetime.now(UTC) if is_trashing else None,
            row_version=Prompt.row_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return int(result.rowcount or 0)


async def empty_trash(db: AsyncSession, *, dataset_id: int) -> int:
    """휴지통의 대화를 영구히 지운다(응답 · 결정 기록도). 지운 수. 데이터셋이 없으면 NotFoundError."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    result = await db.execute(
        delete(Prompt)
        .where(Prompt.dataset_id == dataset_id, IS_TRASHED)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        await datasets_service.touch_dataset(db, dataset_id=dataset_id)
    await db.commit()
    return int(result.rowcount or 0)


# ---------- 안에서만 쓰는 함수 ----------


async def _open_ids(db: AsyncSession, *, dataset_id: int, check: CheckKey) -> Any:
    """한 검사의 문제가 남은 휴지통 밖 대화 번호들(번호 순) 쿼리."""
    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    return problems.open_ids(dataset_id, check, analysis_id=analysis.id if analysis else None)


def _problem_condition(dataset_id: int, check: CheckKey, analysis_id: int | None) -> Any:
    """목록의 문제 거르기 조건. 글의 모양 검사는 칸으로 바로, 해시 · 뜻 검사는 번호 쿼리로."""
    if check in RULE_CHECKS and check != CheckKey.DUPLICATE:
        return has_open(check.value)
    return Prompt.id.in_(
        problems.open_ids(dataset_id, check, analysis_id=analysis_id).scalar_subquery()
    )


def is_rewritable(item: ConversationItem, check: CheckKey) -> bool:
    """다시 쓰기로 풀리는 문제인지: 문제 자리가 모두 마지막 응답에 있다(자리 없는 검사는 마지막 응답의 것).

    다시 쓰기는 마지막 응답만 바꾸므로, 앞 턴의 응답에 있는 문제(예: 루프를 자른 앞 턴)는 풀지 못한다.
    """
    marks = item.prompt.marks.get(check.value) or []
    last = len(item.prompt.messages)
    return all(mark.get("turn") in (None, last) for mark in marks)


def _is_open(item: ConversationItem, check: CheckKey) -> bool:
    """이 대화에 그 검사의 문제가 남았는지."""
    if check == CheckKey.DUPLICATE:
        return item.is_duplicate
    if check == CheckKey.NEAR_DUPLICATE:
        return item.is_near_duplicate
    if check == CheckKey.MISMATCH:
        return item.is_mismatch
    return check in item.prompt.open_checks


async def _items(db: AsyncSession, *, dataset_id: int, ids: list[int]) -> list[ConversationItem]:
    """번호들의 대화를 그 순서대로 (지시 · 마지막 응답 · 해시 · 뜻으로 센 문제 · Jev 판정)."""
    if not ids:
        return []
    prompts = {p.id: p for p in (await db.scalars(select(Prompt).where(Prompt.id.in_(ids)))).all()}
    # 지시마다 마지막(번호가 가장 큰) 응답. 첫 판은 지시마다 응답이 하나다.
    answers: dict[int, Response] = {}
    for answer in (
        await db.scalars(select(Response).where(Response.prompt_id.in_(ids)).order_by(Response.id))
    ).all():
        answers[answer.prompt_id] = answer
    duplicate_ids = await _among(db, problems.duplicate_extras(dataset_id), ids)
    near_ids: set[int] = set()
    mismatch: set[int] = set()
    judgments: dict[int, Judgment] = {}
    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    if analysis is not None:
        near_ids = await _among(db, problems.near_extras(dataset_id, analysis.id), ids)
        mismatch = await _among(db, problems.mismatch_ids(dataset_id, analysis.id), ids)
        judgments = await semantic.judgments_for(db, dataset_id=dataset_id, prompt_ids=ids)
    return [
        ConversationItem(
            prompt=prompts[i],
            answer=answers[i],
            is_duplicate=i in duplicate_ids,
            is_near_duplicate=i in near_ids,
            is_mismatch=i in mismatch,
            judgment=judgments.get(i),
        )
        for i in ids
        if i in prompts and i in answers
    ]


async def _among(db: AsyncSession, query: Any, ids: list[int]) -> set[int]:
    """번호 쿼리의 결과 가운데 ids에 든 것."""
    found = query.subquery()
    return set((await db.scalars(select(found.c.id).where(found.c.id.in_(ids)))).all())


def _jev_read(judgment: Judgment | None) -> JevRead | None:
    if judgment is None:
        return None
    return JevRead(
        follows=judgment.follows,
        refusal_ok=judgment.refusal_ok,
        similarity=judgment.similarity,
        looks_fine=judging.looks_fine(judgment.follows),
    )


def _apply(
    prompt: Prompt,
    answer: Response,
    *,
    check: CheckKey,
    action: Action,
    rewritten: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """결정을 지시 · 응답에 반영하고 (전, 후)를 돌려준다. rewritten은 다시 쓰기의 새 응답."""
    if action == Action.DROP:
        prompt.trashed_at = datetime.now(UTC)
        return {"trashed": False}, {"trashed": True}
    if action == Action.KEEP:
        prompt.kept = [*prompt.kept, check.value]
        prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)
        return {"kept": False, "check": check.value}, {"kept": True, "check": check.value}
    before = {"messages": prompt.messages, "content": answer.content, "source": answer.source}
    if action == Action.REWRITE and rewritten is not None:
        messages, content = prompt.messages, rewritten
        answer.source = Source.SYNTHETIC.value
    elif action == Action.MASK:
        messages = [{**m, "content": rules.mask_pii(m["content"])} for m in prompt.messages]
        content = rules.mask_pii(answer.content)
    else:
        # 루프는 앞 턴의 응답(맥락)에도 있을 수 있다. 응답마다 자르고, 루프가 없는 글은 그대로 둔다.
        messages = [
            {**m, "content": rules.cut_loop(m["content"])} if m["role"] == Role.ASSISTANT else m
            for m in prompt.messages
        ]
        content = rules.cut_loop(answer.content)
    _set_text(prompt, answer, messages=messages, content=content)
    return before, {"messages": messages, "content": content, "source": answer.source}


def _set_text(
    prompt: Prompt, answer: Response, *, messages: list[dict[str, Any]], content: str
) -> None:
    """지시 맥락 · 응답 글을 바꾸고 해시 · 규칙 검사를 다시 잰다."""
    # JSONB 칼럼은 안의 값을 고쳐도 SQLAlchemy가 모르므로 새 목록으로 바꿔 넣는다.
    prompt.messages = [dict(message) for message in messages]
    prompt.text_hash = importing.context_hash(prompt.messages)
    answer.content = content
    prompt.marks = rules.measure(importing.conversation(prompt.messages, content))
    prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)


def _problem_read(
    key: CheckKey, mark: dict[str, Any], messages: list[dict[str, Any]]
) -> ProblemRead:
    turn, start, end = mark.get("turn"), mark.get("start"), mark.get("end")
    if key == CheckKey.STRUCTURE:
        snippet = "역할 순서 " + " → ".join(m["role"] for m in messages)
    elif turn is not None and start is not None and end is not None and turn < len(messages):
        snippet = messages[turn]["content"][start:end]
    else:
        snippet = SNIPPET_WITHOUT_SPAN.get(key, "")
    if len(snippet) > SNIPPET_LENGTH:
        snippet = snippet[:SNIPPET_LENGTH] + "…"
    return ProblemRead(key=key, turn=turn, start=start, end=end, snippet=snippet)


def _escape_like(text: str) -> str:
    """LIKE 패턴의 특수 글자(%, _, 탈출 글자)를 글자 그대로 찾게 한다."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
