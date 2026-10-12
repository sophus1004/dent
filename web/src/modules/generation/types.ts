// 생성 모듈 API의 모양. 이름은 백엔드(modules/generation/schemas.py)와 같다.
import type { SourceCreate } from '@/system/types'

// 검사 이름 (backend rules.CheckKey)
export type CheckKey =
  | 'structure'
  | 'empty_prompt'
  | 'duplicate'
  | 'pii'
  | 'empty_answer'
  | 'truncated'
  | 'code_translated'
  | 'loop'
  | 'refusal'
  | 'short'
  | 'repeat'
  | 'lang_mix'
  | 'broken'
  | 'long'
  // 뜻 분석이 재는 검사
  | 'near_duplicate'
  | 'task_skew'
  | 'mismatch'

// 등급
export type Grade = 'good' | 'warn' | 'bad'

// 등급 매기는 방식: any 하나라도 심각 · warn_any 하나라도 주의 · rate 대화 수에 대한 비율 · share 가장 큰 무리의 몫
export type GradeKind = 'any' | 'warn_any' | 'rate' | 'share'

// 고치는 쪽
export type Who = 'rule' | 'jev' | 'llm' | 'human'

// 검토 결정 (rewrite는 LLM이 연결돼 있어야 한다)
export type Action = 'drop' | 'keep' | 'mask' | 'cut' | 'rewrite'

// 원본 모양
export type Shape = 'messages' | 'sharegpt' | 'alpaca' | 'turns'

export interface DatasetSummaryRead {
  id: number
  name: string
  description: string
  // 휴지통 밖 대화 수
  conversation_count: number
  // 문제가 남은 대화 수 (같은 지시는 빼고)
  problem_count: number
  trash_count: number
  importing: boolean
  created_at: string
  updated_at: string
}

export interface DatasetRead extends DatasetSummaryRead {
  // 흐름의 입구: 1 지시 · 2 응답
  entry_stage: number
}

// ---------- 필드 맞춤 · 가져오기 ----------

export interface TurnColumns {
  user: string
  assistant: string
}

export interface FieldMapping {
  shape: Shape
  messages: string | null
  instruction: string | null
  input: string | null
  output: string | null
  system: string | null
  turns: TurnColumns[]
}

export interface MappingSuggestCreate {
  columns: string[]
}

export interface ImportCreate extends SourceCreate {
  dataset_id: number | null
  new_dataset_name: string | null
  mapping: FieldMapping
}

// ---------- 진단 ----------

export interface CheckRead {
  key: CheckKey
  // 흐름 단계: 1 지시 · 2 응답
  stage: number
  // 문제가 남은 대화 수
  count: number
  // 휴지통 밖 대화 수에 대한 비율 (0~1)
  rate: number
  grade: Grade
  kind: GradeKind
  good_max: number
  warn_max: number
  who: Who[]
  // 검토에서 고를 수 있는 결정 (앞의 것이 규칙이 권하는 것)
  actions: Action[]
}

// 아직 재지 못한 뜻 검사 (진단의 흐린 줄)
export interface WaitingRead {
  key: CheckKey
  stage: number
  // 까닭 (예: 뜻 분석 전 · Jev 미연결)
  reason: string
}

export interface OverviewRead {
  dataset_id: number
  conversations: number
  trashed: number
  // 턴 수(응답 수)별 대화 수 {"1": 48, …}
  turns: Record<string, number>
  checks: CheckRead[]
  // 아직 재지 못한 뜻 검사 (종합 상태 · 지금 단계에 세지 않는다)
  waiting: WaitingRead[]
  // 흐름에서 지금 단계: 1 지시 · 2 응답 · 3 내보내기
  now_stage: number
  computed_at: string
}

// 분포 카드의 칸 하나 (화면이 OverviewRead.turns로 만든다)
export interface BinRead {
  label: string
  count: number
  // 보통 · 주의 · 심각 · 흐림
  tone: 'normal' | 'warn' | 'bad' | 'muted'
}

// ---------- 대화 · 검토 ----------

export interface ProblemRead {
  key: CheckKey
  // 메시지 순서 (대화 전체에 걸친 문제면 null)
  turn: number | null
  start: number | null
  end: number | null
  snippet: string
}

export interface MessageRead {
  role: 'system' | 'user' | 'assistant'
  content: string
}

// 뜻 분석이 이 대화를 Jev에 물은 결과 (예 확률, 묻지 않았으면 null)
export interface JevRead {
  follows: number | null
  refusal_ok: number | null
  similarity: number | null
  // Jev가 규칙의 문제(거절 · 짧은 응답)를 괜찮다고 봤는지
  looks_fine: boolean
}

export interface ConversationRead {
  id: number
  dataset_id: number
  messages: MessageRead[]
  source: string
  problems: ProblemRead[]
  kept: CheckKey[]
  trashed: boolean
  row_version: number
  extra: Record<string, unknown>
  // 뜻 분석의 Jev 판정
  jev: JevRead | null
  // 가져오기 기록 번호 (기록을 지웠으면 null)
  import_id: number | null
  created_at: string
  updated_at: string
}

export interface ConversationPageRead {
  items: ConversationRead[]
  total: number
  limit: number
  offset: number
}

// 대화 목록 거르기
export interface ConversationQuery {
  status?: 'active' | 'trash'
  // 검사 이름 · any(문제 있음) · none(문제 없음)
  problem?: CheckKey | 'any' | 'none' | null
  q?: string | null
  limit?: number
  offset?: number
}

export interface ReviewQueueRead {
  check: CheckKey
  ids: number[]
  total: number
}

export interface DecisionCreate {
  check: CheckKey
  action: Action
}

export interface DecideAllCreate {
  check: CheckKey
  action: 'drop' | 'keep'
  // Jev가 괜찮다고 본 대화만 (거절 · 짧은 응답의 그대로 두기)
  only_jev_ok?: boolean
}

export interface ChangeRead {
  id: number
  prompt_id: number
  check_key: CheckKey | null
  action: Action
  created_at: string
  undone_at: string | null
}

export interface DecisionRead {
  conversation: ConversationRead
  change: ChangeRead
}

export interface ConversationBulkUpdate {
  ids: number[]
  action: 'trash' | 'restore'
}

export interface ChangedRead {
  changed: number
}

export interface NearPartnerRead {
  prompt_id: number
  similarity: number
  // 짝의 마지막 사용자 말 앞부분
  text: string
  trashed: boolean
}

// ---------- 뜻 분석 ----------

// 뜻 분석 요약: 과제 무리 · 근접 중복 쌍 · Jev 상태
export interface AnalysisChecks {
  tasks?: { clusters: number; sizes: number[]; largest_share: number; largest_count: number }
  near?: { pairs: number }
  jev?: { status: 'judged' | 'not_connected' | 'failed'; detail: string }
}

export interface AnalysisRead {
  id: number
  dataset_id: number
  status: 'queued' | 'running' | 'done' | 'failed' | 'canceled'
  conversation_count: number
  checks: AnalysisChecks
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
  ids: number[]
  x: number[]
  y: number[]
  clusters: number[]
  sources: string[]
  flags: number[]
  flag_names: string[]
  cluster_sizes: number[]
}

export interface MapMatchesRead {
  ids: number[]
}

export interface MapTextRead {
  text: string
}

// ---------- 내보내기 ----------

export type ExportFormat = 'sft_jsonl'

export interface ExportCreate {
  formats: ExportFormat[]
}

export interface ExportFileRead {
  format: ExportFormat
  file_name: string
  conversations: number
}

export interface ExportCheckRead {
  key: CheckKey
  count: number
  grade: Grade
}

export interface ExportPreviewRead {
  line: string
  files: ExportFileRead[]
  checks: ExportCheckRead[]
  // 심각이 남아 내보낼 수 없는지
  blocked: boolean
}

// ---------- LLM 도우미 ----------

export type { HelperStateRead } from '@/system/types'

// 되돌린 결과
export interface HelperUndoRead {
  // 전 값으로 돌린 대화 수 · 그 사이 바뀌어 건너뛴 수
  reverted: number
  skipped: number
}
