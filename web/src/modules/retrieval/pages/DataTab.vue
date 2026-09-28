<!--
  데이터 탭 (#/retrieval/<id>/data):
    거르기 줄   [질의 | 문서] · 검색 · 문제 · 출처(질의) · 쓰임(문서) · 상태 (DataToolbar) · 오른쪽 끝 [표 | 지도]
    표          수 · 질의 표(QueryTable) 또는 문서 표(DocumentTable) · 쪽 넘기기
    지도        ?view=map. 질의 ● · 문서 ■ 를 한 지도에(map/MapView). 점을 누르면 그 패널을 연다.
    패널        줄이나 점을 누르면 오른쪽에 질의 패널(QueryPanel) 또는 문서 패널(DocumentPanel).
                J/K로 이 쪽의 다음 · 이전 줄, Esc로 닫는다. 패널에서 바꾸면 이 쪽 · 머리 수를 다시 읽는다.
  보기 · 거르기 · 쪽 · 연 줄은 모두 주소 뒤 ?에 있다(data/filters.ts). 한 번에 한 쪽(50줄)만 읽는다.
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

import { listDocuments, listQueries } from '@/modules/retrieval/api'
import DataToolbar from '@/modules/retrieval/data/DataToolbar.vue'
import DocumentPanel from '@/modules/retrieval/data/DocumentPanel.vue'
import DocumentTable from '@/modules/retrieval/data/DocumentTable.vue'
import {
  hasFilters,
  NO_FILTERS,
  PAGE_SIZE,
  readDataQuery,
  toDocumentListQuery,
  toQueryListQuery,
  writeDataQuery,
  type DataFilters,
  type DataQuery,
  type DataView,
  type OpenItem,
} from '@/modules/retrieval/data/filters'
import QueryPanel from '@/modules/retrieval/data/QueryPanel.vue'
import QueryTable from '@/modules/retrieval/data/QueryTable.vue'
import { useDatasetRefresh } from '@/modules/retrieval/datasetRefresh'
import { jobFinished } from '@/modules/retrieval/jobEvents'
import type { DatasetRead, DocumentPageRead, QueryPageRead } from '@/modules/retrieval/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

const PAGE_SCROLL_OFFSET = 64

// 지도 보기는 WebGL 그림 도구를 쓴다. 지도를 열 때만 불러온다.
const MapView = defineAsyncComponent(() => import('@/modules/retrieval/map/MapView.vue'))

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
const isDocuments = computed(() => current.value.view === 'documents')
const maxTokens = computed(() => props.dataset.settings?.doc_max_tokens ?? 512)

// ---------- 목록 한 쪽 ----------

const queryPage = ref<QueryPageRead | null>(null)
const documentPage = ref<DocumentPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
let listController: AbortController | null = null

const requestKey = computed(() => {
  const view = current.value.view
  if (view === 'map') return JSON.stringify([datasetId.value, 'map'])
  const query = view === 'documents' ? toDocumentListQuery(current.value) : toQueryListQuery(current.value)
  return JSON.stringify([datasetId.value, view, query])
})

const page = computed(() => (isDocuments.value ? documentPage.value : queryPage.value))
const rowIds = computed(() => (page.value?.items ?? []).map((item) => item.id))
const total = computed(() => page.value?.total ?? 0)
const canClear = computed(() => hasFilters(current.value) || current.value.page > 1)
const firstNumber = computed(() => (current.value.page - 1) * PAGE_SIZE + 1)
const lastNumber = computed(() => firstNumber.value + rowIds.value.length - 1)
const hasNextPage = computed(() => current.value.page * PAGE_SIZE < total.value)

watch(requestKey, loadPage, { immediate: true })
watch([helperChanged, jobFinished], loadPage)

async function loadPage(): Promise<void> {
  if (current.value.view === 'map') return
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  loading.value = true
  loadError.value = null
  try {
    if (isDocuments.value) {
      documentPage.value = await listDocuments(datasetId.value, toDocumentListQuery(current.value), controller.signal)
    } else {
      queryPage.value = await listQueries(datasetId.value, toQueryListQuery(current.value), controller.signal)
    }
  } catch (error) {
    if (isAbortError(error)) return
    if (isDocuments.value) documentPage.value = null
    else queryPage.value = null
    loadError.value = errorMessage(error)
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}

// ---------- 주소 바꾸기 ----------

function changeFilters(patch: Partial<DataFilters>): void {
  void router.push({ query: writeDataQuery({ ...current.value, ...patch, page: 1 }) })
}

function clearFilters(): void {
  changeFilters({ ...NO_FILTERS })
}

function setView(view: DataView): void {
  if (view === current.value.view) return
  // 질의와 문서는 거르기가 다르므로 보기를 바꾸면 거르기 · 쪽 · 연 줄을 비운다(지도는 거르기를 그대로 쓴다).
  const keepsFilters = view === 'map' || current.value.view === 'map'
  const base: DataQuery = keepsFilters ? current.value : { ...current.value, ...NO_FILTERS }
  void router.push({ query: writeDataQuery({ ...base, view, page: 1, open: keepsFilters ? current.value.open : null }) })
}

async function goToPage(pageNumber: number): Promise<void> {
  await router.push({ query: writeDataQuery({ ...current.value, page: pageNumber }) })
  const top = card.value?.getBoundingClientRect().top ?? 0
  if (top < 0) window.scrollTo({ top: window.scrollY + top - PAGE_SCROLL_OFFSET })
}

function setOpen(open: OpenItem | null): void {
  void router.replace({ query: writeDataQuery({ ...current.value, open }) })
}

// ---------- 패널 ----------

// 표 보기에서 패널은 그 보기의 것만 연다. 다른 종류(질의 패널에서 문서를 누름)는 보기를 바꿔 연다.
function openQuery(id: number): void {
  if (current.value.view === 'documents') {
    void router.push({ query: writeDataQuery({ ...current.value, ...NO_FILTERS, view: 'queries', page: 1, open: { kind: 'query', id } }) })
    return
  }
  setOpen({ kind: 'query', id })
}

function openDocument(id: number): void {
  if (current.value.view === 'queries') {
    void router.push({
      query: writeDataQuery({ ...current.value, ...NO_FILTERS, view: 'documents', page: 1, open: { kind: 'document', id } }),
    })
    return
  }
  setOpen({ kind: 'document', id })
}

const openIndex = computed(() => {
  const open = current.value.open
  if (!open) return -1
  const matchesView = (open.kind === 'document') === isDocuments.value
  return matchesView ? rowIds.value.indexOf(open.id) : -1
})
const canPrev = computed(() => openIndex.value > 0)
const canNext = computed(() => openIndex.value >= 0 && openIndex.value < rowIds.value.length - 1)

function close(): void {
  setOpen(null)
}

function step(delta: number): void {
  const ids = rowIds.value
  if (!ids.length || current.value.view === 'map') return
  const index = openIndex.value
  const nextIndex = index === -1 ? 0 : Math.min(Math.max(index + delta, 0), ids.length - 1)
  const id = ids[nextIndex]
  setOpen({ kind: isDocuments.value ? 'document' : 'query', id })
  void nextTick(() => {
    const selector = isDocuments.value ? `[data-document-id="${id}"]` : `[data-query-id="${id}"]`
    document.querySelector(selector)?.scrollIntoView({ block: 'nearest' })
  })
}

function onChanged(): void {
  void refreshDataset()
  void loadPage()
}

onBeforeUnmount(() => listController?.abort())

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.closest('input, textarea, select, [contenteditable="true"], [role="menu"], [role="dialog"]') !== null
}

useEventListener(window, 'keydown', (event: KeyboardEvent) => {
  const hasModifier = event.metaKey || event.ctrlKey || event.altKey
  if (event.defaultPrevented || hasModifier || isTypingTarget(event.target)) return
  if (event.code === 'KeyJ') step(1)
  else if (event.code === 'KeyK') step(-1)
  else if (event.key === 'Escape' && current.value.open !== null) close()
  else if (event.key === '/') {
    event.preventDefault()
    toolbar.value?.focusSearch()
  }
})
</script>

<template>
  <div ref="tabRoot" class="px-6 py-5 xl:px-8">
    <div class="flex items-start gap-2">
      <DataToolbar
        ref="toolbar"
        class="min-w-0 flex-1"
        :filters="current"
        :view="current.view === 'map' ? 'queries' : current.view"
        :dataset="dataset"
        @change="changeFilters"
        @view="setView"
      />
      <div class="inline-flex shrink-0 rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)" role="group" aria-label="보기">
        <button
          type="button"
          class="inline-flex h-[26px] items-center gap-1.5 rounded-[5px] px-2.5 text-ui font-[550] transition-colors [&>svg]:size-3.5"
          :class="current.view !== 'map' ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
          :aria-pressed="current.view !== 'map'"
          data-view="table"
          @click="setView('queries')"
        >
          <Table2 />표
        </button>
        <button
          type="button"
          class="inline-flex h-[26px] items-center gap-1.5 rounded-[5px] px-2.5 text-ui font-[550] transition-colors [&>svg]:size-3.5"
          :class="current.view === 'map' ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
          :aria-pressed="current.view === 'map'"
          data-view="map"
          @click="setView('map')"
        >
          <ChartScatter />지도
        </button>
      </div>
    </div>

    <MapView
      v-if="current.view === 'map'"
      :dataset-id="datasetId"
      :filters="current"
      :open="current.open"
      @open="setOpen"
      @change="changeFilters"
    />

    <div v-else ref="card" class="card mt-3 overflow-clip">
      <div class="flex h-11 items-center gap-2 border-b px-4">
        <Skeleton v-if="!page && !loadError" class="h-5 w-24" />
        <template v-else-if="page">
          <b class="text-title font-semibold">{{ isDocuments ? '문서' : '질의' }} {{ fmt(total) }}</b>
          <span v-if="!hasFilters(current)" class="text-ui text-muted-foreground">전체</span>
        </template>
        <div v-if="rowIds.length && total > PAGE_SIZE" class="ml-auto flex items-center gap-0.5 text-ui text-muted-foreground">
          <span class="mr-1.5">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }}</span>
          <Button variant="quiet" size="icon-sm" :disabled="current.page <= 1" aria-label="이전 쪽" @click="goToPage(current.page - 1)">
            <ChevronLeft />
          </Button>
          <Button variant="quiet" size="icon-sm" :disabled="!hasNextPage" aria-label="다음 쪽" @click="goToPage(current.page + 1)">
            <ChevronRight />
          </Button>
        </div>
      </div>

      <DocumentTable
        v-if="isDocuments"
        :rows="documentPage?.items ?? null"
        :loading="loading"
        :error="loadError"
        :open-id="current.open?.kind === 'document' ? current.open.id : null"
        :narrow="current.open !== null || isCramped"
        :can-clear="canClear"
        :max-tokens="maxTokens"
        @open="openDocument($event.id)"
        @clear="clearFilters"
        @retry="loadPage"
      />
      <QueryTable
        v-else
        :rows="queryPage?.items ?? null"
        :loading="loading"
        :error="loadError"
        :open-id="current.open?.kind === 'query' ? current.open.id : null"
        :narrow="current.open !== null || isCramped"
        :can-clear="canClear"
        @open="openQuery($event.id)"
        @clear="clearFilters"
        @retry="loadPage"
      />

      <div v-if="page && total > 0" class="flex h-11 items-center gap-3 border-t bg-muted/30 px-4 text-ui text-muted-foreground">
        <span v-if="rowIds.length">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }} / 전체 {{ fmt(total) }}</span>
        <span v-else>— / 전체 {{ fmt(total) }}</span>
        <span v-if="current.open === null" class="ml-3 hidden items-center gap-1.5 xl:inline-flex">
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
    <QueryPanel
      v-if="current.open?.kind === 'query'"
      :query-id="current.open.id"
      :can-prev="canPrev"
      :can-next="canNext"
      @close="close"
      @step="step"
      @changed="onChanged"
      @open-document="openDocument"
    />
    <DocumentPanel
      v-else-if="current.open?.kind === 'document'"
      :document-id="current.open.id"
      :can-prev="canPrev"
      :can-next="canNext"
      :max-tokens="maxTokens"
      @close="close"
      @step="step"
      @changed="onChanged"
      @open-query="openQuery"
      @open-document="openDocument"
    />
  </Teleport>
</template>
