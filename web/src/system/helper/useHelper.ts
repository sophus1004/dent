// 도우미 창의 상태(모든 모듈): 가장 최근 실행 · 사건들 · 되돌린 카드와 시작 · 멈추기 · 되돌리기 · 허락 · AI로 고치기.
// 시작 · 되돌리기 · 허락 · 남은 것 · AI로 고치기는 무엇을 고치는지 아는 모듈의 API라서 모듈이 HelperApi로 넘긴다.
// 도는 동안(running) 1초마다 실행과 새 사건(마지막 번호 다음부터)을 이어 읽는다. 끝나거나 화면을 떠나면 멈춘다.
// 데이터를 바꾼 사건(바꿈 · 결과)이 오면 notifyHelperChanged로 진단 탭 · 머리가 다시 읽게 한다.
import { computed, onBeforeUnmount, ref, toValue, watch, type Component, type MaybeRefOrGetter } from 'vue'
import type { RouteLocationRaw } from 'vue-router'

import { getHelperRun, listHelperEvents, stopHelperRun } from '@/system/api'
import { notifyHelperChanged } from '@/system/helper/helperSignals'
import { errorMessage, isAbortError } from '@/system/http'
import type { HelperEventRead, HelperLeftItem, HelperLeftRead, HelperRunRead, HelperStateRead } from '@/system/types'

// 처음 화면(실행 없음)에 보일 한 번 실행의 단계 한 줄. 허락이 필요한 단계는 permission.
// key가 있으면 실행 설정 카드가 알려 주는 그 단계의 값(예: 512토큰 · 오버랩 없음)을 옆에 보인다.
export interface PlanStep {
  title: string
  permission?: boolean
  key?: string
}

// AI로 고치기에 사람이 고친 더할 수 {검사 열쇠: {칸 열쇠: 수}} (예: 라벨 균형의 라벨마다 새 문장 수)
export type FixAdds = Record<string, Record<string, number>>

// 모듈이 도우미 창에 넘기는 것: 모듈 API 부르기와 보류 카드의 [정하기] · 남은 것 줄의 → 주소
export interface HelperApi {
  getHelper(datasetId: number, signal?: AbortSignal): Promise<HelperStateRead>
  startHelper(datasetId: number): Promise<HelperRunRead>
  undoHelper(runId: number, eventId?: number): Promise<unknown>
  // 흐름 안에 허락 단계가 있는 모듈만 (검색: 문서 나누기 · 질의 만들기). action은 허락 카드의 다른 동작(예: 먼저 나누기)
  answerPermission?(runId: number, approve: boolean, action?: string): Promise<HelperRunRead>
  // 남은 것(지금 진단으로 센다)과 고른 것을 AI로 고치기 (adds: 사람이 고친 더할 수)
  getLeft(datasetId: number, signal?: AbortSignal): Promise<HelperLeftRead>
  startFix(datasetId: number, keys: string[], adds?: FixAdds): Promise<HelperRunRead>
  // 실행 설정 카드. props: datasetId · startable · canStart · closable,
  // emits: start · close · summary(단계 열쇠 → 값 글). 없으면 창은 [시작]만 보인다.
  settingsCard?: Component
  // 보류 카드의 [정하기]: 데이터 탭에서 그 글을 찾는 주소
  holdRoute(datasetId: number, text: string): RouteLocationRaw
  // 남은 것 줄의 →: 그 문제만 보는 곳
  leftRoute(datasetId: number, item: HelperLeftItem): RouteLocationRaw
}

// 도는 동안 묻는 간격(밀리초)
const POLL_MS = 1_000

// 사건을 한 번에 읽는 수 (백엔드 기본과 같다). 이보다 적게 오면 다 읽은 것이다.
const EVENT_PAGE_SIZE = 200

// 데이터를 바꾼 사건 종류
const CHANGING_KINDS = new Set(['change', 'result'])

/** 데이터셋의 도우미. datasetId가 바뀌면 새로 읽는다. */
export function useHelper(datasetId: MaybeRefOrGetter<number>, api: HelperApi) {
  const run = ref<HelperRunRead | null>(null)
  const events = ref<HelperEventRead[]>([])
  const undone = ref<Set<number>>(new Set())
  const loading = ref(true)
  const loadError = ref<string | null>(null)
  const actionError = ref<string | null>(null)
  // 보내는 중인 일. 하는 동안 버튼을 막는다.
  const busy = ref<'start' | 'stop' | 'undo' | 'permission' | 'fix' | null>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let controller: AbortController | null = null
  let lastId = 0

  const isRunning = computed(() => run.value?.status === 'running')

  watch(() => toValue(datasetId), load, { immediate: true })
  onBeforeUnmount(stopPolling)

  function stopPolling(): void {
    if (timer) clearTimeout(timer)
    timer = null
    controller?.abort()
  }

  async function load(): Promise<void> {
    stopPolling()
    loading.value = true
    loadError.value = null
    run.value = null
    events.value = []
    undone.value = new Set()
    lastId = 0
    const current = new AbortController()
    controller = current
    try {
      const state = await api.getHelper(toValue(datasetId), current.signal)
      run.value = state.run
      undone.value = new Set(state.undone_events)
      if (state.run) await fetchEvents(state.run.id, current.signal)
      schedule()
    } catch (error) {
      if (!isAbortError(error)) loadError.value = errorMessage(error)
    } finally {
      if (controller === current) loading.value = false
    }
  }

  // 마지막 번호 다음 사건들을 다 읽는다. 데이터를 바꾼 사건이 있었으면 true
  async function fetchEvents(runId: number, signal?: AbortSignal): Promise<boolean> {
    let changed = false
    for (;;) {
      const page = await listHelperEvents(runId, lastId, signal)
      if (page.length) {
        events.value = [...events.value, ...page]
        lastId = page[page.length - 1].id
        changed = changed || page.some((event) => CHANGING_KINDS.has(event.kind))
      }
      if (page.length < EVENT_PAGE_SIZE) return changed
    }
  }

  function schedule(): void {
    if (timer) clearTimeout(timer)
    timer = isRunning.value ? setTimeout(poll, POLL_MS) : null
  }

  async function poll(): Promise<void> {
    const current = run.value
    if (!current) return
    const signal = (controller = new AbortController()).signal
    try {
      const fresh = await getHelperRun(current.id, signal)
      const changed = await fetchEvents(current.id, signal)
      run.value = fresh
      const hasEnded = current.status === 'running' && fresh.status !== 'running'
      if (changed || hasEnded) notifyHelperChanged()
    } catch (error) {
      if (isAbortError(error)) return
      // 한 번 못 읽어도 다음에 다시 묻는다.
    }
    schedule()
  }

  async function send(kind: NonNullable<typeof busy.value>, action: () => Promise<void>): Promise<void> {
    if (busy.value) return
    busy.value = kind
    actionError.value = null
    try {
      await action()
    } catch (error) {
      actionError.value = errorMessage(error)
    } finally {
      busy.value = null
    }
  }

  /** 도우미 시작 (끝난 실행이 있으면 새 실행). */
  function start(): Promise<void> {
    return send('start', async () => {
      const created = await api.startHelper(toValue(datasetId))
      stopPolling()
      run.value = created
      events.value = []
      undone.value = new Set()
      lastId = 0
      schedule()
    })
  }

  /** [멈추기]. 다음 도구를 부르기 전에 멈춘다. */
  function stop(): Promise<void> {
    return send('stop', async () => {
      if (run.value) run.value = await stopHelperRun(run.value.id)
    })
  }

  /** 바꾼 카드 하나(eventId) 또는 실행 전체를 되돌린다. */
  function undo(eventId?: number): Promise<void> {
    return send('undo', async () => {
      const current = run.value
      if (!current) return
      await api.undoHelper(current.id, eventId)
      const state = await api.getHelper(toValue(datasetId))
      run.value = state.run
      undone.value = new Set(state.undone_events)
      await fetchEvents(current.id)
      notifyHelperChanged()
    })
  }

  /** 허락(새 글 만들기 등)에 답한다. action은 허락 카드의 다른 동작(예: 먼저 나누기). */
  function answer(approve: boolean, action?: string): Promise<void> {
    return send('permission', async () => {
      const current = run.value
      if (!current || !api.answerPermission) return
      run.value = await api.answerPermission(current.id, approve, action)
      await fetchEvents(current.id)
      schedule()
    })
  }

  /** 고른 남은 것을 AI로 고친다. 같은 실행에 단계가 붙으므로 사건은 이어 읽는다(실행이 없었으면 새 실행). */
  function fix(keys: string[], adds?: FixAdds): Promise<void> {
    return send('fix', async () => {
      const previous = run.value
      const fixed = await api.startFix(toValue(datasetId), keys, adds)
      stopPolling()
      if (!previous || previous.id !== fixed.id) {
        events.value = []
        undone.value = new Set()
        lastId = 0
      }
      run.value = fixed
      await fetchEvents(fixed.id)
      schedule()
    })
  }

  return { run, events, undone, loading, loadError, actionError, busy, isRunning, load, start, stop, undo, answer, fix }
}

// useHelper가 돌려주는 것
export type HelperState = ReturnType<typeof useHelper>
