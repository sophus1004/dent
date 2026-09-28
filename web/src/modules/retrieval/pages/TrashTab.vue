<!--
  휴지통 탭 (#/retrieval/<id>/trash): 휴지통에 넣은 질의 · 문서를 한 쪽에 50줄씩 보고, 골라서 되살리거나 비운다.
    머리   [질의 | 문서] · 휴지통 n · 보관 · 비울 때까지 · [되살리기 n] · [휴지통 비우기]
    표     데이터 탭과 같은 표(QueryTable · DocumentTable) + 맨 앞 고르기 칸 · 쪽 넘기기(?page=)
  되살리기는 bulk(restore), 비우기는 확인 창을 거쳐 DELETE …/trash?kind=로 영구히 지운다(판정 · 지도 점도 함께).
  줄을 누르면 데이터 탭의 휴지통 거르기에서 그 줄을 연다.
-->
<script setup lang="ts">
import { ArchiveRestore, ChevronLeft, ChevronRight, FileText, LoaderCircle, MessageCircleQuestion, Trash2, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Skeleton } from '@/system/ui/skeleton'

import { bulkUpdateDocuments, bulkUpdateQueries, emptyTrash, listDocuments, listQueries } from '@/modules/retrieval/api'
import DocumentTable from '@/modules/retrieval/data/DocumentTable.vue'
import { NO_FILTERS, PAGE_SIZE, writeDataQuery } from '@/modules/retrieval/data/filters'
import QueryTable from '@/modules/retrieval/data/QueryTable.vue'
import { useDatasetRefresh } from '@/modules/retrieval/datasetRefresh'
import type { DatasetRead, DocumentPageRead, QueryPageRead } from '@/modules/retrieval/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

type Kind = 'queries' | 'documents'

const WARNING_TAG_CLASS =
  'inline-flex h-5 shrink-0 items-center rounded-md px-[7px] text-meta font-semibold text-danger-ink shadow-[inset_0_0_0_1px_var(--danger)]'

const route = useRoute()
const router = useRouter()
const refreshDataset = useDatasetRefresh()

const datasetId = computed(() => Number(route.params.datasetId))
const kind = computed<Kind>(() => (route.query.kind === 'documents' ? 'documents' : 'queries'))
const page = computed(() => {
  const value = Number(route.query.page ?? 1)
  return Number.isInteger(value) && value > 0 ? value : 1
})

const queryPage = ref<QueryPageRead | null>(null)
const documentPage = ref<DocumentPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
let controller: AbortController | null = null

const result = computed(() => (kind.value === 'documents' ? documentPage.value : queryPage.value))
const rowCount = computed(() => result.value?.items.length ?? 0)
const total = computed(() => result.value?.total ?? 0)
const firstNumber = computed(() => (page.value - 1) * PAGE_SIZE + 1)
const lastNumber = computed(() => firstNumber.value + rowCount.value - 1)
const hasNextPage = computed(() => page.value * PAGE_SIZE < total.value)
const isEmpty = computed(() => result.value !== null && total.value === 0)

const selected = ref<number[]>([])
const restoring = ref(false)
const restoreError = ref<string | null>(null)

const emptyOpen = ref(false)
const emptying = ref(false)
const emptyError = ref<string | null>(null)

watch(
  [datasetId, kind, page],
  () => {
    selected.value = []
    restoreError.value = null
    void loadTrash()
  },
  { immediate: true },
)
onBeforeUnmount(() => controller?.abort())

async function loadTrash(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  const query = { status: 'trash' as const, limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE }
  try {
    if (kind.value === 'documents') documentPage.value = await listDocuments(datasetId.value, query, current.signal)
    else queryPage.value = await listQueries(datasetId.value, query, current.signal)
  } catch (error) {
    if (isAbortError(error)) return
    loadError.value = errorMessage(error)
  } finally {
    if (!current.signal.aborted) loading.value = false
  }
}

function setKind(next: Kind): void {
  void router.push({ query: next === 'documents' ? { kind: next } : {} })
}

function goToPage(next: number): void {
  const query: Record<string, string> = {}
  if (kind.value === 'documents') query.kind = 'documents'
  if (next > 1) query.page = String(next)
  void router.push({ query })
}

async function restoreSelected(): Promise<void> {
  if (!selected.value.length || restoring.value) return
  restoring.value = true
  restoreError.value = null
  try {
    if (kind.value === 'documents') {
      await bulkUpdateDocuments(datasetId.value, { document_ids: selected.value, action: 'restore' })
    } else {
      await bulkUpdateQueries(datasetId.value, { query_ids: selected.value, action: 'restore' })
    }
    selected.value = []
    void refreshDataset()
    await loadTrash()
    if (rowCount.value === 0 && page.value > 1) goToPage(page.value - 1)
  } catch (error) {
    restoreError.value = errorMessage(error)
  } finally {
    restoring.value = false
  }
}

async function emptyAll(): Promise<void> {
  if (emptying.value) return
  emptying.value = true
  emptyError.value = null
  try {
    await emptyTrash(datasetId.value, kind.value)
    emptyOpen.value = false
    selected.value = []
    void refreshDataset()
    if (page.value > 1) goToPage(1)
    else await loadTrash()
  } catch (error) {
    emptyError.value = errorMessage(error)
  } finally {
    emptying.value = false
  }
}

function openRow(id: number): void {
  const isDocument = kind.value === 'documents'
  void router.push({
    name: 'retrieval-data',
    params: { datasetId: route.params.datasetId },
    query: writeDataQuery({
      ...NO_FILTERS,
      status: 'trash',
      view: isDocument ? 'documents' : 'queries',
      page: 1,
      open: { kind: isDocument ? 'document' : 'query', id },
    }),
  })
}
</script>

<template>
  <div class="max-w-[1320px] px-6 py-5 xl:px-8">
    <section class="card overflow-clip" aria-label="휴지통">
      <div class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
        <div class="inline-flex shrink-0 rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)" role="group" aria-label="질의 · 문서">
          <button
            v-for="item in [{ key: 'queries' as Kind, name: '질의', icon: MessageCircleQuestion }, { key: 'documents' as Kind, name: '문서', icon: FileText }]"
            :key="item.key"
            type="button"
            class="inline-flex h-[26px] items-center gap-1.5 rounded-[5px] px-2.5 text-ui font-[550] transition-colors [&>svg]:size-3.5"
            :class="kind === item.key ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
            :aria-pressed="kind === item.key"
            @click="setKind(item.key)"
          >
            <component :is="item.icon" />{{ item.name }}
          </button>
        </div>
        <Skeleton v-if="!result && !loadError" class="h-5 w-24" />
        <b v-else class="text-title font-semibold">휴지통 {{ fmt(total) }}</b>
        <dl class="flex items-center gap-x-2 text-ui text-muted-foreground">
          <dt>보관</dt>
          <dd class="font-semibold text-foreground">비울 때까지</dd>
        </dl>
        <div v-if="rowCount && total > PAGE_SIZE" class="ml-auto flex items-center gap-0.5 text-ui text-muted-foreground">
          <span class="mr-1.5">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }}</span>
          <Button variant="quiet" size="icon-sm" :disabled="page <= 1" aria-label="이전 쪽" @click="goToPage(page - 1)"><ChevronLeft /></Button>
          <Button variant="quiet" size="icon-sm" :disabled="!hasNextPage" aria-label="다음 쪽" @click="goToPage(page + 1)"><ChevronRight /></Button>
        </div>
        <Button
          v-if="!isEmpty"
          type="button"
          variant="outline"
          size="sm"
          :class="rowCount && total > PAGE_SIZE ? '' : 'ml-auto'"
          :disabled="!selected.length || restoring"
          data-action="trash-restore"
          @click="restoreSelected"
        >
          <LoaderCircle v-if="restoring" class="animate-spin" /><ArchiveRestore v-else />되살리기
          <span v-if="selected.length" class="count-pill">{{ fmt(selected.length) }}</span>
        </Button>
        <Button
          v-if="!isEmpty && result"
          type="button"
          variant="outline"
          size="sm"
          class="text-danger-ink"
          data-action="open-trash-empty"
          @click="emptyError = null; emptyOpen = true"
        >
          <Trash2 />휴지통 비우기
        </Button>
      </div>
      <div v-if="restoreError" class="flex items-center gap-2 border-b px-4 py-2 text-ui" role="alert">
        <TriangleAlert class="size-4 shrink-0 text-danger" />
        <span class="font-semibold text-danger-ink">되살리기 실패</span>
        <span class="text-muted-foreground">{{ restoreError }}</span>
      </div>

      <div v-if="isEmpty" class="py-14 text-center" data-empty="trash">
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted"><Trash2 class="size-5 text-muted-foreground" /></span>
        <div class="mt-3 text-body font-semibold">휴지통 비어 있음</div>
      </div>

      <template v-else>
        <DocumentTable
          v-if="kind === 'documents'"
          v-model:selected="selected"
          :rows="documentPage?.items ?? null"
          :loading="loading"
          :error="loadError"
          :open-id="null"
          :narrow="false"
          :can-clear="false"
          :max-tokens="props.dataset.settings?.doc_max_tokens ?? 512"
          selectable
          @open="openRow($event.id)"
          @retry="loadTrash"
        />
        <QueryTable
          v-else
          v-model:selected="selected"
          :rows="queryPage?.items ?? null"
          :loading="loading"
          :error="loadError"
          :open-id="null"
          :narrow="false"
          :can-clear="false"
          selectable
          @open="openRow($event.id)"
          @retry="loadTrash"
        />
      </template>
    </section>

    <Dialog v-model:open="emptyOpen">
      <DialogContent
        class="w-[min(420px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
        data-dialog="trash-empty"
      >
        <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
          <Trash2 class="size-4 text-danger" />
          <DialogTitle class="text-title font-semibold">휴지통 비우기</DialogTitle>
          <span :class="WARNING_TAG_CLASS">되돌릴 수 없음</span>
          <DialogDescription class="sr-only">휴지통의 질의 · 문서를 영구히 지운다</DialogDescription>
        </DialogHeader>
        <div class="space-y-4 px-5 py-4">
          <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">{{ kind === 'documents' ? '문서' : '질의' }}</dt>
            <dd><b class="font-semibold">{{ fmt(total) }}</b> · 휴지통 전부</dd>
            <dt class="text-muted-foreground">함께 지움</dt>
            <dd>그 판정 · 지도의 점</dd>
          </dl>
          <div v-if="emptyError" class="flex items-start gap-2 text-ui" role="alert">
            <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger" />
            <span class="font-semibold text-danger-ink">실패</span>
            <span class="text-muted-foreground">{{ emptyError }}</span>
          </div>
          <div class="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" @click="emptyOpen = false">취소</Button>
            <Button type="button" class="bg-danger text-white hover:bg-danger/90" :disabled="emptying" data-action="trash-empty" @click="emptyAll">
              <LoaderCircle v-if="emptying" class="animate-spin" /><Trash2 v-else />비우기
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
