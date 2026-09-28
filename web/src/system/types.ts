// 시스템 API의 입출력 모양. 백엔드(src/dent/system/schemas.py)와 이름·모양이 같다.
// 데이터셋 목록 · 올린 파일 · 허깅페이스 미리 보기 · 가져오기 · 작업 · 외부 모델 연결.
// JSON 이름은 snake_case 그대로 쓴다. 시각은 ISO 문자열이다.

// ---------- 데이터셋 목록 ----------

// GET /api/v1/datasets 의 한 줄. 모든 모듈의 데이터셋이 들어 있다.
export interface DatasetRead {
  id: number
  // 이 데이터셋을 가진 모듈. 예: 'classification'
  module: string
  name: string
  description: string
  created_at: string
  updated_at: string
}

// PATCH /api/v1/datasets/{dataset_id}
export interface DatasetUpdate {
  name?: string
  description?: string
}

// ---------- 올린 파일 · 허깅페이스 미리 보기 ----------

// 미리 보기의 한 줄. 열 이름 → 값
export type PreviewRow = Record<string, string | number | null>

// POST /api/v1/uploads, GET /api/v1/uploads/{upload_id}/preview
export interface FilePreviewRead {
  // 올린 파일의 번호(uuid4 hex). 가져오기를 만들 때 넘긴다.
  upload_id: string
  file_name: string
  format: 'csv' | 'xlsx'
  size_bytes: number
  // CSV의 글자 인코딩. XLSX면 null
  encoding: 'utf-8' | 'cp949' | null
  // CSV의 칸 구분자. XLSX면 null
  delimiter: ',' | '\t' | ';' | null
  // XLSX 시트 이름들. CSV면 []
  sheets: string[]
  // 미리 본 시트. CSV면 null
  sheet: string | null
  columns: string[]
  // 처음 20줄
  rows: PreviewRow[]
  // rows 각 줄의 줄 번호. CSV는 파일의 줄(칸 이름 줄이 1), XLSX는 시트의 행 번호.
  // 가져오기가 건너뛴 줄을 알릴 때와 같은 번호다.
  lines: number[]
  // 전체 줄 수. 모르면 null
  row_count: number | null
}

// 허깅페이스 데이터셋의 분할 하나
export interface HuggingFaceSplit {
  name: string
  num_rows: number | null
}

// POST /api/v1/huggingface/preview 의 요청
export interface HuggingFacePreviewRequest {
  // 예: 'klue/klue'
  repo: string
  config?: string | null
  split?: string | null
}

// POST /api/v1/huggingface/preview 의 답
export interface HuggingFacePreviewRead {
  repo: string
  configs: string[]
  config: string
  splits: HuggingFaceSplit[]
  // 미리 본 분할
  split: string
  columns: string[]
  // ClassLabel 칸의 라벨 이름들. 칸 이름 → 이름 목록
  label_names: Record<string, string[]>
  // 처음 20줄. ClassLabel 숫자는 이름으로 바뀌어 있다.
  rows: PreviewRow[]
}

// ---------- 가져오기 ----------

export type ImportSource = 'file' | 'huggingface'

export type ImportStatus = 'queued' | 'running' | 'done' | 'failed'

// 가져올 곳. 모듈의 가져오기 입력이 이것을 이어 받는다.
export interface SourceCreate {
  source: ImportSource
  // 파일일 때
  upload_id?: string | null
  sheet?: string | null
  // 허깅페이스일 때
  repo?: string | null
  config?: string | null
  // 가져올 분할들. null이면 모두
  splits?: string[] | null
}

// 건너뛴 줄 하나
export interface SkippedLine {
  // CSV는 파일의 줄 번호(머리줄이 1), XLSX는 시트의 행 번호, 허깅페이스는 가져온 순서(1부터)
  line: number
  // 예: '빈 문장', '너무 긴 문장', '라벨 없음'
  reason: string
}

// GET /api/v1/imports/{import_id}, GET /api/v1/datasets/{dataset_id}/imports 의 한 줄
// 내장 예시 데이터에 일부러 심은 문제 하나 (진단에 그 수만큼 보인다)
export interface ExamplePlantedRead {
  name: string
  count: number
  // 건 · 무리 · 쌍 · 문서 · 배 …
  unit: string
}

// GET /api/v1/<모듈>/examples: 모듈의 내장 예시 데이터셋 하나
export interface ExampleRead {
  key: string
  // 넣을 때 만드는 데이터셋 이름 · 설명
  name: string
  description: string
  file_name: string
  rows: number
  // 심은 문제 (없으면 깨끗한 대조군)
  planted: ExamplePlantedRead[]
  // 이미 넣었으면 그 데이터셋 번호
  dataset_id: number | null
}

export interface ImportRead {
  id: number
  dataset_id: number
  source: ImportSource
  // 파일 이름, 또는 'repo · config'
  source_name: string
  status: ImportStatus
  // 작업(system_jobs) 번호. 진행률은 jobs.ts의 useJob으로 본다.
  job_id: number | null
  rows_total: number | null
  rows_added: number
  rows_skipped: number
  // 앞쪽 100줄까지만 남긴다.
  skipped_lines: SkippedLine[]
  // 모듈만의 결과. 예: 분류는 { new_labels: ['환불', …] }
  result: Record<string, unknown>
  // 실패했을 때의 한국어 문장
  error: string | null
  created_at: string
  finished_at: string | null
}

// ---------- 작업 ----------

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'canceled'

// GET /api/v1/jobs/{job_id}의 답
export interface JobRead {
  id: number
  // 작업을 낸 모듈. 예: 'classification'
  module: string
  // 작업 종류. 예: 'import'
  kind: string
  // 작업이 다루는 데이터셋 번호. 없거나 지운 데이터셋이면 null
  dataset_id: number | null
  // 넣을 때의 데이터셋 이름. 지운 데이터셋이면 이름만 남는다.
  dataset_name: string | null
  status: JobStatus
  // 처리한 양
  progress_done: number
  // 전체 양. 아직 모르면 null
  progress_total: number | null
  // 끝났을 때 작업이 남긴 결과
  result: Record<string, unknown> | null
  // 실패했을 때의 한국어 문장
  error: string | null
  // 끊겨서 다시 시작한 횟수
  attempts: number
  // 사용자가 멈추라고 했는지
  cancel_requested: boolean
  // 일어난 일. 시각 순
  events: JobEvent[]
  created_at: string
  started_at: string | null
  finished_at: string | null
}

// 작업의 사건 한 줄. type마다 붙는 값이 다르다.
export type JobEvent =
  // 작업 실행기가 꺼냄. attempt는 몇 번째 시작인지(끊겨서 다시 꺼내면 2부터)
  | { type: 'start'; at: string; attempt: number }
  // 단계 시작. total은 그 단계의 전체 양(모르면 null)
  | { type: 'phase'; at: string; phase: string; total: number | null }
  // 외부 서버에 다시 보냄. attempt번째 실패, reason은 까닭 한 마디(예: '시간 초과')
  | { type: 'retry'; at: string; role: string; attempt: number; reason: string }
  // 이번에 쓴 외부 서버
  | { type: 'connection'; at: string; role: string; base_url: string; model: string | null }

// 상태별 작업 수
export type JobCounts = Record<JobStatus, number>

// GET /api/v1/jobs
export interface JobPageRead {
  // 최근에 넣은 것부터
  items: JobRead[]
  // 거르기에 맞는 전체 수
  total: number
  // 상태 거르기만 뺀 같은 조건의 상태별 수
  counts: JobCounts
}

// GET /api/v1/jobs의 거르기
export interface JobQuery {
  status?: JobStatus[]
  module?: string
  kind?: string
  dataset_id?: number
  // 최근 n시간에 넣은 것
  hours?: number
  // 이 시각 뒤에 끝난 것 (ISO)
  finished_after?: string
  limit?: number
  offset?: number
}

// ---------- 외부 모델 연결 ----------

// 연결 역할: 임베딩 서버 · Jev 판정 서버 · LLM
export type ConnectionRole = 'embedding' | 'jev' | 'llm'

// PUT /api/v1/connections/{role}, POST /api/v1/connections/{role}/check 의 요청
// LLM 공급자. 부르는 방식(키 헤더 · 주소)이 공급자마다 다르다.
export type LlmProvider = 'openai' | 'vllm' | 'anthropic' | 'google' | 'xai'

export interface ConnectionUpdate {
  // http:// 또는 https://로 시작한다. 끝의 /는 서버가 뗀다.
  base_url: string
  // 비우면 서버가 고른다(Jev는 자동). 임베딩은 꼭, LLM은 저장할 때 꼭 있어야 한다.
  model: string | null
  // LLM이면 꼭 준다. 다른 역할이면 서버가 버린다.
  provider?: LlmProvider | null
  // 서버 API 키. 비우면 저장된 키를 그대로 쓴다(같은 공급자 · 주소일 때만).
  api_key?: string | null
}

// 확인하며 알아낸 값 하나
export type ConnectionFact = string | number | string[] | null

// POST /api/v1/connections/{role}/check 의 답
export interface ConnectionCheckRead {
  ok: boolean
  // 되면 사실(예: '1024차원 · 42ms'), 안 되면 까닭(예: '연결 거부'). 앞에 '연결됨' · '실패'를 붙여 보인다.
  detail: string
  // 임베딩: dim · latency_ms, Jev: device · loaded · latency_ms · model
  facts: Record<string, ConnectionFact>
}

// GET /api/v1/connections 의 한 줄, PUT의 답
export interface ConnectionRead {
  role: ConnectionRole
  // 실행할 때 올린 내장 모델 이름(bge-m3 · laya). 내장이면 화면에서 바꿀 수 없다. 아니면 null
  embedded: string | null
  // 저장하지 않았으면 null(미연결)
  base_url: string | null
  model: string | null
  // LLM 공급자. LLM이 아니거나 저장하지 않았으면 null
  provider: LlmProvider | null
  // 저장된 API 키의 앞뒤 몇 글자(예: 'sk-p…rCoA'). 키 자체는 오지 않는다. 없으면 null
  api_key_hint: string | null
  updated_at: string | null
  // 마지막 확인 결과. 저장하지 않았으면 null
  check: ConnectionCheckRead | null
}

// ---------- LLM 도우미 ----------

// 도우미 실행의 상태: 도는 중 · 새 문장 허락 기다림 · 끝남 · 멈춤 · 실패
export type HelperRunStatus = 'running' | 'asking' | 'done' | 'stopped' | 'failed'

// 도우미 사건의 종류 (도우미 창이 종류마다 다른 카드로 그린다)
export type HelperEventKind = 'say' | 'jev' | 'llm' | 'change' | 'result' | 'hold' | 'permission' | 'notice' | 'report'

// 단계 줄 하나 (모듈이 채운다)
export interface HelperStep {
  key: string
  no: number
  title: string
  // todo · running · done · skipped · ask · locked · blocked(문에 걸림: 앞 단계에 심각이 남음)
  status: string
  // 바꿈 · 보류 · 유지 · rule · jev · llm · time
  facts?: Record<string, number | string>
  // [처음 값, 지금 값, 등급]
  delta?: [string, string, string]
  // 아직 안 한 단계의 한 줄 (예: '5건', '허락 필요')
  plan?: string
  // 단계 무리 번호와 이름 (검색: 1 문서 · 2 질의 · 3 하드 네거티브). 있으면 도우미 창이 무리 머리 줄을 넣고,
  // 무리 안 단계가 모두 끝나면 한 줄로 접는다.
  stage?: number
  stage_title?: string
  // 무리 머리에 번호 대신 보일 아이콘 (AI로 고치기: sparkles)
  stage_icon?: string
  // AI로 고치기 몇 번째인지와 그 단계가 고치는 검사 열쇠
  fix_round?: number
  check?: string
}

// GET /api/v1/helper/runs/{id}
export interface HelperRunRead {
  id: number
  module: string
  dataset_id: number
  job_id: number | null
  status: HelperRunStatus
  steps: HelperStep[]
  // 지금 단계 번호 (0 = 계획)
  step_now: number
  model: string | null
  llm_tokens: number
  jev_calls: number
  changed: number
  held: number
  stop_requested: boolean
  // 허락을 묻는 새 문장 계획 (asking일 때)
  permission: Record<string, unknown> | null
  error: string | null
  created_at: string
  finished_at: string | null
}

// GET /api/v1/helper/runs/{id}/events 의 한 줄
export interface HelperEventRead {
  id: number
  step: number
  kind: HelperEventKind
  payload: Record<string, unknown>
  created_at: string
}

// 모듈의 도우미 상태 (GET /{모듈}/datasets/{id}/helper 의 답)
export interface HelperStateRead {
  // 가장 최근 실행. 없으면 null
  run: HelperRunRead | null
  // 모든 줄을 되돌린 바꾼 카드 번호들
  undone_events: number[]
}

// 도우미가 끝난 뒤 남은 것 한 줄 (GET /api/v1/<모듈>/datasets/{id}/helper/left). 지금 진단으로 센다.
// 남은 것 줄에서 사람이 고칠 수 있는 더할 수 한 칸 (예: 라벨마다 새 문장 수)
export interface HelperLeftAdd {
  // 칸 열쇠 (AI로 고치기의 adds에 보낸다)
  key: string
  name: string
  // 지금 수 · 계획한 더할 수 · 상한
  now: number
  add: number
  max: number
}

export interface HelperLeftItem {
  // 검사 열쇠 (AI로 고치기에 보낸다)
  key: string
  name: string
  value: string
  unit: string
  sub: string
  grade: 'warn' | 'bad'
  // ai(AI로 고칠 수 있음) · direct(직접 권장, 고를 수는 있다) · blocked(AI로 못 고침)
  group: 'ai' | 'direct' | 'blocked'
  // AI로 고치면 하는 일 · 어림 비용
  how: string
  tokens: number
  jev: number
  // AI로 못 고칠 때 할 일
  action: string
  // → 로 갈 곳을 모듈이 정하는 대상 · 문제 거르기 (없으면 빈 글)
  view_target: string
  view_problem: string
  // 사람이 고칠 수 있는 더할 수 칸들과 그 표의 작은 글 (없으면 빈 목록)
  adds: HelperLeftAdd[]
  adds_note: string
}

export interface HelperLeftRead {
  items: HelperLeftItem[]
}

// ---------- 내보낸 파일 ----------

// 내보내기 상태
export type ExportStatus = 'queued' | 'running' | 'done' | 'failed'

// GET /api/v1/exports/{id} (내려받기는 /api/v1/exports/{id}/file)
export interface ExportRead {
  id: number
  module: string
  dataset_id: number
  // 모듈이 정한 형식 이름
  format: string
  options: Record<string, unknown>
  status: ExportStatus
  file_name: string
  // 파일 크기(바이트). 만들기 전에는 null
  size_bytes: number | null
  // 모듈만의 결과 (질의 · 판정 수 …)
  result: Record<string, unknown>
  job_id: number | null
  error: string | null
  created_at: string
  finished_at: string | null
  // 파일을 지운 시각 (보관 기간이 지남)
  deleted_at: string | null
}
