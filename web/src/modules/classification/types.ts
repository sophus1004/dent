// 분류 모듈 API의 입출력 모양. 백엔드(src/dent/modules/classification/schemas.py)와 이름·모양이 같다.
// JSON 이름은 snake_case 그대로 쓴다. 시각은 ISO 문자열이다.
// 올린 파일·허깅페이스 미리 보기·데이터셋 목록·가져오기 기록의 모양은 system/types.ts에 있다.
import type { HelperRunRead, SourceCreate } from '@/system/types'

// ---------- 공통 값 ----------

// 건강 등급
export type Grade = 'good' | 'warn' | 'bad'

// 한 번에 바꾼 건수 (bulk, cleanup)
export interface ChangedRead {
  changed: number
}

// ---------- 라벨 ----------

export interface LabelRead {
  id: number
  dataset_id: number
  name: string
  description: string
  // 학습에 넣은(제외되지 않고 휴지통에 없는) 건수
  record_count: number
  created_at: string
}

// POST /classification/datasets/{dataset_id}/labels
export interface LabelCreate {
  name: string
  description?: string
}

// PATCH /classification/labels/{label_id}
export interface LabelUpdate {
  name?: string
  description?: string
}

// ---------- 데이터셋 ----------

// 목록에 보일 데이터셋 한 줄
export interface DatasetSummaryRead {
  id: number
  name: string
  description: string
  // 휴지통에 없는 건수
  record_count: number
  // 학습에 넣은 건수
  included_count: number
  // 학습에서 뺀 건수
  excluded_count: number
  // 휴지통에 있는 건수
  trash_count: number
  label_count: number
  // 이 데이터셋의 가져오기가 기다리거나 도는 중인지
  importing: boolean
  created_at: string
  updated_at: string
}

// 데이터셋 하나를 자세히
export interface DatasetRead extends DatasetSummaryRead {
  // 이름 순
  labels: LabelRead[]
  // 도우미 실행 설정 (고친 적이 없으면 기본값)
  settings: SettingsRead
}

// 분류 데이터셋의 도우미 실행 설정
export interface SettingsRead {
  // 새 문장(라벨 균형)의 목표 배율: 가장 많은 라벨 ÷ 이 배율까지 채운다
  balance_target: number
}

// PATCH /datasets/{id}/settings
export type SettingsUpdate = Partial<SettingsRead>

// ---------- 필드 맞춤 ----------

// 가져올 곳의 칸 이름을 DENT의 칸(문장·라벨)에 맞춘 추천. 못 찾으면 null.
export interface SuggestedMapping {
  text: string | null
  label: string | null
}

// POST /classification/mapping/suggest. 시스템 미리 보기의 columns를 넘긴다.
export interface MappingSuggestCreate {
  columns: string[]
}

// ---------- 가져오기 ----------

// 가져오기 3단계(필드 맞추기)에서 고르는 중인 칸 이름. 아직 고르지 않았으면 null.
// text·label을 모두 고르면 ImportMapping이 된다.
export interface MappingDraft {
  text: string | null
  label: string | null
}

// 가져오기 기록 result에 이 가져오기에서 새로 만든 라벨 이름들을 담는 열쇠 (백엔드 importing.py와 같다)
export const NEW_LABELS_KEY = 'new_labels'

// 가져올 곳의 칸 이름: 문장 칸 · 라벨 칸. 둘 다 꼭 고른다.
export interface ImportMapping {
  text: string
  label: string
}

// POST /classification/imports. 가져올 곳(SourceCreate)에 데이터셋과 칸 맞춤을 더한다.
// dataset_id와 new_dataset_name 중 하나만 채운다. 답은 system/types.ts의 ImportRead다.
export interface ImportCreate extends SourceCreate {
  dataset_id: number | null
  new_dataset_name: string | null
  mapping: ImportMapping
}

// ---------- 레코드 ----------

// 학습에서 뺀 이유: 직접 뺌, 중복 정리로 뺌, 도우미가 뺌
export type ExcludeReason = 'manual' | 'duplicate' | 'helper'

// 목록의 상태 거르기. active = 휴지통에 없는 것 전부
export type RecordStatus = 'active' | 'included' | 'excluded' | 'trash'

// 목록의 문제 거르기. near_duplicate · suspect는 다 만든 뜻 분석의 결과로 거른다.
export type RecordProblem = 'duplicate' | 'conflict' | 'short' | 'near_duplicate' | 'suspect'

// 레코드의 extra 칸: 가져올 때 쓰지 않은 나머지 칸들
export type RecordExtra = Record<string, string | number | null>

export interface RecordRead {
  id: number
  dataset_id: number
  text: string
  label_id: number | null
  label_name: string | null
  // null이면 학습에 넣은 것
  exclude_reason: ExcludeReason | null
  is_trashed: boolean
  trashed_at: string | null
  // 고칠 때 함께 보낸다. 다른 곳에서 먼저 고쳤으면 409.
  row_version: number
  extra: RecordExtra
  import_id: number | null
  created_at: string
  updated_at: string
  // 글자까지 같은 문장(휴지통 제외)이 이것 말고 몇 건 더 있는지. 라벨이 달라도 센다.
  duplicate_count: number
  // 같은 문장에 다른 라벨이 달린 것이 있는지
  has_conflict: boolean
}

// GET /classification/datasets/{dataset_id}/records 의 답
export interface RecordPageRead {
  items: RecordRead[]
  total: number
  limit: number
  offset: number
}

// GET /classification/datasets/{dataset_id}/records 의 거르기. 빈 값은 보내지 않는다.
export interface RecordQuery {
  // 기본 active
  status?: RecordStatus
  label_id?: number | null
  problem?: RecordProblem | null
  // 문장 속 글자 찾기(대소문자 무시)
  q?: string | null
  // 이 번호의 문장과 글자까지 같은 문장만
  same_as?: number | null
  // 1~200, 기본 50
  limit?: number
  offset?: number
}

// PATCH /classification/records/{record_id}
export interface RecordUpdate {
  text?: string
  label_id?: number
  // 읽을 때 받은 row_version
  row_version: number
}

export type BulkAction = 'exclude' | 'include' | 'trash' | 'restore' | 'set_label'

// POST /classification/datasets/{dataset_id}/records/bulk
export interface BulkUpdate {
  // 1~1000건
  record_ids: number[]
  action: BulkAction
  // set_label일 때
  label_id?: number
}

// ---------- 진단(overview) ----------

// 라벨 하나의 모습
export interface OverviewLabel {
  label_id: number
  name: string
  // 학습에 넣은 건수
  included: number
  // 학습에서 뺀 건수
  excluded: number
  // 학습에 넣은 것 가운데 이 라벨의 몫(0~1)
  share: number
  // 글자 수. 학습에 넣은 것이 없으면 null
  avg_length: number | null
  min_length: number | null
  max_length: number | null
  // 중복(문장·라벨까지 같은 것) 가운데 중복 빼기로 빠질 건수
  duplicate_extra: number
  // 같은 문장에 다른 라벨이 달린 건수
  conflict_records: number
  // 짧은 문장 건수
  short_records: number
}

// 라벨 균형: 가장 많은 라벨이 가장 적은 라벨의 몇 배인지
export interface BalanceStat {
  // 학습에 넣은 것이 있는 라벨이 둘보다 적으면 null
  ratio: number | null
  max_label: string | null
  min_label: string | null
  grade: Grade
}

// 중복: 문장·라벨이 모두 같은 것. 라벨이 다르면 충돌로 따로 센다.
export interface DuplicateStat {
  groups: number
  // 묶음마다 한 건을 뺀 나머지의 합. 중복 빼기가 빼는 건수와 같다.
  extra_records: number
  // extra_records / 학습에 넣은 건수
  rate: number
  grade: Grade
}

// 같은 문장인데 라벨이 다른 것
export interface ConflictStat {
  groups: number
  records: number
  grade: Grade
}

// 너무 짧은 문장
export interface ShortStat {
  // 이 글자 수보다 짧으면 짧은 문장 (5)
  threshold: number
  records: number
  grade: Grade
}

// 글자 수 분포의 막대 하나. 마지막 막대는 end가 null(그 이상)
export interface HistogramBin {
  start: number
  end: number | null
  count: number
}

export type ProblemKind = 'imbalance' | 'duplicate' | 'conflict' | 'short'

// 손볼 곳 하나. 심각한 것(bad)이 먼저, 같으면 건수가 큰 것이 먼저 온다.
export interface OverviewProblem {
  kind: ProblemKind
  severity: 'bad' | 'warn'
  count: number
  // 한 라벨의 문제일 때 그 라벨
  label_id: number | null
}

// 등급 사다리의 한 칸. 위 칸부터 견주어 처음 맞는 칸의 등급이 된다.
export interface GradeStep {
  grade: Grade
  // le = 이하, lt = 미만. 마지막 칸은 null(앞 칸에 들지 않은 값 전부)
  op: 'le' | 'lt' | null
  value: number | null
}

// 검사 하나의 등급 경계. 백엔드가 등급을 매길 때 쓰는 값 그대로다.
export interface Threshold {
  // ratio = 배, rate = 학습 포함 대비 비율(0~1), records = 건
  unit: 'ratio' | 'rate' | 'records'
  // 좋음부터
  steps: GradeStep[]
}

export interface Thresholds {
  balance: Threshold
  duplicates: Threshold
  conflicts: Threshold
  short: Threshold
}

// 중복 무리 하나의 보기
export interface DuplicateExample {
  text: string
  label_name: string | null
  // 무리의 문장 수 (원본 포함)
  copies: number
}

// 보기 문장에 붙은 라벨 하나와 그 수
export interface ExampleLabel {
  label_id: number | null
  name: string | null
  count: number
}

export interface ConflictExample {
  text: string
  // 많은 순
  labels: ExampleLabel[]
}

export interface ShortExample {
  record_id: number
  text: string
  length: number
}

// 문제마다 보기. 같은 문장 무리는 데이터 탭을 그 문제로 거르면 맨 위에 오는 무리다.
export interface OverviewExamples {
  duplicate: DuplicateExample | null
  conflict: ConflictExample | null
  // 번호 순으로 앞의 몇 건
  short: ShortExample[]
}

// 임베딩이 필요한 분석(근접 중복·의미 쏠림·오라벨 의심)을 할 수 있는지
export interface SemanticStatus {
  available: boolean
  // 화면에 그대로 보여 줄 한 줄
  detail: string
}

// GET /classification/datasets/{dataset_id}/overview. 모든 수는 휴지통에 없는 것만 센다.
export interface OverviewRead {
  dataset_id: number
  total: number
  included: number
  excluded: number
  trashed: number
  // 학습에 넣은 것 가운데 서로 다른 문장 수 (같은 문장은 라벨이 달라도 하나로 센다)
  effective_count: number
  // 학습에 넣은 건수가 많은 라벨부터
  labels: OverviewLabel[]
  balance: BalanceStat
  duplicates: DuplicateStat
  conflicts: ConflictStat
  short: ShortStat
  length_histogram: HistogramBin[]
  problems: OverviewProblem[]
  // 검사마다 등급 경계 (진단 표의 '기준' 칸)
  thresholds: Thresholds
  // 문제마다 보기 (진단 표의 '대상' 칸)
  examples: OverviewExamples
  semantic: SemanticStatus
  computed_at: string
}

// ---------- 의미 지도 · 뜻 분석 ----------

// 의미 지도 만들기(뜻 분석 한 번)의 상태
export type MapStatus = 'queued' | 'running' | 'done' | 'failed' | 'canceled'

// 뜻 분석(의미 지도 만들기) 한 번
export interface MapRead {
  id: number
  status: MapStatus
  // 쓴 임베딩 모델 이름. 작업이 시작하기 전에는 null
  model_name: string | null
  // 좌표를 만든 방법과 값 (method · n_neighbors · min_dist · metric · random_state …)
  params: Record<string, unknown>
  // 지도에 놓은 서로 다른 문장 수
  text_count: number
  // 점 수 (문장 한 건이 점 하나)
  point_count: number
  // 만들기 시작할 때 휴지통 밖 문장 수
  record_count: number
  // 진행률은 system/jobs.ts의 useJob으로 본다.
  job_id: number | null
  // 실패 이유 (한국어 문장)
  error: string | null
  created_at: string
  finished_at: string | null
}

// GET · POST /classification/datasets/{dataset_id}/map, POST …/map/cancel
export interface MapStateRead {
  // 점이 있는 가장 최근 지도(다 만든 것)
  map: MapRead | null
  // map보다 나중에 누른 시도: 만드는 중(queued · running) · 실패 · 취소
  run: MapRead | null
  // map을 만든 뒤 문장이 바뀌었는지
  outdated: boolean
  // map의 뜻 분석 요약. 지도가 없거나 뜻 분석 전에 만든 지도면 null
  checks: MapChecksRead | null
}

// 근접 중복 수
export interface NearDuplicateCounts {
  pairs: number
  // 쌍에 든 서로 다른 문장 수
  texts: number
  // 라벨이 다른 쌍
  label_mismatch: number
}

// 오라벨 의심 수. 판단(수락 · 유지)은 지금 값이다.
export interface SuspectCounts {
  // 이번 뜻 분석에서 찾은 수
  found: number
  // 아직 판단하지 않은 수
  open: number
  // 그 가운데 Jev도 확인한 수
  confirmed_open: number
  accepted: number
  kept: number
  // Jev에 물은 수
  judged: number
  // 문장이 적어(5개 미만) 재지 못한 라벨 수
  unjudged_labels: number
  // 대기 수 ÷ 분석한 문장 수 (등급을 매기는 값, 0~1)
  open_rate: number
  // 라벨 번호 → 그 라벨의 대기 수. 대기가 없는 라벨은 빠진다.
  open_by_label: Record<string, number>
}

// 뜻 분석 때의 Jev 판정 상태
export type JevCheckStatus = 'judged' | 'not_connected' | 'failed' | 'canceled'

export interface JevCheck {
  status: JevCheckStatus
  // 낱말로 된 까닭 · 건수. 예: '16건', '미연결'
  detail: string
}

// 라벨 하나의 의미 쏠림
export interface SkewLabel {
  label_id: number
  label_name: string
  // 라벨의 서로 다른 문장 수
  text_count: number
  // 나눈 무리 수
  cluster_count: number
  // 가장 큰 무리의 비율 (0~1)
  largest_share: number
  // 무리 크기의 고르기 (1이면 고름)
  evenness: number
  // 가장 큰 무리 ≥ 기준
  is_skewed: boolean
  // 가장 큰 무리의 대표 문장
  examples: string[]
}

// 뜻 분석에 쓴 기준값과 등급 경계
export interface MapCheckThresholds {
  near_duplicate_similarity: number
  suspect_label_probability: number
  suspect_suggested_probability: number
  jev_confirm_label_probability: number
  skew_largest_share: number
  // 오라벨 의심 대기 비율: 이 값 미만 통과, suspect_warn_rate 미만 주의, 그 위 심각
  suspect_good_rate: number
  suspect_warn_rate: number
}

// 뜻 검사 세 가지의 등급 (종합 상태가 글자 검사와 함께 센다)
export interface SemanticGrades {
  suspect: Grade
  // 라벨이 다른 쌍이 있으면 주의
  near_duplicate: Grade
  // 쏠린 라벨이 있으면 주의
  skew: Grade
}

// 다 만든 뜻 분석의 요약
export interface MapChecksRead {
  thresholds: MapCheckThresholds
  near_duplicates: NearDuplicateCounts
  suspects: SuspectCounts
  jev: JevCheck
  // 쏠림을 잰 라벨들 (이름 순)
  skews: SkewLabel[]
  // 문장이 적어 쏠림을 재지 못한 라벨 수
  unmeasured_labels: number
  grades: SemanticGrades
}

// 지도 점의 라벨 번호 → 이름
export interface MapLabel {
  id: number
  name: string
}

// GET /classification/datasets/{dataset_id}/map/points. i번째 점 = 각 목록의 i번째 값.
// 라벨 · 표시는 지금 값이고, 휴지통에 있는 문장은 빠진다.
export interface MapPointsRead {
  map_id: number
  // 작은 번호부터
  record_ids: number[]
  // [-1, 1]
  x: number[]
  y: number[]
  // 라벨이 지워진 문장은 null
  label_ids: (number | null)[]
  // 표시 비트 (map/flags.ts의 FLAG)
  flags: number[]
  // 이름 순
  labels: MapLabel[]
}

// GET /classification/datasets/{dataset_id}/map/matches?q=
export interface MapMatchesRead {
  // 휴지통 밖에서 검색어가 든 문장 번호
  record_ids: number[]
}

// ---------- 제안: 오라벨 의심 ----------

// 사람의 판단
export type SuspectDecision = 'accepted' | 'kept'

// GET …/suspects의 decision 거르기. open = 아직 판단하지 않은 것
export type SuspectDecisionFilter = 'open' | SuspectDecision | 'all'

// 오라벨 의심 한 건
export interface SuspectRead {
  // 수락 · 유지할 때 주소에 쓴다
  text_hash: string
  text: string
  // 이 문장 · 라벨인 휴지통 밖 문장 수 (수락하면 이만큼 바뀐다)
  record_count: number
  label: MapLabel
  // 분류기의 추천 라벨
  suggested_label: MapLabel
  // 분류기가 매긴 지금 라벨 · 추천 라벨 확률
  label_probability: number
  suggested_probability: number
  // Jev가 고른 라벨. 묻지 않았으면 null
  jev_label: MapLabel | null
  jev_label_probability: number | null
  jev_confidence: number | null
  // Jev도 지금 라벨이 아니라고 봤는지
  is_confirmed: boolean
  decision: SuspectDecision | null
  decided_at: string | null
}

// GET /classification/datasets/{dataset_id}/suspects
export interface SuspectPageRead {
  items: SuspectRead[]
  total: number
}

// GET …/suspects의 거르기
export interface SuspectQuery {
  // 기본 open
  decision?: SuspectDecisionFilter
  // Jev 확인만
  confirmed?: boolean
  // 1~200, 기본 50
  limit?: number
  offset?: number
}

// POST …/suspects/{text_hash}/accept. 라벨을 주지 않으면 추천 라벨로 바꾼다.
export interface SuspectAccept {
  label_id?: number
}

// GET /classification/records/{record_id}/near-duplicates의 한 줄
export interface NearDuplicateRead {
  // 같은 문장이면 가장 먼저 들어온 것
  record_id: number
  text: string
  label_name: string | null
  // 코사인 유사도
  similarity: number
}

// ---------- LLM 도우미 ----------

// GET /classification/datasets/{dataset_id}/helper
export interface HelperStateRead {
  // 가장 최근 실행. 없으면 null
  run: HelperRunRead | null
  // 모든 줄을 되돌린 바꾼 카드 번호들
  undone_events: number[]
}

// POST /classification/helper/{run_id}/undo 의 답
export interface HelperUndoRead {
  reverted: number
  // 그 사이 다른 곳에서 고쳐 두고 건너뛴 수
  skipped: number
}

// ---------- 내보내기 ----------

// 파일 모양: 학습 jsonl · 학습 표(csv) · 라벨 목록(json)
export type ExportFormat = 'train_jsonl' | 'train_table' | 'labels'

// 문장의 출처: 원본 · 도우미가 만든 새 문장
export type RecordSource = 'original' | 'synthetic'

// POST /datasets/{id}/exports(/preview)
export interface ExportCreate {
  formats: ExportFormat[]
  sources: RecordSource[]
  // 학습 표에 출처 칸(source)을 넣을지. jsonl에는 늘 들어간다
  source_column: boolean
}

// 내보낼 파일 하나의 수
export interface ExportFileRead {
  format: ExportFormat
  file_name: string
  // 넣을 문장 수 (라벨 목록은 0)
  records: number
  labels: number
}

// 내보낼 때 남은 검사 하나 (막지 않고 보이기만)
export interface ExportCheckRead {
  key: string
  name: string
  // 보이는 값 (단위까지)
  value: string
  grade: 'bad' | 'warn'
}

// 내보내기 미리 보기
export interface ExportPreviewRead {
  // 학습 jsonl의 첫 줄
  line: string
  files: ExportFileRead[]
  // 출처마다 학습에 쓰는 문장 수
  source_counts: Record<string, number>
  // 넣지 않는 문장: 학습에서 뺀 것 · 라벨이 없는 것 (휴지통은 늘 뺀다)
  excluded: number
  unlabeled: number
  // 남은 심각 · 주의 (막지 않는다)
  checks: ExportCheckRead[]
}
