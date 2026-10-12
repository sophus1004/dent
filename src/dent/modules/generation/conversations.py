"""대화 목록 · 한 건 · 검토 큐와 검토 결정, 결정 기록 · 되돌리기, 그대로 둔 것 다시 묻기, 휴지통.

대화 한 건 = 지시 줄 + 주 응답(없으면 지시만) + 다른 응답들 + 선호 쌍들. 결정은 바로 반영하고 전 · 후를
generation_changes에 남긴다. 되돌리기는 지금 값이 '후'와 같을 때만 '전'으로 돌린다(그 사이에 바뀌었으면 409).
- 대화 결정: 빼기 · 그대로 두기 · 가리기 · 자르기 · 다시 쓰기(LLM) · 응답 만들기(LLM, 응답이 없는 지시만).
  글을 바꾸는 결정은 규칙 검사를 다시 재고(lengths.measure_marks: 학습할 모델의 토크나이저가 있으면 그것으로)
  그 대화의 Jev 판정을 지운다(옛 글의 판정이라서). LLM이 쓴 응답은 출처가 synthetic이다.
- 쌍 결정(3 선호): 쌍 빼기 · 뒤집기(고른 응답 ↔ 버린 응답) · 그대로 두기. 뒤집으면 SFT에 쓰는 주 응답도
  새 고른 응답으로 옮긴다(선호 데이터의 주 응답은 고른 응답이다).
- 비교 만들기로 더한 쌍(pair)을 되돌리면 쌍과 만든 응답을 지운다. 응답 만들기를 되돌리면 만든 응답을 지운다.
문제가 남았는지는 problems.py의 조건으로 본다(뜻 검사는 다 만든 뜻 분석으로). 영구 삭제는 휴지통 비우기뿐이다.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, cast, delete, exists, func, insert, not_, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.generation import (
    answering,
    importing,
    judging,
    lengths,
    problems,
    rewriting,
    rules,
    semantic,
)
from dent.modules.generation import service as generation_service
from dent.modules.generation.models import (
    Action,
    Change,
    Contamination,
    Judgment,
    Pair,
    Prompt,
    Response,
    Role,
    Source,
)
from dent.modules.generation.rules import CHECKS, CheckKey
from dent.modules.generation.schemas import (
    AnswerRead,
    ChangeRead,
    ConversationBulkUpdate,
    ConversationRead,
    ConversationStatusFilter,
    DecideAllCreate,
    DecisionCreate,
    JevRead,
    MessageRead,
    PairDecisionCreate,
    PairRead,
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

# 검토 큐에 싣는 번호의 최대 수 (화면이 앞에서부터 한 건씩 넘긴다)
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
    CheckKey.NO_ANSWER: "(응답 없음)",
    CheckKey.LONG: "최대 길이를 넘음",
}

# 도구 결과 메시지의 문제 낱말 (호출 쪽의 문제는 rules.tool_problem_text가 준다)
TOOL_RESULT_PROBLEM = "가리키는 호출 없음"
TOOL_OPEN_CALL_PROBLEM = "결과 없는 호출"

# 글을 바꾸는 결정 (규칙 검사를 다시 재고 Jev 판정을 지운다)
TEXT_ACTIONS = (Action.MASK, Action.CUT, Action.REWRITE, Action.ANSWER)

# LLM 요청을 보낼 곳(다시 쓰기 · 응답 만들기). 테스트만 가짜 LLM 서버를 넣는다(평소에는 None = 실제 연결).
REWRITE_TRANSPORT: Any = None

NOT_FOUND_MESSAGE = "대화를 찾을 수 없습니다."
PAIR_NOT_FOUND_MESSAGE = "선호 쌍을 찾을 수 없습니다."
WRONG_ACTION_MESSAGE = "이 검사에서는 할 수 없는 결정입니다."
PAIR_CHECK_MESSAGE = "선호 쌍의 검사는 쌍에서 결정합니다."
NOT_PAIR_CHECK_MESSAGE = "선호 쌍의 검사가 아닙니다."
NO_PROBLEM_MESSAGE = "이 대화에는 그 문제가 남아 있지 않습니다. 새로 불러와 주세요."
PAIR_NO_PROBLEM_MESSAGE = "이 쌍에는 그 문제가 남아 있지 않습니다. 새로 불러와 주세요."
TRASHED_MESSAGE = "휴지통에 있는 대화입니다. 먼저 되살려 주세요."
PAIR_TRASHED_MESSAGE = "뺀 쌍입니다. 결정 기록에서 되돌려 주세요."
UNDONE_MESSAGE = "이미 되돌린 결정입니다."
STALE_UNDO_MESSAGE = "그 뒤에 대화가 바뀌어 이 결정을 되돌릴 수 없습니다."
LLM_NOT_CONNECTED_MESSAGE = "LLM 미연결: 연결 설정에서 LLM을 연결하세요."
EARLIER_TURN_MESSAGE = "앞 턴의 응답에 있는 문제는 다시 쓸 수 없습니다. 빼거나 그대로 두세요."
NO_ANSWER_TO_REWRITE_MESSAGE = "응답이 없는 대화입니다. 응답 만들기를 골라 주세요."
TOOL_ANSWER_MESSAGE = "도구 대화의 응답은 만들지 않습니다. 빼거나 그대로 두세요."
JEV_ONLY_KEEP_MESSAGE = "Jev가 괜찮다고 본 대화에는 '모두 그대로 두기'만 할 수 있습니다."
NOT_KEPT_MESSAGE = "그대로 둔 검사가 아닙니다."

# 검색어 안의 LIKE 특수 글자를 글자 그대로 찾게 할 때 쓰는 탈출 글자
LIKE_ESCAPE = "\\"


@dataclass(frozen=True)
class ConversationItem:
    """대화 한 건: 지시 줄 · 주 응답 · 다른 응답 · 선호 쌍, 해시 · 뜻 · 대조로 센 문제, Jev 판정."""

    # 지시 (맥락 · 문제 표시 · 휴지통)
    prompt: Prompt

    # 주 응답 (SFT에 쓰는 응답). 없으면 지시만이다.
    answer: Response | None

    # 응답 전부 (주 응답이 앞, 그 뒤 번호 순)
    answers: list[Response] = field(default_factory=list)

    # 선호 쌍 전부 (뺀 쌍도, 번호 순)
    pairs: list[Pair] = field(default_factory=list)

    # 같은 지시의 여분인지 (진단의 '같은 지시'에 드는지)
    is_duplicate: bool = False

    # 근접 중복의 여분 · Jev가 확인한 지시 불일치 · Jev가 해롭다고 본 응답인지 (다 만든 뜻 분석)
    is_near_duplicate: bool = False
    is_mismatch: bool = False
    is_unsafe: bool = False

    # 평가셋과 겹친 대조 결과 (그대로 두기로 둔 것도 싣는다). 겹치지 않았으면 None
    contamination: Contamination | None = None

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


async def get_pair_conversation(db: AsyncSession, *, pair_id: int) -> ConversationItem:
    """선호 쌍 하나가 든 대화. 쌍이 없으면 NotFoundError."""
    pair = await db.get(Pair, pair_id, populate_existing=True)
    if pair is None:
        raise NotFoundError(PAIR_NOT_FOUND_MESSAGE)
    return await get_conversation(db, prompt_id=pair.prompt_id)


async def review_queue(
    db: AsyncSession, *, dataset_id: int, check: CheckKey, kept: bool = False
) -> tuple[list[int], int]:
    """한 검사의 문제가 남은 휴지통 밖 번호들(번호 순, 최대 REVIEW_MAX)과 전체 수.

    쌍 검사면 쌍 번호, 아니면 대화 번호다. kept면 그 검사를 '그대로 두기'로 둔 것들(다시 묻기 목록)이다.
    """
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    if kept:
        query = kept_ids(dataset_id, check)
    elif CHECKS[check].unit == "pair":
        query = problems.pair_open_ids(dataset_id, check)
    else:
        query = await _open_ids(db, dataset_id=dataset_id, check=check)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    ids = (await db.scalars(query.limit(REVIEW_MAX))).all()
    return list(ids), int(total or 0)


def kept_ids(dataset_id: int, check: CheckKey) -> Any:
    """이 검사를 '그대로 두기'로 둔 휴지통 밖 번호들(번호 순). 쌍 검사면 쌍 번호."""
    if CHECKS[check].unit == "pair":
        return problems.active_pairs(dataset_id).where(Pair.kept.contains([check.value]))
    return (
        select(Prompt.id)
        .where(Prompt.dataset_id == dataset_id, IS_ACTIVE, problems.is_kept(check))
        .order_by(Prompt.id)
    )


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
    """대화 한 건을 응답 모양으로: 메시지 전체 · 응답들 · 쌍들 · 남은 문제(조각 포함) · 그대로 둔 검사."""
    prompt, answer = item.prompt, item.answer
    messages = _messages(item)
    found = [
        _problem_read(CheckKey(key), mark, messages, tools=prompt.tools)
        for key, marks in prompt.marks.items()
        if key in prompt.open_checks and key in CheckKey.__members__.values()
        for mark in marks
    ]
    kept = set(prompt.kept)
    if answer is None and CheckKey.NO_ANSWER.value not in kept:
        found.append(_problem_read(CheckKey.NO_ANSWER, {}, messages))
    if item.is_duplicate:
        found.append(_problem_read(CheckKey.DUPLICATE, {}, messages))
    if item.is_near_duplicate:
        found.append(_problem_read(CheckKey.NEAR_DUPLICATE, {}, messages))
    if item.contamination is not None and CheckKey.CONTAMINATION.value not in kept:
        found.append(
            _whole(CheckKey.CONTAMINATION, f"평가셋과 겹침 {item.contamination.coverage:.0%}")
        )
    judgment = item.judgment
    if item.is_mismatch and judgment is not None:
        # 응답 전체에 걸친 문제: 마지막 응답 자리에 칠하지 않고 줄로만 보인다.
        found.append(_whole(CheckKey.MISMATCH, f"Jev 따름 {judgment.follows or 0:.2f}"))
    if item.is_unsafe and judgment is not None:
        found.append(_whole(CheckKey.UNSAFE, f"Jev 해로움 {judgment.unsafe or 0:.2f}"))
    # 대화 안의 자리 순으로(대화 전체에 걸친 것이 먼저). JSONB는 열쇠 순서를 지키지 않는다.
    found.sort(
        key=lambda problem: (-1 if problem.turn is None else problem.turn, problem.start or 0)
    )
    is_rewritten = answer is not None and answer.source != Source.ORIGINAL
    return ConversationRead(
        id=prompt.id,
        dataset_id=prompt.dataset_id,
        messages=[_message_read(message) for message in messages],
        has_answer=answer is not None,
        answers=[AnswerRead.model_validate(response) for response in item.answers],
        pairs=[_pair_read(pair) for pair in item.pairs],
        tools=prompt.tools,
        source=answer.source if is_rewritten and answer is not None else prompt.source,
        problems=found,
        kept=[CheckKey(key) for key in prompt.kept if key in CheckKey.__members__.values()],
        trashed=prompt.trashed_at is not None,
        row_version=prompt.row_version,
        extra=prompt.extra,
        jev=_jev_read(judgment),
        import_id=prompt.import_id,
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


# ---------- 대화 결정 ----------


async def decide(
    db: AsyncSession, *, prompt_id: int, data: DecisionCreate
) -> tuple[ConversationItem, Change]:
    """검토 결정 하나를 바로 반영하고 기록한다.

    없으면 NotFoundError, 그 검사에서 할 수 없는 결정 · 쌍 검사 · LLM 미연결이면 InvalidInputError,
    휴지통에 있거나 그 문제가 이미 없으면 ConflictError.
    """
    if CHECKS[data.check].unit == "pair":
        raise InvalidInputError(PAIR_CHECK_MESSAGE)
    if data.action not in CHECKS[data.check].actions:
        raise InvalidInputError(WRONG_ACTION_MESSAGE)
    item = await get_conversation(db, prompt_id=prompt_id)
    prompt = item.prompt
    if prompt.trashed_at is not None:
        raise ConflictError(TRASHED_MESSAGE)
    if not _is_open(item, data.check):
        raise ConflictError(NO_PROBLEM_MESSAGE)

    written = None
    if data.action in (Action.REWRITE, Action.ANSWER):
        written = await _write_with_llm(db, item, check=data.check, action=data.action)
    change = await _apply_decision(db, item, check=data.check, action=data.action, written=written)
    await datasets_service.touch_dataset(db, dataset_id=prompt.dataset_id)
    await db.commit()
    # 커밋하면 속성이 만료되므로, 응답 모양으로 바꾸기 전에 다시 읽는다(비동기 세션은 게으른 읽기를 못 한다).
    await db.refresh(change)
    return await get_conversation(db, prompt_id=prompt_id), change


async def decide_all(db: AsyncSession, *, dataset_id: int, data: DecideAllCreate) -> int:
    """한 검사의 남은 대화(쌍) 모두에 같은 결정(모두 빼기 · 모두 그대로 두기). 바꾼 수. 하나마다 결정 기록을 남긴다."""
    await generation_service.get_dataset(db, dataset_id=dataset_id)
    if data.action not in CHECKS[data.check].actions:
        raise InvalidInputError(WRONG_ACTION_MESSAGE)
    is_pair_check = CHECKS[data.check].unit == "pair"
    if data.only_jev_ok:
        if data.action != Action.KEEP or is_pair_check:
            raise InvalidInputError(JEV_ONLY_KEEP_MESSAGE)
        analysis = await problems.done_analysis(db, dataset_id=dataset_id)
        if analysis is None:
            return 0
        query = problems.jev_fine_ids(dataset_id, analysis.id, data.check)
    elif is_pair_check:
        query = problems.pair_open_ids(dataset_id, data.check)
    else:
        query = await _open_ids(db, dataset_id=dataset_id, check=data.check)
    ids = list((await db.scalars(query)).all())
    if not ids:
        return 0
    action = Action(data.action)
    if is_pair_check:
        await apply_pair_bulk(db, dataset_id=dataset_id, check=data.check, action=action, ids=ids)
    else:
        await apply_bulk(db, dataset_id=dataset_id, check=data.check, action=action, ids=ids)
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
    """대화 하나의 글을 바꾸는 결정(가리기 · 자르기 · 다시 쓰기 · 응답 만들기)을 반영하고 기록한다.

    rewritten은 LLM이 쓴 응답(다시 쓰기 · 응답 만들기)이다. 커밋은 부른 쪽이 한다.
    """
    await _apply_decision(
        db, item, check=check, action=action, written=rewritten, run_id=run_id, event_id=event_id
    )


async def items_of(db: AsyncSession, *, dataset_id: int, ids: list[int]) -> list[ConversationItem]:
    """번호들의 대화를 그 순서대로 (문제 · Jev 판정과 함께)."""
    return await _items(db, dataset_id=dataset_id, ids=ids)


async def open_ids(db: AsyncSession, *, dataset_id: int, check: CheckKey) -> list[int]:
    """한 검사의 문제가 남은 휴지통 밖 번호들(번호 순). 쌍 검사면 쌍 번호."""
    if CHECKS[check].unit == "pair":
        query = problems.pair_open_ids(dataset_id, check)
    else:
        query = await _open_ids(db, dataset_id=dataset_id, check=check)
    return list((await db.scalars(query)).all())


def is_rewritable(item: ConversationItem, check: CheckKey) -> bool:
    """다시 쓰기로 풀리는 문제인지: 주 응답이 있고, 문제 자리가 모두 마지막 응답에 있다(자리 없는 검사는 마지막 응답의 것).

    다시 쓰기는 마지막 응답만 바꾸므로, 앞 턴의 응답에 있는 문제(예: 루프를 자른 앞 턴)는 풀지 못한다.
    """
    if item.answer is None:
        return False
    marks = item.prompt.marks.get(check.value) or []
    last = len(item.prompt.messages)
    return all(mark.get("turn") in (None, last) for mark in marks)


def is_answerable(item: ConversationItem) -> bool:
    """응답 만들기로 채울 수 있는 대화인지: 응답이 없고 도구 대화가 아니다(호출을 지어내지 않게)."""
    return item.answer is None and not item.prompt.tools


# ---------- 쌍 결정 ----------


async def decide_pair(
    db: AsyncSession, *, pair_id: int, data: PairDecisionCreate
) -> tuple[ConversationItem, Change]:
    """선호 쌍 결정 하나(쌍 빼기 · 뒤집기 · 그대로 두기)를 바로 반영하고 기록한다.

    쌍이 없으면 NotFoundError, 쌍 검사가 아니거나 할 수 없는 결정이면 InvalidInputError,
    쌍 · 대화가 빠졌거나 그 문제가 이미 없으면 ConflictError.
    """
    if CHECKS[data.check].unit != "pair":
        raise InvalidInputError(NOT_PAIR_CHECK_MESSAGE)
    if data.action not in CHECKS[data.check].actions:
        raise InvalidInputError(WRONG_ACTION_MESSAGE)
    pair = await db.get(Pair, pair_id, populate_existing=True)
    if pair is None:
        raise NotFoundError(PAIR_NOT_FOUND_MESSAGE)
    prompt = await db.get(Prompt, pair.prompt_id, populate_existing=True)
    if prompt is None or prompt.trashed_at is not None:
        raise ConflictError(TRASHED_MESSAGE)
    if pair.trashed_at is not None:
        raise ConflictError(PAIR_TRASHED_MESSAGE)
    if data.check not in problems.pair_problems(pair):
        raise ConflictError(PAIR_NO_PROBLEM_MESSAGE)
    change = await apply_pair(db, pair=pair, check=data.check, action=Action(data.action))
    prompt.row_version += 1
    await datasets_service.touch_dataset(db, dataset_id=pair.dataset_id)
    await db.commit()
    await db.refresh(change)
    return await get_conversation(db, prompt_id=pair.prompt_id), change


async def apply_pair(
    db: AsyncSession,
    *,
    pair: Pair,
    check: CheckKey,
    action: Action,
    run_id: int | None = None,
    event_id: int | None = None,
) -> Change:
    """쌍 하나에 빼기 · 뒤집기 · 그대로 두기를 반영하고 결정 기록을 남긴다. 커밋은 부른 쪽이 한다."""
    # 쌍 결정은 before · after에 "pair"를 둔다(쌍이 지워져 pair_id가 비어도 대화 결정과 가르게).
    if action == Action.DROP:
        pair.trashed_at = datetime.now(UTC)
        before: dict[str, Any] = {"pair": True, "trashed": False}
        after: dict[str, Any] = {"pair": True, "trashed": True}
    elif action == Action.KEEP:
        pair.kept = [*pair.kept, check.value]
        before = {"pair": True, "kept": False, "check": check.value}
        after = {"pair": True, "kept": True, "check": check.value}
    else:
        main_before = await _main_id(db, prompt_id=pair.prompt_id)
        before = {
            "chosen_id": pair.chosen_id,
            "rejected_id": pair.rejected_id,
            "main_id": main_before,
        }
        await _flip(db, pair)
        after = {
            "chosen_id": pair.chosen_id,
            "rejected_id": pair.rejected_id,
            "main_id": await _main_id(db, prompt_id=pair.prompt_id),
        }
    change = Change(
        dataset_id=pair.dataset_id,
        prompt_id=pair.prompt_id,
        pair_id=pair.id,
        check_key=check.value,
        action=action.value,
        before=before,
        after=after,
        run_id=run_id,
        event_id=event_id,
    )
    db.add(change)
    await db.flush()
    return change


async def apply_pair_bulk(
    db: AsyncSession,
    *,
    dataset_id: int,
    check: CheckKey,
    action: Action,
    ids: list[int],
    run_id: int | None = None,
    event_id: int | None = None,
) -> None:
    """쌍들에 같은 결정(빼기 · 뒤집기 · 그대로 두기)을 하나씩 반영하고 기록한다. 커밋은 부른 쪽이 한다."""
    for pair in (
        await db.scalars(select(Pair).where(Pair.id.in_(ids), Pair.dataset_id == dataset_id))
    ).all():
        await apply_pair(
            db, pair=pair, check=check, action=action, run_id=run_id, event_id=event_id
        )


async def add_pair(
    db: AsyncSession,
    *,
    prompt: Prompt,
    main: Response,
    content: str,
    main_follows: float,
    other_follows: float,
    run_id: int | None = None,
    event_id: int | None = None,
) -> Pair:
    """비교 만들기: 주 응답과 견줄 응답(LLM이 쓴 것)을 더하고, Jev 따름 점수가 높은 쪽을 고른 응답으로 둔 쌍을 더한다.

    주 응답은 그대로 둔다(SFT에 쓰는 응답은 원본이 먼저다). 커밋은 부른 쪽이 한다.
    """
    other = Response(
        dataset_id=prompt.dataset_id,
        prompt_id=prompt.id,
        content=content,
        is_main=False,
        source=Source.SYNTHETIC.value,
    )
    db.add(other)
    await db.flush()
    main_wins = main_follows >= other_follows
    chosen, rejected = (main, other) if main_wins else (other, main)
    pair = Pair(
        dataset_id=prompt.dataset_id,
        prompt_id=prompt.id,
        chosen_id=chosen.id,
        rejected_id=rejected.id,
        source=Source.SYNTHETIC.value,
        marks=rules.measure_pair(
            chosen.content, rejected.content, chosen_score=None, rejected_score=None
        ),
        judge_chosen=main_follows if main_wins else other_follows,
        judge_rejected=other_follows if main_wins else main_follows,
    )
    db.add(pair)
    await db.flush()
    db.add(
        Change(
            dataset_id=prompt.dataset_id,
            prompt_id=prompt.id,
            pair_id=pair.id,
            check_key=None,
            action=Action.PAIR.value,
            before={"pair": None},
            after={"pair_id": pair.id, "response_id": other.id, "content": content},
            run_id=run_id,
            event_id=event_id,
        )
    )
    prompt.row_version += 1
    return pair


# ---------- 그대로 둔 것 다시 묻기 ----------


async def unkeep(db: AsyncSession, *, prompt_id: int, check: CheckKey) -> ConversationItem:
    """대화의 '그대로 두기'를 거둬 그 검사로 다시 묻는다(그 결정 기록은 되돌린 것으로 둔다).

    없으면 NotFoundError, 그대로 둔 검사가 아니면 ConflictError.
    """
    item = await get_conversation(db, prompt_id=prompt_id)
    prompt = item.prompt
    if check.value not in prompt.kept:
        raise ConflictError(NOT_KEPT_MESSAGE)
    prompt.kept = [key for key in prompt.kept if key != check.value]
    prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)
    prompt.row_version += 1
    await _close_keeps(db, prompt_id=prompt_id, pair_id=None, check=check)
    await datasets_service.touch_dataset(db, dataset_id=prompt.dataset_id)
    await db.commit()
    return await get_conversation(db, prompt_id=prompt_id)


async def unkeep_pair(db: AsyncSession, *, pair_id: int, check: CheckKey) -> ConversationItem:
    """쌍의 '그대로 두기'를 거둬 그 검사로 다시 묻는다. 없으면 NotFoundError, 그대로 둔 검사가 아니면 ConflictError."""
    pair = await db.get(Pair, pair_id, populate_existing=True)
    if pair is None:
        raise NotFoundError(PAIR_NOT_FOUND_MESSAGE)
    if check.value not in pair.kept:
        raise ConflictError(NOT_KEPT_MESSAGE)
    pair.kept = [key for key in pair.kept if key != check.value]
    await _close_keeps(db, prompt_id=pair.prompt_id, pair_id=pair.id, check=check)
    await datasets_service.touch_dataset(db, dataset_id=pair.dataset_id)
    await db.commit()
    return await get_conversation(db, prompt_id=pair.prompt_id)


async def _close_keeps(
    db: AsyncSession, *, prompt_id: int, pair_id: int | None, check: CheckKey
) -> None:
    """그대로 두기를 거둔 대화 · 쌍의 '그대로 두기' 결정 기록을 되돌린 것으로 적는다(되돌리기 목록에 남지 않게)."""
    await db.execute(
        update(Change)
        .where(
            Change.prompt_id == prompt_id,
            Change.pair_id.is_(None) if pair_id is None else Change.pair_id == pair_id,
            Change.check_key == check.value,
            Change.action == Action.KEEP.value,
            Change.undone_at.is_(None),
        )
        .values(undone_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )


# ---------- 되돌리기 ----------


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
    """결정 하나를 '전'으로 돌리고 되돌린 시각을 적는다. 그 사이 바뀌었으면 ConflictError."""
    action = Action(change.action)
    is_pair_change = action in (Action.FLIP, Action.PAIR) or change.after.get("pair") is True
    if is_pair_change:
        await _revert_pair(db, change, action)
    else:
        await _revert_conversation(db, change, action)
    change.undone_at = datetime.now(UTC)
    prompt = await db.get(Prompt, change.prompt_id)
    if prompt is not None:
        prompt.row_version += 1


async def _revert_conversation(db: AsyncSession, change: Change, action: Action) -> None:
    item = await get_conversation(db, prompt_id=change.prompt_id)
    prompt, answer = item.prompt, item.answer
    if action == Action.DROP:
        prompt.trashed_at = None
        return
    if action == Action.KEEP:
        prompt.kept = [key for key in prompt.kept if key != change.check_key]
        prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)
        return
    if action == Action.ANSWER:
        is_same = answer is not None and (
            answer.id == change.after.get("answer_id")
            and answer.content == change.after.get("content")
        )
        if not is_same or answer is None:
            raise ConflictError(STALE_UNDO_MESSAGE)
        # 이 응답으로 만든 쌍(비교 만들기)도 함께 지워진다(외래 키). 결정 기록은 쌍 번호만 비고 남는다.
        await db.execute(delete(Response).where(Response.id == answer.id))
        await _set_text(db, prompt, None, messages=prompt.messages, content=None)
        await semantic.forget_judgments(db, prompt_id=prompt.id)
        return
    current = {"messages": prompt.messages, "content": answer.content if answer else None}
    after = {"messages": change.after["messages"], "content": change.after["content"]}
    if current != after:
        raise ConflictError(STALE_UNDO_MESSAGE)
    await _set_text(
        db, prompt, answer, messages=change.before["messages"], content=change.before["content"]
    )
    if answer is not None and "source" in change.before:
        answer.source = change.before["source"]
    await semantic.forget_judgments(db, prompt_id=prompt.id)


async def _revert_pair(db: AsyncSession, change: Change, action: Action) -> None:
    pair = await db.get(Pair, change.pair_id, populate_existing=True) if change.pair_id else None
    if pair is None:
        raise ConflictError(STALE_UNDO_MESSAGE)
    if action == Action.DROP:
        pair.trashed_at = None
    elif action == Action.KEEP:
        pair.kept = [key for key in pair.kept if key != change.check_key]
    elif action == Action.FLIP:
        now = {
            "chosen_id": pair.chosen_id,
            "rejected_id": pair.rejected_id,
            "main_id": await _main_id(db, prompt_id=pair.prompt_id),
        }
        if now != {key: change.after.get(key) for key in now}:
            raise ConflictError(STALE_UNDO_MESSAGE)
        await _flip(db, pair)
    else:
        # 비교 만들기: 더한 쌍과 그때 쓴 응답을 지운다(그 응답이 주 응답이 되지 않았을 때만).
        other = await db.get(Response, change.after.get("response_id"))
        if other is None or other.is_main or other.content != change.after.get("content"):
            raise ConflictError(STALE_UNDO_MESSAGE)
        await db.execute(delete(Pair).where(Pair.id == pair.id))
        await db.execute(delete(Response).where(Response.id == other.id))


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
    """휴지통의 대화를 영구히 지운다(응답 · 쌍 · 결정 기록도). 지운 수. 데이터셋이 없으면 NotFoundError."""
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
    """목록의 문제 거르기 조건. 글의 모양 검사는 칸으로 바로, 해시 · 뜻 · 대조 · 쌍 검사는 번호 쿼리로."""
    # 글의 표시(marks)로 남는 검사만 칸으로 바로 거른다. 같은 지시 · 응답 없음은 표시가 아니다.
    is_mark = check in rules.MARK_CHECKS and check not in (CheckKey.DUPLICATE, CheckKey.NO_ANSWER)
    if is_mark:
        return has_open(check.value)
    if CHECKS[check].unit == "pair":
        pair_prompts = (
            select(Pair.prompt_id)
            .where(Pair.id.in_(problems.pair_open_ids(dataset_id, check).scalar_subquery()))
            .scalar_subquery()
        )
        return Prompt.id.in_(pair_prompts)
    return Prompt.id.in_(
        problems.open_ids(dataset_id, check, analysis_id=analysis_id).scalar_subquery()
    )


def _is_open(item: ConversationItem, check: CheckKey) -> bool:
    """이 대화에 그 검사의 문제가 남았는지."""
    kept = set(item.prompt.kept)
    if check == CheckKey.DUPLICATE:
        return item.is_duplicate
    if check == CheckKey.NEAR_DUPLICATE:
        return item.is_near_duplicate
    if check == CheckKey.MISMATCH:
        return item.is_mismatch
    if check == CheckKey.UNSAFE:
        return item.is_unsafe
    if check == CheckKey.NO_ANSWER:
        return item.answer is None and check.value not in kept
    if check == CheckKey.CONTAMINATION:
        return item.contamination is not None and check.value not in kept
    return check in item.prompt.open_checks


async def _write_with_llm(
    db: AsyncSession, item: ConversationItem, *, check: CheckKey, action: Action
) -> str:
    """검토의 다시 쓰기 · 응답 만들기: LLM이 쓴 응답 글. 할 수 없는 대화 · LLM 미연결이면 InvalidInputError."""
    if action == Action.REWRITE and item.answer is None:
        raise InvalidInputError(NO_ANSWER_TO_REWRITE_MESSAGE)
    if action == Action.REWRITE and not is_rewritable(item, check):
        raise InvalidInputError(EARLIER_TURN_MESSAGE)
    if action == Action.ANSWER and not is_answerable(item):
        raise InvalidInputError(TOOL_ANSWER_MESSAGE)
    connection = await connections.get_connection(db, role=ConnectionRole.LLM)
    if connection is None:
        raise InvalidInputError(LLM_NOT_CONNECTED_MESSAGE)
    if action == Action.ANSWER:
        return await answering.write_answer(
            connection, messages=item.prompt.messages, transport=REWRITE_TRANSPORT
        )
    assert item.answer is not None
    return await rewriting.rewrite_answer(
        connection,
        messages=item.prompt.messages,
        content=item.answer.content,
        check=check,
        transport=REWRITE_TRANSPORT,
    )


async def _apply_decision(
    db: AsyncSession,
    item: ConversationItem,
    *,
    check: CheckKey,
    action: Action,
    written: str | None = None,
    run_id: int | None = None,
    event_id: int | None = None,
) -> Change:
    """대화 결정 하나를 반영하고 결정 기록을 더한다. written은 LLM이 쓴 응답. 커밋은 부른 쪽이 한다."""
    prompt, answer = item.prompt, item.answer
    if action == Action.DROP:
        prompt.trashed_at = datetime.now(UTC)
        before: dict[str, Any] = {"trashed": False}
        after: dict[str, Any] = {"trashed": True}
    elif action == Action.KEEP:
        prompt.kept = [*prompt.kept, check.value]
        prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)
        before, after = {"kept": False, "check": check.value}, {"kept": True, "check": check.value}
    elif action == Action.ANSWER:
        created = Response(
            dataset_id=prompt.dataset_id,
            prompt_id=prompt.id,
            content=written or "",
            is_main=True,
            source=Source.SYNTHETIC.value,
        )
        db.add(created)
        await db.flush()
        await _set_text(db, prompt, created, messages=prompt.messages, content=created.content)
        before, after = {"answer": None}, {"answer_id": created.id, "content": created.content}
    else:
        before = {
            "messages": prompt.messages,
            "content": answer.content if answer else None,
            "source": answer.source if answer else None,
        }
        messages, content = _changed_text(prompt, answer, action=action, written=written)
        if action == Action.REWRITE and answer is not None:
            answer.source = Source.SYNTHETIC.value
        await _set_text(db, prompt, answer, messages=messages, content=content)
        after = {
            "messages": messages,
            "content": content,
            "source": answer.source if answer else None,
        }
    if action in TEXT_ACTIONS:
        await semantic.forget_judgments(db, prompt_id=prompt.id)
    change = Change(
        dataset_id=prompt.dataset_id,
        prompt_id=prompt.id,
        check_key=check.value,
        action=action.value,
        before=before,
        after=after,
        run_id=run_id,
        event_id=event_id,
    )
    db.add(change)
    prompt.row_version += 1
    return change


def _changed_text(
    prompt: Prompt, answer: Response | None, *, action: Action, written: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """가리기 · 자르기 · 다시 쓰기 뒤의 (맥락, 응답 글)."""
    content = answer.content if answer else None
    if action == Action.REWRITE:
        return prompt.messages, written if written is not None else content
    if action == Action.MASK:
        messages = [{**m, "content": rules.mask_pii(m["content"])} for m in prompt.messages]
        return messages, rules.mask_pii(content) if content is not None else None
    # 루프는 앞 턴의 응답(맥락)에도 있을 수 있다. 응답마다 자르고, 루프가 없는 글은 그대로 둔다.
    messages = [
        {**m, "content": rules.cut_loop(m["content"])} if m["role"] == Role.ASSISTANT else m
        for m in prompt.messages
    ]
    return messages, rules.cut_loop(content) if content is not None else None


async def _set_text(
    db: AsyncSession,
    prompt: Prompt,
    answer: Response | None,
    *,
    messages: list[dict[str, Any]],
    content: str | None,
) -> None:
    """지시 맥락 · 주 응답 글을 바꾸고 해시 · 규칙 검사를 다시 잰다(응답을 지웠으면 answer는 None)."""
    # JSONB 칼럼은 안의 값을 고쳐도 SQLAlchemy가 모르므로 새 목록으로 바꿔 넣는다.
    prompt.messages = [dict(message) for message in messages]
    prompt.text_hash = importing.context_hash(prompt.messages)
    if answer is not None and content is not None:
        answer.content = content
    conversation = importing.conversation(
        prompt.messages,
        content if answer is not None else None,
        answer.tool_calls if answer is not None else None,
    )
    prompt.marks = await lengths.measure_marks(
        db, dataset_id=prompt.dataset_id, messages=conversation, tools=prompt.tools
    )
    prompt.open_checks = generation_service.open_checks(prompt.marks, prompt.kept)
    if answer is not None:
        await _remeasure_pairs(db, answer)


async def _remeasure_pairs(db: AsyncSession, answer: Response) -> None:
    """응답 글이 바뀌면 그 응답이 든 쌍의 표시(같은 쌍)를 다시 잰다."""
    pairs = (
        await db.scalars(
            select(Pair).where(or_(Pair.chosen_id == answer.id, Pair.rejected_id == answer.id))
        )
    ).all()
    for pair in pairs:
        chosen = answer if pair.chosen_id == answer.id else await db.get(Response, pair.chosen_id)
        rejected = (
            answer if pair.rejected_id == answer.id else await db.get(Response, pair.rejected_id)
        )
        if chosen is None or rejected is None:
            continue
        pair.marks = rules.measure_pair(
            chosen.content,
            rejected.content,
            chosen_score=chosen.score,
            rejected_score=rejected.score,
        )


async def _main_id(db: AsyncSession, *, prompt_id: int) -> int | None:
    """지시의 주 응답 번호. 없으면 None."""
    return await db.scalar(
        select(Response.id).where(Response.prompt_id == prompt_id, Response.is_main)
    )


async def _flip(db: AsyncSession, pair: Pair) -> None:
    """쌍의 고른 응답 ↔ 버린 응답을 바꾼다(Jev 점수도). 주 응답이 고른 응답이었으면 새 고른 응답으로 옮긴다."""
    old_chosen, old_rejected = pair.chosen_id, pair.rejected_id
    pair.chosen_id, pair.rejected_id = old_rejected, old_chosen
    pair.judge_chosen, pair.judge_rejected = pair.judge_rejected, pair.judge_chosen
    main_id = await _main_id(db, prompt_id=pair.prompt_id)
    if main_id == old_chosen:
        # 주 응답은 지시마다 하나(부분 UNIQUE)라서 옛 주 응답을 먼저 내린다.
        await db.execute(update(Response).where(Response.id == old_chosen).values(is_main=False))
        await db.flush()
        await db.execute(update(Response).where(Response.id == old_rejected).values(is_main=True))
    await db.flush()


async def _items(db: AsyncSession, *, dataset_id: int, ids: list[int]) -> list[ConversationItem]:
    """번호들의 대화를 그 순서대로 (지시 · 응답들 · 쌍들 · 해시 · 뜻 · 대조로 센 문제 · Jev 판정)."""
    if not ids:
        return []
    prompts = {
        p.id: p
        for p in (
            await db.scalars(
                select(Prompt).where(Prompt.id.in_(ids)).execution_options(populate_existing=True)
            )
        ).all()
    }
    answers: dict[int, list[Response]] = {}
    for answer in (
        await db.scalars(
            select(Response)
            .where(Response.prompt_id.in_(ids))
            .order_by(Response.is_main.desc(), Response.id)
            .execution_options(populate_existing=True)
        )
    ).all():
        answers.setdefault(answer.prompt_id, []).append(answer)
    pairs: dict[int, list[Pair]] = {}
    for pair in (
        await db.scalars(
            select(Pair)
            .where(Pair.prompt_id.in_(ids))
            .order_by(Pair.id)
            .execution_options(populate_existing=True)
        )
    ).all():
        pairs.setdefault(pair.prompt_id, []).append(pair)
    contaminations = {
        row.prompt_id: row
        for row in (
            await db.scalars(select(Contamination).where(Contamination.prompt_id.in_(ids)))
        ).all()
    }
    duplicate_ids = await _among(db, problems.duplicate_extras(dataset_id), ids)
    near_ids: set[int] = set()
    mismatch: set[int] = set()
    unsafe: set[int] = set()
    judgments: dict[int, Judgment] = {}
    analysis = await problems.done_analysis(db, dataset_id=dataset_id)
    if analysis is not None:
        near_ids = await _among(db, problems.near_extras(dataset_id, analysis.id), ids)
        mismatch = await _among(db, problems.mismatch_ids(dataset_id, analysis.id), ids)
        unsafe = await _among(db, problems.unsafe_ids(dataset_id, analysis.id), ids)
        judgments = await semantic.judgments_for(db, dataset_id=dataset_id, prompt_ids=ids)
    items = []
    for i in ids:
        if i not in prompts:
            continue
        responses = answers.get(i, [])
        main = responses[0] if responses and responses[0].is_main else None
        items.append(
            ConversationItem(
                prompt=prompts[i],
                answer=main,
                answers=responses,
                pairs=pairs.get(i, []),
                is_duplicate=i in duplicate_ids,
                is_near_duplicate=i in near_ids,
                is_mismatch=i in mismatch,
                is_unsafe=i in unsafe,
                contamination=contaminations.get(i),
                judgment=judgments.get(i),
            )
        )
    return items


async def _among(db: AsyncSession, query: Any, ids: list[int]) -> set[int]:
    """번호 쿼리의 결과 가운데 ids에 든 것."""
    found = query.subquery()
    return set((await db.scalars(select(found.c.id).where(found.c.id.in_(ids)))).all())


def _messages(item: ConversationItem) -> list[dict[str, Any]]:
    """대화 전체 메시지 (맥락 + 주 응답, 응답이 없으면 맥락만)."""
    answer = item.answer
    return importing.conversation(
        item.prompt.messages,
        answer.content if answer is not None else None,
        answer.tool_calls if answer is not None else None,
    )


def _message_read(message: dict[str, Any]) -> MessageRead:
    return MessageRead(
        role=message["role"],
        content=message["content"],
        tool_calls=message.get("tool_calls") or None,
        tool_call_id=message.get("tool_call_id"),
    )


def _pair_read(pair: Pair) -> PairRead:
    kept = [CheckKey(key) for key in pair.kept if key in CheckKey.__members__.values()]
    return PairRead(
        id=pair.id,
        prompt_id=pair.prompt_id,
        chosen_id=pair.chosen_id,
        rejected_id=pair.rejected_id,
        source=pair.source,
        judge_chosen=pair.judge_chosen,
        judge_rejected=pair.judge_rejected,
        problems=[] if pair.trashed_at is not None else problems.pair_problems(pair),
        kept=kept,
        trashed=pair.trashed_at is not None,
    )


def _jev_read(judgment: Judgment | None) -> JevRead | None:
    if judgment is None:
        return None
    return JevRead(
        follows=judgment.follows,
        refusal_ok=judgment.refusal_ok,
        similarity=judgment.similarity,
        unsafe=judgment.unsafe,
        looks_fine=judging.looks_fine(judgment.follows),
    )


def _whole(key: CheckKey, snippet: str) -> ProblemRead:
    """대화 전체에 걸친 문제 한 줄 (자리 없음)."""
    return ProblemRead(key=key, turn=None, start=None, end=None, snippet=snippet)


def _problem_read(
    key: CheckKey,
    mark: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> ProblemRead:
    turn, start, end = mark.get("turn"), mark.get("start"), mark.get("end")
    if key == CheckKey.STRUCTURE:
        snippet = "역할 순서 " + " → ".join(m["role"] for m in messages)
    elif key == CheckKey.TOOL_CALL and turn is not None and turn < len(messages):
        snippet = _tool_snippet(messages[turn], tools)
    elif turn is not None and start is not None and end is not None and turn < len(messages):
        snippet = messages[turn]["content"][start:end]
    else:
        snippet = SNIPPET_WITHOUT_SPAN.get(key, "")
    if len(snippet) > SNIPPET_LENGTH:
        snippet = snippet[:SNIPPET_LENGTH] + "…"
    return ProblemRead(key=key, turn=turn, start=start, end=end, snippet=snippet)


def _tool_snippet(message: dict[str, Any], tools: list[dict[str, Any]] | None) -> str:
    """도구 호출 문제의 낱말: 호출이면 무엇이 틀렸는지, 결과면 가리키는 호출이 없음."""
    if message["role"] == Role.TOOL:
        return TOOL_RESULT_PROBLEM
    for call in message.get("tool_calls") or []:
        if found := rules.tool_problem_text(call, tools):
            name = (call.get("function") or call).get("name") or "?"
            return f"{name} · {found}"
    return TOOL_OPEN_CALL_PROBLEM


def _escape_like(text: str) -> str:
    """LIKE 패턴의 특수 글자(%, _, 탈출 글자)를 글자 그대로 찾게 한다."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
