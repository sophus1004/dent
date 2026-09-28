// 검색 뜻 분석(지도 · 근접 중복 · 기준 검색 순위 · 제안) 상태를 읽고, 만들기 · 멈추기를 보내고,
// 만드는 동안 작업 진행률을 1초마다 본다. 진단 탭 · 데이터 탭의 지도 보기 · 제안 탭이 같이 쓴다. 화면을 떠나면 묻기를 멈춘다(useJob).
//   단계     작업 result의 phase: embedding(임베딩 n / m) → projecting(좌표) → analyzing(무리 · 근접 중복)
//            → ranking(기준 검색) → judging(Jev n / m) → saving(저장)
//   남은 시간 임베딩 · Jev 단계에서만, 그 단계가 시작한 뒤 처리한 속도로 어림한다.
import { computed, onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'

import { errorMessage, isAbortError } from '@/system/http'
import { isJobFinished, useJob } from '@/system/jobs'

import { cancelAnalysis, getAnalysis, startAnalysis } from '@/modules/retrieval/api'
import type { AnalysisRead, AnalysisStateRead } from '@/modules/retrieval/types'

// 작업 단계. queued는 작업 실행기가 아직 꺼내지 않은 것
export type AnalysisPhase = 'queued' | 'embedding' | 'projecting' | 'analyzing' | 'ranking' | 'judging' | 'saving'

// 단계 이름 (작업 기록 화면도 이 이름을 쓴다)
export const ANALYSIS_PHASE_NAMES: Record<AnalysisPhase, string> = {
  queued: '대기',
  embedding: '임베딩',
  projecting: '좌표 계산',
  analyzing: '분석',
  ranking: '기준 검색',
  judging: 'Jev 확인',
  saving: '저장',
}

// 남은 시간을 어림하는 단계 (한 건씩 고르게 처리하는 단계)
const TIMED_PHASES: AnalysisPhase[] = ['embedding', 'judging']

// 1초 = 1000밀리초
const MS_PER_SECOND = 1000

// 만드는 중인 뜻 분석의 진행
export interface AnalysisProgress {
  phase: AnalysisPhase
  done: number
  // 전체 양. 모르면 null
  total: number | null
  // 0~1. 모르면 null
  ratio: number | null
  // 남은 시간(초). 어림할 수 없으면 null
  remainingSeconds: number | null
}

/** 뜻 분석 상태 · 진행과 만들기 · 멈추기. datasetId가 바뀌면 새로 읽는다. */
export function useAnalysis(datasetId: MaybeRefOrGetter<number>) {
  const state = ref<AnalysisStateRead | null>(null)
  const loadError = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  const sending = ref(false)
  let controller: AbortController | null = null

  // 만드는 중(대기 · 실행)인 시도. 없으면 null
  const building = computed<AnalysisRead | null>(() => {
    const run = state.value?.run
    const isBuilding = run?.status === 'queued' || run?.status === 'running'
    return isBuilding ? run : null
  })
  const done = computed(() => state.value?.done ?? null)
  const { job } = useJob(() => building.value?.job_id ?? null)

  const progress = computed<AnalysisProgress | null>(() => {
    if (!building.value) return null
    const current = job.value
    const result = current?.result ?? null
    const phase = (result?.phase as AnalysisPhase | undefined) ?? 'queued'
    const doneCount = current?.progress_done ?? 0
    const total = current?.progress_total ?? null
    const ratio = total ? Math.min(1, doneCount / total) : null
    return { phase, done: doneCount, total, ratio, remainingSeconds: remaining(phase, doneCount, total, result) }
  })

  // 작업이 끝나면(다 만듦 · 실패 · 취소) 상태를 다시 읽는다.
  watch(
    () => job.value?.status,
    (status) => {
      if (status && isJobFinished(status)) void reload()
    },
  )
  watch(() => toValue(datasetId), reload, { immediate: true })
  onBeforeUnmount(() => controller?.abort())

  async function reload(): Promise<void> {
    controller?.abort()
    const current = new AbortController()
    controller = current
    try {
      state.value = await getAnalysis(toValue(datasetId), current.signal)
      loadError.value = null
    } catch (error) {
      if (!isAbortError(error)) loadError.value = errorMessage(error)
    }
  }

  async function send(action: (id: number) => Promise<AnalysisStateRead>): Promise<void> {
    sending.value = true
    actionError.value = null
    try {
      state.value = await action(toValue(datasetId))
    } catch (error) {
      actionError.value = errorMessage(error)
      void reload()
    } finally {
      sending.value = false
    }
  }

  return {
    state,
    done,
    loadError,
    actionError,
    sending,
    building,
    progress,
    reload,
    start: () => send(startAnalysis),
    cancel: () => send(cancelAnalysis),
  }
}

// useAnalysis가 돌려주는 것
export type Analysis = ReturnType<typeof useAnalysis>

function remaining(
  phase: AnalysisPhase,
  done: number,
  total: number | null,
  result: Record<string, unknown> | null,
): number | null {
  const startedAt = typeof result?.phase_started_at === 'string' ? Date.parse(result.phase_started_at) : NaN
  const canEstimate = TIMED_PHASES.includes(phase) && total !== null && done > 0 && !Number.isNaN(startedAt)
  if (!canEstimate) return null
  const elapsedSeconds = (Date.now() - startedAt) / MS_PER_SECOND
  const perSecond = done / Math.max(elapsedSeconds, 1)
  return Math.max(0, (total - done) / perSecond)
}

/** 남은 시간 글자. 60초 아래면 '1분 미만', 그 위는 '약 n분'. */
export function remainingText(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  return minutes < 1 ? '1분 미만' : `약 ${minutes}분`
}

/** 모델 이름의 마지막 부분. 'BAAI/bge-m3' → 'bge-m3' */
export function shortModelName(name: string | null): string {
  if (!name) return '—'
  return name.split('/').pop() || name
}
