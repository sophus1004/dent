"""필드 맞춤 짐작: 원본의 열 이름을 보고 모양(문서만 · 쌍 · MRC · 세 쌍 · 점수)과 칸을 고른다.

시스템의 미리 보기는 열 이름과 앞쪽 줄까지만 준다. 그 열을 검색의 칸에 어떻게 맞출지는 이 모듈의 규칙이다.
머리말 칸 · 묶음 칸은 데이터마다 뜻이 달라 짐작하지 않는다(사람이 고른다).
"""

from dent.modules.retrieval.models import Shape
from dent.modules.retrieval.schemas import FieldMappingRead

# 칸마다 찾는 열 이름. 앞에 있는 이름일수록 먼저 고른다.
QUERY_NAMES = ("query", "question", "anchor", "질의", "질문", "검색어", "sentence1", "q")
POSITIVE_NAMES = (
    "positive",
    "pos",
    "context",
    "passage",
    "지문",
    "정답",
    "positive_passage",
    "sentence2",
)
DOCUMENT_NAMES = ("document", "doc", "passage", "text", "문서")
SCORE_NAMES = ("score", "label", "relevance", "grade", "점수", "판정")
ANSWER_NAMES = ("answer", "answers", "answer_text", "답")
TITLE_NAMES = ("title", "doc_title", "제목")
DOC_KEY_NAMES = ("doc_id", "docid", "document_id", "_id", "id", "문서번호")
TEXT_NAMES = ("text", "content", "body", "document", "본문", "내용")

# 오답 열 이름의 앞머리 (negative_1, neg, hard_negative …)
NEGATIVE_PREFIXES = ("negative", "neg", "hard_negative", "hard_neg", "오답")


def suggest_mapping(columns: list[str]) -> FieldMappingRead:
    """열 이름으로 모양과 칸을 짐작한다. 못 찾은 칸은 None. 분할 열은 쓰지 않는다(한 덩어리로 가져온다)."""
    query = _find(columns, QUERY_NAMES)
    positive = _find(columns, POSITIVE_NAMES, skip={query})
    negatives = [
        column
        for column in columns
        if column.strip().lower().startswith(NEGATIVE_PREFIXES) and column != positive
    ]
    score = _find(columns, SCORE_NAMES, skip={query, positive})
    answer = _find(columns, ANSWER_NAMES, skip={query, positive})
    title = _find(columns, TITLE_NAMES, skip={query, positive})

    if query and positive and negatives:
        shape = Shape.TRIPLET
    elif query and score and (positive or _find(columns, DOCUMENT_NAMES, skip={query})):
        shape = Shape.SCORED
    elif query and positive:
        shape = Shape.MRC if answer else Shape.PAIR
    elif query and _find(columns, DOCUMENT_NAMES, skip={query}):
        shape = Shape.PAIR
        positive = _find(columns, DOCUMENT_NAMES, skip={query})
    else:
        shape = Shape.DOCUMENTS

    if shape == Shape.DOCUMENTS:
        text = _find(columns, TEXT_NAMES)
        return FieldMappingRead(
            shape=shape,
            text=text,
            query=None,
            positive=None,
            negatives=[],
            document=None,
            score=None,
            answer=None,
            title=_find(columns, TITLE_NAMES, skip={text}),
            doc_key=_find(columns, DOC_KEY_NAMES, skip={text}),
            header_columns=[],
            group_column=None,
        )
    document = None
    if shape == Shape.SCORED:
        document = positive or _find(columns, DOCUMENT_NAMES, skip={query})
        positive = None
    return FieldMappingRead(
        shape=shape,
        text=None,
        query=query,
        positive=positive,
        negatives=negatives if shape == Shape.TRIPLET else [],
        document=document,
        score=score if shape == Shape.SCORED else None,
        answer=answer if shape == Shape.MRC else None,
        title=title,
        doc_key=None,
        header_columns=[],
        group_column=None,
    )


def _find(
    columns: list[str], wanted_names: tuple[str, ...], *, skip: set[str | None] | None = None
) -> str | None:
    """wanted_names 순서대로 찾아 처음 맞는 열 이름. 대소문자는 가리지 않고, skip의 열은 고르지 않는다."""
    skipped = skip or set()
    # 대소문자만 다른 열이 여럿이면 앞의 열이 남도록 뒤에서부터 채운다.
    by_lower_name = {
        column.strip().lower(): column for column in reversed(columns) if column not in skipped
    }
    for wanted in wanted_names:
        if wanted in by_lower_name:
            return by_lower_name[wanted]
    return None
