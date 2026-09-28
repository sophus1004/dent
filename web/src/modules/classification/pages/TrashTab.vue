<!--
  휴지통 탭 (#/classification/<id>/trash): 휴지통에 넣은 문장을 한 쪽에 50건씩 보고, 골라서 되살리거나 모두 비운다.
    머리   휴지통 n건 · 보관 · 비울 때까지 · [되살리기 n] (고른 것이 있을 때만 켜진다) · [휴지통 비우기]
    표     데이터 탭과 같은 문장 표(RecordTable) + 맨 앞 고르기 칸 · 쪽 넘기기(?page=)
    비면   '휴지통 비어 있음'
  GET records?status=trash로 읽고, 되살리기는 POST records/bulk(restore)로 보낸다. 되살린 뒤 이 쪽과 머리 건수를
  다시 읽는다(쪽이 비면 앞 쪽으로). 고르기는 이 쪽 안에서만 하고, 쪽을 넘기면 푼다.
  비우기는 확인 창을 거쳐 DELETE .../trash로 모두 영구히 지운다(되돌릴 수 없음, 의미 지도의 점도 함께).
  줄을 누르면 데이터 탭의 휴지통 거르기에서 그 문장을 연다.
-->
<script setup lang="ts">
import { ArchiveRestore, ChevronLeft, ChevronRight, LoaderCircle, Trash2, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Skeleton } from '@/system/ui/skeleton'

import { bulkUpdateRecords, emptyTrash, listRecords } from '@/modules/classification/api'
import { DEFAULT_VIEW, NO_FILTERS, PAGE_SIZE, readDataQuery, writeDataQuery } from '@/modules/classification/data/filters'
import RecordTable from '@/modules/classification/data/RecordTable.vue'
import { useDatasetRefresh } from '@/modules/classification/datasetRefresh'
import type { DatasetRead, RecordPageRead, RecordRead } from '@/modules/classification/types'

defineProps<{
  dataset: DatasetRead
}>()

const route = useRoute()
const router = useRouter()
const refreshDataset = useDatasetRefresh()

const datasetId = computed(() => Number(route.params.datasetId))
// 쪽 번호는 데이터 탭과 같은 방식으로 주소에서 읽는다(?page=)
const page = computed(() => readDataQuery(route.query).page)

const result = ref<RecordPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
let controller: AbortController | null = null

const rows = computed(() => result.value?.items ?? null)
const total = computed(() => result.value?.total ?? 0)
const firstNumber = computed(() => (page.value - 1) * PAGE_SIZE + 1)
const lastNumber = computed(() => firstNumber.value + (rows.value?.length ?? 0) - 1)
const hasNextPage = computed(() => page.value * PAGE_SIZE < total.value)
const isEmpty = computed(() => result.value !== null && total.value === 0)

// 고른 문장 번호와, 되살리기를 보내는 중인지 · 실패한 까닭
const selected = ref<number[]>([])
const restoring = ref(false)
const restoreError = ref<string | null>(null)

watch([datasetId, page], () => {
  selected.value = []
  restoreError.value = null
  void loadTrash()
}, { immediate: true })
onBeforeUnmount(() => controller?.abort())

// 되돌릴 수 없음 태그 (빨강 글자, 테두리)
const WARNING_TAG_CLASS =
  'inline-flex h-5 shrink-0 items-center rounded-md px-[7px] text-meta font-semibold text-danger-ink shadow-[inset_0_0_0_1px_var(--danger)]'

// 휴지통 비우기 확인 창
const emptyOpen = ref(false)
const emptying = ref(false)
const emptyError = ref<string | null>(null)

function openEmptyDialog(): void {
  emptyError.value = null
  emptyOpen.value = true
}

/** 휴지통을 비운다(영구 삭제). 그 뒤 첫 쪽으로 가서 다시 읽고 머리 건수도 맞춘다. */
async function emptyAll(): Promise<void> {
  if (emptying.value) return
  emptying.value = true
  emptyError.value = null
  try {
    await emptyTrash(datasetId.value)
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

/** 고른 문장을 되살린다. 그 뒤 이 쪽과 머리 건수를 다시 읽고, 이 쪽이 비었으면 앞 쪽으로 간다. */
async function restoreSelected(): Promise<void> {
  if (!selected.value.length || restoring.value) return
  restoring.value = true
  restoreError.value = null
  try {
    await bulkUpdateRecords(datasetId.value, { record_ids: selected.value, action: 'restore' })
    selected.value = []
    void refreshDataset()
    await loadTrash()
    const isPageEmptied = rows.value?.length === 0 && page.value > 1
    if (isPageEmptied) goToPage(page.value - 1)
  } catch (error) {
    restoreError.value = errorMessage(error)
  } finally {
    restoring.value = false
  }
}

async function loadTrash(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const query = { status: 'trash' as const, limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE }
    result.value = await listRecords(datasetId.value, query, current.signal)
  } catch (error) {
    if (isAbortError(error)) return
    result.value = null
    loadError.value = errorMessage(error)
  } finally {
    if (!current.signal.aborted) loading.value = false
  }
}

function goToPage(next: number): void {
  void router.push({ query: next > 1 ? { page: String(next) } : {} })
}

// 문장 상세는 데이터 탭의 오른쪽 패널에서 본다(휴지통 거르기 + 그 문장).
function open(record: RecordRead): void {
  void router.push({
    name: 'classification-data',
    params: { datasetId: route.params.datasetId },
    query: writeDataQuery({
      ...NO_FILTERS,
      status: 'trash',
      page: 1,
      recordId: record.id,
      view: DEFAULT_VIEW,
    }),
  })
}
</script>

<template>
  <div class="max-w-[1320px] px-6 py-5 xl:px-8">
    <section class="card overflow-clip" aria-label="휴지통">
      <div class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
        <Skeleton v-if="!result && !loadError" class="h-5 w-24" />
        <b v-else class="text-title font-semibold">휴지통 {{ fmt(total) }}건</b>
        <dl class="flex items-center gap-x-2 text-ui text-muted-foreground">
          <dt>보관</dt>
          <dd class="font-semibold text-foreground">비울 때까지</dd>
        </dl>
        <div v-if="rows?.length && total > PAGE_SIZE" class="ml-auto flex items-center gap-0.5 text-ui text-muted-foreground">
          <span class="mr-1.5">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }}</span>
          <Button variant="quiet" size="icon-sm" :disabled="page <= 1" aria-label="이전 쪽" @click="goToPage(page - 1)">
            <ChevronLeft />
          </Button>
          <Button variant="quiet" size="icon-sm" :disabled="!hasNextPage" aria-label="다음 쪽" @click="goToPage(page + 1)">
            <ChevronRight />
          </Button>
        </div>
        <Button
          v-if="!isEmpty"
          type="button"
          variant="outline"
          size="sm"
          :class="rows?.length && total > PAGE_SIZE ? '' : 'ml-auto'"
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
          @click="openEmptyDialog"
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
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
          <Trash2 class="size-5 text-muted-foreground" />
        </span>
        <div class="mt-3 text-body font-semibold">휴지통 비어 있음</div>
        <dl class="mt-2 inline-grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-left text-ui">
          <dt class="text-muted-foreground">보관</dt>
          <dd>비울 때까지</dd>
          <dt class="text-muted-foreground">학습</dt>
          <dd>휴지통 문장 제외</dd>
        </dl>
      </div>

      <template v-else>
        <RecordTable
          v-model:selected="selected"
          :rows="rows"
          :loading="loading"
          :error="loadError"
          :open-id="null"
          :grouped="false"
          :narrow="false"
          :can-clear="false"
          selectable
          @open="open"
          @retry="loadTrash"
        />
        <div
          v-if="result && total > 0"
          class="flex h-11 items-center gap-3 border-t bg-muted/30 px-4 text-ui text-muted-foreground"
        >
          <span v-if="rows?.length">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }} / 전체 {{ fmt(total) }}</span>
          <span v-else>— / 전체 {{ fmt(total) }}</span>
          <div class="ml-auto flex items-center gap-1">
            <Button variant="quiet" size="xs" :disabled="page <= 1" aria-label="이전 쪽" @click="goToPage(page - 1)">
              <ChevronLeft />이전
            </Button>
            <span class="text-subtle-foreground">·</span>
            <Button variant="quiet" size="xs" :disabled="!hasNextPage" aria-label="다음 쪽" @click="goToPage(page + 1)">
              다음<ChevronRight />
            </Button>
          </div>
        </div>
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
          <DialogDescription class="sr-only">휴지통의 문장을 모두 영구히 지운다</DialogDescription>
        </DialogHeader>
        <div class="space-y-4 px-5 py-4">
          <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">문장</dt>
            <dd><b class="font-semibold">{{ fmt(total) }}</b> · 휴지통 전부</dd>
            <dt class="text-muted-foreground">함께 지움</dt>
            <dd>의미 지도의 그 문장 점</dd>
            <dt class="text-muted-foreground">그대로</dt>
            <dd>휴지통 밖 문장 · 라벨</dd>
          </dl>
          <div v-if="emptyError" class="flex items-start gap-2 text-ui" role="alert">
            <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger" />
            <span class="font-semibold text-danger-ink">실패</span>
            <span class="text-muted-foreground">{{ emptyError }}</span>
          </div>
          <div class="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" @click="emptyOpen = false">취소</Button>
            <Button
              type="button"
              class="bg-danger text-white hover:bg-danger/90"
              :disabled="emptying"
              data-action="trash-empty"
              @click="emptyAll"
            >
              <LoaderCircle v-if="emptying" class="animate-spin" /><Trash2 v-else />비우기
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
