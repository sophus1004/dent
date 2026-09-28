<!--
  문장 상세 패널: 표에서 문장을 누르면 오른쪽에 열린다. 넓은 화면에서는 표 옆에, 좁으면 표 위에 뜬다.
    머리     문장 #번호 · 이전(K) · 다음(J) · 닫기(Esc)
    몸       문장(전체) · 라벨 · 상태 · 표시 · 고치기 · 같은 문장 · 뜻이 가까운 문장 · 메타데이터 · 가져온 곳
    바닥     단축키 · [저장]
  같은 문장은 GET records?same_as=번호로, 뜻이 가까운 문장(근접 중복)은 GET records/{id}/near-duplicates로,
  가져온 곳은 가져오기 기록(GET /imports/{id})으로 따로 읽는다. 뜻이 가까운 문장을 누르면 그 문장을 연다.
  고치기 가운데 [라벨 바꾸기](PATCH records/{id}, row_version을 함께 보낸다)와 [휴지통으로](휴지통의 문장이면
  [되살리기], POST records/bulk)가 된다. 바꾸면 changed를 알려 부모가 목록 · 머리 건수 · 이 문장을 다시 읽는다.
  나머지 고치기 버튼과 [저장]은 data-todo를 단 채 아무 일도 하지 않는다.
-->
<script setup lang="ts">
import {
  ArchiveRestore,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  FileQuestion,
  LoaderCircle,
  Tag,
  Trash2,
  TriangleAlert,
  X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getImport } from '@/system/api'
import { dateTimeText, fmt, showValue } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import type { ImportRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/system/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import {
  bulkUpdateRecords,
  listNearDuplicates,
  listRecords,
  updateRecord,
} from '@/modules/classification/api'
import { EXCLUDE_REASON_NAMES } from '@/modules/classification/data/filters'
import RecordMarks from '@/modules/classification/data/RecordMarks.vue'
import RecordStatusIcon from '@/modules/classification/data/RecordStatusIcon.vue'
import LabelChip from '@/modules/classification/LabelChip.vue'
import type { LabelRead, NearDuplicateRead, RecordRead } from '@/modules/classification/types'

const props = defineProps<{
  datasetId: number
  recordId: number
  // 연 문장. 찾는 중이면 null
  record: RecordRead | null
  // 찾아봤는데 없는 문장인지
  missing: boolean
  canPrev: boolean
  canNext: boolean
  // 라벨 바꾸기에서 고를 데이터셋의 라벨 (이름 순)
  labels: LabelRead[]
}>()

const emit = defineEmits<{
  close: []
  // -1 이전 줄, +1 다음 줄
  step: [delta: number]
  // 같은 문장 목록에서 다른 문장을 연다
  open: [record: RecordRead]
  // 뜻이 가까운 문장 목록에서 번호로 다른 문장을 연다
  openId: [recordId: number]
  // 이 문장의 라벨을 바꿨거나, 휴지통으로 보냈거나 되살렸다
  changed: [recordId: number]
}>()

// 같은 문장 목록에 보일 최대 건수
const SAME_TEXT_LIMIT = 20

// 가져오기 기록은 바뀌지 않으므로 한 번 읽은 것은 다시 묻지 않는다. 번호 → 기록
const importCache = new Map<number, ImportRead>()

// 칸 이름
const FIELD_LABEL_CLASS = 'mb-1.5 flex items-center gap-1.5 text-ui font-[550]'

const sameText = ref<RecordRead[] | null>(null)
const sameTotal = ref(0)
const sameFailed = ref(false)
// 뜻이 가까운 문장. 읽는 중이면 null
const nearTexts = ref<NearDuplicateRead[] | null>(null)
const nearFailed = ref(false)
const source = ref<ImportRead | null>(null)
let sameController: AbortController | null = null
let nearController: AbortController | null = null

// 라벨 바꾸기 · 휴지통으로 보내기 · 되살리기를 보내는 중인지와 실패한 까닭
const moving = ref(false)
const moveError = ref<string | null>(null)

// 라벨 바꾸기에서 고를 라벨: 지금 라벨을 뺀 나머지
const otherLabels = computed(() => props.labels.filter((label) => label.id !== props.record?.label_id))

// 휴지통 밖에 같은 문장이 더 있을 때만 목록을 읽는다. 휴지통의 문장이면 휴지통 밖의 같은 문장을 보인다.
const hasSameText = computed(() => (props.record?.duplicate_count ?? 0) > 0)

const statusText = computed(() => {
  const record = props.record
  if (!record) return ''
  if (record.is_trashed) return `휴지통 · ${dateTimeText(record.trashed_at)}`
  if (record.exclude_reason) return `학습 제외 · ${EXCLUDE_REASON_NAMES[record.exclude_reason]}`
  return '학습 포함'
})

const extraFields = computed(() => Object.entries(props.record?.extra ?? {}))

const hasMarks = computed(() => {
  const record = props.record
  return record !== null && (record.duplicate_count > 0 || record.has_conflict)
})

watch(() => props.record?.id, loadDetails, { immediate: true })
onBeforeUnmount(() => {
  sameController?.abort()
  nearController?.abort()
})

// 문장이 바뀌면 같은 문장 목록과 가져온 곳을 다시 읽는다.
function loadDetails(): void {
  sameController?.abort()
  nearController?.abort()
  sameText.value = null
  sameFailed.value = false
  nearTexts.value = null
  nearFailed.value = false
  moveError.value = null
  const record = props.record
  if (!record) return
  if (hasSameText.value) void loadSameText(record.id)
  void loadNearTexts(record.id)
  void loadSource(record.import_id)
}

async function loadNearTexts(recordId: number): Promise<void> {
  const controller = new AbortController()
  nearController = controller
  try {
    nearTexts.value = await listNearDuplicates(recordId, controller.signal)
  } catch (error) {
    if (!isAbortError(error)) nearFailed.value = true
  }
}

/** 이 문장의 라벨을 바꾼다. 그 사이 다른 곳에서 고쳤으면 409 문장을 보인다. */
async function changeLabel(label: LabelRead): Promise<void> {
  const record = props.record
  if (!record || moving.value) return
  moving.value = true
  moveError.value = null
  try {
    await updateRecord(record.id, { label_id: label.id, row_version: record.row_version })
    emit('changed', record.id)
  } catch (error) {
    moveError.value = errorMessage(error)
  } finally {
    moving.value = false
  }
}

async function loadSameText(recordId: number): Promise<void> {
  sameController = new AbortController()
  try {
    const page = await listRecords(
      props.datasetId,
      { same_as: recordId, limit: SAME_TEXT_LIMIT },
      sameController.signal,
    )
    sameText.value = page.items
    sameTotal.value = page.total
  } catch (error) {
    if (!isAbortError(error)) sameFailed.value = true
  }
}

/** 이 문장을 휴지통으로 보낸다. 휴지통의 문장이면 되살린다. */
async function moveRecord(): Promise<void> {
  const record = props.record
  if (!record || moving.value) return
  moving.value = true
  moveError.value = null
  try {
    const action = record.is_trashed ? 'restore' : 'trash'
    await bulkUpdateRecords(props.datasetId, { record_ids: [record.id], action })
    emit('changed', record.id)
  } catch (error) {
    moveError.value = errorMessage(error)
  } finally {
    moving.value = false
  }
}

async function loadSource(importId: number | null): Promise<void> {
  source.value = importId === null ? null : (importCache.get(importId) ?? null)
  if (importId === null || source.value) return
  try {
    const found = await getImport(importId)
    importCache.set(importId, found)
    // 읽는 사이 다른 문장으로 옮겼으면 버린다.
    if (props.record?.import_id === importId) source.value = found
  } catch {
    // 가져온 곳을 못 읽어도 번호는 보인다.
  }
}
</script>

<template>
  <aside
    class="flex h-[calc(100vh-52px)] w-[400px] flex-col border-l bg-card shadow-(--shadow-panel) duration-200 animate-in fade-in-0 slide-in-from-right-4 max-xl:fixed max-xl:top-[52px] max-xl:right-0 max-xl:bottom-0 max-xl:z-30 max-xl:h-auto max-xl:w-[360px]"
    aria-label="문장 상세"
  >
    <div class="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <span class="text-body font-semibold">문장</span>
      <span class="font-mono text-ui text-subtle-foreground">#{{ recordId }}</span>
      <div class="ml-auto flex items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canPrev" aria-label="이전 문장" @click="emit('step', -1)">
              <ChevronUp />
            </Button>
          </TooltipTrigger>
          <TooltipContent>이전 · K</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canNext" aria-label="다음 문장" @click="emit('step', 1)">
              <ChevronDown />
            </Button>
          </TooltipTrigger>
          <TooltipContent>다음 · J</TooltipContent>
        </Tooltip>
        <span class="mx-1 h-4 w-px bg-border" />
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" aria-label="닫기" @click="emit('close')">
              <X />
            </Button>
          </TooltipTrigger>
          <TooltipContent>닫기 · Esc</TooltipContent>
        </Tooltip>
      </div>
    </div>

    <div v-if="missing" class="flex flex-1 flex-col items-center justify-center gap-2 text-center">
      <span class="grid size-10 place-items-center rounded-lg bg-muted">
        <FileQuestion class="size-5 text-muted-foreground" />
      </span>
      <span class="text-body font-semibold">없는 문장</span>
      <span class="font-mono text-meta text-muted-foreground">#{{ recordId }}</span>
    </div>

    <div v-else-if="!record" class="flex-1 space-y-5 p-4" aria-busy="true">
      <Skeleton class="h-20 w-full rounded-lg" />
      <div class="space-y-2.5">
        <Skeleton class="h-4 w-40" />
        <Skeleton class="h-4 w-28" />
        <Skeleton class="h-4 w-32" />
      </div>
    </div>

    <div v-else class="flex-1 space-y-5 overflow-y-auto p-4">
      <section>
        <div :class="FIELD_LABEL_CLASS">
          문장<span class="ml-auto font-normal text-muted-foreground">{{ fmt(record.text.length) }}자</span>
        </div>
        <p class="rounded-lg border px-3 py-2.5 text-body whitespace-pre-wrap">{{ record.text }}</p>
      </section>

      <dl class="grid grid-cols-[84px_minmax(0,1fr)] items-center gap-x-2.5 gap-y-2 text-ui">
        <dt class="text-muted-foreground">라벨</dt>
        <dd class="min-w-0"><LabelChip :name="record.label_name" /></dd>
        <dt class="text-muted-foreground">상태</dt>
        <dd class="-ml-1 flex items-center gap-1">
          <RecordStatusIcon :record="record" /><span class="pl-1">{{ statusText }}</span>
        </dd>
        <dt class="text-muted-foreground">표시</dt>
        <dd class="flex flex-wrap items-center gap-1">
          <RecordMarks v-if="hasMarks" :record="record" />
          <span v-else class="text-subtle-foreground">—</span>
        </dd>
      </dl>

      <section>
        <div :class="FIELD_LABEL_CLASS">고치기</div>
        <div class="grid grid-cols-2 gap-1.5 [&>button]:justify-start">
          <DropdownMenu>
            <DropdownMenuTrigger as-child>
              <Button
                type="button"
                variant="outline"
                size="sm"
                class="col-span-2"
                :disabled="moving || record.is_trashed || !otherLabels.length"
                data-action="record-label"
              >
                <Tag />라벨 바꾸기<ChevronDown class="ml-auto text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" class="max-h-72 min-w-44 overflow-y-auto">
              <DropdownMenuLabel>라벨</DropdownMenuLabel>
              <DropdownMenuItem v-for="label in otherLabels" :key="label.id" @select="changeLabel(label)">
                <span class="truncate">{{ label.name }}</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <!-- 기능 미구현: 사용자와 함께 구현 -->
          <Button type="button" variant="outline" size="sm" data-todo="record-include">
            <template v-if="record.exclude_reason"><Eye />다시 넣기</template>
            <template v-else><EyeOff />학습에서 빼기</template>
          </Button>
          <Button
            v-if="record.is_trashed"
            type="button"
            variant="outline"
            size="sm"
            :disabled="moving"
            data-action="record-restore"
            @click="moveRecord"
          >
            <LoaderCircle v-if="moving" class="animate-spin" /><ArchiveRestore v-else />되살리기
          </Button>
          <Button
            v-else
            type="button"
            variant="outline"
            size="sm"
            class="hover:text-danger-ink"
            :disabled="moving"
            data-action="record-trash"
            @click="moveRecord"
          >
            <LoaderCircle v-if="moving" class="animate-spin" /><Trash2 v-else />휴지통으로
          </Button>
        </div>
        <div v-if="moveError" class="mt-2 flex items-start gap-1.5 text-ui" role="alert">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-danger" />
          <span class="font-semibold text-danger-ink">실패</span>
          <span class="text-muted-foreground">{{ moveError }}</span>
        </div>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">
          같은 문장<span v-if="hasSameText && sameText" class="count-pill">{{ fmt(sameTotal) }}</span>
        </div>
        <span v-if="!hasSameText" class="text-ui text-subtle-foreground">없음</span>
        <span v-else-if="sameFailed" class="text-ui text-subtle-foreground">불러오기 실패</span>
        <Skeleton v-else-if="!sameText" class="h-10 w-full rounded-lg" />
        <template v-else>
          <ul class="divide-y overflow-hidden rounded-lg border">
            <li v-for="item in sameText" :key="item.id">
              <button
                type="button"
                class="flex h-10 w-full items-center gap-2 px-3 text-left text-ui"
                :class="item.id === record.id ? 'bg-muted' : 'hover:bg-muted/60'"
                @click="item.id !== record.id && emit('open', item)"
              >
                <span class="min-w-0"><LabelChip :name="item.label_name" /></span>
                <RecordStatusIcon :record="item" />
                <span
                  v-if="item.id === record.id"
                  class="ml-auto shrink-0 rounded-md bg-muted px-1.5 text-meta font-semibold text-muted-foreground"
                >
                  현재
                </span>
                <span v-else class="ml-auto shrink-0 font-mono text-meta text-subtle-foreground">#{{ item.id }}</span>
              </button>
            </li>
          </ul>
          <div v-if="sameTotal > sameText.length" class="mt-1.5 text-meta text-muted-foreground">
            +{{ fmt(sameTotal - sameText.length) }}건
          </div>
        </template>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">
          뜻이 가까운 문장<span v-if="nearTexts?.length" class="count-pill">{{ fmt(nearTexts.length) }}</span>
        </div>
        <span v-if="nearFailed" class="text-ui text-subtle-foreground">불러오기 실패</span>
        <Skeleton v-else-if="!nearTexts" class="h-10 w-full rounded-lg" />
        <span v-else-if="!nearTexts.length" class="text-ui text-subtle-foreground">없음</span>
        <ul v-else class="divide-y overflow-hidden rounded-lg border" data-near-duplicates>
          <li v-for="item in nearTexts" :key="item.record_id">
            <button
              type="button"
              class="flex w-full flex-col gap-1 px-3 py-2 text-left text-ui hover:bg-muted/60"
              @click="emit('openId', item.record_id)"
            >
              <span class="flex w-full items-center gap-2">
                <span class="min-w-0"><LabelChip :name="item.label_name" /></span>
                <span class="ml-auto shrink-0 font-mono text-meta text-muted-foreground">
                  유사도 <b class="font-semibold text-foreground">{{ item.similarity.toFixed(3) }}</b>
                </span>
              </span>
              <span class="line-clamp-2 text-muted-foreground" :title="item.text">“{{ item.text }}”</span>
            </button>
          </li>
        </ul>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">메타데이터</div>
        <dl
          v-if="extraFields.length"
          class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-ui"
        >
          <template v-for="[key, value] in extraFields" :key="key">
            <dt class="truncate text-muted-foreground" :title="key">{{ key }}</dt>
            <dd class="min-w-0 break-words">{{ showValue(value) || '—' }}</dd>
          </template>
        </dl>
        <span v-else class="text-ui text-subtle-foreground">없음</span>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">가져온 곳</div>
        <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-ui">
          <dt class="text-muted-foreground">원본</dt>
          <dd class="min-w-0">
            <span v-if="record.import_id === null" class="text-subtle-foreground">—</span>
            <span v-else class="flex min-w-0 items-center gap-1.5">
              <span v-if="source" class="truncate" :title="source.source_name">{{ source.source_name }}</span>
              <Skeleton v-else class="h-3.5 w-32" />
              <span class="shrink-0 font-mono text-meta text-subtle-foreground">#{{ record.import_id }}</span>
            </span>
          </dd>
          <dt class="text-muted-foreground">추가</dt>
          <dd>{{ dateTimeText(record.created_at) }}</dd>
          <dt class="text-muted-foreground">수정</dt>
          <dd>{{ dateTimeText(record.updated_at) }}</dd>
        </dl>
      </section>
    </div>

    <div
      class="flex h-11 shrink-0 items-center gap-3 overflow-hidden border-t bg-muted/30 px-4 text-ui whitespace-nowrap text-muted-foreground"
    >
      <span class="inline-flex items-center gap-1"><span class="kbd">J</span><span class="kbd">K</span>이동</span>
      <span class="inline-flex items-center gap-1"><span class="kbd">Esc</span>닫기</span>
      <!-- 기능 미구현: 사용자와 함께 구현 -->
      <Button type="button" size="sm" class="ml-auto" data-todo="record-save">저장</Button>
    </div>
  </aside>
</template>
