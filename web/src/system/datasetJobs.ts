// 데이터셋 하나에서 기다리거나 도는 작업(system_jobs)을 서버에 물어 화면 어디서나 같은 값을 쓴다.
// 작업 실행기 여럿이 데이터셋끼리 작업을 함께 돌리므로, 진행은 화면 부품이 들고 있지 않고 늘 서버에서 읽는다:
// 다른 데이터셋을 보다 돌아와도, 새로 열어도 그때의 진행이 보인다(데이터셋 머리의 작업 줄 · 가져오기 · 내보내기 · 살피기 …).
// 같은 데이터셋을 보는 부품이 여럿이어도 묻기는 하나다. 기다리거나 도는 작업이 있으면 1초, 없으면 5초마다 묻는다.
// 예: const { running, queued, activeOf, refresh } = useDatasetJobs(() => props.datasetId)
import {
  computed,
  onBeforeUnmount,
  ref,
  shallowRef,
  toValue,
  watch,
  type ComputedRef,
  type MaybeRefOrGetter,
  type Ref,
} from 'vue'

import { listJobs } from '@/system/api'
import type { JobRead } from '@/system/types'

// 다시 묻는 간격(밀리초): 작업이 있을 때 · 없을 때
const ACTIVE_POLL_MS = 1000
const IDLE_POLL_MS = 5000

// 한 데이터셋에서 한 번에 읽는 작업 수의 상한
const LIST_LIMIT = 50

// 데이터셋 하나의 묻기: 읽은 작업 · 보는 부품 수 · 다음 묻기
interface Watcher {
  jobs: Ref<JobRead[]>
  users: number
  timer: ReturnType<typeof setTimeout> | undefined
  controller: AbortController | undefined
}

const watchers = new Map<number, Watcher>()

// useDatasetJobs가 돌려주는 것
export interface DatasetJobs {
  // 기다리거나 도는 작업(넣은 순서)
  jobs: ComputedRef<JobRead[]>
  // 도는 작업. 데이터셋마다 하나다. 없으면 null
  running: ComputedRef<JobRead | null>
  // 기다리는 작업(넣은 순서)
  queued: ComputedRef<JobRead[]>
  // 이 종류의 기다리거나 도는 작업. 없으면 null
  activeOf: (module: string, kind: string) => JobRead | null
  // 바로 다시 묻는다(작업을 넣은 직후)
  refresh: () => Promise<void>
}

/** 데이터셋의 기다리거나 도는 작업을 묻는다. 데이터셋 번호가 바뀌면 그 데이터셋을, 화면을 떠나면 멈춘다. */
export function useDatasetJobs(datasetId: MaybeRefOrGetter<number | null>): DatasetJobs {
  // shallowRef: 안의 jobs(ref)를 풀지 않고 그대로 쓴다.
  const current = shallowRef<Watcher | null>(null)

  watch(
    () => toValue(datasetId),
    (id, previous) => {
      if (previous !== undefined && previous !== null) release(previous)
      current.value = id === null ? null : subscribe(id)
    },
    { immediate: true },
  )
  onBeforeUnmount(() => {
    const id = toValue(datasetId)
    if (id !== null) release(id)
  })

  const jobs = computed(() => current.value?.jobs.value ?? [])
  const running = computed(() => jobs.value.find((job) => job.status === 'running') ?? null)
  const queued = computed(() => jobs.value.filter((job) => job.status === 'queued'))

  function activeOf(module: string, kind: string): JobRead | null {
    return jobs.value.find((job) => job.module === module && job.kind === kind) ?? null
  }

  async function refresh(): Promise<void> {
    const id = toValue(datasetId)
    if (id !== null) await poll(id)
  }

  return { jobs, running, queued, activeOf, refresh }
}

function subscribe(datasetId: number): Watcher {
  let watcher = watchers.get(datasetId)
  if (!watcher) {
    watcher = { jobs: ref([]), users: 0, timer: undefined, controller: undefined }
    watchers.set(datasetId, watcher)
    void poll(datasetId)
  }
  watcher.users += 1
  return watcher
}

function release(datasetId: number): void {
  const watcher = watchers.get(datasetId)
  if (!watcher) return
  watcher.users -= 1
  if (watcher.users > 0) return
  clearTimeout(watcher.timer)
  watcher.controller?.abort()
  watchers.delete(datasetId)
}

async function poll(datasetId: number): Promise<void> {
  const watcher = watchers.get(datasetId)
  if (!watcher) return
  clearTimeout(watcher.timer)
  watcher.controller?.abort()
  const controller = new AbortController()
  watcher.controller = controller
  try {
    const page = await listJobs(
      { dataset_id: datasetId, status: ['queued', 'running'], limit: LIST_LIMIT },
      controller.signal,
    )
    // 최근에 넣은 것부터 오므로 넣은 순서로 뒤집는다.
    watcher.jobs.value = [...page.items].sort((left, right) => left.id - right.id)
  } catch {
    if (controller.signal.aborted) return
    // 서버가 잠깐 안 되면 다음에 다시 묻는다(서버 상태는 사이드바 아래 상태 줄이 알린다).
  }
  if (controller.signal.aborted || watchers.get(datasetId) !== watcher) return
  const interval = watcher.jobs.value.length ? ACTIVE_POLL_MS : IDLE_POLL_MS
  watcher.timer = setTimeout(() => void poll(datasetId), interval)
}
