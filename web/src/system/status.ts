// /readyz의 답을 화면 줄로 바꾼다. 홈의 시스템 · 모델 칸, 상태 화면, 사이드바 상태 줄이 같이 쓴다.
// 시스템 줄(DB · 스키마 · 저장 공간 · 작업 실행기): "이름 · 상태 · 값". 서버의 detail은 문장일 때가 있어
// 그대로 보이지 않고, 필요한 값(버전, 여유 공간 …)만 뽑아 쓴다.
// 모델 줄(임베딩 · Jev · LLM): /readyz의 models에서 모델 이름 · 출처(내장 · 공급자 · 주소) · 상태 · 값 · 내려받기 진행.
import {
  Bot,
  Cpu,
  Database,
  Gavel,
  HardDrive,
  Layers,
  Waypoints,
  type LucideIcon,
} from '@lucide/vue'

import { LLM_PROVIDERS } from '@/system/connections/connections'
import type { CheckRead, ModelInfoRead, ReadinessRead } from '@/system/readiness'

// good: 됨 · bad: 준비 조건(DB·스키마·저장 공간)이 안 되거나 저장한 연결이 실패 · off: 종료됨이나 미연결(참고 항목)
export type SystemTone = 'good' | 'bad' | 'off'

export interface SystemRow {
  key: string
  icon: LucideIcon
  // 예: 'DB'
  name: string
  tone: SystemTone
  // 상태 낱말. 예: '정상', '최신', '실행 중', '연결됨', '실패', '미연결'
  word: string
  // 참고 값. 예: 'PostgreSQL 16.4 · vector 0.8.0', '여유 120 GB · 모델 4.3 GB'. 없으면 ''
  value: string
  // 값 앞의 태그. 예: '내장'(내장 DB). 없으면 null
  tag: string | null
  // 서버가 준 원래 설명. 준비 조건이 안 될 때 상태 화면에서만 보인다.
  detail: string
}

// 모델 줄의 상태: good 실행 중 · 연결됨, run 내려받는 중 · 확인하는 중 · 불러오는 중, bad 실패, off 미연결
export type ModelTone = SystemTone | 'run'

export interface ModelRow {
  // 역할: embedding · jev · llm
  key: ModelInfoRead['role']
  icon: LucideIcon
  // 예: '임베딩'
  name: string
  // 모델 이름. 예: 'BAAI/bge-m3'. 미연결이거나 Jev 자동 고르기면 ''
  model: string
  // 출처 태그: 내장이면 '내장', LLM이면 공급자 이름, 그 밖의 외부 서버는 ''
  source: string
  // 내장 모델인지 (출처 태그를 강조 색으로)
  embedded: boolean
  tone: ModelTone
  // 상태 낱말: 실행 중 · 연결됨 · 내려받는 중 · 확인하는 중 · 불러오는 중 · 실패 · 미연결
  word: string
  // 값이나 까닭. 예: 'mps · 1024차원 · 47ms', 'api.openai.com · 1117ms', '연결 거부'
  value: string
  // 내려받는 중이면 0~1과 글자(예: '43% · 0.9 / 2.2 GB · 약 2분'), 아니면 null
  progress: { fraction: number; text: string } | null
}

// 서버가 이 확인을 빠뜨렸을 때 쓰는 값
const MISSING: CheckRead = { ok: false, detail: '' }

// 모델 줄의 역할마다 아이콘과 이름
const MODEL_ROLES: Record<ModelInfoRead['role'], { icon: LucideIcon; name: string }> = {
  embedding: { icon: Waypoints, name: '임베딩' },
  jev: { icon: Gavel, name: 'Jev' },
  llm: { icon: Bot, name: 'LLM' },
}

// 1GB (내려받기 진행을 GB로 보인다)
const BYTES_PER_GB = 1024 ** 3

// 1분 (남은 시간을 분으로 보인다)
const SECONDS_PER_MINUTE = 60

/** /readyz의 답을 시스템 줄 네 개(DB · 스키마 · 저장 공간 · 작업 실행기)로. */
export function systemRows(readiness: ReadinessRead): SystemRow[] {
  const db = readiness.checks.db ?? MISSING
  const schema = readiness.checks.schema ?? MISSING
  const storage = readiness.checks.storage ?? MISSING
  const worker = readiness.info.worker ?? MISSING
  const isEmbeddedDb = db.detail.startsWith('내장 · ')
  return [
    requiredRow('db', Database, 'DB', db, '정상', databaseValue(db.detail), isEmbeddedDb ? '내장' : null),
    requiredRow('schema', Layers, '스키마', schema, '최신', insideParens(schema.detail)),
    requiredRow('storage', HardDrive, '저장 공간', storage, '정상', storageValue(storage.detail)),
    {
      key: 'worker',
      icon: Cpu,
      name: '작업 실행기',
      tone: worker.ok ? 'good' : 'off',
      word: worker.ok ? '실행 중' : '종료됨',
      value: queuedJobs(worker.detail),
      tag: null,
      detail: worker.detail,
    },
  ]
}

/** /readyz의 models를 모델 줄 세 개(임베딩 · Jev · LLM)로. */
export function modelRows(readiness: ReadinessRead): ModelRow[] {
  return (readiness.models ?? []).map(modelRow)
}

// 모델 한 줄. 상태마다 낱말과 값을 고른다.
function modelRow(info: ModelInfoRead): ModelRow {
  const role = MODEL_ROLES[info.role]
  const embedded = info.embedded !== null
  const base = {
    key: info.role,
    icon: role.icon,
    name: role.name,
    // Jev는 연결에 모델 이름이 없어(서버가 고름) 내장 모델 이름을 보인다
    model: info.model ?? info.embedded ?? '',
    source: sourceOf(info),
    embedded,
    progress: null,
  }
  if (info.state === 'ok') {
    if (embedded) return { ...base, tone: 'good', word: '실행 중', value: withDevice(info) }
    // 외부 서버: 모델 이름이 없으면(Jev 자동 고르기) 첫 줄에 주소를 둔다. LLM의 값은 모델 이름으로 시작해 뺀다.
    const host = hostOf(info.base_url) ?? ''
    const detail = withoutPrefix(info.detail, info.model)
    const model = base.model || host
    const value = base.model ? joined([host, detail]) : detail
    return { ...base, model, tone: 'good', word: '연결됨', value }
  }
  if (info.state === 'downloading' || info.state === 'verifying') {
    const word = info.state === 'downloading' ? '내려받는 중' : '확인하는 중'
    return { ...base, tone: 'run', word, value: '', progress: downloadProgress(info) }
  }
  if (info.state === 'loading') {
    return { ...base, tone: 'run', word: '불러오는 중', value: joined([info.device, info.detail]) }
  }
  if (info.state === 'failed') return { ...base, tone: 'bad', word: '실패', value: info.detail }
  return { ...base, model: '', source: embedded ? '내장' : '', tone: 'off', word: '미연결', value: '' }
}

// 출처 태그: 내장 · LLM 공급자 이름 · 그 밖은 없음(값에 주소가 보인다)
function sourceOf(info: ModelInfoRead): string {
  if (info.embedded !== null) return '내장'
  return LLM_PROVIDERS.find((provider) => provider.value === info.provider)?.name ?? ''
}

// 'https://api.openai.com/v1' → 'api.openai.com'. 없으면 null
function hostOf(address: string | null): string | null {
  if (!address) return null
  try {
    return new URL(address).host
  } catch {
    return null
  }
}

// 내장 모델의 값: 장치를 앞에 둔다(Jev처럼 확인 결과가 이미 장치로 시작하면 그대로)
function withDevice(info: ModelInfoRead): string {
  const hasDevice = info.device !== null && info.detail.startsWith(info.device)
  return hasDevice ? info.detail : joined([info.device, info.detail])
}

// 'gpt-5.4-mini · 1292ms'에서 앞의 'gpt-5.4-mini · '를 뗀다. 앞이 다르면 그대로
function withoutPrefix(detail: string, prefix: string | null): string {
  const head = prefix ? `${prefix} · ` : null
  return head && detail.startsWith(head) ? detail.slice(head.length) : detail
}

// 비지 않은 조각을 ' · '로 잇는다
function joined(parts: (string | null | undefined)[]): string {
  return parts.filter((part) => part).join(' · ')
}

// 내려받기 진행: 0~1과 '43% · 0.9 / 2.2 GB · 약 2분'
function downloadProgress(info: ModelInfoRead): { fraction: number; text: string } {
  const total = info.total_bytes ?? 0
  const done = info.done_bytes ?? 0
  const fraction = total > 0 ? Math.min(done / total, 1) : 0
  const sizes = `${(done / BYTES_PER_GB).toFixed(1)} / ${(total / BYTES_PER_GB).toFixed(1)} GB`
  const eta = info.eta_s === null ? null : `약 ${Math.max(1, Math.round(info.eta_s / SECONDS_PER_MINUTE))}분`
  return { fraction, text: joined([`${Math.floor(fraction * 100)}%`, sizes, eta]) }
}

// 준비 조건 한 줄. 안 되면 '오류'
function requiredRow(
  key: string,
  icon: LucideIcon,
  name: string,
  check: CheckRead,
  okWord: string,
  okValue: string,
  tag: string | null = null,
): SystemRow {
  const tone = check.ok ? 'good' : 'bad'
  const word = check.ok ? okWord : '오류'
  const value = check.ok ? okValue : ''
  return { key, icon, name, tone, word, value, tag: check.ok ? tag : null, detail: check.detail }
}

// 'localhost:5432/dent · PostgreSQL 16.4 · vector 0.8.0' → 'PostgreSQL 16.4 · vector 0.8.0'
// '내장 · /…/storage/pgdata · PostgreSQL 18.6 · vector 0.8.6' → 'PostgreSQL 18.6 · vector 0.8.6'
function databaseValue(detail: string): string {
  const parts = detail.split(' · ')
  return parts.slice(parts[0] === '내장' ? 2 : 1).join(' · ')
}

// '최신 (a1b2c3)' → 'a1b2c3'
function insideParens(detail: string): string {
  return detail.match(/\((.+)\)/)?.[1] ?? ''
}

// '/Users/…/storage · 여유 120GB · 모델 4.3GB' → '여유 120 GB · 모델 4.3 GB'
function storageValue(detail: string): string {
  const free = detail.match(/여유 (\d+)GB/)?.[1]
  const models = detail.match(/모델 ([\d.]+)GB/)?.[1]
  return joined([free ? `여유 ${free} GB` : null, models ? `모델 ${models} GB` : null])
}

// '실행 중 · 대기 작업 0건' → '대기 0건'
function queuedJobs(detail: string): string {
  const count = detail.match(/대기 작업 (\d+)건/)?.[1]
  return count ? `대기 ${count}건` : ''
}
