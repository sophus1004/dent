// 검색 모듈 API의 입출력 모양. 백엔드(src/dent/modules/retrieval/schemas.py)와 이름·모양이 같다.
// JSON 이름은 snake_case 그대로 쓴다. 시각은 ISO 문자열이다.
// 올린 파일·허깅페이스 미리 보기·데이터셋 목록·가져오기 기록·도우미 실행의 모양은 system/types.ts에 있다.
import type { Grade } from '@/system/diagnosis/grades'
import type { SourceCreate } from '@/system/types'

// ---------- 공통 값 ----------

// 원본의 모양: 문서만 · 쌍 · MRC · 세 쌍 · 점수. 모양이 흐름의 입구 단계를 정한다.
export type Shape = 'documents' | 'pair' | 'mrc' | 'triplet' | 'scored'

// 질의 출처
export type QuerySource = 'original' | 'synthetic' | 'human'

// 판정 출처
export type JudgmentSource = 'original' | 'mined' | 'synthetic' | 'helper' | 'human'

// 질의를 학습에서 뺀 이유
export type ExcludeReason = 'manual' | 'duplicate' | 'helper'

// 제안 종류: 거짓 오답 · 빠진 정답 · 정답 의심
export type SuggestionKind = 'false_negative' | 'missing_positive' | 'suspect_positive'

// 제안 판단
export type SuggestionDecision = 'accepted' | 'kept'

// 뜻 분석 상태
export type AnalysisStatus = 'queued' | 'running' | 'done' | 'failed' | 'canceled'

// 목록의 상태 거르기: 휴지통 밖 전체 · 학습에 씀 · 학습에서 뺌 · 휴지통
export type StatusFilter = 'active' | 'included' | 'excluded' | 'trash'

// 질의 문제 거르기 (진단 검사 이름)
export type QueryProblem =
  | 'no_positive'
  | 'short_long'
  | 'conflict'
  | 'duplicate'
  | 'context'
  | 'easy_pair'
  | 'suspect'
  | 'false_negative'
  | 'missing'
  | 'no_negative'
  | 'same_negative'
  | 'easy_negative'

// 문서 문제 거르기 (repeat = 고르지 않은 반복 구간이 든 문서)
export type DocumentProblem = 'broken' | 'repeat' | 'long' | 'duplicate' | 'near' | 'pick'

// 문서 쓰임 거르기
export type DocumentUse = 'positive' | 'negative' | 'unused'

// 여러 줄을 바꾼 수
export interface ChangedRead {
  changed: number
}

// ---------- 필드 맞춤 · 가져오기 ----------

// 필드 맞춤: 모양과 어느 열이 무엇인지. 못 고른 칸은 null
export interface FieldMapping {
  shape: Shape
  text: string | null
  query: string | null
  positive: string | null
  negatives: string[]
  document: string | null
  score: string | null
  answer: string | null
  title: string | null
  doc_key: string | null
  // 머리말 칸들 (학습 글 머리말에 붙는 값) · 묶음 칸 (같은 값이면 한 묶음)
  header_columns: string[]
  group_column: string | null
}

// POST /retrieval/mapping/suggest
export interface MappingSuggestCreate {
  columns: string[]
}

// POST /retrieval/imports (원본에 분할이 있어도 한 덩어리로 가져온다)
export interface ImportCreate extends SourceCreate {
  dataset_id: number | null
  new_dataset_name: string | null
  mapping: FieldMapping
}

// 가져오기 기록 result의 수 (importing.RESULT_KEYS)
export interface ImportResultCounts {
  queries_added: number
  documents_added: number
  documents_merged: number
  judgments_added: number
  conflicts: number
}

// ---------- 데이터셋 · 설정 ----------

export interface SettingsRead {
  shape: Shape
  // 학습할 모델. null이면 연결된 임베딩 모델
  target_model: string | null
  query_max_tokens: number
  doc_max_tokens: number
  negatives: number
  mine_rank_from: number
  mine_rank_to: number
  mine_margin: number
  // 질의마다 모아 두는 오답 풀
  negative_pool: number
  // 학습 글 머리말에 구획 경로 · 질의 만들기 되찾기 거르기 · 쉬운 쌍 상한(몫)
  section_header: boolean
  round_trip: boolean
  easy_pair_cap: number
  // 가장 최근 오답 훑기 결과 (없으면 빈 객체) · 반복 구간을 살핀 시각
  negative_scan: NegativeScanRead | Record<string, never>
  scanned_at: string | null
  // 도우미 실행 설정: 문서 나누기 오버랩(토큰, 0 = 없음) · 청크마다 질의 수 · 질문형 몫(%)
  chunk_overlap: number
  queries_per_chunk: number
  question_share: number
}

// GET /datasets/{id}/helper/estimate: 실행 설정 카드의 어림
export interface HelperEstimateRead {
  // 청크 크기를 넘는 문서 수 · 그 문서들이 나뉠 청크 수 · 나누면 코퍼스의 청크 수
  long_documents: number
  long_chunks: number
  chunks: number
  // 질의를 만들 청크 수 · 남을 질의 수 · LLM 토큰 어림
  targets: number
  queries: number
  tokens: number
}

export type SettingsUpdate = Partial<
  Omit<SettingsRead, 'shape' | 'negative_scan' | 'scanned_at'>
>

// 오답 훑기 표의 한 칸: 순위 묶음 × 정답 대비 문턱
export interface NegativeScanCellRead {
  rank_from: number
  rank_to: number
  margin: number
  // 문턱을 통과하는 후보 · 그 가운데 Jev 예(거짓 오답) · 남는 오답
  asked: number
  false_negatives: number
  kept: number
}

export interface NegativeScanRead {
  queries: number
  cells: NegativeScanCellRead[]
  measured_at: string | null
}

// ---------- 반복 구간 ----------

export type RepeatKind = 'sentence' | 'meta'
export type RepeatDecision = 'remove' | 'keep'

export interface RepeatRead {
  id: number
  kind: RepeatKind
  text: string
  samples: string[]
  document_count: number
  head_count: number
  tail_count: number
  suggestion: RepeatDecision
  reason: string | null
  decision: RepeatDecision | null
}

// POST /retrieval/datasets/{id}/repeats/bulk
export interface RepeatBulkUpdate {
  repeat_ids: number[]
  decision?: RepeatDecision | null
  // 틀마다 제안대로 (decision은 보지 않는다)
  follow_suggestion?: boolean
}

// 반복 구간이 든 보기 문서 하나: 구간 앞 글 · 구간 · 뒤 글
export interface RepeatSampleRead {
  document_id: number
  title: string
  before: string
  span: string
  after: string
}

export interface RepeatListRead {
  items: RepeatRead[]
  // remove · keep · undecided
  counts: Record<string, number>
  documents: number
  scanned_at: string | null
}

export interface DatasetSummaryRead {
  id: number
  name: string
  description: string
  query_count: number
  document_count: number
  judgment_count: number
  positive_count: number
  negative_count: number
  trash_count: number
  importing: boolean
  shape: Shape | null
  entry_stage: number | null
  created_at: string
  updated_at: string
}

export interface DatasetRead extends DatasetSummaryRead {
  settings: SettingsRead | null
}

// ---------- 질의 · 문서 · 판정 ----------

export interface DocumentBriefRead {
  id: number
  title: string
  snippet: string
  token_count: number
}

export interface QueryRead {
  id: number
  dataset_id: number
  text: string
  source: QuerySource
  source_document_id: number | null
  answer: string | null
  exclude_reason: ExcludeReason | null
  is_trashed: boolean
  trashed_at: string | null
  row_version: number
  extra: Record<string, unknown>
  import_id: number | null
  created_at: string
  updated_at: string
  positive_count: number
  negative_count: number
  positives: DocumentBriefRead[]
  marks: string[]
}

export interface QueryPageRead {
  items: QueryRead[]
  total: number
  limit: number
  offset: number
}

// GET /retrieval/datasets/{id}/queries 의 거르기
export interface QueryListQuery {
  q?: string | null
  source?: QuerySource | null
  status?: StatusFilter
  problem?: QueryProblem | null
  limit?: number
  offset?: number
}

export interface QueryUpdate {
  text?: string
  excluded?: boolean
  trashed?: boolean
  row_version: number
}

export type QueryBulkAction = 'exclude' | 'include' | 'trash' | 'restore'

export interface QueryBulkUpdate {
  query_ids: number[]
  action: QueryBulkAction
}

export interface DocumentRead {
  id: number
  dataset_id: number
  doc_key: string | null
  title: string
  text: string
  // 머리말 칸 값 · 구획 경로 · 묶음 칸 값
  header: string
  section: string
  group_key: string | null
  // 학습 글 (머리말 + 떼기로 고른 구간을 뺀 본문). 임베딩 · 내보내기가 쓰는 글
  training_text: string
  token_count: number
  source_document_id: number | null
  chunk_index: number | null
  skip_generation: boolean
  is_trashed: boolean
  is_replaced: boolean
  row_version: number
  extra: Record<string, unknown>
  import_id: number | null
  created_at: string
  updated_at: string
  positive_count: number
  negative_count: number
  synthetic_count: number
  marks: string[]
}

export interface DocumentPageRead {
  items: DocumentRead[]
  total: number
  limit: number
  offset: number
}

// GET /retrieval/datasets/{id}/documents 의 거르기
export interface DocumentListQuery {
  q?: string | null
  use?: DocumentUse | null
  status?: StatusFilter
  problem?: DocumentProblem | null
  limit?: number
  offset?: number
}

export interface DocumentUpdate {
  title?: string
  text?: string
  skip_generation?: boolean
  trashed?: boolean
  row_version: number
}

export type DocumentBulkAction = 'trash' | 'restore' | 'skip_generation' | 'allow_generation'

export interface DocumentBulkUpdate {
  document_ids: number[]
  action: DocumentBulkAction
}

export interface JudgmentRead {
  query_id: number
  document_id: number
  grade: number
  source: JudgmentSource
  conflict: boolean
  teacher_score: number | null
}

export interface RankedDocumentRead {
  document: DocumentBriefRead
  grade: number | null
  source: JudgmentSource | null
  rank: number | null
  similarity: number | null
  flag: SuggestionKind | null
  jev_probability: number | null
}

export interface QueryDetailRead {
  query: QueryRead
  first_positive_rank: number | null
  ranked: RankedDocumentRead[]
  outside: RankedDocumentRead[]
  has_ranking: boolean
}

export interface JudgedQueryRead {
  query_id: number
  text: string
  grade: number | null
  rank: number | null
  similarity: number | null
  jev_probability: number | null
}

export interface DocumentDetailRead {
  document: DocumentRead
  uses: JudgedQueryRead[]
  nearby: JudgedQueryRead[]
  synthetic: JudgedQueryRead[]
  siblings: DocumentBriefRead[]
}

// ---------- 진단 ----------

export interface LadderStepRead {
  grade: Grade
  text: string
}

// 볼 데이터의 대상
export type ViewTarget = 'queries' | 'documents' | 'suggestions'

export interface CheckRead {
  key: string
  stage: number
  item: string | null
  grade: Grade
  value: string
  unit: string
  sub: string
  ladder: LadderStepRead[]
  view_target: ViewTarget | null
  view_problem: string | null
  view_count: number
}

// 흐름 칸의 상태: 끝 · 심각 · 주의 · 할 차례 · 허락 기다림 · 필요 없음 · 앞 단계 뒤 · 잠김
export type FlowState = 'done' | 'bad' | 'warn' | 'todo' | 'ask' | 'skip' | 'wait' | 'lock'

export interface FlowItemRead {
  key: string
  state: FlowState
  text: string
}

export interface FlowStageRead {
  no: number
  name: string
  count: number
  state: FlowState
  open_count: number
  items: FlowItemRead[]
}

export type FlowMoveKey = 'generate' | 'mine' | 'export'

export interface FlowMoveRead {
  key: FlowMoveKey
  state: FlowState
  text: string
}

export interface FlowRead {
  stages: FlowStageRead[]
  moves: FlowMoveRead[]
  entry: number | null
  current: number | FlowMoveKey | null
}

// 기준 검색 점수 (학습에 쓰는 질의 전부)
export interface KpiRead {
  recall_at_10: number
  mrr_at_10: number
  // 잰 질의 수
  evaluated: number
  baseline_recall_at_10: number | null
  baseline_mrr_at_10: number | null
}

export interface BinRead {
  label: string
  count: number
  // 보통 · 주의 · 심각 · 흐림(아직 모르는 것. 예: 분석에 없는 질의)
  tone: 'normal' | 'warn' | 'bad' | 'muted'
}

export interface OverviewRead {
  dataset_id: number
  query_count: number
  document_count: number
  positive_count: number
  negative_count: number
  flow: FlowRead
  checks: CheckRead[]
  kpi: KpiRead | null
  first_rank: BinRead[]
  negatives_per_query: BinRead[]
  query_types: BinRead[]
  document_lengths: BinRead[]
  judgment_sources: BinRead[]
  computed_at: string
}

// ---------- 뜻 분석 ----------

export interface AnalysisRead {
  id: number
  dataset_id: number
  status: AnalysisStatus
  query_count: number
  document_count: number
  job_id: number | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface AnalysisStateRead {
  done: AnalysisRead | null
  run: AnalysisRead | null
  outdated: boolean
  model: string | null
}

export interface MapPointsRead {
  analysis_id: number
  kinds: ('query' | 'document')[]
  ids: number[]
  x: number[]
  y: number[]
  clusters: number[]
  sources: (string | null)[]
  flags: number[]
  flag_names: string[]
  empty_clusters: number[]
}

// ---------- 제안 ----------

export interface SuggestionRead {
  id: number
  kind: SuggestionKind
  query_id: number
  query_text: string
  document: DocumentBriefRead
  grade: number | null
  rank: number
  similarity: number
  jev_probability: number | null
  is_confirmed: boolean
  decision: SuggestionDecision | null
}

export interface SuggestionPageRead {
  items: SuggestionRead[]
  total: number
  limit: number
  offset: number
  pending_counts: Record<string, number>
  confirmed_pending: number
}

export interface SuggestionListQuery {
  kind?: SuggestionKind | null
  decided?: boolean
  limit?: number
  offset?: number
}

// ---------- 도우미 ----------

export type { HelperStateRead } from '@/system/types'

export interface HelperUndoRead {
  reverted: number
  skipped: number
}

// ---------- 내보내기 ----------

export type ExportFormat = 'train_jsonl' | 'train_table' | 'beir' | 'corpus'

export interface ExportCreate {
  formats: ExportFormat[]
  sources: JudgmentSource[]
  negatives: number
  teacher_scores: boolean
  // 학습 표에 출처 칸(source: original · synthetic · human)을 넣을지. jsonl · BEIR에는 늘 넣는다
  source_column: boolean
}

// 내보낼 파일 하나의 수 (학습에 쓰는 질의 모두)
export interface ExportFileRead {
  format: ExportFormat
  file_name: string
  queries: number
  positives: number
  negatives: number
  documents: number
}

export interface ExportPreviewRead {
  // 학습 jsonl의 첫 줄 (고른 대로, 글은 줄여서)
  line: string
  files: ExportFileRead[]
  // 출처마다 판정 수 (넣을 출처 칸)
  source_counts: Record<string, number>
  // 오답이 목표보다 적은 학습 질의 수 · 학습 질의 평균 오답 수(풀 전체)
  lacking: number
  average_negatives: number
  // 교사 점수가 없어 Jev에 물을 판정 수
  teacher_missing: number
  // 그 판정을 Jev에 묻는 데 걸릴 어림 시간(초). 가장 최근에 잰 속도로 센다. 잰 적이 없으면 null
  teacher_seconds: number | null
  // 남은 주의 (검사 이름: 수)
  warnings: Record<string, number>
  // 남은 심각 (검사 이름들). 있으면 내보낼 수 없다.
  blocked: string[]
}
