"""분류 모듈의 입출력 모양 (Pydantic). JSON 이름은 snake_case이고 모두 /api/v1/classification 아래에서 쓴다.

입력은 <Name>Create·<Name>Update, 출력은 <Name>Read로 이름 짓는다.
입력 문자열은 앞뒤 공백을 지운 뒤 길이를 검사한다(공백만 있는 값도 걸리게).
데이터셋 목록·가져오기 기록·올린 파일의 모양은 시스템(dent.system.schemas)의 것을 쓴다.
"""

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from dent.modules.classification.models import (
    LABEL_NAME_MAX_LENGTH,
    TEXT_MAX_LENGTH,
    ExcludeReason,
    MapStatus,
)
from dent.system.models import DATASET_NAME_MAX_LENGTH
from dent.system.schemas import DESCRIPTION_MAX_LENGTH, HelperRunRead, SourceCreate

# 한 번에 고칠 수 있는 문장 수. 이보다 많은 일은 작업으로 돌려야 API 서버가 버틴다.
BULK_MAX_RECORDS = 1_000

# 열 이름의 최대 길이
COLUMN_NAME_MAX_LENGTH = 500

# 필드 맞춤을 짐작할 때 한 번에 받는 열 이름 수. 이보다 많은 열은 원본 표로 보기 어렵다.
MAPPING_COLUMNS_MAX = 1_000

# 새 문장 목표 배율의 범위 (1배면 모든 라벨을 가장 많은 라벨만큼, 3배를 넘으면 균형을 맞추는 뜻이 없다)
BALANCE_TARGET_RANGE = (1.0, 3.0)

# 문장 목록의 상태 거르기: 휴지통 밖 전체 · 학습에 씀 · 학습에서 뺌 · 휴지통
RecordStatusFilter = Literal["active", "included", "excluded", "trash"]

# 문장 목록의 문제 거르기: 중복 · 라벨 충돌 · 짧은 문장 · 근접 중복 · 오라벨 의심(뒤 둘은 뜻 분석 결과)
ProblemFilter = Literal["duplicate", "conflict", "short", "near_duplicate", "suspect"]

# 여러 문장에 한 번에 하는 일
BulkAction = Literal["exclude", "include", "trash", "restore", "set_label"]

# 진단 등급: 좋음 · 살펴볼 것 · 고칠 것
Grade = Literal["good", "warn", "bad"]

# 진단에서 찾은 문제 종류
ProblemKind = Literal["imbalance", "duplicate", "conflict", "short"]

# 등급 경계를 견주는 방법: le(이하) · lt(미만)
ThresholdOperator = Literal["le", "lt"]

# 등급 경계를 견주는 값: ratio(배) · rate(학습에 쓰는 문장 대비 비율 0~1) · records(건)
ThresholdUnit = Literal["ratio", "rate", "records"]


# ---------- 라벨 ----------


class LabelCreate(BaseModel):
    """라벨 만들기 입력."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 라벨 이름. 데이터셋 안에서 겹치지 않아야 한다.
    name: str = Field(min_length=1, max_length=LABEL_NAME_MAX_LENGTH)

    # 라벨 기준 설명
    description: str = Field(default="", max_length=DESCRIPTION_MAX_LENGTH)


class LabelUpdate(BaseModel):
    """라벨 고치기 입력. 준 값만 바꾼다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 새 이름
    name: str | None = Field(default=None, min_length=1, max_length=LABEL_NAME_MAX_LENGTH)

    # 새 설명
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class LabelRead(BaseModel):
    """라벨 한 건."""

    # 라벨 번호
    id: int

    # 데이터셋 번호
    dataset_id: int

    # 라벨 이름
    name: str

    # 라벨 기준 설명
    description: str

    # 학습에 쓰는(빼지 않았고 휴지통에 없는) 문장 수
    record_count: int

    # 만든 시각
    created_at: datetime


# ---------- 데이터셋 ----------


class DatasetSummaryRead(BaseModel):
    """데이터셋 목록의 한 줄."""

    # 데이터셋 번호
    id: int

    # 이름
    name: str

    # 설명
    description: str

    # 휴지통에 없는 문장 수
    record_count: int

    # 학습에 쓰는 문장 수
    included_count: int

    # 학습에서 뺀 문장 수
    excluded_count: int

    # 휴지통에 있는 문장 수
    trash_count: int

    # 라벨 수
    label_count: int

    # 이 데이터셋으로 가져오는 중인지 (가져오기가 대기 중이거나 실행 중)
    importing: bool

    # 만든 시각
    created_at: datetime

    # 마지막으로 바뀐 시각
    updated_at: datetime


class SettingsRead(BaseModel):
    """분류 데이터셋의 도우미 실행 설정."""

    model_config = ConfigDict(from_attributes=True)

    # 새 문장(라벨 균형)의 목표 배율: 가장 많은 라벨 ÷ 이 배율까지 채운다
    balance_target: float


class SettingsUpdate(BaseModel):
    """도우미 실행 설정 고치기. 준 값만 바꾼다."""

    # 새 문장의 목표 배율
    balance_target: float | None = Field(
        default=None, ge=BALANCE_TARGET_RANGE[0], le=BALANCE_TARGET_RANGE[1]
    )


class DatasetRead(DatasetSummaryRead):
    """데이터셋 하나의 자세한 정보."""

    # 라벨 목록 (이름 순)
    labels: list[LabelRead]

    # 도우미 실행 설정 (고친 적이 없으면 기본값)
    settings: SettingsRead


# ---------- 필드 맞춤 ----------


class FieldMappingRead(BaseModel):
    """열 이름으로 짐작한 필드 맞춤. 못 찾으면 null."""

    # 문장 열
    text: str | None

    # 라벨 열
    label: str | None


class MappingSuggestCreate(BaseModel):
    """필드 맞춤 짐작 입력: 원본의 열 이름들."""

    # 원본의 열 이름들 (시스템 미리 보기의 columns)
    columns: list[str] = Field(max_length=MAPPING_COLUMNS_MAX)


# ---------- 가져오기 ----------


class FieldMappingCreate(BaseModel):
    """가져올 때 쓸 필드 맞춤: 어느 열이 문장·라벨인지. 원본에 분할이 있어도 하나로 가져온다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 문장 열 이름
    text: str = Field(min_length=1, max_length=COLUMN_NAME_MAX_LENGTH)

    # 라벨 열 이름
    label: str = Field(min_length=1, max_length=COLUMN_NAME_MAX_LENGTH)


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


# ---------- 문장 ----------


class RecordUpdate(BaseModel):
    """문장 하나 고치기 입력. 준 값만 바꾼다. row_version은 불러올 때 받은 값을 그대로 보낸다."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 새 문장
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX_LENGTH)

    # 새 라벨 번호. 같은 데이터셋의 라벨이어야 한다.
    label_id: int | None = None

    # 불러올 때의 수정 번호. 그 사이 다른 곳에서 고쳤으면 409.
    row_version: int


class RecordBulkUpdate(BaseModel):
    """여러 문장에 한 번에 하는 일."""

    # 대상 문장 번호들
    record_ids: list[int] = Field(min_length=1, max_length=BULK_MAX_RECORDS)

    # 할 일: exclude · include · trash · restore · set_label
    action: BulkAction

    # set_label일 때 붙일 라벨 번호
    label_id: int | None = None


class ChangedRead(BaseModel):
    """여러 줄을 바꾼 결과."""

    # 실제로 바뀐 문장 수 (이미 그 상태였던 것은 세지 않는다)
    changed: int


class RecordRead(BaseModel):
    """문장 한 건."""

    # 문장 번호
    id: int

    # 데이터셋 번호
    dataset_id: int

    # 문장
    text: str

    # 라벨 번호
    label_id: int | None

    # 라벨 이름
    label_name: str | None

    # 학습에서 뺀 이유: manual · duplicate. null이면 학습에 쓴다.
    exclude_reason: ExcludeReason | None

    # 휴지통에 있는지
    is_trashed: bool

    # 휴지통에 넣은 시각
    trashed_at: datetime | None

    # 수정 번호. 고칠 때 그대로 돌려보낸다.
    row_version: int

    # 원본의 남는 필드들
    extra: dict[str, Any]

    # 이 문장을 넣은 가져오기 번호
    import_id: int | None

    # 만든 시각
    created_at: datetime

    # 마지막으로 고친 시각
    updated_at: datetime

    # 같은 데이터셋에서 같은 문장인 다른 문장 수 (휴지통 제외)
    duplicate_count: int

    # 같은 문장이 다른 라벨로도 있는지 (휴지통 제외)
    has_conflict: bool


class RecordPageRead(BaseModel):
    """문장 목록 한 쪽."""

    # 이 쪽의 문장들
    items: list[RecordRead]

    # 거른 조건에 맞는 전체 문장 수
    total: int

    # 한 쪽의 최대 문장 수
    limit: int

    # 건너뛴 문장 수
    offset: int


# ---------- 진단 ----------


class OverviewLabelRead(BaseModel):
    """진단의 라벨 한 줄. 학습에 쓰는 문장(included) 기준이다."""

    # 라벨 번호
    label_id: int

    # 라벨 이름
    name: str

    # 학습에 쓰는 문장 수
    included: int

    # 학습에서 뺀 문장 수
    excluded: int

    # 학습에 쓰는 전체 문장 가운데 이 라벨의 몫 (0~1)
    share: float

    # 문장 길이(글자 수) 평균. 문장이 없으면 null.
    avg_length: float | None

    # 가장 짧은 문장의 글자 수
    min_length: int | None

    # 가장 긴 문장의 글자 수
    max_length: int | None

    # 이 라벨의 중복(문장까지 같은 것) 가운데 중복 빼기로 빠질 수
    duplicate_extra: int

    # 이 라벨의 문장 가운데 같은 문장이 다른 라벨로도 있는 수
    conflict_records: int

    # 이 라벨의 짧은 문장 수
    short_records: int


class BalanceRead(BaseModel):
    """라벨 균형."""

    # 가장 많은 라벨 수 ÷ 가장 적은 라벨 수. 문장이 있는 라벨이 둘보다 적으면 null.
    ratio: float | None

    # 가장 많은 라벨 이름
    max_label: str | None

    # 가장 적은 라벨 이름
    min_label: str | None

    # 등급
    grade: Grade


class DuplicatesRead(BaseModel):
    """중복: 문장·라벨이 모두 같은 것. 라벨이 다르면 충돌로 따로 센다."""

    # 중복 무리 수 (2건 이상인 것)
    groups: int

    # 무리마다 하나만 남기면 빠질 문장 수
    extra_records: int

    # extra_records ÷ 학습에 쓰는 문장 수
    rate: float

    # 등급
    grade: Grade


class ConflictsRead(BaseModel):
    """같은 문장인데 라벨이 다른 것(라벨 충돌)."""

    # 충돌하는 문장 무리 수
    groups: int

    # 그 무리에 든 문장 수
    records: int

    # 등급
    grade: Grade


class ShortRead(BaseModel):
    """너무 짧은 문장."""

    # 이 글자 수보다 짧으면 짧은 문장으로 본다
    threshold: int

    # 짧은 문장 수
    records: int

    # 등급
    grade: Grade


class HistogramBinRead(BaseModel):
    """문장 길이 막대 하나: start 이상 end 미만의 글자 수."""

    # 시작 글자 수
    start: int

    # 끝 글자 수 (포함하지 않음). 마지막 막대는 끝이 없어 null.
    end: int | None

    # 문장 수
    count: int


class ProblemRead(BaseModel):
    """진단에서 찾은 문제 하나. 등급이 좋음이 아닌 것만 나온다."""

    # 종류: imbalance · duplicate · conflict · short
    kind: ProblemKind

    # 심각도: bad(고칠 것) · warn(살펴볼 것)
    severity: Literal["bad", "warn"]

    # 걸린 문장 수. imbalance는 가장 적은 라벨의 문장 수, duplicate는 빠질 문장 수.
    count: int

    # 관련 라벨 번호. imbalance는 가장 적은 라벨, 나머지는 null.
    label_id: int | None


class GradeStepRead(BaseModel):
    """등급 사다리의 한 칸. 위 칸부터 견주어 처음 맞는 칸의 등급이 된다."""

    # 이 칸의 등급
    grade: Grade

    # 견주는 방법. 마지막 칸은 null(앞 칸에 들지 않은 값 전부)
    op: ThresholdOperator | None = None

    # 경계 값. 마지막 칸은 null
    value: float | None = None


class ThresholdRead(BaseModel):
    """검사 하나의 등급 경계. 진단이 등급을 매길 때 쓰는 값 그대로다."""

    # 견주는 값의 단위
    unit: ThresholdUnit

    # 등급 사다리 (좋음부터)
    steps: list[GradeStepRead]


class ThresholdsRead(BaseModel):
    """검사마다 등급 경계. 화면이 기준을 따로 적지 않고 이것을 보여 준다."""

    # 라벨 균형: 가장 많은 라벨 ÷ 가장 적은 라벨 (배)
    balance: ThresholdRead

    # 중복: 빠질 문장 ÷ 학습에 쓰는 문장
    duplicates: ThresholdRead

    # 라벨 충돌: 충돌 무리의 문장 ÷ 학습에 쓰는 문장
    conflicts: ThresholdRead

    # 짧은 문장: 짧은 문장 수
    short: ThresholdRead


class DuplicateExampleRead(BaseModel):
    """중복 무리 하나의 보기."""

    # 문장 (무리에서 번호가 가장 작은 것)
    text: str

    # 라벨 이름
    label_name: str | None

    # 무리의 문장 수 (원본 포함)
    copies: int


class ExampleLabelRead(BaseModel):
    """보기 문장에 붙은 라벨 하나와 그 수."""

    # 라벨 번호
    label_id: int | None

    # 라벨 이름
    name: str | None

    # 이 라벨이 붙은 문장 수
    count: int


class ConflictExampleRead(BaseModel):
    """라벨 충돌 무리 하나의 보기."""

    # 문장 (무리에서 번호가 가장 작은 것)
    text: str

    # 붙은 라벨과 수 (많은 순)
    labels: list[ExampleLabelRead]


class ShortExampleRead(BaseModel):
    """짧은 문장 하나."""

    # 문장 번호
    record_id: int

    # 문장
    text: str

    # 글자 수
    length: int


class ExamplesRead(BaseModel):
    """문제마다 보기 (화면의 '대상' 칸). 문제가 없으면 null·빈 목록.

    같은 문장 무리는 해시 순으로 첫 무리다. 데이터 탭을 그 문제로 거르면 맨 위에 오는 무리와 같다.
    """

    # 중복 무리
    duplicate: DuplicateExampleRead | None

    # 라벨 충돌 무리
    conflict: ConflictExampleRead | None

    # 짧은 문장 앞의 몇 건 (번호 순)
    short: list[ShortExampleRead]


class SemanticRead(BaseModel):
    """임베딩이 필요한 분석(근접 중복·의미 쏠림·오라벨 의심)을 볼 수 있는지."""

    # 볼 수 있는지
    available: bool

    # 사람이 읽을 한 줄
    detail: str


class OverviewRead(BaseModel):
    """데이터셋 진단. 휴지통에 없는 문장만 세고, 'included'는 학습에서 빼지 않은 문장이다."""

    # 데이터셋 번호
    dataset_id: int

    # 휴지통에 없는 문장 수
    total: int

    # 학습에 쓰는 문장 수
    included: int

    # 학습에서 뺀 문장 수
    excluded: int

    # 휴지통에 있는 문장 수
    trashed: int

    # 학습에 쓰는 문장 가운데 서로 다른 문장 수 (같은 문장은 라벨이 달라도 하나로 센다)
    effective_count: int

    # 라벨별 진단 (학습에 쓰는 문장이 많은 순)
    labels: list[OverviewLabelRead]

    # 라벨 균형
    balance: BalanceRead

    # 중복
    duplicates: DuplicatesRead

    # 라벨 충돌
    conflicts: ConflictsRead

    # 짧은 문장
    short: ShortRead

    # 문장 길이 분포
    length_histogram: list[HistogramBinRead]

    # 찾은 문제 (고칠 것 먼저, 그다음 많은 순)
    problems: list[ProblemRead]

    # 검사마다 등급 경계
    thresholds: ThresholdsRead

    # 문제마다 보기
    examples: ExamplesRead

    # 임베딩 분석을 볼 수 있는지
    semantic: SemanticRead

    # 계산한 시각
    computed_at: datetime


# ---------- 의미 지도 ----------


class MapRead(BaseModel):
    """의미 지도 만들기 한 번."""

    # 지도 번호
    id: int

    # 상태: queued · running · done · failed · canceled
    status: MapStatus

    # 쓴 임베딩 모델 이름. 작업이 시작하기 전에는 null
    model_name: str | None

    # 좌표를 만든 방법과 값 (method · n_neighbors · min_dist · metric · random_state …)
    params: dict[str, Any]

    # 지도에 놓은 서로 다른 문장 수
    text_count: int

    # 점 수 (문장 한 건이 점 하나)
    point_count: int

    # 만들기 시작할 때 휴지통 밖 문장 수
    record_count: int

    # 이 지도를 만드는 작업 번호. 진행률은 /api/v1/jobs/{job_id}로 본다.
    job_id: int | None

    # 실패 이유 (한국어 문장)
    error: str | None

    # 만들기를 누른 시각
    created_at: datetime

    # 끝난 시각
    finished_at: datetime | None


class NearDuplicateCountsRead(BaseModel):
    """근접 중복 수."""

    # 쌍 수
    pairs: int

    # 쌍에 든 서로 다른 문장 수
    texts: int

    # 라벨이 다른 쌍
    label_mismatch: int


class SuspectCountsRead(BaseModel):
    """오라벨 의심 수. 판단(수락 · 유지)은 지금 값이다."""

    # 이번 뜻 분석에서 찾은 수
    found: int

    # 아직 판단하지 않은 수
    open: int

    # 그 가운데 Jev도 확인한 수
    confirmed_open: int

    # 수락한 수
    accepted: int

    # 유지한 수
    kept: int

    # Jev에 물은 수
    judged: int

    # 문장이 적어(5개 미만) 재지 못한 라벨 수
    unjudged_labels: int

    # 대기 수 ÷ 분석한 문장 수 (등급을 매기는 값, 0~1)
    open_rate: float

    # 라벨 번호 → 그 라벨의 대기 수. 대기가 없는 라벨은 빠진다(JSON 열쇠는 글자).
    open_by_label: dict[int, int]


class JevCheckRead(BaseModel):
    """뜻 분석 때의 Jev 판정 상태."""

    # judged(물음) · not_connected(미연결) · failed(실패) · canceled(멈춤)
    status: str

    # 낱말로 된 까닭 · 건수. 예: '16건', '미연결'
    detail: str


class SkewLabelRead(BaseModel):
    """라벨 하나의 의미 쏠림."""

    # 라벨 번호와 지금 이름
    label_id: int
    label_name: str

    # 라벨의 서로 다른 문장 수
    text_count: int

    # 나눈 무리 수
    cluster_count: int

    # 가장 큰 무리의 비율 (0~1)
    largest_share: float

    # 무리 크기의 고르기 (1이면 고름)
    evenness: float

    # 쏠림인지 (가장 큰 무리 ≥ 기준)
    is_skewed: bool

    # 가장 큰 무리의 대표 문장
    examples: list[str]


class SemanticGradesRead(BaseModel):
    """뜻 검사 세 가지의 등급. 진단의 종합 상태가 글자 검사와 함께 센다."""

    # 오라벨 의심: 대기 비율 < suspect_good_rate 통과 · < suspect_warn_rate 주의 · 그 위 심각
    suspect: Grade

    # 근접 중복: 라벨이 다른 쌍이 있으면 주의
    near_duplicate: Grade

    # 의미 쏠림: 쏠린 라벨이 있으면 주의
    skew: Grade


class MapChecksRead(BaseModel):
    """다 만든 뜻 분석의 요약: 근접 중복 · 오라벨 의심 · 의미 쏠림."""

    # 쓴 기준값 (near_duplicate_similarity · suspect_label_probability · … · skew_largest_share)
    # 와 등급 경계 (suspect_good_rate · suspect_warn_rate)
    thresholds: dict[str, float]

    near_duplicates: NearDuplicateCountsRead

    suspects: SuspectCountsRead

    jev: JevCheckRead

    # 쏠림을 잰 라벨들 (이름 순)
    skews: list[SkewLabelRead]

    # 문장이 적어 쏠림을 재지 못한 라벨 수
    unmeasured_labels: int

    # 검사마다 등급
    grades: SemanticGradesRead


class MapStateRead(BaseModel):
    """데이터셋의 뜻 분석(의미 지도 포함) 상태."""

    # 점이 있는 가장 최근 지도(다 만든 것). 없으면 null
    map: MapRead | None

    # map보다 나중에 누른 시도: 만드는 중(queued · running)이거나 실패 · 취소한 것. 없으면 null
    run: MapRead | None

    # map을 만든 뒤 문장이 바뀌었는지 (휴지통 밖 문장 수나 데이터셋 수정 시각이 달라짐)
    outdated: bool

    # map의 뜻 분석 요약. 지도가 없거나 뜻 분석 전에 만든 지도면 null
    checks: MapChecksRead | None


class MapLabelRead(BaseModel):
    """지도 점의 라벨 번호를 이름으로 바꾸는 목록의 한 줄."""

    # 라벨 번호
    id: int

    # 라벨 이름
    name: str


class MapPointsRead(BaseModel):
    """의미 지도의 점들. 점 수만큼 긴 목록을 칸마다 따로 둔다(줄마다 사전을 두면 몇 배 커진다).

    i번째 점 = (record_ids[i], x[i], y[i], label_ids[i], flags[i]).
    라벨 · 표시는 지도를 만든 때가 아니라 지금 값이다. 휴지통에 있는 문장은 빠진다.
    """

    # 지도 번호
    map_id: int

    # 문장 번호 (작은 것부터)
    record_ids: list[int]

    # 가로 좌표 [-1, 1]
    x: list[float]

    # 세로 좌표 [-1, 1]
    y: list[float]

    # 라벨 번호. 라벨이 지워진 문장은 null
    label_ids: list[int | None]

    # 표시 비트: 1 중복 · 2 라벨 충돌 · 8 짧은 문장 · 16 학습 제외 (4는 비움)
    flags: list[int]

    # 라벨 번호 → 이름 (이름 순)
    labels: list[MapLabelRead]


class MapMatchesRead(BaseModel):
    """검색어에 맞는 문장 번호들. 지도에서 검색어로 점을 강조할 때 쓴다."""

    # 휴지통 밖에서 검색어가 든 문장 번호 (작은 것부터)
    record_ids: list[int]


# 오라벨 의심 목록의 판단 거르기: 아직 · 수락 · 유지 · 전부
SuspectDecisionFilter = Literal["open", "accepted", "kept", "all"]


class SuspectRead(BaseModel):
    """오라벨 의심 한 건 (제안 탭의 한 줄)."""

    # 문장 해시 (수락 · 유지할 때 주소에 쓴다)
    text_hash: str

    # 문장 (같은 문장이면 가장 먼저 들어온 것)
    text: str

    # 이 문장 · 라벨인 휴지통 밖 문장 수 (수락하면 이만큼 바뀐다)
    record_count: int

    # 지금 라벨
    label: MapLabelRead

    # 추천 라벨 (분류기)
    suggested_label: MapLabelRead

    # 분류기가 매긴 지금 라벨 · 추천 라벨 확률
    label_probability: float
    suggested_probability: float

    # Jev가 고른 라벨. 묻지 않았으면 null
    jev_label: MapLabelRead | None

    # Jev가 매긴 지금 라벨 확률과 확신도
    jev_label_probability: float | None
    jev_confidence: float | None

    # Jev도 지금 라벨이 아니라고 봤는지
    is_confirmed: bool

    # 사람의 판단: accepted · kept. 아직이면 null
    decision: str | None

    # 판단한 시각
    decided_at: datetime | None


class SuspectPageRead(BaseModel):
    """오라벨 의심 목록 한 쪽."""

    items: list[SuspectRead]

    # 거르기에 맞는 전체 수
    total: int


class SuspectAccept(BaseModel):
    """오라벨 의심 수락 입력. 라벨을 주지 않으면 추천 라벨로 바꾼다."""

    # 바꿀 라벨 번호. 같은 데이터셋의 라벨이어야 한다.
    label_id: int | None = None


class NearDuplicateRead(BaseModel):
    """한 문장과 근접 중복인 다른 문장."""

    # 문장 번호 (같은 문장이면 가장 먼저 들어온 것)
    record_id: int

    # 문장
    text: str

    # 라벨 이름. 없으면 null
    label_name: str | None

    # 두 문장의 코사인 유사도
    similarity: float


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

    # 전 값으로 돌린 문장 수
    reverted: int

    # 그 사이 다른 곳에서 고쳐 두고 건너뛴 수
    skipped: int


# ---------- 내보내기 ----------

# 내보내기 파일 모양: 학습 jsonl · 학습 표(csv) · 라벨 목록(json)
ExportFormat = Literal["train_jsonl", "train_table", "labels"]

# 문장의 출처: 원본 · 도우미가 만든 새 문장
RecordSource = Literal["original", "synthetic"]


class ExportCreate(BaseModel):
    """내보내기 시작: 파일 모양들 · 넣을 출처 · 학습 표의 출처 칸."""

    # 만들 파일 모양 (고른 모양마다 파일 하나)
    formats: list[ExportFormat] = Field(min_length=1, max_length=len(get_args(ExportFormat)))

    # 넣을 문장의 출처
    sources: list[RecordSource] = Field(
        default_factory=lambda: list(get_args(RecordSource)), min_length=1
    )

    # 학습 표에 출처 칸(source: original · synthetic)을 넣을지. jsonl에는 늘 넣는다.
    source_column: bool = True


class ExportFileRead(BaseModel):
    """내보낼 파일 하나의 수."""

    # 파일 모양
    format: ExportFormat

    # 파일 이름
    file_name: str

    # 넣을 문장 수 (라벨 목록은 0)
    records: int

    # 라벨 수
    labels: int


class ExportCheckRead(BaseModel):
    """내보낼 때 남은 검사 하나. 막지 않고 보이기만 한다."""

    # 검사 열쇠 (conflict · suspect · short · duplicate · near_duplicate · balance · skew)
    key: str

    # 검사 이름
    name: str

    # 보이는 값 (단위까지. 예: '20건', '3.8배')
    value: str

    # 등급
    grade: Literal["bad", "warn"]


class ExportPreviewRead(BaseModel):
    """내보내기 미리 보기: 첫 줄 · 파일마다 수 · 출처별 문장 수 · 빠지는 문장 · 남은 검사."""

    # 학습 jsonl의 첫 줄 (고른 대로)
    line: str

    # 고른 파일마다 수
    files: list[ExportFileRead]

    # 출처마다 학습에 쓰는 문장 수 (넣을 출처 칸)
    source_counts: dict[str, int]

    # 넣지 않는 문장: 학습에서 뺀 것 · 라벨이 없는 것 (휴지통은 늘 뺀다)
    excluded: int
    unlabeled: int

    # 남은 심각 · 주의 (막지 않는다)
    checks: list[ExportCheckRead]
