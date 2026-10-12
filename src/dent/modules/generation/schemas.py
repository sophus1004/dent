"""생성 모듈 API의 입출력 모양: 데이터셋, 필드 맞춤 · 가져오기, 진단, 대화 · 검토 결정, 뜻 분석, 내보내기."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dent.modules.generation.models import Action, AnalysisStatus, Shape
from dent.modules.generation.rules import CheckKey, GradeKind, Who
from dent.system.models import DATASET_NAME_MAX_LENGTH
from dent.system.schemas import HelperRunRead, SourceCreate

# 원본 열 이름의 최대 길이
COLUMN_NAME_MAX_LENGTH = 500

# 필드 맞춤 짐작에 넘기는 열 이름의 최대 개수
MAPPING_COLUMNS_MAX = 1_000

# 턴마다 칸(input1 · output1 …)을 맞출 수 있는 최대 턴 수
MAX_TURN_COLUMNS = 20

# 한 번에 휴지통에 넣거나 되살리는 대화의 최대 수
BULK_MAX_IDS = 1_000

# 대화 목록의 상태 거르기: 휴지통 밖 · 휴지통
ConversationStatusFilter = Literal["active", "trash"]

# 문제 거르기: 검사 이름 · 문제 있음(any) · 문제 없음(none)
ProblemFilter = CheckKey | Literal["any", "none"]

# 등급
Grade = Literal["good", "warn", "bad"]

# 내보낼 파일 모양 (첫 판: TRL messages jsonl)
ExportFormat = Literal["sft_jsonl"]


# ---------- 데이터셋 ----------


class DatasetSummaryRead(BaseModel):
    """데이터셋 목록의 한 줄."""

    # 데이터셋 번호
    id: int

    # 이름
    name: str

    # 설명
    description: str

    # 휴지통에 없는 대화 수
    conversation_count: int

    # 문제가 남은 대화 수 (같은 지시는 빼고 센다. 진단의 검사별 수와 다를 수 있다)
    problem_count: int

    # 휴지통에 있는 대화 수
    trash_count: int

    # 이 데이터셋으로 가져오는 중인지 (가져오기가 대기 중이거나 실행 중)
    importing: bool

    # 만든 시각
    created_at: datetime

    # 마지막으로 바뀐 시각
    updated_at: datetime


class DatasetRead(DatasetSummaryRead):
    """데이터셋 하나의 자세한 정보. 흐름의 입구 단계를 더한다."""

    # 흐름의 입구: 1 지시 · 2 응답 (첫 판은 응답이 있는 데이터만 받아 늘 2)
    entry_stage: int


# ---------- 필드 맞춤 · 가져오기 ----------


class TurnColumns(BaseModel):
    """턴 하나의 칸: 사용자 칸과 응답 칸 (turns 모양)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 사용자 칸 이름 (예: input1)
    user: str = Field(min_length=1, max_length=COLUMN_NAME_MAX_LENGTH)

    # 응답 칸 이름 (예: output1)
    assistant: str = Field(min_length=1, max_length=COLUMN_NAME_MAX_LENGTH)


class FieldMappingRead(BaseModel):
    """열 이름으로 짐작한 필드 맞춤. 못 찾은 칸은 null."""

    # 모양: messages · sharegpt · alpaca · turns
    shape: Shape

    # 대화 목록 칸 (messages · sharegpt)
    messages: str | None

    # 지시 · 덧붙인 입력 · 응답 · 시스템 칸 (alpaca)
    instruction: str | None
    input: str | None
    output: str | None
    system: str | None

    # 턴마다 칸 (turns)
    turns: list[TurnColumns]


class MappingSuggestCreate(BaseModel):
    """필드 맞춤 짐작 입력: 원본의 열 이름들."""

    # 원본의 열 이름들 (시스템 미리 보기의 columns)
    columns: list[str] = Field(max_length=MAPPING_COLUMNS_MAX)


class FieldMappingCreate(BaseModel):
    """가져올 때 쓸 필드 맞춤. 모양마다 꼭 있어야 하는 칸이 다르다. 원본에 분할이 있어도 하나로 가져온다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 모양
    shape: Shape

    # 대화 목록 칸 (messages · sharegpt에서 꼭)
    messages: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 지시 칸 (alpaca에서 꼭)
    instruction: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 덧붙인 입력 칸 (alpaca, 선택). 있으면 지시 뒤에 빈 줄을 두고 붙인다.
    input: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 응답 칸 (alpaca에서 꼭)
    output: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 시스템 칸 (alpaca · turns, 선택)
    system: str | None = Field(default=None, max_length=COLUMN_NAME_MAX_LENGTH)

    # 턴마다 칸 (turns에서 꼭, 앞 턴부터)
    turns: list[TurnColumns] = Field(default_factory=list, max_length=MAX_TURN_COLUMNS)

    @model_validator(mode="after")
    def _needs_columns(self) -> "FieldMappingCreate":
        missing = {
            Shape.MESSAGES: not self.messages,
            Shape.SHAREGPT: not self.messages,
            Shape.ALPACA: not (self.instruction and self.output),
            Shape.TURNS: not self.turns,
        }[self.shape]
        if missing:
            raise ValueError("이 모양에 꼭 있어야 하는 칸을 골라 주세요.")
        return self

    def used_columns(self) -> set[str]:
        """맞춤에 쓴 열 이름들 (남는 열은 extra에 담는다)."""
        names = {self.messages, self.instruction, self.input, self.output, self.system}
        names |= {name for turn in self.turns for name in (turn.user, turn.assistant)}
        return {name for name in names if name}


class ImportCreate(SourceCreate):
    """가져오기 시작 입력: 가져올 곳(SourceCreate)과 데이터셋, 필드 맞춤.

    dataset_id와 new_dataset_name 가운데 하나만 준다.
    """

    # 더할 기존 데이터셋 번호
    dataset_id: int | None = None

    # 새로 만들 데이터셋 이름
    new_dataset_name: str | None = Field(
        default=None, min_length=1, max_length=DATASET_NAME_MAX_LENGTH
    )

    # 필드 맞춤
    mapping: FieldMappingCreate


# ---------- 진단 ----------


class CheckRead(BaseModel):
    """진단의 검사 한 줄: 수 · 등급과 등급 규칙, 누가 고치는지."""

    # 검사 이름
    key: CheckKey

    # 흐름 단계: 1 지시 · 2 응답
    stage: int

    # 문제가 남은 대화 수 (사람이 '그대로 두기'로 둔 것은 빼고)
    count: int

    # 휴지통 밖 대화 수에 대한 비율 (0~1)
    rate: float

    # 등급
    grade: Grade

    # 등급 매기는 방식과 비율 경계 (화면의 ⓘ 등급 사다리)
    kind: GradeKind
    good_max: float
    warn_max: float

    # 고치는 쪽 (rule · jev · llm · human)
    who: list[Who]

    # 검토에서 고를 수 있는 결정 (앞의 것이 규칙이 권하는 것)
    actions: list[Action]


class WaitingRead(BaseModel):
    """아직 재지 못한 뜻 검사 한 줄 (진단의 흐린 줄)."""

    # 검사 이름
    key: CheckKey

    # 흐름 단계: 1 지시 · 2 응답
    stage: int

    # 까닭 (예: 뜻 분석 전 · Jev 미연결)
    reason: str


class OverviewRead(BaseModel):
    """데이터셋 진단: 대화 수, 검사들, 흐름에서 지금 단계."""

    # 데이터셋 번호
    dataset_id: int

    # 휴지통 밖 대화 수
    conversations: int

    # 휴지통에 있는 대화 수
    trashed: int

    # 턴 수(응답 수)별 대화 수 {1: 48, 2: 30, …}
    turns: dict[int, int]

    # 잰 검사들 (단계 · 등급 순이 아니라 검사 정의 순. 화면이 정렬한다)
    checks: list[CheckRead]

    # 아직 재지 못한 뜻 검사들 (뜻 분석 전 · Jev 없음). 종합 상태와 지금 단계에 세지 않는다.
    waiting: list[WaitingRead]

    # 흐름에서 지금 단계: 1 지시 · 2 응답 · 3 내보내기(남은 문제 없음)
    now_stage: int

    # 계산한 시각
    computed_at: datetime


# ---------- 대화 · 검토 ----------


class ProblemRead(BaseModel):
    """대화 안의 문제 하나: 검사와 자리(메시지 순서, 글 안 시작 · 끝)와 그 조각."""

    # 검사 이름
    key: CheckKey

    # 메시지 순서 (대화 전체에 걸친 문제면 null)
    turn: int | None

    # 글 안의 시작 · 끝 (자리가 없으면 null)
    start: int | None
    end: int | None

    # 문제 조각 (최대 몇십 자, 목록 · 검토 큐가 보인다)
    snippet: str


class MessageRead(BaseModel):
    """대화의 메시지 하나."""

    # 역할: system · user · assistant
    role: str

    # 글
    content: str


class JevRead(BaseModel):
    """뜻 분석이 이 대화를 Jev에 물은 결과 (예 확률, 묻지 않았으면 null)."""

    # 응답이 지시를 따른다
    follows: float | None

    # 거절이 마땅하다 (거절로 잡힌 대화만)
    refusal_ok: float | None

    # 지시 ↔ 응답 뜻 유사도 (지시 불일치 후보를 고른 값)
    similarity: float | None

    # Jev가 규칙의 문제(거절 · 짧은 응답)를 괜찮다고 봤는지 → 검토가 [그대로 두기]를 권한다
    looks_fine: bool


class ConversationRead(BaseModel):
    """대화 한 건: 맥락 + 마지막 응답, 남은 문제, '그대로 두기'로 둔 검사."""

    # 대화(지시) 번호
    id: int

    # 데이터셋 번호
    dataset_id: int

    # 메시지들 (맥락 · 마지막 응답까지)
    messages: list[MessageRead]

    # 출처: original · synthetic · human (응답을 LLM이 다시 썼으면 synthetic)
    source: str

    # 남은 문제 (사람이 '그대로 두기'로 둔 검사는 빼고)
    problems: list[ProblemRead]

    # 사람이 '그대로 두기'로 둔 검사들
    kept: list[CheckKey]

    # 휴지통에 있는지
    trashed: bool

    # 고칠 때마다 오르는 번호
    row_version: int

    # 원본에서 쓰지 않은 남는 필드들
    extra: dict[str, Any]

    # 뜻 분석의 Jev 판정 (분석이 없거나 묻지 않았으면 null)
    jev: JevRead | None

    # 가져오기 기록 번호 (기록을 지웠으면 null)
    import_id: int | None

    # 넣은 시각 · 마지막으로 바꾼 시각
    created_at: datetime
    updated_at: datetime


class ConversationPageRead(BaseModel):
    """대화 목록 한 쪽."""

    # 대화들
    items: list[ConversationRead]

    # 거른 전체 수
    total: int

    # 쪽 크기 · 건너뛴 수
    limit: int
    offset: int


class ReviewQueueRead(BaseModel):
    """검토 큐: 한 검사의 문제가 남은 대화 번호들(번호 순)."""

    # 검사 이름
    check: CheckKey

    # 대화 번호들 (최대 REVIEW_MAX개)
    ids: list[int]

    # 문제가 남은 전체 수
    total: int


class DecisionCreate(BaseModel):
    """검토 결정 하나: 어느 검사를 보고 무엇을 했는지."""

    # 검사 이름
    check: CheckKey

    # 결정: drop · keep · mask · cut · rewrite (rewrite는 LLM이 연결돼 있어야 한다)
    action: Action


class DecideAllCreate(BaseModel):
    """한 검사의 남은 대화 모두에 같은 결정."""

    # 검사 이름
    check: CheckKey

    # 결정: 모두 빼기 · 모두 그대로 두기
    action: Literal["drop", "keep"]

    # Jev가 괜찮다고 본 대화만 (거절 · 짧은 응답의 '그대로 두기')
    only_jev_ok: bool = False


class ChangeRead(BaseModel):
    """결정 기록 한 줄."""

    model_config = ConfigDict(from_attributes=True)

    # 결정 번호
    id: int

    # 대화 번호
    prompt_id: int

    # 검사 이름
    check_key: str | None

    # 결정
    action: str

    # 결정한 시각
    created_at: datetime

    # 되돌린 시각 (아직이면 null)
    undone_at: datetime | None


class DecisionRead(BaseModel):
    """결정한 뒤의 대화와 그 결정 기록."""

    # 결정한 뒤의 대화
    conversation: ConversationRead

    # 남긴 결정 기록
    change: ChangeRead


class ConversationBulkUpdate(BaseModel):
    """여러 대화를 한 번에 휴지통에 넣거나 되살린다."""

    # 대화 번호들
    ids: list[int] = Field(min_length=1, max_length=BULK_MAX_IDS)

    # 휴지통으로 · 되살리기
    action: Literal["trash", "restore"]


class ChangedRead(BaseModel):
    """여러 줄을 바꾼 결과."""

    # 실제로 바뀐 대화 수 (이미 그 상태였던 것은 세지 않는다)
    changed: int


class NearPartnerRead(BaseModel):
    """뜻이 가까운 짝 하나 (검토 · 대화 패널)."""

    # 짝 대화 번호
    prompt_id: int

    # 지시 벡터의 코사인 유사도
    similarity: float

    # 짝의 마지막 사용자 말 앞부분
    text: str

    # 짝이 휴지통에 있는지
    trashed: bool


# ---------- 뜻 분석 ----------


class AnalysisRead(BaseModel):
    """뜻 분석 한 번의 상태."""

    model_config = ConfigDict(from_attributes=True)

    # 분석 번호 · 데이터셋 번호
    id: int
    dataset_id: int

    # 상태: queued · running · done · failed · canceled
    status: AnalysisStatus

    # 분석한 대화 수
    conversation_count: int

    # 요약: 과제 무리 · 근접 중복 쌍 · Jev 상태
    checks: dict[str, Any]

    # 만드는 작업 번호 · 실패 이유
    job_id: int | None
    error: str | None

    # 누른 시각 · 끝난 시각
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

    # 분석 번호
    analysis_id: int

    # 대화 번호 · 좌표 · 과제 무리 · 출처 · 문제 표시(flag_names 자리의 비트)
    ids: list[int]
    x: list[float]
    y: list[float]
    clusters: list[int]
    sources: list[str]
    flags: list[int]

    # 문제 표시 비트의 이름 (자리 순서)
    flag_names: list[str]

    # 과제 무리마다 대화 수 (분석할 때)
    cluster_sizes: list[int]


class MapMatchesRead(BaseModel):
    """검색어가 든 대화 번호들 (지도 강조)."""

    ids: list[int]


class MapTextRead(BaseModel):
    """지도 점 하나의 글 (말풍선): 마지막 사용자 말 앞부분."""

    text: str


# ---------- LLM 도우미 ----------


class HelperStateRead(BaseModel):
    """데이터셋의 도우미 상태: 가장 최근 실행과, 모든 줄을 되돌린 카드 번호들."""

    # 가장 최근 실행. 없으면 null
    run: HelperRunRead | None

    # 되돌린 바꾼 카드(사건) 번호들. 화면이 그 카드의 되돌리기를 끈다.
    undone_events: list[int]


class HelperUndo(BaseModel):
    """되돌리기 입력. event_id가 있으면 그 카드만, 없으면 실행 전체."""

    event_id: int | None = None


class HelperUndoRead(BaseModel):
    """되돌린 결과."""

    # 전 값으로 돌린 대화 수
    reverted: int

    # 그 사이 다른 곳에서 고쳐 두고 건너뛴 수
    skipped: int


# ---------- 내보내기 ----------


class ExportCreate(BaseModel):
    """내보내기 입력: 만들 파일 모양들."""

    # 파일 모양 (한 번에 여럿)
    formats: list[ExportFormat] = Field(min_length=1)


class ExportFileRead(BaseModel):
    """만들 파일 하나의 미리 보기."""

    # 파일 모양
    format: ExportFormat

    # 파일 이름
    file_name: str

    # 넣을 대화 수
    conversations: int


class ExportCheckRead(BaseModel):
    """내보내기 창의 남은 문제 한 줄."""

    # 검사 이름
    key: CheckKey

    # 문제가 남은 대화 수
    count: int

    # 등급
    grade: Grade


class ExportPreviewRead(BaseModel):
    """내보내기 미리 보기: 첫 줄 · 파일마다 수 · 남은 문제 · 막힘."""

    # 첫 줄 (글은 줄여서)
    line: str

    # 파일마다
    files: list[ExportFileRead]

    # 남은 심각 · 주의
    checks: list[ExportCheckRead]

    # 심각이 남아 내보낼 수 없는지
    blocked: bool
