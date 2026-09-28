// 오래 걸리는 작업(system_jobs)을 1초마다 물어봐서 진행률을 보여 준다.
// 예: const { job, isFinished } = useJob(() => importRead.value?.job_id ?? null)
import {
  computed,
  onBeforeUnmount,
  ref,
  toValue,
  watch,
  type ComputedRef,
  type MaybeRefOrGetter,
  type Ref,
} from 'vue'

import { getJob } from '@/system/api'
import { ApiError, errorMessage } from '@/system/http'
import type { JobRead, JobStatus } from '@/system/types'

// 다시 물어보는 간격(밀리초)
const POLL_INTERVAL_MS = 1000

// 더 기다릴 필요가 없는 상태
const FINISHED_STATUSES: JobStatus[] = ['done', 'failed', 'canceled']

/** 작업이 끝난 상태인지(done·failed·canceled). */
export function isJobFinished(status: JobStatus): boolean {
  return FINISHED_STATUSES.includes(status)
}

/** 진행률(0~1). 전체 양을 모르면 null. */
export function jobProgress(job: JobRead): number | null {
  if (!job.progress_total) return null
  return Math.min(1, job.progress_done / job.progress_total)
}

// 1초 = 1000밀리초 · 1분 = 60초
const MS_PER_SECOND = 1000
const SECONDS_PER_MINUTE = 60

/**
 * 지금 단계의 남은 시간(초). 단계가 시작한 뒤(result.phase_started_at) 처리한 속도로 남은 양을 나눈다.
 * 도는 중이 아니거나, 전체 양을 모르거나, 아직 하나도 못 했으면 null.
 */
export function jobRemainingSeconds(job: JobRead, now: number = Date.now()): number | null {
  const startedAt = typeof job.result?.phase_started_at === 'string' ? Date.parse(job.result.phase_started_at) : NaN
  const total = job.progress_total
  const canEstimate = job.status === 'running' && total !== null && job.progress_done > 0 && !Number.isNaN(startedAt)
  if (!canEstimate) return null
  const elapsedSeconds = (now - startedAt) / MS_PER_SECOND
  const perSecond = job.progress_done / Math.max(elapsedSeconds, 1)
  return Math.max(0, (total - job.progress_done) / perSecond)
}

/** 남은 시간 글자. 1분 아래면 '1분 미만', 그 위는 '약 n분'. */
export function remainingText(seconds: number): string {
  const minutes = Math.round(seconds / SECONDS_PER_MINUTE)
  return minutes < 1 ? '1분 미만' : `약 ${minutes}분`
}

// useJob이 돌려주는 것
export interface JobWatch {
  // 마지막으로 받은 작업 상태. 아직 못 받았으면 null
  job: Ref<JobRead | null>
  // 물어보다 난 문제. 잠깐 끊긴 것이면 다음 번에 다시 물어본다.
  error: Ref<string | null>
  // done·failed·canceled 중 하나가 됐는지
  isFinished: ComputedRef<boolean>
}

/** 작업을 끝날 때까지 1초마다 물어본다. jobId가 바뀌면 새 작업을 본다. 화면을 떠나면 멈춘다. */
export function useJob(jobId: MaybeRefOrGetter<number | null>): JobWatch {
  const job = ref<JobRead | null>(null)
  const error = ref<string | null>(null)
  const isFinished = computed(() => job.value !== null && isJobFinished(job.value.status))

  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined

  function stop(): void {
    clearTimeout(timer)
    controller?.abort()
  }

  async function poll(id: number): Promise<void> {
    const current = new AbortController()
    controller = current
    try {
      job.value = await getJob(id, current.signal)
      error.value = null
    } catch (caught) {
      if (current.signal.aborted) return
      error.value = errorMessage(caught)
      const isMissing = caught instanceof ApiError && caught.status === 404
      if (isMissing) return
    }
    const shouldContinue = !current.signal.aborted && !isFinished.value
    if (shouldContinue) timer = setTimeout(() => void poll(id), POLL_INTERVAL_MS)
  }

  watch(
    () => toValue(jobId),
    (id) => {
      stop()
      job.value = null
      error.value = null
      if (id !== null) void poll(id)
    },
    { immediate: true },
  )
  onBeforeUnmount(stop)

  return { job, error, isFinished }
}
