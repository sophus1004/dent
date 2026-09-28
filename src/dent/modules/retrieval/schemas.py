"""검색 모듈의 입출력 모양 (Pydantic). JSON 이름은 snake_case이고 모두 /api/v1/retrieval 아래에서 쓴다.

입력은 <Name>Create·<Name>Update, 출력은 <Name>Read로 이름 짓는다.
입력 문자열은 앞뒤 공백을 지운 뒤 길이를 검사한다(공백만 있는 값도 걸리게).
데이터셋 목록 · 가져오기 기록 · 올린 파일 · 도우미 실행의 모양은 시스템(dent.system.schemas)의 것을 쓴다.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dent.modules.retrieval.models import (
    GRADE_MAX,
    GRADE_MIN,
    QUERY_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    AnalysisStatus,
    ExcludeReason,
    JudgmentSource,
    QuerySource,
    RepeatDecision,
    RepeatKind,
    Shape,
    SuggestionDecision,
    SuggestionKind,
)
from dent.system.models import DATASET_NAME_MAX_LENGTH, MODEL_NAME_MAX_LENGTH
from dent.system.schemas import HelperRunRead, SourceCreate

# 열 이름의 최대 길이
COLUMN_NAME_MAX_LENGTH = 500

# 필드 맞춤을 짐작할 때 한 번에 받는 열 이름 수
MAPPING_COLUMNS_MAX = 1_000

# 오답 칸을 여럿 고를 때의 최대 수
NEGATIVE_COLUMNS_MAX = 50

# 머리말 칸을 여럿 고를 때의 최대 수
HEADER_COLUMNS_MAX = 10

# 한 번에 고칠 수 있는 질의 · 문서 수
BULK_MAX_ITEMS = 1_000

# 문서 본문을 고칠 때의 최대 글자 수
DOCUMENT_EDIT_MAX_LENGTH = 200_000

# 설정의 범위: 최대 토큰 · 오답 수 · 순위 · 유사도 배 · 오답 풀 · 쉬운 쌍 상한
MAX_TOKENS_RANGE = (8, 8_192)
NEGATIVES_RANGE = (1, 50)
MINE_RANK_RANGE = (1, 1_000)
MINE_MARGIN_RANGE = (0.5, 1.0)
NEGATIVE_POOL_RANGE = (1, 100)
EASY_PAIR_CAP_RANGE = (0.0, 1.0)
# 도우미 실행 설정: 오버랩 토큰 · 청크마다 질의 수 · 질문형 몫(%)
CHUNK_OVERLAP_RANGE = (0, 256)
QUERIES_PER_CHUNK_RANGE = (1, 8)
QUESTION_SHARE_RANGE = (0, 100)

# 등급: 좋음 · 살펴볼 것 · 고칠 것
Grade = Literal["good", "warn", "bad"]

# 질의 목록의 상태 거르기: 휴지통 밖 전체 · 학습에 씀 · 학습에서 뺌 · 휴지통
StatusFilter = Literal["active", "included", "excluded", "trash"]

# 질의 목록의 문제 거르기 (진단 검사 이름)
QueryProblem = Literal[
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
]

# 문서 목록의 문제 거르기 (repeat = 고르지 않은 반복 구간이 든 문서)
DocumentProblem = Literal["broken", "repeat", "long", "duplicate", "near", "pick"]

# 문서 쓰임 거르기: 정답으로 · 오답으로 · 안 쓰임
DocumentUse = Literal["positive", "negative", "unused"]

# 여러 질의에 한 번에 하는 일
QueryBulkAction = Literal["exclude", "include", "trash", "restore"]

# 여러 문서에 한 번에 하는 일
DocumentBulkAction = Literal["trash", "restore", "skip_generation", "allow_generation"]

# 흐름 칸의 상태: done 끝 · bad 심각 · warn 주의 · todo 할 차례 · ask 허락 기다림 · skip 필요 없음 · wait 앞 단계 뒤 · lock 잠김
FlowState = Literal["done", "bad", "warn", "todo", "ask", "skip", "wait", "lock"]


# ---------- 필드 맞춤 ----------


class FieldMappingCreate(BaseModel):
    """가져올 때 쓸 필드 맞춤: 원본의 모양과, 어느 열이 무엇인지.

    모양마다 필수 칸: documents(text) · pair(query, positive) · mrc(query, positive) ·
    triplet(query, positive) · scored(query, document, score).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # 원본의 모양
    shape: Shape

    # 문서 본문 열 (문서만)
    text: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 질의 열 (쌍 · MRC의 질문 · 세 쌍 · 점수)
    query: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 정답 문서 열 (쌍 · MRC의 지문 · 세 쌍)
    positive: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 오답 문서 열들 (세 쌍, 여럿)
    negatives: list[str] = Field(default_factory=list, max_length=NEGATIVE_COLUMNS_MAX)

    # 문서 열 (점수)
    document: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 점수 열 (점수: 0/1, 0~3, 0~1 실수)
    score: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 답 열 (MRC의 답 근거)
    answer: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 문서 제목 열
    title: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 원래 문서 번호 열 (문서만)
    doc_key: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 머리말 칸들: 값을 학습 글 머리말에 붙인다(제목 다음, 고른 순서대로)
    header_columns: list[str] = Field(default_factory=list, max_length=HEADER_COLUMNS_MAX)

    # 묶음 칸: 값이 같은 문서를 한 묶음으로 본다(같은 원문 · 같은 제품 …)
    group_column: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    @model_validator(mode="after")
    def _check_required(self) -> "FieldMappingCreate":
        """모양마다 필수 칸이 있는지 본다."""
        required: dict[Shape, tuple[str, ...]] = {
            Shape.DOCUMENTS: ("text",),
            Shape.PAIR: ("query", "positive"),
            Shape.MRC: ("query", "positive"),
            Shape.TRIPLET: ("query", "positive"),
            Shape.SCORED: ("query", "document", "score"),
        }
        missing = [name for name in required[self.shape] if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.shape.value} 모양에는 {', '.join(missing)} 칸이 필요합니다.")
        return self

    def used_columns(self) -> set[str]:
        """맞춘 열 이름들 (나머지 열은 메타데이터가 된다)."""
        names = [
            self.text,
            self.query,
            self.positive,
            self.document,
            self.score,
            self.answer,
            self.title,
            self.doc_key,
            self.group_column,
            *self.negatives,
            *self.header_columns,
        ]
        return {name for name in names if name}


class FieldMappingRead(BaseModel):
    """열 이름으로 짐작한 모양과 필드 맞춤. 못 찾은 칸은 null."""

    shape: Shape
    text: str | None
    query: str | None
    positive: str | None
    negatives: list[str]
    document: str | None
    score: str | None
    answer: str | None
    title: str | None
    doc_key: str | None
    header_columns: list[str]
    group_column: str | None


class MappingSuggestCreate(BaseModel):
    """필드 맞춤 짐작 입력: 원본의 열 이름들."""

    # 원본의 열 이름들 (시스템 미리 보기의 columns)
    columns: list[str] = Field(max_length=MAPPING_COLUMNS_MAX)


class ImportCreate(SourceCreate):
    """가져오기 시작 입력: 가져올 곳(SourceCreate)과 데이터셋, 필드 맞춤. dataset_id와 new_dataset_name 가운데 하나만."""

    # 더할 기존 데이터셋 번호
    dataset_id: int | None = None

    # 새로 만들 데이터셋 이름
    new_dataset_name: str | None = Field(
        default=None, min_length=1, max_length=DATASET_NAME_MAX_LENGTH
    )

    # 필드 맞춤 (원본에 분할 열이 있어도 쓰지 않고 한 덩어리로 가져온다)
    mapping: FieldMappingCreate


# ---------- 데이터셋 · 설정 ----------


class SettingsRead(BaseModel):
    """검색 데이터셋의 설정."""

    model_config = ConfigDict(from_attributes=True)

    # 처음 가져온 원본의 모양
    shape: Shape

    # 학습할 모델. null이면 연결된 임베딩 모델.
    target_model: str | None

    # 질의 · 문서 최대 토큰
    query_max_tokens: int
    doc_max_tokens: int

    # 질의마다 오답 수
    negatives: int

    # 오답 찾기 순위 범위와 거짓 오답 경계(정답 유사도의 배)
    mine_rank_from: int
    mine_rank_to: int
    mine_margin: float

    # 질의마다 모아 두는 오답 풀
    negative_pool: int

    # 학습 글 머리말에 구획 경로를 붙이는지 · 질의 만들기의 되찾기 거르기 · 쉬운 쌍 상한(몫)
    section_header: bool
    round_trip: bool
    easy_pair_cap: float

    # 도우미 실행 설정: 문서 나누기 오버랩(토큰) · 청크마다 질의 수 · 질문형 몫(%)
    chunk_overlap: int
    queries_per_chunk: int
    question_share: int

    # 가장 최근 오답 훑기 결과 (없으면 빈 객체) · 반복 구간을 살핀 시각
    negative_scan: dict[str, Any]
    scanned_at: datetime | None


class SettingsUpdate(BaseModel):
    """데이터셋 설정 고치기. 준 값만 바꾼다. target_model을 빈 문자열로 주면 연결된 임베딩으로 돌린다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target_model: str | None = Field(default=None, max_length=MODEL_NAME_MAX_LENGTH)
    query_max_tokens: int | None = Field(
        default=None, ge=MAX_TOKENS_RANGE[0], le=MAX_TOKENS_RANGE[1]
    )
    doc_max_tokens: int | None = Field(default=None, ge=MAX_TOKENS_RANGE[0], le=MAX_TOKENS_RANGE[1])
    negatives: int | None = Field(default=None, ge=NEGATIVES_RANGE[0], le=NEGATIVES_RANGE[1])
    mine_rank_from: int | None = Field(default=None, ge=MINE_RANK_RANGE[0], le=MINE_RANK_RANGE[1])
    mine_rank_to: int | None = Field(default=None, ge=MINE_RANK_RANGE[0], le=MINE_RANK_RANGE[1])
    mine_margin: float | None = Field(
        default=None, ge=MINE_MARGIN_RANGE[0], le=MINE_MARGIN_RANGE[1]
    )
    negative_pool: int | None = Field(
        default=None, ge=NEGATIVE_POOL_RANGE[0], le=NEGATIVE_POOL_RANGE[1]
    )
    section_header: bool | None = None
    round_trip: bool | None = None
    easy_pair_cap: float | None = Field(
        default=None, ge=EASY_PAIR_CAP_RANGE[0], le=EASY_PAIR_CAP_RANGE[1]
    )
    chunk_overlap: int | None = Field(
        default=None, ge=CHUNK_OVERLAP_RANGE[0], le=CHUNK_OVERLAP_RANGE[1]
    )
    queries_per_chunk: int | None = Field(
        default=None, ge=QUERIES_PER_CHUNK_RANGE[0], le=QUERIES_PER_CHUNK_RANGE[1]
    )
    question_share: int | None = Field(
        default=None, ge=QUESTION_SHARE_RANGE[0], le=QUESTION_SHARE_RANGE[1]
    )


class DatasetSummaryRead(BaseModel):
    """데이터셋 목록의 한 줄."""

    id: int
    name: str
    description: str

    # 휴지통 밖 질의 · 문서 수
    query_count: int
    document_count: int

    # 판정 수 · 정답 · 오답 (휴지통 밖 질의 · 문서)
    judgment_count: int
    positive_count: int
    negative_count: int

    # 휴지통에 있는 질의 · 문서 수
    trash_count: int

    # 가져오는 중인지
    importing: bool

    # 처음 가져온 모양과 흐름의 입구 단계 (가져오기 전이면 null)
    shape: Shape | None
    entry_stage: int | None

    created_at: datetime
    updated_at: datetime


class DatasetRead(DatasetSummaryRead):
    """데이터셋 하나의 자세한 정보."""

    # 설정 (가져오기 전이면 null)
    settings: SettingsRead | None


# ---------- 질의 · 문서 · 판정 ----------


class DocumentBriefRead(BaseModel):
    """문서를 한 줄로 보일 때의 모양."""

    id: int
    title: str
    # 본문 앞부분
    snippet: str
    token_count: int


class QueryRead(BaseModel):
    """질의 한 건 (목록의 한 줄)."""

    id: int
    dataset_id: int
    text: str
    source: QuerySource
    source_document_id: int | None
    answer: str | None
    exclude_reason: ExcludeReason | None
    is_trashed: bool
    trashed_at: datetime | None
    row_version: int
    extra: dict[str, Any]
    import_id: int | None
    created_at: datetime
    updated_at: datetime

    # 정답 · 오답 수
    positive_count: int
    negative_count: int

    # 정답 문서 (앞의 몇 개)
    positives: list[DocumentBriefRead]

    # 이 질의에 걸린 문제 (검사 이름)
    marks: list[str]


class QueryPageRead(BaseModel):
    """질의 목록 한 쪽."""

    items: list[QueryRead]
    total: int
    limit: int
    offset: int


class QueryUpdate(BaseModel):
    """질의 하나 고치기. 준 값만 바꾼다. row_version은 불러올 때 받은 값을 그대로 보낸다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    text: str | None = Field(default=None, min_length=1, max_length=QUERY_MAX_LENGTH)
    # 학습에서 빼기(true) · 되돌리기(false)
    excluded: bool | None = None
    # 휴지통으로(true) · 되살리기(false)
    trashed: bool | None = None
    row_version: int


class QueryBulkUpdate(BaseModel):
    """여러 질의에 한 번에 하는 일."""

    query_ids: list[int] = Field(min_length=1, max_length=BULK_MAX_ITEMS)
    action: QueryBulkAction


class DocumentRead(BaseModel):
    """문서 한 건 (목록의 한 줄)."""

    id: int
    dataset_id: int
    doc_key: str | None
    title: str
    text: str
    # 머리말 칸 값 · 구획 경로 · 묶음 칸 값
    header: str
    section: str
    group_key: str | None
    # 학습 글 (머리말 + 떼기로 고른 구간을 뺀 본문). 임베딩 · 내보내기가 쓰는 글.
    training_text: str
    token_count: int
    source_document_id: int | None
    chunk_index: int | None
    skip_generation: bool
    is_trashed: bool
    is_replaced: bool
    row_version: int
    extra: dict[str, Any]
    import_id: int | None
    created_at: datetime
    updated_at: datetime

    # 이 문서를 정답 · 오답으로 쓰는 질의 수
    positive_count: int
    negative_count: int

    # 이 문서로 만든 합성 질의 수
    synthetic_count: int

    # 이 문서에 걸린 문제 (검사 이름)
    marks: list[str]


class DocumentPageRead(BaseModel):
    """문서 목록 한 쪽."""

    items: list[DocumentRead]
    total: int
    limit: int
    offset: int


class DocumentUpdate(BaseModel):
    """문서 하나 고치기. 준 값만 바꾼다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=TITLE_MAX_LENGTH)
    text: str | None = Field(default=None, min_length=1, max_length=DOCUMENT_EDIT_MAX_LENGTH)
    skip_generation: bool | None = None
    trashed: bool | None = None
    row_version: int


class DocumentBulkUpdate(BaseModel):
    """여러 문서에 한 번에 하는 일."""

    document_ids: list[int] = Field(min_length=1, max_length=BULK_MAX_ITEMS)
    action: DocumentBulkAction


class ChangedRead(BaseModel):
    """여러 줄을 바꾼 결과."""

    changed: int


class JudgmentSet(BaseModel):
    """판정 하나 두기: 등급 0(오답) ~ 3."""

    grade: int = Field(ge=GRADE_MIN, le=GRADE_MAX)


class JudgmentRead(BaseModel):
    """판정 한 줄."""

    model_config = ConfigDict(from_attributes=True)

    query_id: int
    document_id: int
    grade: int
    source: JudgmentSource
    conflict: bool
    teacher_score: float | None


class RankedDocumentRead(BaseModel):
    """질의 패널의 판정 · 순위 한 줄: 문서와 그 판정 · 기준 검색 순위 · 유사도 · Jev."""

    document: DocumentBriefRead

    # 판정 등급 (없으면 null = 모름) · 출처
    grade: int | None
    source: JudgmentSource | None

    # 기준 검색 순위 · 유사도 (뜻 분석 전이면 null)
    rank: int | None
    similarity: float | None

    # 제안(거짓 오답 · 빠진 정답 · 정답 의심)이 있으면 그 종류와 Jev 예 확률
    flag: SuggestionKind | None
    jev_probability: float | None


class QueryDetailRead(BaseModel):
    """질의 패널: 질의와, 판정 · 순위(상위 10 + 10위 밖 판정)."""

    query: QueryRead

    # 첫 정답 순위 (뜻 분석 전이거나 정답이 없으면 null)
    first_positive_rank: int | None

    # 기준 검색 상위 (판정 · 모름 모두). 뜻 분석 전이면 판정 목록만.
    ranked: list[RankedDocumentRead]

    # 10위 밖의 판정 (정답 · 오답)
    outside: list[RankedDocumentRead]

    # 순위가 있는지 (뜻 분석이 있는지)
    has_ranking: bool


class JudgedQueryRead(BaseModel):
    """문서 패널의 한 줄: 이 문서를 쓰는 질의(또는 가까운 질의)와 판정."""

    query_id: int
    text: str
    grade: int | None
    rank: int | None
    similarity: float | None
    jev_probability: float | None


class DocumentDetailRead(BaseModel):
    """문서 패널: 문서와, 쓰는 질의 · 가까운 질의(판정 없음) · 이 문서로 만든 질의 · 같은 원문의 청크."""

    document: DocumentRead
    uses: list[JudgedQueryRead]
    nearby: list[JudgedQueryRead]
    synthetic: list[JudgedQueryRead]
    siblings: list[DocumentBriefRead]


# ---------- 반복 구간 ----------


class RepeatRead(BaseModel):
    """반복 구간 하나(틀)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: RepeatKind
    text: str
    samples: list[str]

    # 든 문서 수 · 앞머리 · 꼬리에 있는 문서 수
    document_count: int
    head_count: int
    tail_count: int

    # 규칙 · 도우미의 제안과 까닭, 사람의 결정 (아직이면 null)
    suggestion: RepeatDecision
    reason: str | None
    decision: RepeatDecision | None


class RepeatListRead(BaseModel):
    """반복 구간 목록과 결정별 수(remove · keep · undecided) · 든 문서 수 · 살핀 시각."""

    items: list[RepeatRead]
    counts: dict[str, int]
    documents: int
    scanned_at: datetime | None


class RepeatUpdate(BaseModel):
    """반복 구간 고르기: remove(학습 글에서 뗌) · keep(남김) · null(고르기 전으로)."""

    decision: RepeatDecision | None


class RepeatBulkUpdate(BaseModel):
    """반복 구간 여럿을 한 번에 고르기. follow_suggestion이면 틀마다 제안대로(decision은 보지 않는다)."""

    repeat_ids: list[int] = Field(min_length=1, max_length=BULK_MAX_ITEMS)
    decision: RepeatDecision | None = None
    follow_suggestion: bool = False


class RepeatSampleRead(BaseModel):
    """반복 구간이 든 보기 문서 하나: 구간 앞 글 · 구간 · 뒤 글(앞뒤는 잘라서)."""

    document_id: int
    title: str
    before: str
    span: str
    after: str


# ---------- 진단 ----------


class LadderStepRead(BaseModel):
    """등급 사다리 한 칸의 글자. 예: 통과 '< 2%'."""

    grade: Grade
    text: str


class CheckRead(BaseModel):
    """검사 한 줄 (진단 카드 · 흐름 줄의 항목)."""

    # 검사 이름 (예: no_positive)
    key: str

    # 흐름 단계 1 · 2 · 3 과 흐름 항목 (예: answer)
    stage: int
    item: str | None

    grade: Grade

    # 큰 숫자 · 단위 · 아래 작은 사실
    value: str
    unit: str
    sub: str

    # 등급 사다리 (ⓘ)
    ladder: list[LadderStepRead]

    # 볼 데이터: 대상(queries · documents · suggestions)과 문제 거르기, 그 수
    view_target: Literal["queries", "documents", "suggestions"] | None
    view_problem: str | None
    view_count: int


class FlowItemRead(BaseModel):
    """흐름 단계의 전처리 항목 하나의 상태."""

    key: str
    state: FlowState
    text: str


class FlowStageRead(BaseModel):
    """흐름 단계 하나 (1 문서 · 2 질의 · 3 하드 네거티브)."""

    no: int
    name: str
    # 이 단계의 수 (문서 · 질의 · 오답)
    count: int
    state: FlowState
    # 남은 문제 수 (심각 + 주의)
    open_count: int
    items: list[FlowItemRead]


class FlowMoveRead(BaseModel):
    """단계 사이 넘어가기 (질의 만들기 · 오답 찾기 · 나누기 · 내보내기)."""

    key: Literal["generate", "mine", "export"]
    state: FlowState
    text: str


class FlowRead(BaseModel):
    """흐름: 단계 셋 · 넘어가기 셋 · 입구 · 지금."""

    stages: list[FlowStageRead]
    moves: list[FlowMoveRead]
    # 입구 단계 (가져오기 전이면 null)
    entry: int | None
    # 지금: 남은 일이 있는 첫 단계(1 · 2 · 3) 또는 넘어가기(generate · mine · export)
    current: int | str | None


class KpiRead(BaseModel):
    """기준 검색 점수 (학습에 쓰는 질의 전부)."""

    recall_at_10: float
    mrr_at_10: float
    # 잰 질의 수
    evaluated: int
    # 첫 뜻 분석의 값 (정제 전)
    baseline_recall_at_10: float | None
    baseline_mrr_at_10: float | None


class BinRead(BaseModel):
    """분포 막대 한 칸."""

    label: str
    count: int
    # 칸 색: 보통 · 주의 · 심각 · 흐림(아직 모르는 것. 예: 분석에 없는 질의)
    tone: Literal["normal", "warn", "bad", "muted"]


class OverviewRead(BaseModel):
    """진단: 흐름 · 단계별 검사 · 기준 검색 점수 · 분포."""

    dataset_id: int
    query_count: int
    document_count: int
    positive_count: int
    negative_count: int
    flow: FlowRead
    checks: list[CheckRead]
    kpi: KpiRead | None

    # 분포: 첫 정답 순위 · 질의당 오답 · 질의 유형 · 문서 길이(토큰) · 판정 출처
    first_rank: list[BinRead]
    negatives_per_query: list[BinRead]
    query_types: list[BinRead]
    document_lengths: list[BinRead]
    judgment_sources: list[BinRead]

    # 진단 시각
    computed_at: datetime


# ---------- 뜻 분석 ----------


class AnalysisRead(BaseModel):
    """뜻 분석 한 번의 상태."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    status: AnalysisStatus
    query_count: int
    document_count: int
    job_id: int | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class AnalysisStateRead(BaseModel):
    """뜻 분석 상태: 다 만든 것 · 도는(또는 막 끝난) 것 · 분석 이후 바뀌었는지 · 모델."""

    done: AnalysisRead | None
    run: AnalysisRead | None
    outdated: bool
    model: str | None


class MapPointsRead(BaseModel):
    """지도의 점들 (칸마다 목록: 같은 자리가 같은 점)."""

    analysis_id: int
    kinds: list[str]
    ids: list[int]
    x: list[float]
    y: list[float]
    clusters: list[int]
    # 점마다 출처 · 문제 표시(문제 이름 목록의 자리 비트)
    sources: list[str | None]
    flags: list[int]
    # 문제 표시 비트의 이름 (자리 순서)
    flag_names: list[str]
    # 질의 없는 무리 (문서가 있는데 질의가 0인 무리 번호)
    empty_clusters: list[int]


class MapMatchesRead(BaseModel):
    """검색어가 든 질의 · 문서 번호들 (지도 강조)."""

    query_ids: list[int]
    document_ids: list[int]


class MapTextRead(BaseModel):
    """지도 점 하나의 글 (말풍선). 질의면 제목은 빈 글."""

    text: str
    title: str


class NegativeScanCellRead(BaseModel):
    """오답 훑기 표의 한 칸: 순위 묶음 × 정답 대비 문턱."""

    rank_from: int
    rank_to: int
    margin: float
    # Jev에 물은 후보 수 · 그 가운데 Jev 예(거짓 오답) 수 · 문턱을 통과해 남는 오답 수
    asked: int
    false_negatives: int
    kept: int


class NegativeScanRead(BaseModel):
    """오답 훑기 결과: 질의 표본 수 · 칸들 · 잰 시각."""

    queries: int
    cells: list[NegativeScanCellRead]
    measured_at: datetime | None


class JobStartedRead(BaseModel):
    """작업을 넣은 결과 (진행률은 /api/v1/jobs/{job_id})."""

    job_id: int


# ---------- 제안 ----------


class SuggestionRead(BaseModel):
    """제안 한 건."""

    id: int
    kind: SuggestionKind
    query_id: int
    query_text: str
    document: DocumentBriefRead
    # 지금 판정 (없으면 null = 모름)
    grade: int | None
    rank: int
    similarity: float
    jev_probability: float | None
    is_confirmed: bool
    decision: SuggestionDecision | None


class SuggestionPageRead(BaseModel):
    """제안 목록 한 쪽과 종류별 대기 수."""

    items: list[SuggestionRead]
    total: int
    limit: int
    offset: int
    pending_counts: dict[str, int]
    confirmed_pending: int


# ---------- 도우미 ----------


class HelperStateRead(BaseModel):
    """데이터셋의 도우미 상태: 가장 최근 실행과 되돌린 카드."""

    run: HelperRunRead | None
    undone_events: list[int]


class HelperUndo(BaseModel):
    """되돌리기: 카드 하나(event_id) 또는 실행 전체(null)."""

    event_id: int | None = None


class HelperUndoRead(BaseModel):
    """되돌린 결과."""

    reverted: int
    skipped: int


class HelperEstimateRead(BaseModel):
    """도우미 실행 설정 카드의 어림 (고른 값으로 나누기 · 질의 만들기를 해 보면)."""

    # 청크 크기를 넘는 문서 수 · 그 문서들이 나뉠 청크 수 · 나누면 코퍼스의 청크 수
    long_documents: int
    long_chunks: int
    chunks: int

    # 질의를 만들 청크 수 · 남을 질의 수(청크마다 수 × 청크) · LLM 토큰 어림
    targets: int
    queries: int
    tokens: int


class HelperPermission(BaseModel):
    """허락에 답한다. action이 chunk_first면 질의 만들기 허락 카드의 [먼저 나누기](건너뛴 문서 나누기부터)."""

    approve: bool
    action: Literal["chunk_first"] | None = None


# ---------- 내보내기 ----------

# 내보내기 파일 모양: 학습 jsonl(bge-m3 FlagEmbedding) · 학습 표(sentence-transformers) · 평가 BEIR ·
# 코퍼스(운영 색인에 넣을 학습 글 + 정리 규칙)
ExportFormat = Literal["train_jsonl", "train_table", "beir", "corpus"]


class ExportCreate(BaseModel):
    """내보내기 시작: 파일 모양들 · 넣을 출처 · 질의마다 오답 수(학습 표) · 교사 점수(기본 켬) · 출처 칸."""

    formats: list[ExportFormat] = Field(min_length=1, max_length=4)
    sources: list[JudgmentSource] = Field(
        default_factory=lambda: [
            JudgmentSource.ORIGINAL,
            JudgmentSource.MINED,
            JudgmentSource.SYNTHETIC,
            JudgmentSource.HELPER,
            JudgmentSource.HUMAN,
        ],
        min_length=1,
    )
    negatives: int = Field(default=7, ge=NEGATIVES_RANGE[0], le=NEGATIVES_RANGE[1])
    teacher_scores: bool = True
    # 학습 표에 출처 칸(source: original · synthetic · human)을 넣을지. jsonl · BEIR에는 늘 넣는다.
    source_column: bool = True


class ExportFileRead(BaseModel):
    """내보낼 파일 하나의 수."""

    format: ExportFormat
    file_name: str

    # 넣은 질의 · 정답 · 오답 · 문서 수 (train · valid · test로 나누지 않고 모두)
    queries: int
    positives: int
    negatives: int
    documents: int


class ExportPreviewRead(BaseModel):
    """내보내기 미리 보기: 한 줄 · 파일마다 수 · 출처별 판정 수 · 오답 모자람 · 남은 주의."""

    # 학습 jsonl의 첫 줄 (고른 대로, 글은 줄여서)
    line: str
    files: list[ExportFileRead]
    # 출처마다 판정 수 (넣을 출처 칸)
    source_counts: dict[str, int]
    # 오답이 목표보다 적은 학습 질의 수 · 학습 질의 평균 오답 수(풀 전체)
    lacking: int
    average_negatives: float
    # 교사 점수가 없어 Jev에 물을 판정 수 (교사 점수를 고르면)
    teacher_missing: int
    # 그 판정을 Jev에 묻는 데 걸릴 어림 시간(초). 가장 최근에 잰 교사 점수 속도로 센다. 잰 적이 없으면 null
    teacher_seconds: float | None
    # 남은 주의 (검사 이름: 수)
    warnings: dict[str, int]
    # 남은 심각 (검사 이름들). 있으면 내보낼 수 없다.
    blocked: list[str]
