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
import type { JobRead, JobStatus, JobWaiting } from '@/system/types'

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

// 남은 시간을 잴 때 보는 최근 구간(밀리초)과, 최근 구간으로 재기 시작하는 최소 길이(밀리초).
// 단계 전체 평균은 다른 작업이 같은 모델 서버를 함께 쓰기 시작하면(작업 실행기 여럿) 속도가 바뀐 것을 늦게 따라간다.
const RECENT_WINDOW_MS = 60_000
const MIN_RECENT_SPAN_MS = 5_000

// 작업마다 지금 단계의 진행 기록: (본 시각, 처리한 양). 단계가 바뀌면 새로 쌓는다.
const progressSamples = new Map<number, { phaseStartedAt: string; points: [number, number][] }>()

/**
 * 지금 단계의 남은 시간(초). 최근 1분 동안 처리한 속도로 남은 양을 나눈다. 기록이 아직 짧으면 단계가 시작한 뒤
 * (result.phase_started_at) 처리한 속도로. 도는 중이 아니거나, 전체 양을 모르거나, 아직 하나도 못 했으면 null.
 */
export function jobRemainingSeconds(job: JobRead, now: number = Date.now()): number | null {
  const phaseStartedAt = typeof job.result?.phase_started_at === 'string' ? job.result.phase_started_at : null
  const startedAt = phaseStartedAt === null ? NaN : Date.parse(phaseStartedAt)
  const total = job.progress_total
  const canEstimate = job.status === 'running' && total !== null && job.progress_done > 0 && !Number.isNaN(startedAt)
  if (!canEstimate || phaseStartedAt === null) {
    progressSamples.delete(job.id)
    return null
  }
  const left = total - job.progress_done
  const recent = recentRate(job.id, phaseStartedAt, job.progress_done, now)
  if (recent !== null) return recent > 0 ? Math.max(0, left / recent) : null
  const elapsedSeconds = (now - startedAt) / MS_PER_SECOND
  const perSecond = job.progress_done / Math.max(elapsedSeconds, 1)
  return Math.max(0, left / perSecond)
}

// 최근 구간의 처리 속도(1초에). 기록이 MIN_RECENT_SPAN_MS보다 짧으면 null.
function recentRate(jobId: number, phaseStartedAt: string, done: number, now: number): number | null {
  let sample = progressSamples.get(jobId)
  if (!sample || sample.phaseStartedAt !== phaseStartedAt) {
    sample = { phaseStartedAt, points: [] }
    progressSamples.set(jobId, sample)
  }
  const last = sample.points.at(-1)
  if (!last || last[1] !== done) sample.points.push([now, done])
  while (sample.points.length > 2 && now - sample.points[0][0] > RECENT_WINDOW_MS) sample.points.shift()
  const first = sample.points[0]
  const span = now - first[0]
  if (span < MIN_RECENT_SPAN_MS) return null
  return (done - first[1]) / (span / MS_PER_SECOND)
}

// 기다리는 까닭 낱말 (작업의 waiting)
const WAITING_WORDS: Record<JobWaiting, string> = {
  dataset: '앞 작업',
  worker: '실행기 모두 바쁨',
}

/** 기다리는 까닭: '앞 작업'(같은 데이터셋의 앞 작업을 기다림) · '실행기 모두 바쁨'. 대기가 아니거나 모르면 null. */
export function waitingReason(job: Pick<JobRead, 'waiting'> | null | undefined): string | null {
  return job?.waiting ? WAITING_WORDS[job.waiting] : null
}

/** 대기 낱말: '대기 · 앞 작업' · '대기 · 실행기 모두 바쁨' · 까닭을 모르면 '대기'. */
export function waitingText(job: Pick<JobRead, 'waiting'> | null | undefined): string {
  const reason = waitingReason(job)
  return reason ? `대기 · ${reason}` : '대기'
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
