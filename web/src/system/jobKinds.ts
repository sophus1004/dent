// 작업을 사람이 읽는 말로: 종류 이름 · 단계 이름 · 상태 이름 · 다시 할 수 있는지. 작업 기록 화면과 알림이 쓴다.
// 종류와 단계 이름은 모듈이 DentModule.jobKinds로 알려 준다. system은 모듈을 모르고 이 목록만 본다.
import { fmt } from '@/system/format'
import type { DentModule, ModuleJobKind } from '@/system/module'
import type { JobEvent, JobRead, JobStatus } from '@/system/types'

// 시스템 층이 넣는 작업의 모듈 이름과 종류 (백엔드 jobs.py의 SYSTEM_MODULE · CLEANUP_KIND)
const SYSTEM_MODULE = 'system'
const SYSTEM_JOB_KINDS: Record<string, ModuleJobKind> = { cleanup: { name: '정리' } }

// 상태 이름
export const JOB_STATUS_NAMES: Record<JobStatus, string> = {
  queued: '대기',
  running: '도는 중',
  done: '완료',
  failed: '실패',
  canceled: '멈춤',
}

// 외부 서버 역할 이름 (사건의 role)
export const ROLE_NAMES: Record<string, string> = { embedding: '임베딩', jev: 'Jev', llm: 'LLM' }

// 끝난 상태 가운데 다시 할 만한 것
const RETRYABLE_STATUSES: JobStatus[] = ['failed', 'canceled']

// 종류 거르기의 한 칸
export interface JobKindOption {
  // 주소에 쓰는 열쇠. 예: 'classification.map'
  key: string
  module: string
  kind: string
  name: string
}

/** 거를 수 있는 모든 작업 종류: 모듈이 알려 준 것 + 시스템 정리. */
export function jobKindOptions(modules: DentModule[]): JobKindOption[] {
  const fromModules = modules.flatMap((module) =>
    Object.entries(module.jobKinds).map(([kind, info]) => ({ key: `${module.id}.${kind}`, module: module.id, kind, name: info.name })),
  )
  const fromSystem = Object.entries(SYSTEM_JOB_KINDS).map(([kind, info]) => ({
    key: `${SYSTEM_MODULE}.${kind}`,
    module: SYSTEM_MODULE,
    kind,
    name: info.name,
  }))
  return [...fromModules, ...fromSystem]
}

/** 작업의 종류 정보. 모르는 종류면 null. */
export function findJobKind(modules: DentModule[], job: Pick<JobRead, 'module' | 'kind'>): ModuleJobKind | null {
  if (job.module === SYSTEM_MODULE) return SYSTEM_JOB_KINDS[job.kind] ?? null
  return modules.find((module) => module.id === job.module)?.jobKinds[job.kind] ?? null
}

/** 종류 이름. 모르는 종류면 백엔드 이름 그대로. 예: '의미 지도' */
export function jobKindName(modules: DentModule[], job: Pick<JobRead, 'module' | 'kind'>): string {
  return findJobKind(modules, job)?.name ?? job.kind
}

/** 단계 이름. 모르면 백엔드 이름 그대로. 예: 'embedding' → '임베딩' */
export function phaseName(kind: ModuleJobKind | null, phase: string): string {
  return kind?.phases?.[phase] ?? phase
}

/** 다시 할 수 있는지: 모듈이 다시 하기를 알려 준 종류이고, 실패 · 멈춤이고, 데이터셋이 남아 있다. */
export function canRetry(kind: ModuleJobKind | null, job: JobRead): boolean {
  return kind?.retry !== undefined && RETRYABLE_STATUSES.includes(job.status) && job.dataset_id !== null
}

/** 지금(끝났으면 마지막) 단계. 단계 없이 도는 작업이면 null. */
export function currentPhase(job: JobRead): string | null {
  const phases = eventsOf(job, 'phase')
  return phases.at(-1)?.phase ?? null
}

/** 어디까지 했는지. 예: '임베딩 60,416 / 82,973'. 단계도 양도 모르면 빈 글자. */
export function progressText(kind: ModuleJobKind | null, job: JobRead): string {
  const phase = currentPhase(job)
  const amount = job.progress_total ? `${fmt(job.progress_done)} / ${fmt(job.progress_total)}` : ''
  if (!phase) return amount
  return amount ? `${phaseName(kind, phase)} ${amount}` : phaseName(kind, phase)
}

/** 한 종류의 사건만 시각 순으로. */
export function eventsOf<T extends JobEvent['type']>(job: JobRead, type: T): Extract<JobEvent, { type: T }>[] {
  return job.events.filter((event): event is Extract<JobEvent, { type: T }> => event.type === type)
}
