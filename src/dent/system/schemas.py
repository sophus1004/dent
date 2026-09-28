"""시스템 API의 입출력 모양 (Pydantic): 올린 파일, 허깅페이스 미리 보기, 데이터셋 목록, 가져오기 기록, 작업,
외부 모델 연결, 도우미 실행, 내보낸 파일.

입력은 <Name>Create·<Name>Update, 출력은 <Name>Read로 이름 짓는다. JSON 이름은 snake_case다.
입력 문자열은 앞뒤 공백을 지운 뒤 길이를 검사한다(공백만 있는 값도 걸리게).
모듈은 이 모양을 가져다 쓰거나 이어 받아도 된다(예: 분류의 가져오기 입력은 SourceCreate를 이어 받는다).
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dent.system.connections import FactValue
from dent.system.models import (
    API_KEY_MAX_LENGTH,
    DATASET_NAME_MAX_LENGTH,
    MODEL_NAME_MAX_LENGTH,
    ConnectionRole,
    ExportStatus,
    HelperEventKind,
    HelperRunStatus,
    ImportSource,
    ImportStatus,
    LlmProvider,
)

# 설명 입력의 최대 글자 수. 기준을 몇 문단 적을 수 있을 만큼.
DESCRIPTION_MAX_LENGTH = 5_000

# 허깅페이스 저장소 이름("소유자/이름")과 구성·분할 이름의 최대 길이
HF_NAME_MAX_LENGTH = 200

# 서버 주소의 최대 길이. 긴 경로가 붙은 주소도 들어가게.
BASE_URL_MAX_LENGTH = 500

# 원본 칸 값. 파일과 허깅페이스의 값을 JSON에 담을 수 있는 모양으로 바꾼 것이다.
CellValue = str | int | float | None


# ---------- 데이터셋 ----------


class DatasetRead(BaseModel):
    """데이터셋 한 건 (모든 모듈)."""

    model_config = ConfigDict(from_attributes=True)

    # 데이터셋 번호
    id: int

    # 이 데이터셋을 가진 모듈 (예: classification)
    module: str

    # 이름
    name: str

    # 설명
    description: str

    # 만든 시각
    created_at: datetime

    # 마지막으로 바뀐 시각
    updated_at: datetime


class DatasetUpdate(BaseModel):
    """데이터셋 고치기 입력. 준 값만 바꾼다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 새 이름. 같은 모듈의 다른 데이터셋과 겹치지 않아야 한다.
    name: str | None = Field(default=None, min_length=1, max_length=DATASET_NAME_MAX_LENGTH)

    # 새 설명
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


# ---------- 올린 파일 · 허깅페이스 미리 보기 ----------


class FilePreviewRead(BaseModel):
    """올린 파일의 미리 보기."""

    # 올린 파일 번호 (uuid4 16진수 32자)
    upload_id: str

    # 저장한 파일 이름
    file_name: str

    # 형식: csv(.tsv 포함) · xlsx
    format: Literal["csv", "xlsx"]

    # 파일 크기(바이트)
    size_bytes: int

    # 글자 인코딩. csv만 있다.
    encoding: Literal["utf-8", "cp949"] | None

    # 칸 구분자. csv만 있다.
    delimiter: Literal[",", "\t", ";"] | None

    # 엑셀 시트 이름들. csv면 빈 목록.
    sheets: list[str]

    # 미리 보는 시트. csv면 null.
    sheet: str | None

    # 열 이름들 (첫 줄)
    columns: list[str]

    # 앞쪽 몇 줄 {열 이름: 값}
    rows: list[dict[str, CellValue]]

    # rows 각 줄의 줄 번호. csv는 파일의 줄(칸 이름 줄이 1), 엑셀은 시트의 행 번호다.
    # 가져오기가 건너뛴 줄을 알릴 때와 같은 번호라서, 화면이 두 번호를 맞춰 보일 수 있다.
    lines: list[int]

    # 전체 데이터 줄 수 (첫 줄 제외, 빈 줄 제외)
    row_count: int | None


class HuggingFacePreviewCreate(BaseModel):
    """허깅페이스 데이터셋 미리 보기 입력."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 저장소 이름. 예: "klue/klue"
    repo: str = Field(min_length=1, max_length=HF_NAME_MAX_LENGTH)

    # 구성 이름. 없으면 첫 구성.
    config: str | None = Field(default=None, max_length=HF_NAME_MAX_LENGTH)

    # 미리 볼 분할 이름. 없으면 첫 분할.
    split: str | None = Field(default=None, max_length=HF_NAME_MAX_LENGTH)


class HuggingFaceSplitRead(BaseModel):
    """허깅페이스 데이터셋의 분할 하나."""

    # 분할 이름 (예: train)
    name: str

    # 줄 수. 모르면 null.
    num_rows: int | None


class HuggingFacePreviewRead(BaseModel):
    """허깅페이스 데이터셋의 미리 보기."""

    # 저장소 이름
    repo: str

    # 구성 이름들
    configs: list[str]

    # 미리 보는 구성
    config: str

    # 이 구성의 분할들
    splits: list[HuggingFaceSplitRead]

    # 미리 보는 분할
    split: str

    # 열 이름들
    columns: list[str]

    # 번호로 저장된 라벨 열(ClassLabel)의 이름 목록 {열 이름: [라벨 이름, ...]}
    label_names: dict[str, list[str]]

    # 앞쪽 몇 줄. 라벨 번호는 이름으로 바꿔 보여 준다.
    rows: list[dict[str, CellValue]]


# ---------- 가져오기 ----------


class SourceCreate(BaseModel):
    """가져올 곳 입력: 올린 파일이나 허깅페이스. 모듈의 가져오기 입력이 이것을 이어 받는다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 가져올 곳: file · huggingface
    source: ImportSource

    # 올린 파일 번호 (file)
    upload_id: str | None = None

    # 엑셀 시트 이름 (file, xlsx). 없으면 첫 시트.
    sheet: str | None = None

    # 허깅페이스 저장소 이름 (huggingface)
    repo: str | None = Field(default=None, max_length=HF_NAME_MAX_LENGTH)

    # 허깅페이스 구성 이름 (huggingface). 없으면 첫 구성.
    config: str | None = Field(default=None, max_length=HF_NAME_MAX_LENGTH)

    # 가져올 허깅페이스 분할들 (huggingface). null이면 전부.
    splits: list[str] | None = None


class ExamplePlantedRead(BaseModel):
    """예시 데이터에 일부러 심은 문제 하나: 검사 이름과 수 (진단에 그 수만큼 보여야 한다)."""

    # 검사 이름 (진단과 같은 낱말)
    name: str

    # 심은 수 (검사의 단위대로: 건 · 무리 · 쌍 · 문서 …)
    count: int

    # 수의 단위 (건 · 무리 · 쌍 · 문서 · 배 …)
    unit: str


class ExampleRead(BaseModel):
    """내장 예시 데이터셋 하나 (모듈의 example_data)."""

    # 예시 열쇠 (넣기 주소에 쓴다)
    key: str

    # 넣을 때 만드는 데이터셋 이름 · 설명
    name: str
    description: str

    # 예시 파일 이름 · 줄 수
    file_name: str
    rows: int

    # 일부러 심은 문제들 (없으면 빈 목록: 깨끗한 대조군)
    planted: list[ExamplePlantedRead]

    # 이미 넣었으면 그 데이터셋 번호 (같은 이름의 데이터셋)
    dataset_id: int | None


class SkippedLineRead(BaseModel):
    """가져오면서 건너뛴 줄 하나."""

    # 원본의 줄 번호. csv는 파일 줄(머리줄 = 1), 엑셀은 시트 행 번호, 허깅페이스는 1부터 센 순서.
    line: int

    # 건너뛴 이유 (예: 빈 문장)
    reason: str


class ImportRead(BaseModel):
    """가져오기 한 번의 기록."""

    model_config = ConfigDict(from_attributes=True)

    # 가져오기 번호
    id: int

    # 데이터셋 번호
    dataset_id: int

    # 가져온 곳: file · huggingface
    source: ImportSource

    # 가져온 곳 이름. 파일 이름이나 "저장소 · 구성"
    source_name: str

    # 상태: queued · running · done · failed
    status: ImportStatus

    # 처리하는 작업 번호. 진행률은 /api/v1/jobs/{job_id}로 본다.
    job_id: int | None

    # 원본 전체 줄 수. 읽기 전에는 null.
    rows_total: int | None

    # 넣은 줄 수
    rows_added: int

    # 건너뛴 줄 수
    rows_skipped: int

    # 건너뛴 줄과 이유 (앞의 몇 줄만)
    skipped_lines: list[SkippedLineRead]

    # 모듈만의 결과 (예: 분류의 new_labels)
    result: dict[str, Any]

    # 실패 이유
    error: str | None

    # 시작한 시각
    created_at: datetime

    # 끝난 시각
    finished_at: datetime | None


# ---------- 작업 ----------


class JobRead(BaseModel):
    """작업 한 건의 상태."""

    model_config = ConfigDict(from_attributes=True)

    # 작업 번호
    id: int

    # 작업을 넣은 모듈 (예: classification)
    module: str

    # 작업 종류 (예: import)
    kind: str

    # 작업이 다루는 데이터셋 번호. 없거나 지운 데이터셋이면 null
    dataset_id: int | None

    # 넣을 때의 데이터셋 이름. 데이터셋을 지웠으면 dataset_id는 null이고 이름만 남는다.
    dataset_name: str | None

    # 상태: queued · running · done · failed · canceled
    status: str

    # 처리한 양
    progress_done: int

    # 전체 양. 모르면 null
    progress_total: int | None

    # 결과 요약. 끝나기 전에는 null
    result: dict[str, Any] | None

    # 실패 이유. 사용자에게 보여줄 한국어 문장
    error: str | None

    # 끊겨서 다시 시작한 횟수
    attempts: int

    # 사용자가 멈추라고 했는지
    cancel_requested: bool

    # 일어난 일(시작 · 단계 · 다시 보내기 · 쓴 연결). 한 줄은 {"type", "at", ...}이고 시각 순이다.
    events: list[dict[str, Any]]

    # 넣은 시각
    created_at: datetime

    # 시작한 시각
    started_at: datetime | None

    # 끝난 시각
    finished_at: datetime | None


class JobCountsRead(BaseModel):
    """상태별 작업 수."""

    # 대기
    queued: int

    # 도는 중
    running: int

    # 완료
    done: int

    # 실패
    failed: int

    # 멈춤
    canceled: int


class JobPageRead(BaseModel):
    """작업 목록 한 쪽."""

    # 이 쪽의 작업들. 최근에 넣은 것부터
    items: list[JobRead]

    # 거르기에 맞는 전체 수
    total: int

    # 상태 거르기만 뺀 같은 조건의 상태별 수
    counts: JobCountsRead


# ---------- 외부 모델 연결 ----------


class ConnectionUpdate(BaseModel):
    """연결 저장 · 확인 입력."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 서버 주소. http:// 또는 https://로 시작한다. 끝의 /는 떼고 저장한다.
    base_url: str = Field(min_length=1, max_length=BASE_URL_MAX_LENGTH)

    # 모델 이름. 비우면 서버가 고른다(Jev는 자동 고르기). 임베딩은 꼭, LLM은 저장할 때 꼭 있어야 한다.
    model: str | None = Field(default=None, max_length=MODEL_NAME_MAX_LENGTH)

    # LLM 공급자. LLM이면 꼭 있어야 하고, 다른 역할이면 버린다.
    provider: LlmProvider | None = None

    # 서버 API 키. 비우면 저장된 키를 그대로 쓴다(같은 공급자 · 주소일 때만).
    api_key: str | None = Field(default=None, max_length=API_KEY_MAX_LENGTH)


class ConnectionCheckRead(BaseModel):
    """연결을 확인한 결과."""

    # 쓸 수 있는지
    ok: bool

    # 짧은 낱말 줄. 되면 사실(예: '1024차원 · 42ms'), 안 되면 까닭(예: '연결 거부')
    detail: str

    # 알아낸 값. 임베딩: dim · latency_ms, Jev: device · loaded · latency_ms · model,
    # LLM: models(이름 순) · latency_ms · model
    facts: dict[str, FactValue]


class ConnectionRead(BaseModel):
    """역할 하나의 연결과 마지막 확인 결과."""

    # 역할: embedding · jev · llm
    role: ConnectionRole

    # 실행할 때 올린 내장 모델 이름(bge-m3 · laya). 내장이면 화면에서 바꿀 수 없다. 아니면 null
    embedded: str | None

    # 서버 주소. 저장하지 않았으면 null(미연결)
    base_url: str | None

    # 모델 이름. 비어 있으면 서버가 고른다.
    model: str | None

    # LLM 공급자. LLM이 아니거나 저장하지 않았으면 null
    provider: LlmProvider | None

    # 저장된 API 키의 앞뒤 몇 글자(예: 'sk-p…rCoA'). 키 자체는 내보내지 않는다. 없으면 null
    api_key_hint: str | None

    # 마지막으로 저장한 시각. 저장하지 않았으면 null
    updated_at: datetime | None

    # 마지막 확인 결과. 저장하지 않았으면 null
    check: ConnectionCheckRead | None


# ---------- LLM 도우미 ----------


class HelperRunRead(BaseModel):
    """도우미 실행 한 번 (도우미 창의 머리와 단계 줄)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    module: str
    dataset_id: int

    # 처리하는 작업 번호. 없으면 null
    job_id: int | None

    # running · asking · done · stopped · failed
    status: HelperRunStatus

    # 단계 줄: [{key, no, title, status, facts, delta, plan}]
    steps: list[dict[str, Any]]

    # 지금 단계 번호 (0 = 계획)
    step_now: int

    # 쓴 LLM 모델 이름
    model: str | None

    # 쓴 LLM 토큰 · Jev에 물은 수 · 바꾼 문장 수 · 사람에게 넘긴 수
    llm_tokens: int
    jev_calls: int
    changed: int
    held: int

    # [멈추기]를 눌렀는지
    stop_requested: bool

    # 허락을 묻는 새 문장 만들기 계획 (asking일 때)
    permission: dict[str, Any] | None

    # 실패 · 멈춤 이유 (한국어 문장)
    error: str | None

    created_at: datetime
    finished_at: datetime | None


class HelperEventRead(BaseModel):
    """도우미 사건 하나 (도우미 창의 말 · 카드 한 줄)."""

    model_config = ConfigDict(from_attributes=True)

    id: int

    # 단계 번호 (0 = 계획)
    step: int

    # say · jev · llm · change · result · hold · permission · notice · report
    kind: HelperEventKind

    # 종류마다 다른 내용 (화면이 그대로 그린다)
    payload: dict[str, Any]

    created_at: datetime


# AI로 고치기: 한 번에 고를 수 있는 검사 열쇠의 최대 수
MAX_FIX_KEYS = 32

# AI로 고치기에 사람이 고친 더할 수: 검사마다 칸 수 · 칸 하나의 최대 수
MAX_FIX_ADD_CELLS = 200
MAX_FIX_ADD = 10_000

# 검사 하나의 더할 수 {칸 열쇠: 수}
FixAdds = Annotated[
    dict[str, Annotated[int, Field(ge=0, le=MAX_FIX_ADD)]], Field(max_length=MAX_FIX_ADD_CELLS)
]


class HelperLeftAddRead(BaseModel):
    """남은 것 줄에서 사람이 고칠 수 있는 더할 수 한 칸 (예: 라벨마다 새 문장 수)."""

    model_config = ConfigDict(from_attributes=True)

    # 칸 열쇠 (AI로 고치기의 adds에 보낸다)
    key: str

    # 칸 이름 · 지금 수 · 계획한 더할 수 · 상한
    name: str
    now: int
    add: int
    max: int


class HelperLeftItemRead(BaseModel):
    """도우미가 끝난 뒤 남은 것 한 줄 (지금 진단으로 센다)."""

    # 검사 열쇠 (AI로 고치기에 보낸다)
    key: str

    # 검사 이름 · 값 · 단위 · 작은 글
    name: str
    value: str
    unit: str
    sub: str

    # 등급 (warn · bad)
    grade: str

    # 묶음: ai(AI로 고칠 수 있음) · direct(직접 권장, 고를 수는 있다) · blocked(AI로 못 고침)
    group: Literal["ai", "direct", "blocked"]

    # AI로 고치면 하는 일 (blocked면 빈 글)
    how: str

    # 어림 비용: LLM 토큰 · Jev 호출
    tokens: int
    jev: int

    # AI로 못 고칠 때 할 일 (blocked만)
    action: str

    # → 로 갈 곳을 모듈 화면이 정하는 대상 · 문제 거르기 (모듈마다 뜻이 다르다. 없으면 빈 글)
    view_target: str
    view_problem: str

    # 사람이 고칠 수 있는 더할 수 칸들과 그 표의 작은 글 (없으면 빈 목록)
    adds: list[HelperLeftAddRead]
    adds_note: str


class HelperLeftRead(BaseModel):
    """남은 것 목록. 도우미 실행이 없으면 빈 목록."""

    items: list[HelperLeftItemRead]


class HelperFixCreate(BaseModel):
    """AI로 고치기: 고른 검사 열쇠들 (남은 것의 ai · direct 줄)과 사람이 고친 더할 수."""

    keys: list[str] = Field(min_length=1, max_length=MAX_FIX_KEYS)

    # 검사마다 {칸 열쇠: 더할 수} (남은 것 줄의 adds를 고친 값). 주지 않으면 모듈의 계획대로.
    adds: dict[str, FixAdds] = Field(default_factory=dict, max_length=MAX_FIX_KEYS)


# ---------- 내보낸 파일 ----------


class ExportRead(BaseModel):
    """내보내기 한 번 (내려받기는 /api/v1/exports/{id}/file)."""

    model_config = ConfigDict(from_attributes=True)

    id: int

    # 만든 모듈
    module: str

    # 데이터셋 번호
    dataset_id: int

    # 모듈이 정한 형식 이름
    format: str

    # 모듈이 받은 선택
    options: dict[str, Any]

    # queued · running · done · failed
    status: ExportStatus

    # 내려받을 때의 파일 이름
    file_name: str

    # 파일 크기(바이트). 만들기 전에는 null.
    size_bytes: int | None

    # 모듈만의 결과 (질의 · 판정 수 …)
    result: dict[str, Any]

    # 만드는 작업 번호 (진행률은 /api/v1/jobs/{job_id})
    job_id: int | None

    # 실패 이유
    error: str | None

    created_at: datetime
    finished_at: datetime | None

    # 파일을 지운 시각 (보관 기간이 지남)
    deleted_at: datetime | None
