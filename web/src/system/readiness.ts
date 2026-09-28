// DENT가 쓸 준비가 됐는지(/readyz)를 30초마다 물어본다. 사이드바 상태 줄, 홈의 시스템 · 모델 칸, 상태 화면이 같이 쓴다.
// 내장 모델을 받거나 불러오는 동안은 진행이 보이게 2초마다 묻는다.
// 화면에 보일 줄(이름 · 상태 · 값)로 바꾸는 일은 status.ts가 한다.
import { computed, ref } from 'vue'

// 다시 물어보는 간격(밀리초)
const POLL_INTERVAL_MS = 30 * 1000

// 내장 모델을 받거나 불러오는 동안 다시 물어보는 간격(밀리초). 진행 막대가 움직여 보이게 짧게 둔다.
const PREPARING_POLL_INTERVAL_MS = 2 * 1000

export interface CheckRead {
  // 되는지
  ok: boolean
  // 사람이 읽을 한 줄
  detail: string
}

// 홈의 모델 칸 한 줄. src/dent/api/health.py의 ModelInfoRead와 같다.
export interface ModelInfoRead {
  // 역할
  role: 'embedding' | 'jev' | 'llm'
  // 내장 모델 이름(bge-m3 · laya). 내장이 아니면 null
  embedded: string | null
  // 연결 주소. 미연결이면 null
  base_url: string | null
  // 모델 이름. 비어 있으면 null(Jev 자동 고르기 · 미연결)
  model: string | null
  // LLM 공급자. 없으면 null
  provider: string | null
  // 상태
  state: 'ok' | 'downloading' | 'verifying' | 'loading' | 'failed' | 'off'
  // 값이나 까닭. 예: '1024차원 · 47ms', '연결 거부'
  detail: string
  // 파일 받기 · 확인: 한 · 전체 바이트와 남은 초. 받거나 확인하는 중이 아니면 null
  done_bytes: number | null
  total_bytes: number | null
  eta_s: number | null
  // 쓰는 장치 (mps · cuda · cpu). 모르면 null
  device: string | null
}

// /readyz의 답. src/dent/api/health.py의 ReadinessRead와 같다.
export interface ReadinessRead {
  // DB·테이블·storage가 모두 되면 true
  ready: boolean
  // 준비의 조건: storage, db, schema
  checks: Record<string, CheckRead>
  // 참고로만 보는 상태: worker, embedding, jev, llm.
  // 연결은 '연결됨 · 1024차원 · 42ms' · '실패 · 연결 거부' · '미연결'처럼 낱말로 시작한다.
  info: Record<string, CheckRead>
  // 홈의 모델 칸: 임베딩 · Jev · LLM 순서로 한 줄씩
  models: ModelInfoRead[]
}

// 마지막으로 받은 답. 아직 못 받았거나 서버에 붙지 못했으면 null
export const readiness = ref<ReadinessRead | null>(null)

// 서버에 붙지 못했으면 true
export const unreachable = ref(false)

// 한 번이라도 물어봤는지. 처음 답을 받기 전에는 화면이 '확인 중'으로 보인다.
export const checkedOnce = ref(false)

// 사이드바 상태 점의 색: 모두 되면 good, 작업 실행기가 종료됐으면 warn, 안 되면 bad
export const overallGrade = computed<'good' | 'warn' | 'bad' | 'unknown'>(() => {
  if (!checkedOnce.value) return 'unknown'
  if (unreachable.value || !readiness.value?.ready) return 'bad'
  const isWorkerOn = readiness.value.info.worker?.ok ?? false
  return isWorkerOn ? 'good' : 'warn'
})

let pollTimer: ReturnType<typeof setInterval> | undefined
let preparingTimer: ReturnType<typeof setTimeout> | undefined

/** 지금 한 번 물어본다. 준비되지 않았으면 서버가 503과 함께 같은 모양의 답을 준다. */
export async function refreshReadiness(): Promise<void> {
  // 503도 읽어야 하므로 request 대신 fetch를 바로 쓴다.
  try {
    const response = await fetch('/readyz', { headers: { Accept: 'application/json' } })
    readiness.value = (await response.json()) as ReadinessRead
    unreachable.value = false
  } catch {
    readiness.value = null
    unreachable.value = true
  } finally {
    checkedOnce.value = true
    scheduleWhilePreparing()
  }
}

// 내장 모델을 받거나 불러오는 중이면 곧 한 번 더 묻는다(30초 간격과 따로).
function scheduleWhilePreparing(): void {
  clearTimeout(preparingTimer)
  const isPreparing = (readiness.value?.models ?? []).some(
    (model) => model.state === 'downloading' || model.state === 'verifying' || model.state === 'loading',
  )
  if (isPreparing) preparingTimer = setTimeout(() => void refreshReadiness(), PREPARING_POLL_INTERVAL_MS)
}

/** 임베딩 · Jev · LLM이 연결돼 쓸 수 있는지. 진단 탭 · 도우미 창이 쓴다. */
export function isConnected(role: 'embedding' | 'jev' | 'llm'): boolean {
  return readiness.value?.info[role]?.ok === true
}

/** 30초마다 물어보기 시작한다. 앱이 실행될 때 한 번 부른다. */
export function startReadinessPolling(): void {
  if (pollTimer) return
  void refreshReadiness()
  pollTimer = setInterval(() => void refreshReadiness(), POLL_INTERVAL_MS)
}
