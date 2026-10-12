<!--
  데이터 탭 (#/generation/<id>/data): 데이터셋의 대화를 한 쪽에 50건씩 본다(분류 · 검색 데이터 탭과 같은 틀).
    거르기 줄   검색 · 문제 · 상태 (DataToolbar) · 오른쪽 끝 보기 [표 | 지도]
    표          건수 · 대화 표(ConversationTable: 지시 · 응답 · 턴 · 문제 · 번호) · 쪽 넘기기
    지도        ?view=map. 의미 지도(map/MapView): 거르기에 맞는 점을 또렷하게, 점을 누르면 패널을 연다.
    패널        줄을 누르면 오른쪽에 대화 상세(ConversationPanel). J/K로 이 쪽의 다음·이전 줄, Esc로 닫는다.
                패널에서 휴지통으로 보내거나 되살리면 이 쪽 · 머리 건수 · 연 대화를 다시 읽는다.
  거르기 · 쪽 · 연 대화는 모두 주소 뒤 ?에 있다(data/filters.ts). 주소가 바뀌면 그 쪽 하나만 새로 읽고,
  앞의 요청이 아직 돌고 있으면 끊는다. 문제 아이콘의 색(등급)은 진단(GET …/overview)을 한 번 읽어 쓴다.
  패널은 DatasetLayout의 오른쪽 자리(#dataset-side)에 그려서, 데이터셋 머리 옆까지 세로로 꽉 차게 한다.
-->
<script setup lang="ts">
import { ChartScatter, ChevronLeft, ChevronRight, Table2 } from '@lucide/vue'
import { useElementSize, useEventListener } from '@vueuse/core'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, ref, useTemplateRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { helperChanged } from '@/system/helper/helperSignals'
import { fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { remPx } from '@/system/layout/rem'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { getConversation, getOverview, listConversations } from '@/modules/generation/api'
import ConversationPanel from '@/modules/generation/data/ConversationPanel.vue'
import ConversationTable from '@/modules/generation/data/ConversationTable.vue'
import DataToolbar from '@/modules/generation/data/DataToolbar.vue'
import {
  hasFilters,
  NO_FILTERS,
  PAGE_SIZE,
  readDataQuery,
  toConversationQuery,
  writeDataQuery,
  type DataFilters,
  type DataQuery,
  type DataView,
} from '@/modules/generation/data/filters'
import { useDatasetRefresh } from '@/modules/generation/datasetRefresh'
import type { CheckKey, ConversationPageRead, ConversationRead, DatasetRead, Grade } from '@/modules/generation/types'

const props = defineProps<{
  // 머리(DatasetLayout)가 읽은 데이터셋. 상태별 건수를 거르기 줄에 쓴다.
  dataset: DatasetRead
}>()

// 쪽을 넘길 때 표 윗부분이 상단 바(52px) 밑에서 조금 떨어져 보이게 하는 여백
const PAGE_SCROLL_OFFSET = 64

// 지도 보기는 WebGL 그림 도구(regl-scatterplot)를 쓴다. 지도를 열 때만 불러와 첫 화면을 가볍게 둔다.
const MapView = defineAsyncComponent(() => import('@/modules/generation/map/MapView.vue'))

// 보기 바꾸기 단추
const VIEWS: { key: DataView; name: string; icon: typeof Table2 }[] = [
  { key: 'table', name: '표', icon: Table2 },
  { key: 'map', name: '지도', icon: ChartScatter },
]

// 표가 놓인 칸이 이 폭(rem)보다 좁으면(도우미 창을 옆에 둘 때) 상세 패널을 연 때와 같은 좁은 표를 쓴다.
const NARROW_TABLE_REM = 51.25

const route = useRoute()
const router = useRouter()
const refreshDataset = useDatasetRefresh()

const tabRoot = useTemplateRef<HTMLElement>('tabRoot')
const { width: tabWidth } = useElementSize(tabRoot)
const isCramped = computed(() => tabWidth.value > 0 && tabWidth.value < NARROW_TABLE_REM * remPx())

const toolbar = ref<InstanceType<typeof DataToolbar> | null>(null)
const card = ref<HTMLElement | null>(null)

const datasetId = computed(() => Number(route.params.datasetId))
const current = computed(() => readDataQuery(route.query))

// 검사마다 등급 (문제 아이콘 · 칠하는 색). 못 읽으면 모두 주의 색
const grades = ref<Partial<Record<CheckKey, Grade>>>({})

// ---------- 목록 한 쪽 ----------

// 읽은 쪽. 아직 없으면 null (회색 줄을 보인다)
const result = ref<ConversationPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
let listController: AbortController | null = null

// 목록 요청이 바뀌는 값만 모은 열쇠. 연 대화만 바뀌면 다시 읽지 않는다.
const requestKey = computed(() => JSON.stringify([datasetId.value, toConversationQuery(current.value)]))

const rows = computed(() => result.value?.items ?? null)
const total = computed(() => result.value?.total ?? 0)
const canClear = computed(() => hasFilters(current.value) || current.value.page > 1)
const firstNumber = computed(() => (current.value.page - 1) * PAGE_SIZE + 1)
const lastNumber = computed(() => firstNumber.value + (rows.value?.length ?? 0) - 1)
const hasNextPage = computed(() => current.value.page * PAGE_SIZE < total.value)

watch(requestKey, loadConversations, { immediate: true })
// 도우미가 데이터를 바꾸면 이 쪽을 다시 읽는다.
watch(helperChanged, loadConversations)
watch(datasetId, loadGrades, { immediate: true })
// 머리가 다시 읽히면(검토 · 휴지통에서 바꿈) 등급도 맞춘다.
watch(() => props.dataset.updated_at, loadGrades)

async function loadGrades(): Promise<void> {
  try {
    const overview = await getOverview(datasetId.value)
    grades.value = Object.fromEntries(overview.checks.map((check) => [check.key, check.grade]))
  } catch {
    // 색만 못 맞춘다. 표는 그대로 보인다.
  }
}

async function loadConversations(): Promise<void> {
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  loading.value = true
  loadError.value = null
  try {
    result.value = await listConversations(datasetId.value, toConversationQuery(current.value), controller.signal)
  } catch (error) {
    if (isAbortError(error)) return
    result.value = null
    loadError.value = errorMessage(error)
  } finally {
    // 끊긴 요청이면 새 요청이 돌고 있으므로 loading을 그대로 둔다.
    if (!controller.signal.aborted) loading.value = false
  }
}

// ---------- 주소 바꾸기 ----------

/** 거르기를 바꾼다. 결과가 달라지므로 첫 쪽으로 간다. 뒤로 가기로 돌아올 수 있게 기록을 남긴다. */
function changeFilters(patch: Partial<DataFilters>): void {
  void router.push({ query: writeDataQuery({ ...current.value, ...patch, page: 1 }) })
}

function clearFilters(): void {
  changeFilters({ ...NO_FILTERS })
}

async function goToPage(page: number): Promise<void> {
  await router.push({ query: writeDataQuery({ ...current.value, page }) })
  // 아래쪽의 [다음]을 눌렀으면 새 쪽을 위에서부터 보이게 표 머리로 올린다.
  const top = card.value?.getBoundingClientRect().top ?? 0
  if (top < 0) window.scrollTo({ top: window.scrollY + top - PAGE_SCROLL_OFFSET })
}

/** 표 · 지도 보기를 바꾼다. 뒤로 가기로 돌아올 수 있게 기록을 남긴다. */
function setView(view: DataView): void {
  void router.push({ query: writeDataQuery({ ...current.value, view }) })
}

// 대화를 열고 닫는 것은 기록을 남기지 않는다(J/K로 옮길 때마다 뒤로 가기가 쌓이지 않게).
function setOpenId(conversationId: number | null): void {
  const next: DataQuery = { ...current.value, conversationId }
  void router.replace({ query: writeDataQuery(next) })
}

// ---------- 오른쪽 패널 ----------

// 패널에 보일 대화. 이 쪽에 있으면 그것을, 없으면 따로 찾은 것을 쓴다.
const openConversation = ref<ConversationRead | null>(null)
const openMissing = ref(false)
let lookupController: AbortController | null = null

const openIndex = computed(() => rows.value?.findIndex((item) => item.id === current.value.conversationId) ?? -1)
const canPrev = computed(() => openIndex.value > 0)
const canNext = computed(() => rows.value !== null && openIndex.value < rows.value.length - 1)

watch([() => current.value.conversationId, result], syncOpenConversation, { immediate: true })
onBeforeUnmount(() => {
  listController?.abort()
  lookupController?.abort()
})

function syncOpenConversation(): void {
  const conversationId = current.value.conversationId
  openMissing.value = false
  if (conversationId === null) {
    openConversation.value = null
    return
  }
  const onPage = rows.value?.find((item) => item.id === conversationId)
  if (onPage) {
    openConversation.value = onPage
    return
  }
  const alreadyOpen = openConversation.value?.id === conversationId
  // 첫 쪽을 읽는 중이면 그것부터 기다린다. 그 쪽에 있을 때가 많다.
  if (alreadyOpen || !result.value) return
  void lookUpConversation(conversationId)
}

// 쪽에 없는 대화(주소로 바로 왔음)를 번호로 읽는다. 다른 데이터셋의 번호면 없는 대화다.
async function lookUpConversation(conversationId: number): Promise<void> {
  lookupController?.abort()
  const controller = new AbortController()
  lookupController = controller
  openConversation.value = null
  try {
    const found = await getConversation(conversationId, controller.signal)
    if (found.dataset_id === datasetId.value) openConversation.value = found
    else openMissing.value = true
  } catch (error) {
    if (!isAbortError(error)) openMissing.value = true
  }
}

function open(conversation: ConversationRead): void {
  openConversation.value = conversation
  setOpenId(conversation.id)
}

/** 패널에서 대화를 휴지통으로 보냈거나 되살렸다: 이 쪽 · 머리 건수 · 연 대화를 다시 읽는다. */
async function onConversationChanged(conversationId: number): Promise<void> {
  void refreshDataset()
  await loadConversations()
  // 거르기 때문에 이 쪽에서 빠졌으면(휴지통 밖 목록에서 휴지통으로 보냄) 번호로 따로 읽는다. 패널을 비우지 않고 바꿔 끼운다.
  const isOnPage = rows.value?.some((item) => item.id === conversationId) ?? false
  if (isOnPage || current.value.conversationId !== conversationId) return
  try {
    const fresh = await getConversation(conversationId)
    if (current.value.conversationId === conversationId) openConversation.value = fresh
  } catch {
    // 못 읽으면 앞의 값을 그대로 보인다.
  }
}

function close(): void {
  setOpenId(null)
}

// J/K: 이 쪽 안에서 다음·이전 줄을 연다. 아무것도 안 열려 있으면 첫 줄부터.
function step(delta: number): void {
  const list = rows.value
  if (!list?.length) return
  const index = openIndex.value
  const nextIndex = index === -1 ? 0 : Math.min(Math.max(index + delta, 0), list.length - 1)
  const conversation = list[nextIndex]
  open(conversation)
  void nextTick(() => {
    const row = document.querySelector(`[data-conversation-id="${conversation.id}"]`)
    row?.scrollIntoView({ block: 'nearest' })
  })
}

// ---------- 글쇠 ----------

// 글자를 치는 칸이나 펼친 목록·대화상자 안에서는 글쇠를 가로채지 않는다.
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.closest('input, textarea, select, [contenteditable="true"], [role="menu"], [role="dialog"]') !== null
}

// 한글 자판에서도 되도록 글쇠 위치(code)로 가린다. J = ㅓ, K = ㅏ
useEventListener(window, 'keydown', (event: KeyboardEvent) => {
  const hasModifier = event.metaKey || event.ctrlKey || event.altKey
  if (event.defaultPrevented || hasModifier || isTypingTarget(event.target)) return
  if (event.code === 'KeyJ') step(1)
  else if (event.code === 'KeyK') step(-1)
  else if (event.key === 'Escape' && current.value.conversationId !== null) close()
  else if (event.key === '/') {
    event.preventDefault()
    toolbar.value?.focusSearch()
  }
})
</script>

<template>
  <div ref="tabRoot" class="px-6 py-5 xl:px-8">
    <div class="flex items-start gap-2">
      <DataToolbar ref="toolbar" class="min-w-0 flex-1" :filters="current" :dataset="dataset" @change="changeFilters" />
      <div class="inline-flex shrink-0 rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)" role="group" aria-label="보기">
        <button
          v-for="view in VIEWS"
          :key="view.key"
          type="button"
          class="inline-flex h-[26px] items-center gap-1.5 rounded-[5px] px-2.5 text-ui font-[550] transition-colors [&>svg]:size-3.5"
          :class="current.view === view.key ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
          :aria-pressed="current.view === view.key"
          :data-view="view.key"
          @click="setView(view.key)"
        >
          <component :is="view.icon" />{{ view.name }}
        </button>
      </div>
    </div>

    <MapView
      v-if="current.view === 'map'"
      :dataset-id="datasetId"
      :filters="current"
      :open-id="current.conversationId"
      @open="setOpenId"
      @change="changeFilters"
    />

    <div v-else ref="card" class="card mt-3 overflow-clip">
      <div class="flex h-11 items-center gap-2 border-b px-4">
        <Skeleton v-if="!result && !loadError" class="h-5 w-24" />
        <template v-else-if="result">
          <b class="text-title font-semibold">{{ fmt(total) }}건</b>
          <span v-if="!hasFilters(current)" class="text-ui text-muted-foreground">전체</span>
        </template>
        <div v-if="rows?.length && total > PAGE_SIZE" class="ml-auto flex items-center gap-0.5 text-ui text-muted-foreground">
          <span class="mr-1.5">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }}</span>
          <Button variant="quiet" size="icon-sm" :disabled="current.page <= 1" aria-label="이전 쪽" @click="goToPage(current.page - 1)">
            <ChevronLeft />
          </Button>
          <Button variant="quiet" size="icon-sm" :disabled="!hasNextPage" aria-label="다음 쪽" @click="goToPage(current.page + 1)">
            <ChevronRight />
          </Button>
        </div>
      </div>

      <ConversationTable
        :rows="rows"
        :loading="loading"
        :error="loadError"
        :open-id="current.conversationId"
        :narrow="current.conversationId !== null || isCramped"
        :can-clear="canClear"
        :grades="grades"
        @open="open"
        @clear="clearFilters"
        @retry="loadConversations"
      />

      <div v-if="result && total > 0" class="flex h-11 items-center gap-3 border-t bg-muted/30 px-4 text-ui text-muted-foreground">
        <span v-if="rows?.length">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }} / 전체 {{ fmt(total) }}</span>
        <span v-else>— / 전체 {{ fmt(total) }}</span>
        <span v-if="current.conversationId === null" class="ml-3 hidden items-center gap-1.5 xl:inline-flex">
          <span class="kbd">J</span><span class="kbd">K</span>이동
          <span class="ml-2 kbd">/</span>검색
        </span>
        <div class="ml-auto flex items-center gap-1">
          <Button variant="quiet" size="xs" :disabled="current.page <= 1" aria-label="이전 쪽" @click="goToPage(current.page - 1)">
            <ChevronLeft />이전
          </Button>
          <span class="text-subtle-foreground">·</span>
          <Button variant="quiet" size="xs" :disabled="!hasNextPage" aria-label="다음 쪽" @click="goToPage(current.page + 1)">
            다음<ChevronRight />
          </Button>
        </div>
      </div>
    </div>
  </div>

  <Teleport defer to="#dataset-side">
    <ConversationPanel
      v-if="current.conversationId !== null"
      :dataset-id="datasetId"
      :conversation-id="current.conversationId"
      :conversation="openConversation"
      :missing="openMissing"
      :can-prev="canPrev"
      :can-next="canNext"
      :grades="grades"
      @close="close"
      @step="step"
      @changed="onConversationChanged"
    />
  </Teleport>
</template>
