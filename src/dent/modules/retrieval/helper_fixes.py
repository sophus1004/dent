"""검색 도우미가 끝난 뒤: 남은 것(지금 진단으로 센 줄)과 AI로 고치기(사람이 고른 검사마다 규칙 · Jev · LLM).

남은 것은 저장하지 않는다. 진단의 검사 가운데 통과하지 못한 것을 셋으로 나눠 그때마다 센다
(사람이 직접 고치면 줄이 줄거나 사라진다):
  ai       AI로 고칠 수 있음. 규칙으로 되는 것(학습에서 빼기 · 합치기 · 나누기 …)과
           Jev · LLM으로 판정할 것(판정 충돌 · 정답 없음 · 빠진 정답 · 정답 의심 · 거짓 오답 · 문맥 의존)
  direct   직접 권장. 뜻 판단(반복 구간). 고를 수는 있다
  blocked  AI로 못 고침. 할 일(문서 더하기 · 오답 다시 찾기)만
고치기는 규칙 → Jev → LLM 순서다. LLM은 규칙 · Jev로 정하지 못한 것만 묶음(JUDGE_BATCH)으로 본다.
애매하면: 판정 충돌은 판정을 떼고(모름), 정답 없음은 질의를 학습에서 빼고, 나머지는 그대로 둔다(유지).
바꾼 것은 모두 변경 기록(helper_changes)에 남아 카드 · 실행 단위로 되돌린다. 반복 구간 결정만 반복 구간 창에서 바꾼다.
"""

import itertools
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dent.modules.retrieval import edits, generation, helper_changes, judging, problems, repeats
from dent.modules.retrieval import overview as overview_service
from dent.modules.retrieval import service as retrieval_service
from dent.modules.retrieval.models import (
    POSITIVE_MIN_GRADE,
    Document,
    HelperField,
    HelperTarget,
    Judgment,
    Query,
    Ranking,
    Repeat,
    Suggestion,
    SuggestionDecision,
    SuggestionKind,
)
from dent.modules.retrieval.service import (
    DOCUMENT_ACTIVE,
    DOCUMENT_INPUT,
    QUERY_ACTIVE,
    QUERY_INCLUDED,
)
from dent.system import connections
from dent.system import helper as helper_service
from dent.system.agent import LEFT_AI, LEFT_BLOCKED, LEFT_DIRECT, Agent, LeftItem
from dent.system.exceptions import ExternalServiceError
from dent.system.models import Connection, ConnectionRole, HelperEventKind
from dent.system.text import make_text_hash

# 검사 하나를 고칠 때 보는 최대 수 (비용 어림도 이 수로 끊는다)
FIX_MAX_ITEMS = 300

# LLM에 한 번에 보이는 항목 수 · 그 묶음의 지시 몫(토큰) · 항목 하나의 글자 수
JUDGE_BATCH = 12
JUDGE_PROMPT_TOKENS = 400
QUERY_CHARS = 300
DOCUMENT_CHARS = 900

# 정답 없는 질의마다 기준 검색 상위 몇 문서를 보나
FIND_POSITIVE_TOP = 3

# LLM 카드에 보일 줄 수
CARD_ROWS = 8

# 판정 답과 카드의 낱말
YES = "yes"
NO = "no"
UNSURE = "unsure"
ANSWER_WORDS = {YES: "예", NO: "아니오", UNSURE: "모름"}

JUDGE_PROMPT = (
    "너는 검색 학습 데이터의 판정자다. 항목마다 문서가 질의의 답을 담았는지 본다. "
    "분명히 담았으면 yes, 분명히 아니면 no, 문서만으로 가를 수 없으면 unsure. "
    '답은 JSON 하나만: {"items": [{"id": 번호, "answer": "yes" | "no" | "unsure"}]}'
)
REWRITE_PROMPT = (
    "너는 검색 질의를 고쳐 쓴다. 질의가 ‘이 글’ · ‘그는’처럼 문맥에 기대면, 문서의 고유 이름을 넣어 "
    "혼자 읽어도 뜻이 분명한 한 문장으로 고친다. 고칠 수 없으면 빈 글. "
    '답은 JSON 하나만: {"items": [{"id": 번호, "text": "고친 질의 또는 빈 글"}]}'
)


@dataclass(frozen=True)
class FixSpec:
    """남은 것 한 줄의 묶음과 AI로 고치는 방법."""

    # 묶음 (ai · direct · blocked)
    group: str

    # AI로 고치면 하는 일
    how: str = ""

    # 항목 하나에 드는 LLM 토큰 · Jev 호출 (어림)
    tokens_per_item: int = 0
    jev_per_item: int = 0

    # AI로 못 고칠 때 할 일
    action: str = ""


# 검사 열쇠 → 묶음 · 방법. 없는 검사는 BLOCKED_SPEC.
FIX_SPECS = {
    "broken": FixSpec(LEFT_AI, "깨진 글자 정리 · 규칙"),
    "duplicate_document": FixSpec(LEFT_AI, "학습 글이 같은 문서 합치기 · 규칙"),
    "long": FixSpec(LEFT_AI, "문서 나누기 · 규칙"),
    "pick": FixSpec(LEFT_AI, "질의 안 만듦 표시 · 규칙"),
    "short_long": FixSpec(LEFT_AI, "학습에서 빼기 · 규칙"),
    "duplicate_query": FixSpec(LEFT_AI, "여분 빼기 · 규칙"),
    "same_negative": FixSpec(LEFT_AI, "오답 떼기 · 규칙"),
    "conflict": FixSpec(LEFT_AI, "Jev + LLM · 엇갈리거나 애매하면 판정 떼기", 220, 1),
    "no_positive": FixSpec(
        LEFT_AI, "Jev · LLM이 기준 검색 상위 3 문서 봄 · 못 찾으면 빼기", 600, FIND_POSITIVE_TOP
    ),
    "missing": FixSpec(LEFT_AI, "Jev 확인은 받기 · 나머지 LLM 판정 · 예면 정답으로", 220),
    "suspect": FixSpec(LEFT_AI, "Jev 확인은 받기 · 나머지 LLM 판정 · 아니면 정답 떼기", 220),
    "false_negative": FixSpec(LEFT_AI, "Jev 기준대로 · 나머지 LLM 판정 · 예면 정답으로", 220),
    "context": FixSpec(LEFT_AI, "LLM 고쳐 쓰기 · 안 되면 빼기", 200),
    "no_queries": FixSpec(LEFT_AI, "질의 만들기 · 거르기 다섯"),
    "repeat": FixSpec(LEFT_DIRECT, "제안대로 떼기 · 남김 확정"),
    "no_negative": FixSpec(LEFT_BLOCKED, action="문서 더하기 · 오답 다시 찾기"),
    "easy_negative": FixSpec(LEFT_BLOCKED, action="오답 다시 찾기"),
    "topic_skew": FixSpec(LEFT_BLOCKED, action="문서 더하기"),
    "easy_pair": FixSpec(LEFT_BLOCKED, action="질의 다시 만들기"),
}
BLOCKED_SPEC = FixSpec(LEFT_BLOCKED, action="진단에서 보기")

# 고치는 순서: 흐름(문서 → 질의 → 정답 → 오답)대로 해야 앞 고치기가 뒤를 흐리지 않는다.
FIX_ORDER = (
    "broken",
    "duplicate_document",
    "long",
    "pick",
    "repeat",
    "no_queries",
    "short_long",
    "duplicate_query",
    "context",
    "conflict",
    "no_positive",
    "missing",
    "suspect",
    "false_negative",
    "same_negative",
)

# 뜻 분석(근접 중복 · 기준 검색 순위 · 제안)이 있어야 고칠 수 있는 검사
NEEDS_ANALYSIS = frozenset({"no_positive", "missing", "suspect", "false_negative"})


# ---------- 남은 것 ----------


async def left_items(db: AsyncSession, *, dataset_id: int) -> list[LeftItem]:
    """지금 진단에서 통과하지 못한 검사마다 한 줄 (묶음 · 방법 · 어림 비용). 데이터셋이 없으면 NotFoundError."""
    overview = await overview_service.get_overview(db, dataset_id=dataset_id)
    has_jev = await connections.get_connection(db, role=ConnectionRole.JEV) is not None
    items = []
    for check in overview.checks:
        if check.grade == "good":
            continue
        spec = FIX_SPECS.get(check.key, BLOCKED_SPEC)
        count = min(max(check.view_count, 1), FIX_MAX_ITEMS)
        tokens = count * spec.tokens_per_item
        if spec.tokens_per_item:
            tokens += math.ceil(count / JUDGE_BATCH) * JUDGE_PROMPT_TOKENS
        if check.key == "no_queries":
            tokens = (await generation.plan(db, dataset_id=dataset_id)).tokens
        items.append(
            LeftItem(
                key=check.key,
                name=overview_service.CHECK_NAMES.get(check.key, check.key),
                value=check.value,
                unit=check.unit,
                sub=check.sub,
                grade=check.grade,
                group=spec.group,
                how=spec.how,
                tokens=tokens,
                jev=count * spec.jev_per_item if has_jev else 0,
                action=spec.action,
                view_target=check.view_target or "",
                view_problem=check.view_problem or "",
            )
        )
    return items


# ---------- 고치기 ----------


@dataclass
class Fixing:
    """AI로 고치기 한 번에 필요한 것: 도우미(LLM · 말 · 사건) · 연결 · 가짜 서버(테스트)."""

    # 도우미 실행 (LLM 묻기 · 말 · 사건 · 지금 단계 번호)
    agent: Agent

    # LLM · Jev 연결 (Jev가 없으면 None)
    llm_connection: Connection
    jev_connection: Connection | None

    # 가짜 서버 (테스트만)
    transport: httpx.AsyncBaseTransport | None
    jev_transport: httpx.AsyncBaseTransport | None
    embedding_transport: httpx.AsyncBaseTransport | None

    # 처리하는 작업 번호 (질의 만들기 진행률)
    job_id: int | None

    @property
    def db(self) -> AsyncSession:
        return self.agent.db

    @property
    def dataset_id(self) -> int:
        return self.agent.dataset_id

    async def record(
        self, log: edits.ChangeLog, *, action: str, tool: str, samples: Sequence[str] = ()
    ) -> int:
        """바꾼 카드와 변경 기록을 적는다(지금 단계). 바꾼 줄 수."""
        await self.db.flush()
        return await helper_changes.record(
            self.db,
            run_id=self.agent.run_id,
            step=self.agent.step_no,
            dataset_id=self.dataset_id,
            log=log,
            action=action,
            tool=tool,
            samples=samples,
        )

    async def ask_jev(self, query: str, document: str) -> float | None:
        """Jev에 '예' 확률을 묻는다. Jev가 없거나 실패하면 None(실패는 한 번 알리고 Jev 없이 이어 간다)."""
        if self.jev_connection is None:
            return None
        try:
            probability = await judging.ask_contains(
                self.jev_connection, query=query, document=document, transport=self.jev_transport
            )
        except ExternalServiceError as error:
            await self.agent.notice(error.message, tone="warn")
            self.jev_connection = None
            return None
        await helper_service.add_usage(self.db, run_id=self.agent.run_id, jev_calls=1)
        return probability

    async def judge(
        self, items: list[tuple[int, str, str]], *, prompt: str = JUDGE_PROMPT
    ) -> dict[int, str]:
        """(번호, 질의, 문서)마다 LLM의 yes · no · unsure. 답이 없는 번호는 빠진다. LLM 카드를 적는다."""
        return await judge_pairs(self.agent, items, prompt=prompt)


async def judge_pairs(
    agent: Agent, items: list[tuple[int, str, str]], *, prompt: str = JUDGE_PROMPT
) -> dict[int, str]:
    """(번호, 질의, 문서)마다 LLM의 yes · no · unsure. 답이 없는 번호는 빠진다. LLM 카드를 적는다."""
    answers: dict[int, str] = {}
    for batch in itertools.batched(items, JUDGE_BATCH):
        user = "\n\n".join(
            f"[{item_id}] 질의: {query[:QUERY_CHARS]}\n문서: {document[:DOCUMENT_CHARS]}"
            for item_id, query, document in batch
        )
        data = await agent.ask_json(system=prompt, user=user)
        for row in _items(data):
            answer = str(row.get("answer") or "")
            item_id = _int(row.get("id"))
            if answer in (YES, NO, UNSURE) and item_id is not None:
                answers[item_id] = answer
    by_id = {item_id: (query, document) for item_id, query, document in items}
    await agent.emit(
        HelperEventKind.LLM,
        {
            "meta": f"LLM 판정 {len(answers)} / {len(items)}",
            "rows": [
                {
                    "text": by_id[item_id][0],
                    "from": by_id[item_id][1][:80],
                    "to": ANSWER_WORDS[answer],
                    "why": "",
                }
                for item_id, answer in list(answers.items())[:CARD_ROWS]
                if item_id in by_id
            ],
        },
    )
    return answers


def jev_answer(probability: float | None) -> str | None:
    """Jev 확률을 확신한 답으로: 예 ≥ 기준이면 yes, 예 ≤ 1 - 기준이면 no, 그 밖은 None."""
    if probability is None:
        return None
    if probability >= judging.CONFIRM_PROBABILITY:
        return YES
    if probability <= 1 - judging.CONFIRM_PROBABILITY:
        return NO
    return None


FixFunction = Callable[[Fixing], Awaitable[dict[str, Any]]]


def _items(data: Any) -> list[dict[str, Any]]:
    """LLM 답 {"items": [...]}의 줄들. 모양이 틀리면 빈 목록."""
    items = data.get("items") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _rule(
    fixing: Fixing, *, action: str, run: Callable[[edits.ChangeLog], Awaitable[int]]
) -> dict[str, Any]:
    """규칙 하나: 바꾸고 카드를 적는다."""
    log = edits.ChangeLog()
    await run(log)
    changed = await fixing.record(log, action=action, tool="규칙")
    return {"changed": changed, "rule": 1}


async def _analysis_id(fixing: Fixing) -> int | None:
    analysis = await retrieval_service.latest_done_analysis(fixing.db, dataset_id=fixing.dataset_id)
    return analysis.id if analysis else None


async def _exclude_problem(fixing: Fixing, problem: str) -> dict[str, Any]:
    settings = await retrieval_service.find_settings(fixing.db, dataset_id=fixing.dataset_id)
    condition = problems.query_problem(
        problem, settings=settings, analysis_id=await _analysis_id(fixing)
    )
    return await _rule(
        fixing,
        action="학습에서 빼기",
        run=lambda log: edits.exclude_queries(
            fixing.db, dataset_id=fixing.dataset_id, condition=condition, log=log
        ),
    )


async def fix_broken(fixing: Fixing) -> dict[str, Any]:
    return await _rule(
        fixing,
        action="글자 정리",
        run=lambda log: edits.clean_documents(fixing.db, dataset_id=fixing.dataset_id, log=log),
    )


async def fix_duplicate_document(fixing: Fixing) -> dict[str, Any]:
    return await _rule(
        fixing,
        action="같은 문서 합치기",
        run=lambda log: edits.merge_duplicate_documents(
            fixing.db, dataset_id=fixing.dataset_id, log=log
        ),
    )


async def fix_long(fixing: Fixing) -> dict[str, Any]:
    async def split(log: edits.ChangeLog) -> int:
        _, pieces = await edits.split_long_documents(
            fixing.db, dataset_id=fixing.dataset_id, log=log
        )
        return pieces

    return await _rule(fixing, action="문서 나누기", run=split)


async def fix_pick(fixing: Fixing) -> dict[str, Any]:
    return await _rule(
        fixing,
        action="질의 안 만듦 표시",
        run=lambda log: edits.mark_skip_generation(
            fixing.db, dataset_id=fixing.dataset_id, log=log
        ),
    )


async def fix_short_long(fixing: Fixing) -> dict[str, Any]:
    return await _exclude_problem(fixing, "short_long")


async def fix_duplicate_query(fixing: Fixing) -> dict[str, Any]:
    return await _exclude_problem(fixing, "duplicate")


async def fix_same_negative(fixing: Fixing) -> dict[str, Any]:
    return await _rule(
        fixing,
        action="오답 떼기",
        run=lambda log: edits.remove_same_negatives(
            fixing.db, dataset_id=fixing.dataset_id, log=log
        ),
    )


async def fix_conflict(fixing: Fixing) -> dict[str, Any]:
    """판정 충돌: Jev와 LLM이 함께 본다. 같은 답이거나 Jev가 애매하고 LLM이 답하면 그대로, 엇갈리거나 LLM도
    애매하면 판정을 뗀다(모름). Jev 하나만으로 정답을 바꾸지 않는다(검증: 답이 분명한 정답에 Jev가 예 0.16을 줘
    참 정답이 오답이 됐다)."""
    rows = (
        await fixing.db.execute(
            select(Judgment, Query.text, DOCUMENT_INPUT)
            .join(Query, Query.id == Judgment.query_id)
            .join(Document, Document.id == Judgment.document_id)
            .where(
                Judgment.dataset_id == fixing.dataset_id,
                Judgment.conflict.is_(True),
                QUERY_ACTIVE,
                DOCUMENT_ACTIVE,
            )
            .limit(FIX_MAX_ITEMS)
        )
    ).tuples()
    pending: list[tuple[int, str, str]] = []
    judgments: dict[int, tuple[Judgment, float | None]] = {}
    log = edits.ChangeLog()
    jev_calls = 0
    for index, (judgment, query_text, document_text) in enumerate(rows):
        probability = await fixing.ask_jev(query_text, document_text)
        jev_calls += probability is not None
        judgments[index] = (judgment, probability)
        pending.append((index, query_text, document_text))
    answers = await fixing.judge(pending) if pending else {}
    for index, _query, _document in pending:
        judgment, probability = judgments[index]
        answer = answers.get(index, UNSURE)
        jev = jev_answer(probability)
        is_split = jev is not None and answer != UNSURE and answer != jev
        if answer == UNSURE or is_split:
            await edits.remove_judgment(fixing.db, judgment, log=log)
        else:
            grade = POSITIVE_MIN_GRADE if answer == YES else 0
            await _set(fixing, judgment, grade, probability if jev else None, log)
    changed = await fixing.record(log, action="판정 바꾸기", tool="Jev + LLM")
    return {"changed": changed, "jev": jev_calls, "llm": len(pending)}


async def _set(
    fixing: Fixing, judgment: Judgment, grade: int, score: float | None, log: edits.ChangeLog
) -> None:
    await edits.set_grade(
        fixing.db,
        dataset_id=fixing.dataset_id,
        query_id=judgment.query_id,
        document_id=judgment.document_id,
        grade=grade,
        teacher_score=score,
        log=log,
    )


async def fix_no_positive(fixing: Fixing) -> dict[str, Any]:
    """정답 없는 질의: 기준 검색 상위 3 문서를 Jev · LLM에 묻고, LLM이 예이고 Jev가 아니라고 확신하지 않는
    첫 문서를 정답으로. 못 찾으면 학습에서 뺀다."""
    analysis_id = await _analysis_id(fixing)
    queries = list(
        (
            await fixing.db.execute(
                select(Query.id, Query.text)
                .where(
                    Query.dataset_id == fixing.dataset_id,
                    QUERY_INCLUDED,
                    ~problems.positive_exists(),
                )
                .order_by(Query.id)
                .limit(FIX_MAX_ITEMS)
            )
        ).tuples()
    )
    log = edits.ChangeLog()
    found: set[int] = set()
    pending: list[tuple[int, str, str]] = []
    candidates: dict[int, tuple[int, int]] = {}
    for query_id, query_text in queries:
        ranked = []
        if analysis_id is not None:
            ranked = list(
                (
                    await fixing.db.execute(
                        select(Document.id, DOCUMENT_INPUT)
                        .join(Ranking, Ranking.document_id == Document.id)
                        .where(
                            Ranking.analysis_id == analysis_id,
                            Ranking.query_id == query_id,
                            DOCUMENT_ACTIVE,
                        )
                        .order_by(Ranking.rank)
                        .limit(FIND_POSITIVE_TOP)
                    )
                ).tuples()
            )
        for document_id, document_text in ranked:
            key = len(candidates)
            candidates[key] = (
                query_id,
                document_id,
                await fixing.ask_jev(query_text, document_text),
            )
            pending.append((key, query_text, document_text))
    # 정답을 새로 붙이는 판단은 Jev 하나로 하지 않는다: LLM이 예이고 Jev가 아니라고 확신하지 않는 첫 문서.
    answers = await fixing.judge(pending) if pending else {}
    for key, (query_id, document_id, probability) in candidates.items():
        is_agreed = answers.get(key) == YES and jev_answer(probability) != NO
        if query_id not in found and is_agreed:
            await _positive(fixing, query_id, document_id, probability, log)
            found.add(query_id)
    changed = await fixing.record(log, action="정답 붙이기", tool="Jev → LLM")
    missing = [query_id for query_id, _ in queries if query_id not in found]
    if missing:
        exclude_log = edits.ChangeLog()
        await edits.exclude_queries(
            fixing.db,
            dataset_id=fixing.dataset_id,
            condition=Query.id.in_(missing),
            log=exclude_log,
        )
        changed += await fixing.record(exclude_log, action="학습에서 빼기", tool="정답 못 찾음")
    return {"changed": changed, "llm": len(pending)}


async def _positive(
    fixing: Fixing, query_id: int, document_id: int, score: float | None, log: edits.ChangeLog
) -> None:
    await edits.set_grade(
        fixing.db,
        dataset_id=fixing.dataset_id,
        query_id=query_id,
        document_id=document_id,
        grade=POSITIVE_MIN_GRADE,
        teacher_score=score,
        log=log,
    )


async def _suggestions(
    fixing: Fixing, analysis_id: int, kind: SuggestionKind
) -> list[tuple[Suggestion, str, str]]:
    """확인되지 않은 대기 제안과 그 질의 · 문서 글."""
    return list(
        (
            await fixing.db.execute(
                select(Suggestion, Query.text, DOCUMENT_INPUT)
                .join(Query, Query.id == Suggestion.query_id)
                .join(Document, Document.id == Suggestion.document_id)
                .where(
                    Suggestion.analysis_id == analysis_id,
                    Suggestion.kind == kind.value,
                    Suggestion.decision.is_(None),
                )
                .order_by(Suggestion.id)
                .limit(FIX_MAX_ITEMS)
            )
        ).tuples()
    )


async def _fix_suggestions(
    fixing: Fixing, kind: SuggestionKind, *, action: str, accept_on: str
) -> dict[str, Any]:
    """제안: Jev가 확인한 것은 받고, 나머지는 LLM이 본다. accept_on이면 받고, 아니면 유지(다시 묻지 않음)."""
    analysis_id = await _analysis_id(fixing)
    if analysis_id is None:
        return {}
    log = edits.ChangeLog()
    await edits.accept_suggestions(
        fixing.db,
        dataset_id=fixing.dataset_id,
        analysis_id=analysis_id,
        kind=kind,
        confirmed_only=True,
        log=log,
    )
    rows = await _suggestions(fixing, analysis_id, kind)
    answers = await fixing.judge(
        [(suggestion.id, query, document) for suggestion, query, document in rows]
    )
    now = datetime.now(UTC)
    for suggestion, _query, _document in rows:
        if answers.get(suggestion.id) == accept_on:
            if kind == SuggestionKind.SUSPECT_POSITIVE:
                judgment = await fixing.db.get(
                    Judgment, (suggestion.query_id, suggestion.document_id)
                )
                if judgment is not None:
                    await edits.remove_judgment(fixing.db, judgment, log=log)
            else:
                await _positive(fixing, suggestion.query_id, suggestion.document_id, None, log)
            suggestion.decision = SuggestionDecision.ACCEPTED.value
        else:
            suggestion.decision = SuggestionDecision.KEPT.value
        suggestion.decided_at = now
    changed = await fixing.record(log, action=action, tool="Jev → LLM")
    return {"changed": changed, "llm": len(rows)}


async def fix_missing(fixing: Fixing) -> dict[str, Any]:
    return await _fix_suggestions(
        fixing, SuggestionKind.MISSING_POSITIVE, action="정답 붙이기", accept_on=YES
    )


async def fix_suspect(fixing: Fixing) -> dict[str, Any]:
    return await _fix_suggestions(
        fixing, SuggestionKind.SUSPECT_POSITIVE, action="정답 떼기", accept_on=NO
    )


async def fix_false_negative(fixing: Fixing) -> dict[str, Any]:
    return await _fix_suggestions(
        fixing, SuggestionKind.FALSE_NEGATIVE, action="정답 붙이기", accept_on=YES
    )


async def fix_context(fixing: Fixing) -> dict[str, Any]:
    """문맥 의존 질의: LLM이 정답 문서를 보고 고유 이름을 넣어 고쳐 쓴다. 못 고치면 학습에서 뺀다."""
    settings = await retrieval_service.find_settings(fixing.db, dataset_id=fixing.dataset_id)
    condition = problems.query_problem(
        "context", settings=settings, analysis_id=await _analysis_id(fixing)
    )
    rows = list(
        (
            await fixing.db.execute(
                select(Query)
                .where(Query.dataset_id == fixing.dataset_id, QUERY_INCLUDED, condition)
                .order_by(Query.id)
                .limit(FIX_MAX_ITEMS)
            )
        ).scalars()
    )
    log = edits.ChangeLog()
    failed: list[int] = []
    rewritten: list[str] = []
    for batch in itertools.batched(rows, JUDGE_BATCH):
        lines = []
        for query in batch:
            document = await fixing.db.scalar(
                select(DOCUMENT_INPUT)
                .join(Judgment, Judgment.document_id == Document.id)
                .where(Judgment.query_id == query.id, Judgment.grade >= POSITIVE_MIN_GRADE)
                .limit(1)
            )
            lines.append(
                f"[{query.id}] 질의: {query.text[:QUERY_CHARS]}\n문서: {(document or '')[:DOCUMENT_CHARS]}"
            )
        data = await fixing.agent.ask_json(system=REWRITE_PROMPT, user="\n\n".join(lines))
        texts = {_int(item.get("id")): str(item.get("text") or "").strip() for item in _items(data)}
        for query in batch:
            text = texts.get(query.id, "")
            if not text or text == query.text:
                failed.append(query.id)
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
            rewritten.append(text)
    changed = await fixing.record(log, action="질의 고치기", tool="LLM", samples=rewritten)
    if failed:
        exclude_log = edits.ChangeLog()
        await edits.exclude_queries(
            fixing.db,
            dataset_id=fixing.dataset_id,
            condition=Query.id.in_(failed),
            log=exclude_log,
        )
        changed += await fixing.record(exclude_log, action="학습에서 빼기", tool="고쳐 쓰기 못 함")
    return {"changed": changed, "llm": len(rows)}


async def fix_no_queries(fixing: Fixing) -> dict[str, Any]:
    """질의 없음: 도우미의 질의 만들기와 같은 거르기로 만든다(시험 없이)."""
    documents = await generation.target_documents(
        fixing.db, dataset_id=fixing.dataset_id, limit=generation.MAX_DOCUMENTS_PER_RUN
    )
    log = edits.ChangeLog()
    outcome = await generation.generate(
        fixing.db,
        dataset_id=fixing.dataset_id,
        documents=documents,
        llm_connection=fixing.llm_connection,
        log=log,
        transport=fixing.transport,
        embedding_transport=fixing.embedding_transport,
        jev_transport=fixing.jev_transport,
    )
    await helper_service.add_usage(
        fixing.db,
        run_id=fixing.agent.run_id,
        llm_tokens=outcome.tokens,
        jev_calls=outcome.jev_calls,
    )
    fixing.agent.tokens += outcome.tokens
    changed = await fixing.record(
        log,
        action="새 질의 더하기",
        tool=f"청크 {len(documents):,}",
        samples=[text for text, _source, passed, _reason in outcome.samples if passed],
    )
    return {"changed": changed, "llm": outcome.tokens, "jev": outcome.jev_calls}


async def fix_repeat(fixing: Fixing) -> dict[str, Any]:
    """반복 구간: 아직 안 고른 틀을 제안(규칙 · 도우미)대로 확정한다. 되돌리기는 반복 구간 창에서 결정을 바꾼다."""
    ids = list(
        await fixing.db.scalars(
            select(Repeat.id).where(
                Repeat.dataset_id == fixing.dataset_id, Repeat.decision.is_(None)
            )
        )
    )
    changed = await repeats.decide_repeats(
        fixing.db,
        dataset_id=fixing.dataset_id,
        repeat_ids=ids,
        decision=None,
        follow_suggestion=True,
    )
    await fixing.agent.notice(f"반복 구간 제안대로 {changed} · 바꾸기는 반복 구간 창에서")
    return {"changed": changed, "rule": 1}


# 검사 열쇠 → 고치는 함수
FIXERS: dict[str, FixFunction] = {
    "broken": fix_broken,
    "duplicate_document": fix_duplicate_document,
    "long": fix_long,
    "pick": fix_pick,
    "short_long": fix_short_long,
    "duplicate_query": fix_duplicate_query,
    "same_negative": fix_same_negative,
    "conflict": fix_conflict,
    "no_positive": fix_no_positive,
    "missing": fix_missing,
    "suspect": fix_suspect,
    "false_negative": fix_false_negative,
    "context": fix_context,
    "no_queries": fix_no_queries,
    "repeat": fix_repeat,
}


def ordered(keys: Sequence[str]) -> list[str]:
    """고른 검사를 고치는 순서로(흐름 순서, 모르는 것은 뒤에)."""
    rank = {key: index for index, key in enumerate(FIX_ORDER)}
    return sorted(dict.fromkeys(keys), key=lambda key: rank.get(key, len(rank)))
