<!--
  데이터 탭 (#/classification/<id>/data): 데이터셋의 문장을 한 쪽에 50건씩 보기만 한다.
    거르기 줄   검색 · 문제 · 라벨 · 상태 (DataToolbar) · 오른쪽 끝 보기 [표 | 지도]
    표          건수 · 문장 표(RecordTable) · 쪽 넘기기
    지도        ?view=map. 의미 지도(map/MapView): 거르기에 맞는 점을 또렷하게, 점을 누르면 패널을 연다.
    패널        줄이나 점을 누르면 오른쪽에 문장 상세(RecordPanel). J/K로 이 쪽의 다음·이전 줄, Esc로 닫는다.
                패널에서 휴지통으로 보내거나 되살리면 이 쪽 · 머리 건수 · 연 문장을 다시 읽는다.
  거르기 · 쪽 · 연 문장 · 보기는 모두 주소 뒤 ?에 있다(data/filters.ts). 주소가 바뀌면 그 쪽 하나만 새로 읽고,
  앞의 요청이 아직 돌고 있으면 끊는다. 127,600건이어도 한 번에 한 쪽(50건)만 읽는다(지도 보기의 J/K도 이 쪽을 쓴다).
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
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'
import { remPx } from '@/system/layout/rem'

import { getRecord, listRecords } from '@/modules/classification/api'
import DataToolbar from '@/modules/classification/data/DataToolbar.vue'
import {
  GROUPED_PROBLEMS,
  hasFilters,
  NO_FILTERS,
  PAGE_SIZE,
  readDataQuery,
  toRecordQuery,
  writeDataQuery,
  type DataFilters,
  type DataQuery,
  type DataView,
} from '@/modules/classification/data/filters'
import RecordPanel from '@/modules/classification/data/RecordPanel.vue'
import RecordTable from '@/modules/classification/data/RecordTable.vue'
import { useDatasetRefresh } from '@/modules/classification/datasetRefresh'
import type { DatasetRead, RecordPageRead, RecordRead } from '@/modules/classification/types'

defineProps<{
  // 머리(DatasetLayout)가 읽은 데이터셋. 라벨 목록과 건수를 거르기 줄에 쓴다.
  dataset: DatasetRead
}>()

// 쪽을 넘길 때 표 윗부분이 상단 바(52px) 밑에서 조금 떨어져 보이게 하는 여백
const PAGE_SCROLL_OFFSET = 64

// 지도 보기는 WebGL 그림 도구(regl-scatterplot)를 쓴다. 지도를 열 때만 불러와 첫 화면을 가볍게 둔다.
const MapView = defineAsyncComponent(() => import('@/modules/classification/map/MapView.vue'))

// 보기 바꾸기 단추
const VIEWS: { key: DataView; name: string; icon: typeof Table2 }[] = [
  { key: 'table', name: '표', icon: Table2 },
  { key: 'map', name: '지도', icon: ChartScatter },
]

const route = useRoute()

// 표가 놓인 칸이 이 폭(rem)보다 좁으면(도우미 창을 옆에 둘 때) 상세 패널을 연 때와 같은 좁은 표를 쓴다.
const NARROW_TABLE_REM = 51.25
const tabRoot = useTemplateRef<HTMLElement>('tabRoot')
const { width: tabWidth } = useElementSize(tabRoot)
const isCramped = computed(() => tabWidth.value > 0 && tabWidth.value < NARROW_TABLE_REM * remPx())
const router = useRouter()
const refreshDataset = useDatasetRefresh()

const toolbar = ref<InstanceType<typeof DataToolbar> | null>(null)
const card = ref<HTMLElement | null>(null)

const datasetId = computed(() => Number(route.params.datasetId))
const current = computed(() => readDataQuery(route.query))

// ---------- 목록 한 쪽 ----------

// 읽은 쪽. 아직 없으면 null (회색 줄을 보인다)
const result = ref<RecordPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
let listController: AbortController | null = null

// 목록 요청이 바뀌는 값만 모은 열쇠. 연 문장(record)만 바뀌면 다시 읽지 않는다.
const requestKey = computed(() => JSON.stringify([datasetId.value, toRecordQuery(current.value)]))

const rows = computed(() => result.value?.items ?? null)
const total = computed(() => result.value?.total ?? 0)
const isGrouped = computed(() => {
  const problem = current.value.problem
  return problem !== null && GROUPED_PROBLEMS.includes(problem)
})
const canClear = computed(() => hasFilters(current.value) || current.value.page > 1)
const firstNumber = computed(() => (current.value.page - 1) * PAGE_SIZE + 1)
const lastNumber = computed(() => firstNumber.value + (rows.value?.length ?? 0) - 1)
const hasNextPage = computed(() => current.value.page * PAGE_SIZE < total.value)

watch(requestKey, loadRecords, { immediate: true })
// 도우미가 데이터를 바꾸면 이 쪽을 다시 읽는다(라벨 · 학습 포함이 바뀐다).
watch(helperChanged, loadRecords)

async function loadRecords(): Promise<void> {
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  loading.value = true
  loadError.value = null
  try {
    result.value = await listRecords(datasetId.value, toRecordQuery(current.value), controller.signal)
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

// 문장을 열고 닫는 것은 기록을 남기지 않는다(J/K로 옮길 때마다 뒤로 가기가 쌓이지 않게).
function setOpenId(recordId: number | null): void {
  const next: DataQuery = { ...current.value, recordId }
  void router.replace({ query: writeDataQuery(next) })
}

// ---------- 오른쪽 패널 ----------

// 패널에 보일 문장. 이 쪽에 있으면 그것을, 없으면 따로 찾은 것을 쓴다.
const openRecord = ref<RecordRead | null>(null)
const openMissing = ref(false)
let lookupController: AbortController | null = null

const openIndex = computed(() => rows.value?.findIndex((record) => record.id === current.value.recordId) ?? -1)
const canPrev = computed(() => openIndex.value > 0)
const canNext = computed(() => rows.value !== null && openIndex.value < rows.value.length - 1)

watch([() => current.value.recordId, result], syncOpenRecord, { immediate: true })
onBeforeUnmount(() => {
  listController?.abort()
  lookupController?.abort()
})

function syncOpenRecord(): void {
  const recordId = current.value.recordId
  openMissing.value = false
  if (recordId === null) {
    openRecord.value = null
    return
  }
  const onPage = rows.value?.find((record) => record.id === recordId)
  if (onPage) {
    openRecord.value = onPage
    return
  }
  const alreadyOpen = openRecord.value?.id === recordId
  // 첫 쪽을 읽는 중이면 그것부터 기다린다. 그 쪽에 있을 때가 많다.
  if (alreadyOpen || !result.value) return
  void lookUpRecord(recordId)
}

// 쪽에 없는 문장(주소로 바로 왔거나 지도에서 눌렀음)을 번호로 읽는다. 다른 데이터셋의 번호면 없는 문장이다.
async function lookUpRecord(recordId: number): Promise<void> {
  lookupController?.abort()
  const controller = new AbortController()
  lookupController = controller
  openRecord.value = null
  try {
    const found = await getRecord(recordId, controller.signal)
    if (found.dataset_id === datasetId.value) openRecord.value = found
    else openMissing.value = true
  } catch (error) {
    if (!isAbortError(error)) openMissing.value = true
  }
}

function open(record: RecordRead): void {
  openRecord.value = record
  setOpenId(record.id)
}

/** 패널에서 라벨을 바꿨거나 문장을 휴지통으로 보냈거나 되살렸다: 이 쪽 · 머리 건수 · 연 문장을 다시 읽는다. */
async function onRecordChanged(recordId: number): Promise<void> {
  void refreshDataset()
  await loadRecords()
  // 이 쪽에 남아 있으면 쪽을 읽을 때 새 값이 패널에 붙는다. 거르기 때문에 빠졌으면(휴지통 밖 목록에서
  // 휴지통으로 보냄) 번호로 따로 읽는다. 패널을 비우지 않고 바꿔 끼운다.
  const isOnPage = rows.value?.some((record) => record.id === recordId) ?? false
  if (isOnPage || current.value.recordId !== recordId) return
  try {
    const fresh = await getRecord(recordId)
    if (current.value.recordId === recordId) openRecord.value = fresh
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
  const record = list[nextIndex]
  open(record)
  void nextTick(() => {
    const row = document.querySelector(`[data-record-id="${record.id}"]`)
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
  else if (event.key === 'Escape' && current.value.recordId !== null) close()
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
      :open-id="current.recordId"
      @open="setOpenId"
      @change="changeFilters"
    />

    <div v-else ref="card" class="card mt-3 overflow-clip">
      <div class="flex h-11 items-center gap-2 border-b px-4">
        <Skeleton v-if="!result && !loadError" class="h-5 w-24" />
        <template v-else-if="result">
          <b class="text-title font-semibold">{{ fmt(total) }}건</b>
          <span v-if="!hasFilters(current)" class="text-ui text-muted-foreground">전체</span>
          <span v-if="isGrouped" class="text-ui text-muted-foreground">· 같은 문장끼리</span>
        </template>
        <div v-if="rows?.length && total > PAGE_SIZE" class="ml-auto flex items-center gap-0.5 text-ui text-muted-foreground">
          <span class="mr-1.5">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }}</span>
          <Button
            variant="quiet"
            size="icon-sm"
            :disabled="current.page <= 1"
            aria-label="이전 쪽"
            @click="goToPage(current.page - 1)"
          >
            <ChevronLeft />
          </Button>
          <Button variant="quiet" size="icon-sm" :disabled="!hasNextPage" aria-label="다음 쪽" @click="goToPage(current.page + 1)">
            <ChevronRight />
          </Button>
        </div>
      </div>

      <RecordTable
        :rows="rows"
        :loading="loading"
        :error="loadError"
        :open-id="current.recordId"
        :grouped="isGrouped"
        :narrow="current.recordId !== null || isCramped"
        :can-clear="canClear"
        @open="open"
        @clear="clearFilters"
        @retry="loadRecords"
      />

      <div
        v-if="result && total > 0"
        class="flex h-11 items-center gap-3 border-t bg-muted/30 px-4 text-ui text-muted-foreground"
      >
        <span v-if="rows?.length">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }} / 전체 {{ fmt(total) }}</span>
        <span v-else>— / 전체 {{ fmt(total) }}</span>
        <span v-if="current.recordId === null" class="ml-3 hidden items-center gap-1.5 xl:inline-flex">
          <span class="kbd">J</span><span class="kbd">K</span>이동
          <span class="ml-2 kbd">/</span>검색
        </span>
        <div class="ml-auto flex items-center gap-1">
          <Button
            variant="quiet"
            size="xs"
            :disabled="current.page <= 1"
            aria-label="이전 쪽"
            @click="goToPage(current.page - 1)"
          >
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
    <RecordPanel
      v-if="current.recordId !== null"
      :dataset-id="datasetId"
      :record-id="current.recordId"
      :record="openRecord"
      :missing="openMissing"
      :can-prev="canPrev"
      :can-next="canNext"
      :labels="dataset.labels"
      @close="close"
      @step="step"
      @open="open"
      @open-id="setOpenId"
      @changed="onRecordChanged"
    />
  </Teleport>
</template>
