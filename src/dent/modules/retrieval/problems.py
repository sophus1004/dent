"""검사마다 문제인 질의 · 문서를 고르는 SQL 조건. 진단(overview.py)의 수와 데이터 탭의 문제 거르기(records.py)가
같은 조건을 써서, 진단에서 '12건'을 누르면 데이터 탭에 같은 12건이 나온다.

뜻 분석이 만드는 문제(근접 중복 · 정답 의심 · 거짓 오답 · 빠진 정답 · 쉬운 오답)는 가장 최근에 다 만든 분석의
결과를 본다(analysis_id). 분석이 없으면 그 문제는 없는 것(false)이다.
문서의 깨진 글자 · 청크 고르기는 글을 넣거나 바꿀 때 재 둔 표시(marks)를, 반복 구간은 문서마다 든 반복 구간 표를 본다
(요청마다 글을 정규식으로 다시 읽지 않는다).
"""

from typing import Any

from sqlalchemy import ColumnElement, and_, exists, false, func, or_, select
from sqlalchemy.orm import aliased

from dent.modules.retrieval.models import (
    POSITIVE_MIN_GRADE,
    DatasetSettings,
    Document,
    DocumentRepeat,
    ItemKind,
    Judgment,
    JudgmentSource,
    NearDuplicate,
    Query,
    Ranking,
    Repeat,
    Suggestion,
    SuggestionKind,
)
from dent.modules.retrieval.service import (
    DOCUMENT_ACTIVE,
    IS_NEGATIVE,
    IS_POSITIVE,
    QUERY_ACTIVE,
)
from dent.modules.retrieval.text_rules import (
    EASY_PAIR_MIN_CHARS,
    EASY_PAIR_OVERLAP,
    LONG_QUERY_CHARS_PER_TOKEN,
    SHORT_QUERY_CHARS,
    SQL_CONTEXT_DEPENDENT,
)

# 질의 문제 이름 (데이터 탭의 문제 거르기 순서)
QUERY_PROBLEMS = (
    "no_positive",
    "short_long",
    "conflict",
    "duplicate",
    "context",
    "easy_pair",
    "suspect",
    "false_negative",
    "missing",
    "no_negative",
    "same_negative",
    "easy_negative",
)

# 문서 문제 이름
DOCUMENT_PROBLEMS = ("broken", "repeat", "long", "duplicate", "near", "pick")

# 원본 · 사람의 판정 출처. 이 판정이 있는 문서는 데이터를 만든 사람이 자리를 정한 것이라 질의를 만들지 않는다
# (원본 오답은 특정 질의 가까이에 일부러 둔 문서여서, 거기서 만든 질의는 그 질의와 겹치거나 거짓 오답을 만든다).
PEOPLE_JUDGMENT_SOURCES = (JudgmentSource.ORIGINAL.value, JudgmentSource.HUMAN.value)

# 문제 이름 → 제안 종류
SUGGESTION_BY_PROBLEM = {
    "suspect": SuggestionKind.SUSPECT_POSITIVE,
    "false_negative": SuggestionKind.FALSE_NEGATIVE,
    "missing": SuggestionKind.MISSING_POSITIVE,
}


def positive_exists() -> ColumnElement[bool]:
    """질의(Query)에 코퍼스 문서 정답이 하나라도 있는지."""
    return exists(
        select(1)
        .select_from(Judgment)
        .join(Document, Document.id == Judgment.document_id)
        .where(Judgment.query_id == Query.id, IS_POSITIVE, DOCUMENT_ACTIVE)
    )


def negative_count() -> ColumnElement[int]:
    """질의(Query)의 코퍼스 문서 오답 수 (스칼라 부분 질의)."""
    return (
        select(func.count())
        .select_from(Judgment)
        .join(Document, Document.id == Judgment.document_id)
        .where(Judgment.query_id == Query.id, IS_NEGATIVE, DOCUMENT_ACTIVE)
        .scalar_subquery()
    )


def query_problem(
    problem: str, *, settings: DatasetSettings | None, analysis_id: int | None
) -> ColumnElement[bool]:
    """질의 문제 하나의 조건. Query에 걸린다(부른 쪽이 dataset_id · 휴지통 조건을 더한다)."""
    if problem == "no_positive":
        return ~positive_exists()
    if problem == "short_long":
        max_tokens = settings.query_max_tokens if settings else 64
        return or_(
            func.char_length(Query.text) < SHORT_QUERY_CHARS,
            func.char_length(Query.text) > max_tokens * LONG_QUERY_CHARS_PER_TOKEN,
        )
    if problem == "conflict":
        return exists().where(Judgment.query_id == Query.id, Judgment.conflict.is_(True))
    if problem == "duplicate":
        other = aliased(Query)
        return exists().where(
            other.dataset_id == Query.dataset_id,
            other.text_hash == Query.text_hash,
            other.id < Query.id,
            *included_conditions(other),
        )
    if problem == "context":
        return Query.text.op("~")(SQL_CONTEXT_DEPENDENT)
    if problem == "easy_pair":
        compact_length = func.char_length(func.replace(Query.text, " ", ""))
        return and_(
            compact_length >= EASY_PAIR_MIN_CHARS,
            exists().where(
                Judgment.query_id == Query.id, IS_POSITIVE, Judgment.overlap >= EASY_PAIR_OVERLAP
            ),
        )
    if problem in SUGGESTION_BY_PROBLEM:
        if analysis_id is None:
            return false()
        return exists().where(
            Suggestion.analysis_id == analysis_id,
            Suggestion.kind == SUGGESTION_BY_PROBLEM[problem].value,
            Suggestion.query_id == Query.id,
            Suggestion.decision.is_(None),
        )
    if problem == "no_negative":
        wanted = settings.negatives if settings else 7
        return and_(positive_exists(), negative_count() < wanted)
    if problem == "same_negative":
        positive_doc = aliased(Document)
        positive_judgment = aliased(Judgment)
        # 정답과 같은 중복 묶음의 오답: 본문이 같거나, 뜻 분석이 있으면 근접 중복 쌍.
        is_same = positive_doc.text_hash == Document.text_hash
        if analysis_id is not None:
            # 오답 문서(Document)와 정답 문서(positive_doc)는 바깥 질의의 것이라 잇는다고 적어 준다.
            # 안 적으면 문서 표를 새로 읽어 '정답에 근접 중복이 하나라도 있는 질의'를 센다.
            is_same = or_(
                is_same,
                select(1)
                .select_from(NearDuplicate)
                .where(
                    NearDuplicate.analysis_id == analysis_id,
                    NearDuplicate.kind == ItemKind.DOCUMENT.value,
                    or_(
                        and_(
                            NearDuplicate.item_a == Document.id,
                            NearDuplicate.item_b == positive_doc.id,
                        ),
                        and_(
                            NearDuplicate.item_a == positive_doc.id,
                            NearDuplicate.item_b == Document.id,
                        ),
                    ),
                )
                .correlate(Document, positive_doc)
                .exists(),
            )
        # 두 겹 안쪽의 부분 질의라서 바깥 질의(Query)에 이어 붙이라고 적어 준다(안 적으면 모든 질의와 곱해진다).
        same_as_positive = (
            select(1)
            .select_from(positive_judgment)
            .join(positive_doc, positive_doc.id == positive_judgment.document_id)
            .where(
                positive_judgment.query_id == Query.id,
                positive_judgment.grade >= 1,
                positive_doc.id != Document.id,
                is_same,
            )
            .correlate(Query, Document)
        )
        return exists(
            select(1)
            .select_from(Judgment)
            .join(Document, Document.id == Judgment.document_id)
            .where(
                Judgment.query_id == Query.id,
                IS_NEGATIVE,
                DOCUMENT_ACTIVE,
                exists(same_as_positive),
            )
        )
    if problem == "easy_negative":
        if analysis_id is None or settings is None:
            return false()
        return exists(
            select(1)
            .select_from(Judgment)
            .join(
                Ranking,
                and_(
                    Ranking.analysis_id == analysis_id,
                    Ranking.query_id == Judgment.query_id,
                    Ranking.document_id == Judgment.document_id,
                ),
            )
            .where(Judgment.query_id == Query.id, IS_NEGATIVE, Ranking.rank > settings.mine_rank_to)
        )
    return false()


def included_conditions(query: Any) -> list[ColumnElement[bool]]:
    """다른 질의(aliased)가 학습에 쓰이는지: 휴지통 밖 · 뺀 이유 없음."""
    return [query.trashed_at.is_(None), query.exclude_reason.is_(None)]


def document_problem(
    problem: str,
    *,
    settings: DatasetSettings | None,
    analysis_id: int | None,
) -> ColumnElement[bool]:
    """문서 문제 하나의 조건. Document에 걸린다."""
    if problem == "broken":
        return Document.marks.has_key("broken")
    if problem == "repeat":
        # 아직 떼기 · 남김을 고르지 않은 반복 구간이 든 문서
        return exists(
            select(1)
            .select_from(DocumentRepeat)
            .join(Repeat, Repeat.id == DocumentRepeat.repeat_id)
            .where(DocumentRepeat.document_id == Document.id, Repeat.decision.is_(None))
        )
    if problem == "long":
        max_tokens = settings.doc_max_tokens if settings else 512
        return Document.token_count > max_tokens
    if problem == "duplicate":
        other = aliased(Document)
        return exists().where(
            other.dataset_id == Document.dataset_id,
            other.input_hash == Document.input_hash,
            other.id < Document.id,
            other.trashed_at.is_(None),
            other.replaced_at.is_(None),
        )
    if problem == "near":
        if analysis_id is None:
            return false()
        return exists().where(
            NearDuplicate.analysis_id == analysis_id,
            NearDuplicate.kind == ItemKind.DOCUMENT.value,
            or_(NearDuplicate.item_a == Document.id, NearDuplicate.item_b == Document.id),
        )
    if problem == "pick":
        return and_(Document.marks.has_key("pick"), needs_queries())
    return false()


def needs_queries() -> ColumnElement[bool]:
    """질의를 만들 문서: '질의 안 만듦'이 아니고, 정답으로 쓰인 적도 만든 질의도 없고, 원본 · 사람이 판정한 적 없는 것.

    입구와 상관없이 같다. 문서만 모양은 모든 문서, 데이터를 더하면 새 문서, 지문을 나누면 답이 없는 청크가 된다.
    찾기로 붙은 오답(mined)은 세지 않는다(오답을 먼저 찾아도 질의를 만든다).
    """
    judged = aliased(Judgment)
    made = aliased(Query)
    is_judged = or_(judged.grade >= POSITIVE_MIN_GRADE, judged.source.in_(PEOPLE_JUDGMENT_SOURCES))
    return and_(
        Document.skip_generation.is_(False),
        ~exists().where(judged.document_id == Document.id, is_judged),
        ~exists().where(made.source_document_id == Document.id, made.trashed_at.is_(None)),
    )


def duplicate_member(analysis_id: int | None) -> ColumnElement[bool]:
    """중복 묶음의 대표가 아닌 문서: 번호가 더 작은 같은 본문 문서나, 가장 최근 뜻 분석의 근접 중복 짝이 있다.

    질의 만들기는 묶음의 대표에만 질의를 만든다. 진단의 '질의 없는 문서'와 질의 만들기가 같은 조건을 써서
    만들 것이 없는데 흐름이 질의 만들기에 머무는 일이 없게 한다(모든 문서를 읽는 묶음 계산 대신 SQL 한 번).
    """
    earlier = aliased(Document)
    same_text = exists().where(
        earlier.dataset_id == Document.dataset_id,
        earlier.text_hash == Document.text_hash,
        earlier.id < Document.id,
        earlier.trashed_at.is_(None),
        earlier.replaced_at.is_(None),
    )
    if analysis_id is None:
        return same_text
    partner = aliased(Document)
    near = exists().where(
        NearDuplicate.analysis_id == analysis_id,
        NearDuplicate.kind == ItemKind.DOCUMENT.value,
        or_(
            and_(NearDuplicate.item_b == Document.id, NearDuplicate.item_a == partner.id),
            and_(NearDuplicate.item_a == Document.id, NearDuplicate.item_b == partner.id),
        ),
        partner.id < Document.id,
        partner.trashed_at.is_(None),
        partner.replaced_at.is_(None),
    )
    return or_(same_text, near)


def generation_target(analysis_id: int | None) -> ColumnElement[bool]:
    """질의 만들기의 대상: 질의가 필요하고(needs_queries) 중복 묶음의 대표인 문서."""
    return and_(needs_queries(), ~duplicate_member(analysis_id))


def document_use(use: str) -> ColumnElement[bool]:
    """문서 쓰임 거르기: positive(정답으로) · negative(오답으로) · unused(안 쓰임, 휴지통 밖 질의 기준)."""
    active_query = aliased(Query)
    uses = (
        select(1)
        .select_from(Judgment)
        .join(active_query, active_query.id == Judgment.query_id)
        .where(Judgment.document_id == Document.id, active_query.trashed_at.is_(None))
    )
    if use == "positive":
        return exists(uses.where(IS_POSITIVE))
    if use == "negative":
        return exists(uses.where(IS_NEGATIVE))
    return ~exists(uses)


def active_query_in(dataset_id: int) -> ColumnElement[bool]:
    """데이터셋의 휴지통 밖 질의."""
    return and_(Query.dataset_id == dataset_id, QUERY_ACTIVE)


def active_document_in(dataset_id: int) -> ColumnElement[bool]:
    """데이터셋의 코퍼스 문서 (휴지통 밖 · 나누기로 대신하지 않은 것)."""
    return and_(Document.dataset_id == dataset_id, DOCUMENT_ACTIVE)
