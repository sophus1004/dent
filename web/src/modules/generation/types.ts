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
  // 응답이 없는 지시(지시만) · 도구 호출 형식
  | 'no_answer'
  | 'tool_call'
  // 뜻 분석이 재는 검사
  | 'near_duplicate'
  | 'task_skew'
  | 'mismatch'
  | 'unsafe'
  // 평가셋과 대조하는 검사
  | 'contamination'
  // 3 선호: 쌍 하나가 단위
  | 'same_pair'
  | 'judge_reversed'
  | 'small_margin'
  | 'length_bias'

// 등급
export type Grade = 'good' | 'warn' | 'bad'

// 등급 매기는 방식: any 하나라도 심각 · warn_any 하나라도 주의 · rate 대화 수에 대한 비율 · share 가장 큰 무리의 몫
export type GradeKind = 'any' | 'warn_any' | 'rate' | 'share'

// 고치는 쪽
export type Who = 'rule' | 'jev' | 'llm' | 'human'

// 검토 결정 (rewrite · answer는 LLM이 연결돼 있어야 한다). flip은 선호 쌍 뒤집기, pair는 도우미의 비교 만들기
export type Action = 'drop' | 'keep' | 'mask' | 'cut' | 'rewrite' | 'answer' | 'flip' | 'pair'

// 원본 모양: 대화 목록 · ShareGPT · Alpaca · 턴마다 칸 · 선호(고른 · 버린 응답) · 두 응답 + 선호 점수
export type Shape = 'messages' | 'sharegpt' | 'alpaca' | 'turns' | 'preference' | 'pairwise'

// 만들 데이터: SFT · 선호
export type Goal = 'sft' | 'preference'

// 검사 · 검토 큐의 단위: 대화 · 선호 쌍
export type Unit = 'conversation' | 'pair'

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

// 데이터셋 설정 (만들 데이터 · 평가셋 · 학습할 모델)
export interface SettingsRead {
  goal: Goal
  // 평가셋인지 (다른 데이터셋의 오염 대조에 쓰인다)
  is_eval: boolean
  // 학습할 모델(허깅페이스 저장소 이름). 비면 긴 대화를 글자 수로 어림한다.
  tokenizer_model: string | null
  // 학습할 최대 길이(토큰)
  max_tokens: number
  // 지금 긴 대화 표시를 잰 모델 · 최대 길이 (설정과 다르면 다시 재는 중)
  measured_model: string | null
  measured_max_tokens: number | null
}

// 설정 고치기 (준 값만). 학습할 모델을 빈 글로 주면 지운다.
export interface SettingsUpdate {
  goal?: Goal
  is_eval?: boolean
  tokenizer_model?: string | null
  max_tokens?: number
}

export interface DatasetRead extends DatasetSummaryRead {
  // 흐름의 입구: 1 지시(지시만) · 2 응답 · 3 선호(쌍)
  entry_stage: number
  settings: SettingsRead
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
  // 응답 칸 (alpaca, 없으면 지시만)
  output: string | null
  system: string | null
  turns: TurnColumns[]
  // 도구 정의 칸 (도구 대화, 선택)
  tools?: string | null
  // 선호: 지시(선택) · 고른 응답 · 버린 응답 · 두 점수(선택). 두 응답: 지시 · 응답 1 · 응답 2 · 선호 점수
  prompt?: string | null
  chosen?: string | null
  rejected?: string | null
  chosen_score?: string | null
  rejected_score?: string | null
  response1?: string | null
  response2?: string | null
  preference?: string | null
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
  // 흐름 단계: 1 지시 · 2 응답 · 3 선호
  stage: number
  // 단위: 대화 · 선호 쌍
  unit: Unit
  // 문제가 남은 수 (그대로 둔 것은 빼고)
  count: number
  // 비율의 분모 (휴지통 밖 대화 · 쌍 수, 표본이면 판정한 수)
  total: number
  // 분모에 대한 비율 (0~1)
  rate: number
  // 분모가 표본인지 (안전처럼 앞 몇천 건만 Jev에 물은 검사)
  sampled: boolean
  // '그대로 두기'로 둔 수 (많으면 규칙의 오탐을 의심한다)
  kept: number
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
  // 만들 데이터 · 평가셋인지
  goal: Goal
  is_eval: boolean
  // 응답이 없는 대화 수 (응답 만들기의 할 일)
  unanswered: number
  // 휴지통 밖 쌍 수 · 쌍 없는 지시 수 (비교 만들기의 할 일)
  pairs: number
  unpaired: number
  // 긴 대화를 잰 방법: 학습할 모델의 토큰 · 글자 수 어림
  length_by: 'tokens' | 'chars'
  // 턴 수(응답 수)별 대화 수 {"1": 48, …}
  turns: Record<string, number>
  checks: CheckRead[]
  // 아직 재지 못한 뜻 검사 (종합 상태 · 지금 단계에 세지 않는다)
  waiting: WaitingRead[]
  // 흐름에서 지금 단계: 1 지시 · 2 응답 · 3 선호 · 4 내보내기
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

// 도구 호출 하나 (OpenAI 모양: {id, type: 'function', function: {name, arguments}})
export interface ToolCall {
  id?: string
  type?: string
  function?: { name?: string; arguments?: unknown }
  name?: string
  arguments?: unknown
}

export interface MessageRead {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  // 응답이 부른 도구들
  tool_calls: ToolCall[] | null
  // 도구 결과가 답하는 호출 번호 (tool 메시지만)
  tool_call_id: string | null
}

// 지시의 응답 하나 (주 응답 · 선호 쌍의 다른 응답)
export interface AnswerRead {
  id: number
  content: string
  tool_calls: ToolCall[] | null
  source: string
  // SFT에 쓰는 주 응답인지
  is_main: boolean
  score: number | null
}

// 선호 쌍 하나
export interface PairRead {
  id: number
  prompt_id: number
  chosen_id: number
  rejected_id: number
  source: string
  // Jev가 응답마다 따로 본 '지시를 따른다' 확률
  judge_chosen: number | null
  judge_rejected: number | null
  problems: CheckKey[]
  kept: CheckKey[]
  trashed: boolean
}

// 뜻 분석이 이 대화를 Jev에 물은 결과 (예 확률, 묻지 않았으면 null)
export interface JevRead {
  follows: number | null
  refusal_ok: number | null
  similarity: number | null
  // 응답이 해롭다 (안전 표본에 든 대화만)
  unsafe: number | null
  // Jev가 규칙의 문제(거절 · 짧은 응답)를 괜찮다고 봤는지
  looks_fine: boolean
}

export interface ConversationRead {
  id: number
  dataset_id: number
  // 맥락 + 주 응답 (응답이 없으면 맥락만)
  messages: MessageRead[]
  has_answer: boolean
  // 응답들(주 응답이 앞) · 선호 쌍들 · 도구 정의
  answers: AnswerRead[]
  pairs: PairRead[]
  tools: Record<string, unknown>[] | null
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
  // 번호의 단위: 대화 번호 · 쌍 번호
  unit: Unit
  ids: number[]
  total: number
}

// 선호 쌍 결정
export interface PairDecisionCreate {
  check: CheckKey
  action: 'drop' | 'flip' | 'keep'
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
  // 쌍에 한 결정이면 쌍 번호
  pair_id: number | null
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
  safety?: { judged: number }
  pairs?: { judged: number }
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

// 내보낼 파일 모양: SFT 대화 · 선호 쌍(DPO) · 지시만
export type ExportFormat = 'sft_jsonl' | 'dpo_jsonl' | 'prompts_jsonl'

export interface ExportCreate {
  formats: ExportFormat[]
}

export interface ExportFileRead {
  format: ExportFormat
  file_name: string
  // 넣을 줄 수 (대화 · 쌍 · 지시)
  conversations: number
  unit: Unit
  // 이 파일을 막는 심각이 있는지
  blocked: boolean
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
