<!--
  검토 (#/generation/<id>/review?check=<검사>): 진단의 검사 줄 [→]로 들어와 한 검사의 문제를 한 건씩 보고 결정한다.
    도구 줄   [← 진단] 검토 · 고칠 것 n건 · 끝 n(이번 검토에서 한 결정)
    왼쪽      고칠 것: 단계(1 지시 · 2 응답)마다 문제가 남은 검사 · 남은 수(등급 색) · 끝 n. 누르면 그 검사의 큐.
              기다림: 뜻 분석 · Jev가 있어야 재는 검사(다음 판, 흐림)
    가운데    대화 한 건(ConversationView, 문제 자리 칠함 · 보고 있는 검사는 진하게) · n / m · #번호 · 출처 · 응답 수 · [←] [→]
              · 진행 막대(결정한 수 / 큐). 이미 결정한 대화면 맨 위에 결정 · [되돌리기].
    오른쪽    결정: 검사가 허락한 결정(서버 actions, 맨 위가 규칙이 권하는 것) + 나중에. 숫자 키 1~n.
              결정마다 하는 쪽 태그(규칙 · 사람 · LLM). 다시 쓰기는 LLM이 연결돼 있을 때만 켜진다(응답만, 출처 합성).
              뜻 분석의 Jev가 규칙의 문제(거절 · 짧은 응답)를 괜찮다고 봤으면 [그대로 두기]를 권한다(가운데에 Jev 값).
              근접 중복이면 가운데 위에 뜻이 가까운 짝(번호 · 유사도 · 지시 앞부분).
              결정 기록: 되돌리지 않은 결정 최근 것부터 · [되돌리기](Z는 맨 위 것).
              남은 n건 한 번에: 모두 빼기 · 모두 그대로 두기 · Jev 괜찮음 모두 그대로 두기(확인 창을 거친다,
              대화마다 결정 기록이 남아 하나씩 되돌릴 수 있다).
  결정은 바로 반영된다(POST /conversations/{id}/decide). 결정하면 다음 대화로 넘어가고, 진단 수 · 머리를 다시 읽는다.
  큐는 검사를 고를 때 한 번 읽는다(문제가 남은 대화 번호들). 결정한 대화도 큐에 남아 ←로 돌아가 볼 수 있다.
  키: 1~n 결정 · ← → 앞뒤 · Z 되돌리기. 입력 칸 · 확인 창에서는 받지 않는다.
  폭이 70rem보다 좁으면 세 칸을 위아래로 쌓는다(가운데 → 오른쪽 → 왼쪽, @container).
-->
<script setup lang="ts">
import {
  ArrowLeft,
  ArrowRight,
  CircleCheck,
  Clock,
  LoaderCircle,
  SkipForward,
  TriangleAlert,
  Undo2,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { GRADES } from '@/system/diagnosis/grades'
import { helperChanged } from '@/system/helper/helperSignals'
import { clockText, fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { isConnected } from '@/system/readiness'
import { pushToast } from '@/system/toasts'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Skeleton } from '@/system/ui/skeleton'

import {
  decide,
  decideAll,
  getConversation,
  getOverview,
  getReviewQueue,
  listChanges,
  listNearPartners,
  undoChange,
} from '@/modules/generation/api'
import {
  ACTION_DONE_WORDS,
  ACTION_WHO,
  ACTION_WORDS,
  CHECK_WORDS,
  PENDING_BY_STAGE,
  SOURCE_WORDS,
  checkWords,
} from '@/modules/generation/checks'
import ConversationView from '@/modules/generation/ConversationView.vue'
import { useDatasetRefresh } from '@/modules/generation/datasetRefresh'
import type {
  Action,
  ChangeRead,
  CheckKey,
  CheckRead,
  ConversationRead,
  DatasetRead,
  Grade,
  NearPartnerRead,
  OverviewRead,
} from '@/modules/generation/types'
import WhoTag from '@/modules/generation/WhoTag.vue'

const props = defineProps<{
  dataset: DatasetRead
}>()

// 왼쪽 목록의 단계 이름
const STAGE_NAMES: Record<number, string> = { 1: '1 지시', 2: '2 응답' }

// 늘 흐린 줄인 검사(서버가 아직 재지 않는 것)의 까닭
const ALWAYS_PENDING_REASON = '다음 판'

// Jev 값을 보이는 검사 (규칙이 잡았지만 Jev가 한 번 더 본 것) · 짝을 보이는 검사
const JEV_CHECKS: CheckKey[] = ['refusal', 'short', 'mismatch']

// 결정 기록에 보일 최대 줄 수 (서버도 최근 30개만 준다)
const CHANGE_LOG_MAX = 30

const route = useRoute()
const router = useRouter()
const refreshDataset = useDatasetRefresh()

const datasetId = computed(() => props.dataset.id)
const overview = ref<OverviewRead | null>(null)
const loadError = ref<string | null>(null)

// 지금 검사의 큐
const ids = ref<number[]>([])
const queueTotal = ref(0)
const queueLoading = ref(false)
// 보고 있는 자리 (ids.length면 큐 끝)
const position = ref(0)
const conversation = ref<ConversationRead | null>(null)
const conversationError = ref<string | null>(null)
// 이번 큐에서 한 결정: 대화 번호 → 결정 기록
const decided = ref(new Map<number, ChangeRead>())
// 이번 검토에서 검사마다 한 결정 수 (왼쪽 '끝 n')
const doneCounts = reactive<Partial<Record<CheckKey, number>>>({})
const changes = ref<ChangeRead[]>([])
const busy = ref(false)
// 보내는 중인 결정 (다시 쓰기는 몇 초 걸려 그 칸에 도는 표시를 둔다)
const busyAction = ref<Action | null>(null)
const confirmAction = ref<'drop' | 'keep' | null>(null)
// 확인 창이 Jev가 괜찮다고 본 것만 그대로 두는지
const confirmJevOnly = ref(false)
// 근접 중복의 가까운 짝 (보고 있는 대화)
const partners = ref<NearPartnerRead[]>([])
const llmConnected = computed(() => isConnected('llm'))
let conversationController: AbortController | null = null

const checkKey = computed<CheckKey | null>(() => {
  const asked = route.query.check
  return typeof asked === 'string' && asked in CHECK_WORDS ? (asked as CheckKey) : null
})
const check = computed<CheckRead | null>(() => overview.value?.checks.find((item) => item.key === checkKey.value) ?? null)
const grades = computed(() =>
  Object.fromEntries((overview.value?.checks ?? []).map((item) => [item.key, item.grade])) as Partial<Record<CheckKey, Grade>>,
)

// 왼쪽 목록: 문제가 남은 검사 (단계마다, 등급이 급한 것부터)
const queueGroups = computed(() =>
  [1, 2].map((stage) => ({
    stage,
    checks: (overview.value?.checks ?? [])
      .filter((item) => item.stage === stage && item.actions.length > 0 && (item.count > 0 || doneCounts[item.key]))
      .sort((a, b) => GRADES[a.grade].rank - GRADES[b.grade].rank),
  })),
)
// 기다림: 서버가 아직 재지 못한 뜻 검사(까닭과 함께) + 늘 흐린 줄
const waitingRows = computed(() => [
  ...(overview.value?.waiting ?? []).map((item) => ({ key: item.key as string, reason: item.reason })),
  ...[...PENDING_BY_STAGE[1], ...PENDING_BY_STAGE[2]].map((key) => ({ key, reason: ALWAYS_PENDING_REASON })),
])
// 고칠 것: 검토할 수 있는 검사(결정이 있는 것)의 남은 수. 과제 쏠림처럼 데이터셋의 모양인 것은 뺀다.
const openTotal = computed(() =>
  (overview.value?.checks ?? []).filter((item) => item.actions.length > 0).reduce((sum, item) => sum + item.count, 0),
)
const doneTotal = computed(() => Object.values(doneCounts).reduce((sum, count) => sum + (count ?? 0), 0))

const currentId = computed(() => ids.value[position.value] ?? null)
const isQueueEnd = computed(() => ids.value.length > 0 && position.value >= ids.value.length)
const decision = computed(() => (currentId.value === null ? null : decided.value.get(currentId.value) ?? null))
// 보고 있는 대화에 이 검사의 문제가 아직 있는지 (없으면 결정 칸을 끈다)
const isOpenHere = computed(
  () =>
    conversation.value !== null &&
    !conversation.value.trashed &&
    conversation.value.problems.some((problem) => problem.key === checkKey.value),
)
const decidedInQueue = computed(() => ids.value.filter((id) => decided.value.has(id)).length)
const answerCount = computed(() => conversation.value?.messages.filter((message) => message.role === 'assistant').length ?? 0)

// 결정 칸: 검사가 허락한 결정(맨 위가 규칙이 권하는 것) + 나중에
type Option = { kind: 'action'; action: Action } | { kind: 'later' }
const options = computed<Option[]>(() => [
  ...(check.value?.actions ?? []).map((action) => ({ kind: 'action' as const, action })),
  { kind: 'later' as const },
])
// 권하는 결정: 규칙이 권하는 것(맨 앞), Jev가 괜찮다고 본 거절 · 짧은 응답이면 그대로 두기
const recommended = computed<Action | null>(() => {
  const actions = check.value?.actions ?? []
  const jev = conversation.value?.jev
  const isJevFine = jev?.looks_fine === true && checkKey.value !== 'mismatch' && actions.includes('keep')
  return isJevFine ? 'keep' : (actions[0] ?? null)
})
const recommendedBy = computed(() => (recommended.value !== (check.value?.actions[0] ?? null) ? 'Jev가 권함' : '규칙이 권함'))
// 보고 있는 대화의 Jev 값 (Jev가 본 검사에서만)
const jevFacts = computed(() => {
  const jev = conversation.value?.jev
  if (!jev || !checkKey.value || !JEV_CHECKS.includes(checkKey.value)) return null
  return jev
})
// Jev가 괜찮다고 본 것만 그대로 두기 (거절 · 짧은 응답, Jev가 판정했을 때)
const canKeepJevFine = computed(
  () =>
    (checkKey.value === 'refusal' || checkKey.value === 'short') &&
    (overview.value?.checks ?? []).some((item) => item.key === 'mismatch'),
)

// 다시 쓰기는 마지막 응답만 바꾼다: 이 검사의 문제가 모두 마지막 응답에 있어야 풀린다(자리 없는 것은 마지막 응답의 것).
const isRewritable = computed(() => {
  const current = conversation.value
  if (!current) return false
  const last = current.messages.length - 1
  return current.problems.filter((problem) => problem.key === checkKey.value).every((problem) => problem.turn === null || problem.turn === last)
})

function isOptionDisabled(option: Option): boolean {
  if (busy.value) return true
  if (option.kind === 'later') return isQueueEnd.value || !ids.value.length
  if (option.action === 'rewrite' && (!llmConnected.value || !isRewritable.value)) return true
  return !isOpenHere.value
}
// 남은 것 한 번에 할 수 있는 결정
const bulkActions = computed(() =>
  (['drop', 'keep'] as const).filter((action) => check.value?.actions.includes(action)),
)

watch(datasetId, () => {
  void loadOverview()
  void loadChanges()
}, { immediate: true })
watch([checkKey, datasetId], loadQueue, { immediate: true })
// 도우미가 데이터를 바꾸면 진단 수 · 결정 기록 · 보고 있는 대화를 다시 읽는다.
watch(helperChanged, () => {
  void loadOverview()
  void loadChanges()
  void loadConversation()
})
watch(currentId, loadConversation)
watch([currentId, checkKey], loadPartners)
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  conversationController?.abort()
})

async function loadOverview(): Promise<void> {
  try {
    overview.value = await getOverview(datasetId.value)
    loadError.value = null
    // 검사를 고르지 않고 들어오면 가장 급한 검사로
    if (!checkKey.value) {
      const first = queueGroups.value.flatMap((group) => group.checks).find((item) => item.count > 0)
      if (first) void router.replace({ query: { ...route.query, check: first.key } })
    }
  } catch (error) {
    if (!overview.value) loadError.value = errorMessage(error)
  }
}

async function loadChanges(): Promise<void> {
  try {
    changes.value = await listChanges(datasetId.value)
  } catch {
    // 기록을 못 읽어도 검토는 할 수 있다.
  }
}

async function loadQueue(): Promise<void> {
  const key = checkKey.value
  ids.value = []
  queueTotal.value = 0
  position.value = 0
  decided.value = new Map()
  conversation.value = null
  if (!key) return
  queueLoading.value = true
  try {
    const queue = await getReviewQueue(datasetId.value, key)
    if (key !== checkKey.value) return
    ids.value = queue.ids
    queueTotal.value = queue.total
  } catch (error) {
    conversationError.value = errorMessage(error)
  } finally {
    queueLoading.value = false
  }
}

async function loadConversation(): Promise<void> {
  conversationController?.abort()
  const id = currentId.value
  conversationError.value = null
  if (id === null) {
    conversation.value = null
    return
  }
  const controller = new AbortController()
  conversationController = controller
  try {
    const read = await getConversation(id, controller.signal)
    if (id === currentId.value) conversation.value = read
  } catch (error) {
    if (isAbortError(error)) return
    conversationError.value = errorMessage(error)
  }
}

/** 근접 중복을 볼 때 가까운 짝을 읽는다. */
async function loadPartners(): Promise<void> {
  partners.value = []
  const id = currentId.value
  if (id === null || checkKey.value !== 'near_duplicate') return
  try {
    const found = await listNearPartners(id)
    if (id === currentId.value) partners.value = found
  } catch {
    // 짝을 못 읽어도 결정은 할 수 있다.
  }
}

/** 결정한 뒤: 진단 수 · 머리 · 사이드바를 다시 읽는다. */
async function afterChange(): Promise<void> {
  await Promise.all([loadOverview(), refreshDataset()])
}

function goTo(index: number): void {
  position.value = Math.max(0, Math.min(index, ids.value.length))
}

function next(): void {
  goTo(position.value + 1)
}

function prev(): void {
  goTo(position.value - 1)
}

function selectCheck(key: CheckKey): void {
  void router.replace({ query: { ...route.query, check: key } })
}

/** 결정 하나. 바로 반영하고 다음 대화로 넘어간다. */
async function choose(action: Action): Promise<void> {
  const id = currentId.value
  const key = checkKey.value
  if (id === null || key === null || busy.value || !isOpenHere.value) return
  if (action === 'rewrite' && !llmConnected.value) return
  busy.value = true
  busyAction.value = action
  try {
    const read = await decide(id, { check: key, action })
    if (id === currentId.value) conversation.value = read.conversation
    decided.value = new Map(decided.value).set(id, read.change)
    changes.value = [read.change, ...changes.value].slice(0, CHANGE_LOG_MAX)
    doneCounts[key] = (doneCounts[key] ?? 0) + 1
    next()
    void afterChange()
  } catch (error) {
    pushToast({ tone: 'fail', title: ACTION_WORDS[action].name, detail: errorMessage(error), actions: [] })
    // 다른 곳에서 이미 바뀌었을 수 있다(409). 지금 모양으로 다시 읽는다.
    void loadConversation()
  } finally {
    busy.value = false
    busyAction.value = null
  }
}

function pick(option: Option): void {
  if (option.kind === 'later') next()
  else void choose(option.action)
}

/** 결정 하나 되돌리기. 이번 큐의 대화면 그 자리로 돌아가 다시 보인다. */
async function undo(change: ChangeRead): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    const read = await undoChange(change.id)
    changes.value = changes.value.filter((item) => item.id !== change.id)
    const key = change.check_key
    if (decided.value.get(change.prompt_id)?.id === change.id) {
      const nextDecided = new Map(decided.value)
      nextDecided.delete(change.prompt_id)
      decided.value = nextDecided
    }
    if (key && doneCounts[key]) doneCounts[key] = doneCounts[key]! - 1
    const index = ids.value.indexOf(change.prompt_id)
    if (index >= 0 && key === checkKey.value) {
      goTo(index)
      conversation.value = read
    }
    void afterChange()
  } catch (error) {
    pushToast({ tone: 'fail', title: '되돌리기', detail: errorMessage(error), actions: [] })
    void loadChanges()
  } finally {
    busy.value = false
  }
}

/** 남은 것 모두에 같은 결정 (확인 창에서). */
async function runBulk(): Promise<void> {
  const key = checkKey.value
  const action = confirmAction.value
  if (!key || !action || busy.value) return
  busy.value = true
  try {
    const result = await decideAll(datasetId.value, { check: key, action, only_jev_ok: confirmJevOnly.value })
    confirmAction.value = null
    confirmJevOnly.value = false
    doneCounts[key] = (doneCounts[key] ?? 0) + result.changed
    pushToast({
      tone: 'done',
      title: `${checkWords(key).name} · ${bulkName(action, confirmJevOnly.value)}`,
      detail: `대화 ${fmt(result.changed)}건 · 결정 기록에서 하나씩 되돌리기`,
      actions: [],
    })
    await Promise.all([afterChange(), loadChanges()])
    await loadQueue()
  } catch (error) {
    pushToast({ tone: 'fail', title: '한 번에 결정', detail: errorMessage(error), actions: [] })
  } finally {
    busy.value = false
  }
}

/** 한 번에 하는 결정의 이름. */
function bulkName(action: 'drop' | 'keep', jevOnly: boolean): string {
  if (jevOnly) return 'Jev 괜찮음 모두 그대로 두기'
  return action === 'drop' ? '모두 빼기' : '모두 그대로 두기'
}

function openBulk(action: 'drop' | 'keep', jevOnly: boolean): void {
  confirmJevOnly.value = jevOnly
  confirmAction.value = action
}

/** 1~n 결정 · ← → 앞뒤 · Z 되돌리기. 입력 칸 · 확인 창 · 수정 키와 함께면 받지 않는다. */
function onKeydown(event: KeyboardEvent): void {
  if (event.metaKey || event.ctrlKey || event.altKey || confirmAction.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
  const number = Number(event.key)
  const isOptionKey = /^[1-9]$/.test(event.key) && number <= options.value.length
  if (isOptionKey) {
    event.preventDefault()
    pick(options.value[number - 1])
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    prev()
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    next()
  } else if (event.key.toLowerCase() === 'z' && changes.value[0]) {
    event.preventDefault()
    void undo(changes.value[0])
  }
}

// 다음 검사: 목록에서 지금 검사 다음으로 문제가 남은 것
const nextCheck = computed(() => {
  const all = queueGroups.value.flatMap((group) => group.checks).filter((item) => item.count > 0)
  const index = all.findIndex((item) => item.key === checkKey.value)
  return all[index + 1] ?? all.find((item) => item.key !== checkKey.value) ?? null
})
</script>

<template>
  <div class="@container max-w-[1480px] space-y-3 px-6 py-5 xl:px-8" data-review>
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <Button variant="quiet" size="sm" as-child>
        <RouterLink :to="{ name: 'generation-diagnosis', params: { datasetId } }" data-action="back-diagnosis">
          <ArrowLeft />진단
        </RouterLink>
      </Button>
      <h2 class="text-[15px] font-semibold">검토</h2>
      <span class="text-ui text-muted-foreground">
        고칠 것 <b class="font-semibold text-foreground">{{ fmt(openTotal) }}</b>건
        · 끝 <b class="font-semibold text-foreground" data-done-total>{{ fmt(doneTotal) }}</b>
      </span>
    </div>

    <section v-if="loadError" class="card flex h-28 items-center justify-center gap-3 text-ui" aria-label="검토 오류">
      <TriangleAlert class="size-4 text-danger" />
      <span class="font-semibold text-danger-ink">오류</span>
      <span class="text-muted-foreground">{{ loadError }}</span>
      <Button variant="outline" size="sm" @click="loadOverview">다시 불러오기</Button>
    </section>

    <div v-else class="grid items-start gap-3.5 @min-[70rem]:grid-cols-[15rem_minmax(0,1fr)_18.75rem]">
      <!-- 왼쪽: 고칠 것 -->
      <section class="card order-3 overflow-hidden @min-[70rem]:order-none" aria-label="고칠 것" data-queue-list>
        <template v-if="overview">
          <template v-for="group in queueGroups" :key="group.stage">
            <div class="caps px-3 pt-2.5 pb-1">{{ STAGE_NAMES[group.stage] }}</div>
            <button
              v-for="item in group.checks"
              :key="item.key"
              type="button"
              class="grid w-full grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-2 border-b px-3 py-[9px] text-left text-[13px] last:border-b-0 hover:bg-muted/60"
              :class="item.key === checkKey ? 'bg-accent hover:bg-accent' : ''"
              :aria-current="item.key === checkKey ? 'true' : undefined"
              :data-queue-check="item.key"
              @click="selectCheck(item.key)"
            >
              <component :is="checkWords(item.key).icon" class="size-[15px] text-muted-foreground" />
              <b class="truncate font-semibold" :class="item.key === checkKey ? 'text-accent-foreground' : ''">{{ checkWords(item.key).name }}</b>
              <span class="whitespace-nowrap">
                <span class="font-[650] tabular-nums" :class="item.count ? GRADES[item.grade].textClass : 'text-subtle-foreground'">
                  {{ fmt(item.count) }}
                </span>
                <span v-if="doneCounts[item.key]" class="text-meta font-medium text-subtle-foreground"> · 끝 {{ doneCounts[item.key] }}</span>
              </span>
            </button>
            <div v-if="!group.checks.length" class="px-3 pb-2 text-meta text-subtle-foreground">없음</div>
          </template>
          <div class="caps px-3 pt-2.5 pb-1">기다림</div>
          <div
            v-for="row in waitingRows"
            :key="row.key"
            class="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-2 border-b px-3 py-[9px] text-[13px] opacity-60 last:border-b-0"
            :data-queue-pending="row.key"
          >
            <component :is="checkWords(row.key).icon" class="size-[15px] text-muted-foreground" />
            <b class="truncate font-semibold">{{ checkWords(row.key).name }}</b>
            <span class="text-meta text-subtle-foreground">{{ row.reason }}</span>
          </div>
          <div v-if="!waitingRows.length" class="px-3 pb-2 text-meta text-subtle-foreground">없음</div>
        </template>
        <div v-else class="space-y-2 p-3">
          <Skeleton v-for="index in 5" :key="index" class="h-5 w-full" />
        </div>
      </section>

      <!-- 가운데: 대화 한 건 -->
      <section class="card overflow-hidden" aria-label="대화" data-review-item>
        <div class="flex min-h-11 flex-wrap items-center gap-x-2.5 gap-y-1 border-b py-1.5 pr-2 pl-[18px]">
          <h2 v-if="checkKey" class="inline-flex items-center gap-2 text-body font-semibold">
            <component :is="checkWords(checkKey).icon" class="size-4 text-muted-foreground" />{{ checkWords(checkKey).name }}
          </h2>
          <span v-if="ids.length && !isQueueEnd" class="flex flex-wrap items-center gap-x-1.5 text-meta text-muted-foreground">
            <span><b class="font-semibold text-foreground" data-position>{{ position + 1 }}</b> / {{ fmt(ids.length) }}</span>
            <template v-if="queueTotal > ids.length"><span class="text-border-strong">·</span>전체 {{ fmt(queueTotal) }}</template>
            <template v-if="conversation">
              <span class="text-border-strong">·</span><span class="font-mono">#{{ conversation.id }}</span>
              <span class="text-border-strong">·</span>{{ SOURCE_WORDS[conversation.source] ?? conversation.source }}
              <span class="text-border-strong">·</span>응답 {{ answerCount }}
            </template>
          </span>
          <span class="ml-auto flex gap-1">
            <Button type="button" variant="quiet" size="icon-sm" aria-label="앞 대화" :disabled="position <= 0" @click="prev">
              <ArrowLeft />
            </Button>
            <Button type="button" variant="quiet" size="icon-sm" aria-label="다음 대화" :disabled="isQueueEnd || !ids.length" @click="next">
              <ArrowRight />
            </Button>
          </span>
        </div>
        <div class="h-1 bg-muted" aria-hidden="true">
          <div class="h-full bg-primary transition-[width]" :style="{ width: `${ids.length ? (decidedInQueue / ids.length) * 100 : 0}%` }" />
        </div>

        <div v-if="queueLoading" class="space-y-3 px-[18px] py-4">
          <Skeleton class="h-4 w-2/3" />
          <Skeleton class="h-4 w-full" />
          <Skeleton class="h-4 w-5/6" />
        </div>
        <div v-else-if="!checkKey || !ids.length" class="flex h-40 flex-col items-center justify-center gap-3 text-ui" data-review-empty>
          <span class="inline-flex items-center gap-2 font-semibold text-success-ink"><CircleCheck class="size-4 text-success" />남은 것 없음</span>
          <Button v-if="nextCheck" variant="outline" size="sm" @click="selectCheck(nextCheck.key)">
            {{ checkWords(nextCheck.key).name }} {{ fmt(nextCheck.count) }}<ArrowRight />
          </Button>
        </div>
        <div v-else-if="isQueueEnd" class="flex h-40 flex-col items-center justify-center gap-3 text-ui" data-review-end>
          <span class="inline-flex items-center gap-2 font-semibold">
            <CircleCheck class="size-4 text-success" />끝 · 결정 {{ fmt(decidedInQueue) }} / {{ fmt(ids.length) }}
          </span>
          <span class="flex gap-2">
            <Button variant="outline" size="sm" @click="goTo(0)"><ArrowLeft />처음으로</Button>
            <Button v-if="nextCheck" size="sm" @click="selectCheck(nextCheck.key)">
              {{ checkWords(nextCheck.key).name }} {{ fmt(nextCheck.count) }}<ArrowRight />
            </Button>
          </span>
        </div>
        <div v-else-if="conversationError" class="flex h-28 items-center justify-center gap-2 text-ui">
          <TriangleAlert class="size-4 text-danger" />
          <span class="text-muted-foreground">{{ conversationError }}</span>
          <Button variant="outline" size="sm" @click="loadConversation">다시 불러오기</Button>
        </div>
        <template v-else-if="conversation">
          <div
            v-if="decision || !isOpenHere"
            class="flex flex-wrap items-center gap-2 border-b bg-[color-mix(in_oklab,var(--muted)_45%,var(--card))] px-[18px] py-2 text-ui"
            data-review-decided
          >
            <template v-if="decision">
              <CircleCheck class="size-4 text-success" />
              <b class="font-semibold">결정 · {{ ACTION_WORDS[decision.action].name }}</b>
              <WhoTag :who="ACTION_WHO[decision.action]" />
              <Button type="button" variant="outline" size="xs" class="ml-auto" :disabled="busy" @click="undo(decision)">
                <Undo2 />되돌리기
              </Button>
            </template>
            <template v-else>
              <Clock class="size-4 text-muted-foreground" />
              <b class="font-semibold text-muted-foreground">{{ conversation.trashed ? '휴지통' : '문제 없음' }}</b>
            </template>
          </div>
          <div
            v-if="jevFacts"
            class="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-[18px] py-2 text-meta text-muted-foreground"
            data-review-jev
          >
            <WhoTag who="jev" />
            <span v-if="jevFacts.follows !== null">
              지시 따름 <b class="font-semibold text-foreground">{{ jevFacts.follows.toFixed(2) }}</b>
            </span>
            <span v-if="jevFacts.refusal_ok !== null">
              거절 마땅 <b class="font-semibold text-foreground">{{ jevFacts.refusal_ok.toFixed(2) }}</b>
            </span>
            <span v-if="jevFacts.similarity !== null">
              지시 ↔ 응답 뜻 <b class="font-semibold text-foreground">{{ jevFacts.similarity.toFixed(2) }}</b>
            </span>
            <span v-if="jevFacts.looks_fine && checkKey !== 'mismatch'" class="font-semibold text-success-ink">괜찮음</span>
          </div>
          <div v-if="partners.length" class="border-b px-[18px] py-2" data-review-partners>
            <div class="caps mb-1">뜻이 가까운 지시</div>
            <div
              v-for="partner in partners"
              :key="partner.prompt_id"
              class="grid grid-cols-[3.5rem_3rem_minmax(0,1fr)] items-center gap-2 py-0.5 text-meta"
            >
              <span class="font-mono text-muted-foreground">#{{ partner.prompt_id }}</span>
              <span class="font-semibold tabular-nums">{{ partner.similarity.toFixed(2) }}</span>
              <span class="truncate" :class="partner.trashed ? 'text-subtle-foreground line-through' : ''" :title="partner.text">
                {{ partner.text }}
              </span>
            </div>
          </div>
          <div class="px-[18px] pt-1 pb-2">
            <ConversationView :conversation="conversation" :grades="grades" :focus="checkKey" />
          </div>
        </template>
        <div v-else class="space-y-3 px-[18px] py-4">
          <Skeleton class="h-4 w-2/3" />
          <Skeleton class="h-4 w-full" />
        </div>
      </section>

      <!-- 오른쪽: 결정 · 결정 기록 · 남은 것 한 번에 -->
      <div class="grid gap-3.5 @min-[70rem]:sticky @min-[70rem]:top-16">
        <section class="card p-3.5" aria-label="결정" data-decide>
          <div class="caps mb-2">결정</div>
          <div class="grid gap-2">
            <button
              v-for="(option, index) in options"
              :key="option.kind === 'action' ? option.action : 'later'"
              type="button"
              class="grid grid-cols-[1.625rem_minmax(0,1fr)] items-start gap-2 rounded-[10px] border bg-card px-3 py-2.5 text-left text-[13px] transition-shadow hover:border-border-strong disabled:cursor-default disabled:opacity-45"
              :class="option.kind === 'action' && option.action === recommended ? 'border-[color-mix(in_oklab,var(--primary)_55%,transparent)] shadow-[0_0_0_1px_color-mix(in_oklab,var(--primary)_30%,transparent)]' : ''"
              :disabled="isOptionDisabled(option)"
              :data-option="option.kind === 'action' ? option.action : 'later'"
              @click="pick(option)"
            >
              <span
                class="grid size-[22px] place-items-center rounded-md text-meta font-bold"
                :class="option.kind === 'action' && option.action === recommended ? 'bg-primary text-primary-foreground' : 'bg-muted'"
              >
                <LoaderCircle v-if="option.kind === 'action' && busyAction === option.action" class="size-3 animate-spin" />
                <template v-else>{{ index + 1 }}</template>
              </span>
              <span class="min-w-0">
                <span class="flex flex-wrap items-center gap-1.5">
                  <template v-if="option.kind === 'action'">
                    <b class="font-semibold">{{ ACTION_WORDS[option.action].name }}</b>
                    <WhoTag :who="ACTION_WHO[option.action]" />
                  </template>
                  <b v-else class="inline-flex items-center gap-1 font-semibold"><SkipForward class="size-3.5" />나중에</b>
                </span>
                <small class="mt-0.5 block text-meta text-muted-foreground">
                  <template v-if="option.kind === 'action' && option.action === 'rewrite' && !llmConnected">LLM 미연결</template>
                  <template v-else-if="option.kind === 'action' && option.action === 'rewrite' && !isRewritable">앞 턴의 응답 · 다시 쓰기로 안 풀림</template>
                  <template v-else-if="option.kind === 'action'">
                    {{ option.action === recommended ? `${recommendedBy} · ` : '' }}{{ ACTION_WORDS[option.action].note }}
                  </template>
                  <template v-else>건너뛰고 다음</template>
                </small>
              </span>
            </button>
          </div>
          <div class="mt-2.5 text-meta text-muted-foreground">
            <span class="kbd">1</span>~<span class="kbd">{{ options.length }}</span> 결정 · <span class="kbd">←</span><span class="kbd">→</span> 앞뒤
            · <span class="kbd">Z</span> 되돌리기
          </div>
        </section>

        <section class="card p-3.5" aria-label="결정 기록" data-change-log>
          <div class="caps mb-1.5 flex items-center">결정 기록<span class="ml-auto normal-case">{{ changes.length ? fmt(changes.length) : '' }}</span></div>
          <div v-if="changes.length" class="grid max-h-72 overflow-auto text-[12.5px]">
            <div
              v-for="change in changes"
              :key="change.id"
              class="grid grid-cols-[3.25rem_minmax(0,1fr)_auto] items-center gap-1.5 border-b py-1.5 last:border-b-0"
              :data-change="change.id"
            >
              <span class="font-mono text-muted-foreground tabular-nums">#{{ change.prompt_id }}</span>
              <span class="flex min-w-0 items-center gap-1.5">
                <span class="truncate">{{ change.check_key ? checkWords(change.check_key).name : '' }} · {{ ACTION_DONE_WORDS[change.action] }}</span>
                <span class="shrink-0 text-meta text-subtle-foreground">{{ clockText(change.created_at) }}</span>
              </span>
              <Button type="button" variant="quiet" size="icon-sm" aria-label="되돌리기" :disabled="busy" @click="undo(change)">
                <Undo2 />
              </Button>
            </div>
          </div>
          <div v-else class="py-1 text-meta text-subtle-foreground">없음</div>
        </section>

        <section v-if="check && check.count && bulkActions.length" class="card grid gap-2 p-3.5" aria-label="남은 것 한 번에">
          <div class="caps">남은 {{ fmt(check.count) }}건 한 번에</div>
          <Button
            v-for="action in bulkActions"
            :key="action"
            type="button"
            variant="outline"
            class="justify-start"
            :disabled="busy"
            :data-bulk="action"
            @click="openBulk(action, false)"
          >
            <WhoTag :who="ACTION_WHO[action]" />{{ bulkName(action, false) }} {{ fmt(check.count) }}
          </Button>
          <Button
            v-if="canKeepJevFine"
            type="button"
            variant="outline"
            class="justify-start"
            :disabled="busy"
            data-bulk="keep-jev"
            @click="openBulk('keep', true)"
          >
            <WhoTag who="jev" />{{ bulkName('keep', true) }}
          </Button>
        </section>
      </div>
    </div>

    <Dialog :open="confirmAction !== null" @update:open="(isOpen: boolean) => { if (!isOpen) confirmAction = null }">
      <DialogContent class="w-[min(420px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none">
        <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
          <DialogTitle class="text-title font-semibold">{{ confirmAction ? bulkName(confirmAction, confirmJevOnly) : '' }}</DialogTitle>
          <DialogDescription class="sr-only">이 검사의 남은 대화 모두에 같은 결정을 한다</DialogDescription>
        </DialogHeader>
        <div v-if="check && confirmAction" class="space-y-4 px-5 py-4">
          <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">검사</dt>
            <dd class="font-semibold">{{ checkWords(check.key).name }}</dd>
            <dt class="text-muted-foreground">대화</dt>
            <dd v-if="confirmJevOnly">Jev가 지시 따름 · 거절 마땅으로 본 것 (≥ 0.8)</dd>
            <dd v-else><b class="font-semibold">{{ fmt(check.count) }}</b></dd>
            <dt class="text-muted-foreground">결정</dt>
            <dd>{{ ACTION_WORDS[confirmAction].name }} · {{ ACTION_WORDS[confirmAction].note }}</dd>
            <dt class="text-muted-foreground">되돌리기</dt>
            <dd>결정 기록에서 한 건씩</dd>
          </dl>
          <div class="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" @click="confirmAction = null">취소</Button>
            <Button type="button" :disabled="busy" data-action="bulk-go" @click="runBulk">
              <LoaderCircle v-if="busy" class="animate-spin" />{{ confirmAction ? bulkName(confirmAction, confirmJevOnly) : '' }}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
