<!--
  반복 구간 고르기 창. 진단의 '반복 구간' 줄 [→] · 데이터셋 [⋯] → [반복 구간]으로 연다.
    머리    떼기 n · 남김 n · 고를 것 n · 문서 n · [다시 살피기] (작업 ("retrieval", "scan"), 끝나면 다시 읽는다)
    왼쪽    [고를 것 | 떼기 | 남김 | 전체] · 종류 · 틀 목록(고르기 칸 · 틀 · 종류 · 문서 · 앞 · 뒤 · 지금 결정 또는 제안)
    오른쪽  누른 틀: 사실(문서 · 제안 · 까닭) · [떼기] [남김] [고르기 전] → 보기 문서 3(구간 강조) → 학습 글 · 떼면(뗀 곳 줄긋기)
    아래    [고른 것 떼기] [고른 것 남김] [제안대로 모두 n](고를 것만)
  떼기는 본문을 바꾸지 않고 학습 글에서만 뺀다. 고르면 진단 · 데이터가 다시 읽는다(jobEvents.notifyDataChanged).
-->
<script setup lang="ts">
import {
  AtSign,
  Check,
  CheckCheck,
  LoaderCircle,
  Pin,
  RefreshCw,
  Repeat as RepeatIcon,
  RotateCcw,
  Scissors,
  Stamp,
  TriangleAlert,
} from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { NativeSelect } from '@/system/ui/native-select'

import { decideRepeat, decideRepeats, getRepeatSamples, listRepeats, scanRepeats } from '@/modules/retrieval/api'
import { jobFinished, notifyDataChanged } from '@/modules/retrieval/jobEvents'
import type { RepeatDecision, RepeatKind, RepeatListRead, RepeatRead, RepeatSampleRead } from '@/modules/retrieval/types'

const props = defineProps<{
  datasetId: number
}>()

const emit = defineEmits<{
  changed: []
}>()

const open = defineModel<boolean>('open', { required: true })

type StateFilter = 'undecided' | 'remove' | 'keep' | 'all'

const STATE_FILTERS: { key: StateFilter; name: string }[] = [
  { key: 'undecided', name: '고를 것' },
  { key: 'remove', name: '떼기' },
  { key: 'keep', name: '남김' },
  { key: 'all', name: '전체' },
]

const KIND_NAMES: Record<RepeatKind, string> = { sentence: '반복 문장', meta: '메타 모양' }

const list = ref<RepeatListRead | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref<string | null>(null)
const scanning = ref(false)
const stateFilter = ref<StateFilter>('undecided')
const kindFilter = ref<RepeatKind | 'all'>('all')
const selectedId = ref<number | null>(null)
const checked = ref<number[]>([])
const samples = ref<RepeatSampleRead[] | null>(null)
let samplesController: AbortController | null = null

const items = computed(() => list.value?.items ?? [])
const visible = computed(() =>
  items.value.filter((item) => {
    const isState =
      stateFilter.value === 'all' ||
      (stateFilter.value === 'undecided' ? item.decision === null : item.decision === stateFilter.value)
    return isState && (kindFilter.value === 'all' || item.kind === kindFilter.value)
  }),
)
const selected = computed(() => items.value.find((item) => item.id === selectedId.value) ?? null)
const undecided = computed(() => items.value.filter((item) => item.decision === null))
const isAllVisibleChecked = computed(
  () => visible.value.length > 0 && visible.value.every((item) => checked.value.includes(item.id)),
)
const counts = computed(() => list.value?.counts ?? {})

// 주소(?repeats=1)로 들어오면 처음부터 열려 있으므로 바로 읽는다.
watch(
  open,
  (isOpen) => {
    if (!isOpen) return
    checked.value = []
    selectedId.value = null
    void load({ pickFilter: true })
  },
  { immediate: true },
)
// 다시 살피기 작업이 끝나면(다른 작업이 끝나도) 목록을 다시 읽는다.
watch(jobFinished, () => {
  if (!open.value) return
  scanning.value = false
  void load({ pickFilter: false })
})
watch(selectedId, loadSamples)
// 거르기를 바꿔 누른 틀이 목록에서 빠지면 목록의 첫 틀을 보인다.
watch([stateFilter, kindFilter], () => {
  if (!visible.value.some((item) => item.id === selectedId.value)) selectedId.value = visible.value[0]?.id ?? null
})

async function load({ pickFilter }: { pickFilter: boolean }): Promise<void> {
  loading.value = list.value === null
  error.value = null
  // 고른 틀이 거르기에서 빠지면(고를 것 → 떼기) 같은 자리의 다음 틀로 넘어간다.
  const position = Math.max(
    0,
    visible.value.findIndex((item) => item.id === selectedId.value),
  )
  try {
    list.value = await listRepeats(props.datasetId)
    if (pickFilter) stateFilter.value = undecided.value.length ? 'undecided' : 'all'
    const stillShown = visible.value.some((item) => item.id === selectedId.value)
    if (!stillShown) selectedId.value = visible.value[Math.min(position, visible.value.length - 1)]?.id ?? null
    checked.value = checked.value.filter((id) => items.value.some((item) => item.id === id))
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    loading.value = false
  }
}

async function loadSamples(): Promise<void> {
  samplesController?.abort()
  samples.value = null
  if (selectedId.value === null) return
  const controller = new AbortController()
  samplesController = controller
  try {
    const found = await getRepeatSamples(selectedId.value, controller.signal)
    if (!controller.signal.aborted) samples.value = found
  } catch {
    if (!controller.signal.aborted) samples.value = []
  }
}

function toggleChecked(id: number): void {
  checked.value = checked.value.includes(id) ? checked.value.filter((item) => item !== id) : [...checked.value, id]
}

function toggleAllVisible(): void {
  const ids = visible.value.map((item) => item.id)
  checked.value = isAllVisibleChecked.value
    ? checked.value.filter((id) => !ids.includes(id))
    : [...new Set([...checked.value, ...ids])]
}

async function run(key: string, work: () => Promise<unknown>): Promise<void> {
  busy.value = key
  error.value = null
  try {
    await work()
    await load({ pickFilter: false })
    notifyDataChanged()
    emit('changed')
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    busy.value = null
  }
}

function decideOne(item: RepeatRead, decision: RepeatDecision | null): Promise<void> {
  return run(`one-${decision}`, () => decideRepeat(item.id, decision))
}

function decideChecked(decision: RepeatDecision): Promise<void> {
  const ids = [...checked.value]
  return run(`checked-${decision}`, async () => {
    await decideRepeats(props.datasetId, { repeat_ids: ids, decision })
    checked.value = []
  })
}

function followSuggestions(): Promise<void> {
  const ids = undecided.value.map((item) => item.id)
  return run('follow', () => decideRepeats(props.datasetId, { repeat_ids: ids, follow_suggestion: true }))
}

async function scan(): Promise<void> {
  scanning.value = true
  error.value = null
  try {
    await scanRepeats(props.datasetId)
  } catch (failure) {
    scanning.value = false
    error.value = errorMessage(failure)
  }
}

function filterCount(key: StateFilter): number {
  if (key === 'all') return items.value.length
  return counts.value[key] ?? 0
}

const DECISION_TAG = 'inline-flex h-5 items-center gap-1 rounded-md px-1.5 text-meta font-[650] whitespace-nowrap'
const REMOVE_TAG = `${DECISION_TAG} bg-accent text-accent-foreground`
const KEEP_TAG = `${DECISION_TAG} text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]`
const SUGGEST_TAG = `${DECISION_TAG} font-[550] text-subtle-foreground shadow-[inset_0_0_0_1px_var(--border)]`
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent
      class="flex max-h-[calc(100vh-72px)] w-[min(1040px,calc(100vw-32px))] max-w-none flex-col gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="retrieval-repeats"
    >
      <DialogHeader class="flex-row items-center gap-2.5 border-b px-[18px] py-3 pr-12 text-left">
        <Stamp class="size-4 text-muted-foreground" />
        <DialogTitle class="text-title font-semibold">반복 구간</DialogTitle>
        <DialogDescription class="truncate text-ui text-muted-foreground" data-repeat-counts>
          <template v-if="list">
            떼기 {{ fmt(counts.remove ?? 0) }} · 남김 {{ fmt(counts.keep ?? 0) }} · 고를 것 {{ fmt(counts.undecided ?? 0) }}
            · 문서 {{ fmt(list.documents) }}
          </template>
        </DialogDescription>
        <Button
          type="button"
          size="sm"
          variant="outline"
          class="ml-auto"
          :disabled="scanning"
          data-action="scan-repeats"
          @click="scan"
        >
          <LoaderCircle v-if="scanning" class="animate-spin" /><RefreshCw v-else />{{ scanning ? '살피는 중' : '다시 살피기' }}
        </Button>
      </DialogHeader>

      <div v-if="loading" class="flex flex-1 items-center justify-center p-12 text-muted-foreground">
        <LoaderCircle class="size-5 animate-spin" />
      </div>
      <div
        v-else-if="list && !list.scanned_at"
        class="flex flex-1 flex-col items-center justify-center gap-2 p-12 text-ui text-muted-foreground"
      >
        <Stamp class="size-5" />살피기 전
      </div>
      <div
        v-else-if="list && !items.length"
        class="flex flex-1 flex-col items-center justify-center gap-2 p-12 text-ui text-muted-foreground"
      >
        <Check class="size-5" />반복 구간 없음
      </div>

      <div v-else-if="list" class="grid min-h-0 flex-1 md:grid-cols-[minmax(0,1fr)_400px]">
        <!-- 왼쪽: 틀 목록 -->
        <div class="min-h-0 overflow-y-auto border-r" data-repeat-list>
          <div class="sticky top-0 z-10 flex items-center gap-2 border-b bg-card px-3.5 py-2.5">
            <button
              type="button"
              class="grid size-4 shrink-0 place-items-center rounded-[4px]"
              :class="isAllVisibleChecked ? 'bg-primary text-primary-foreground' : 'shadow-[inset_0_0_0_1.5px_var(--border-strong)]'"
              :aria-pressed="isAllVisibleChecked"
              aria-label="보이는 틀 모두 고르기"
              @click="toggleAllVisible"
            >
              <Check v-if="isAllVisibleChecked" class="size-3" :stroke-width="3" />
            </button>
            <span class="inline-flex gap-0.5 rounded-[9px] border p-[3px]">
              <button
                v-for="filter in STATE_FILTERS"
                :key="filter.key"
                type="button"
                class="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-ui font-[550]"
                :class="stateFilter === filter.key ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
                :data-repeat-filter="filter.key"
                @click="stateFilter = filter.key"
              >
                {{ filter.name }}<span class="text-meta opacity-75">{{ fmt(filterCount(filter.key)) }}</span>
              </button>
            </span>
            <NativeSelect v-model="kindFilter" class="ml-auto h-8 w-28" aria-label="종류">
              <option value="all">종류 · 전체</option>
              <option value="sentence">반복 문장</option>
              <option value="meta">메타 모양</option>
            </NativeSelect>
          </div>

          <div
            v-for="item in visible"
            :key="item.id"
            class="grid cursor-pointer grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2.5 border-b px-3.5 py-2.5"
            :class="item.id === selectedId ? 'bg-[color-mix(in_oklab,var(--primary)_7%,var(--card))] shadow-[inset_3px_0_0_var(--primary)]' : 'hover:bg-muted/50'"
            :data-repeat="item.id"
            @click="selectedId = item.id"
          >
            <button
              type="button"
              class="mt-0.5 grid size-4 place-items-center rounded-[4px]"
              :class="checked.includes(item.id) ? 'bg-primary text-primary-foreground' : 'shadow-[inset_0_0_0_1.5px_var(--border-strong)]'"
              :aria-pressed="checked.includes(item.id)"
              aria-label="고르기"
              @click.stop="toggleChecked(item.id)"
            >
              <Check v-if="checked.includes(item.id)" class="size-3" :stroke-width="3" />
            </button>
            <span class="min-w-0">
              <span class="line-clamp-2 text-ui font-semibold break-all">{{ item.text }}</span>
              <span class="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-meta text-muted-foreground">
                <span class="inline-flex items-center gap-1 font-[550]">
                  <AtSign v-if="item.kind === 'meta'" class="size-3.5" /><RepeatIcon v-else class="size-3.5" />{{ KIND_NAMES[item.kind] }}
                </span>
                <span>문서 <b class="font-semibold text-foreground">{{ fmt(item.document_count) }}</b></span>
                <span>앞 {{ fmt(item.head_count) }} · 뒤 {{ fmt(item.tail_count) }}</span>
              </span>
            </span>
            <span>
              <span v-if="item.decision === 'remove'" :class="REMOVE_TAG"><Scissors class="size-3" />떼기</span>
              <span v-else-if="item.decision === 'keep'" :class="KEEP_TAG"><Pin class="size-3" />남김</span>
              <span v-else :class="SUGGEST_TAG">제안 · {{ item.suggestion === 'remove' ? '떼기' : '남김' }}</span>
            </span>
          </div>
          <div v-if="!visible.length" class="p-6 text-center text-ui text-subtle-foreground">없음</div>
        </div>

        <!-- 오른쪽: 누른 틀 -->
        <div v-if="selected" class="grid min-h-0 content-start gap-3.5 overflow-y-auto px-4 pt-3.5 pb-5" data-repeat-detail>
          <div>
            <div class="mb-1.5 text-ui font-semibold">틀</div>
            <div class="rounded-[9px] border border-border-strong px-3 py-2 text-body font-semibold break-all">{{ selected.text }}</div>
          </div>
          <dl class="grid grid-cols-[48px_minmax(0,1fr)] items-center gap-x-2.5 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">문서</dt>
            <dd>
              <b class="font-semibold">{{ fmt(selected.document_count) }}</b>
              · 앞 {{ fmt(selected.head_count) }} · 뒤 {{ fmt(selected.tail_count) }}
            </dd>
            <dt class="text-muted-foreground">제안</dt>
            <dd class="flex min-w-0 items-center gap-1.5">
              <span :class="selected.suggestion === 'remove' ? REMOVE_TAG : KEEP_TAG">
                <Scissors v-if="selected.suggestion === 'remove'" class="size-3" /><Pin v-else class="size-3" />
                {{ selected.suggestion === 'remove' ? '떼기' : '남김' }}
              </span>
              <span class="truncate text-muted-foreground" :title="selected.reason ?? ''">{{ selected.reason }}</span>
            </dd>
            <dt class="text-muted-foreground">결정</dt>
            <dd class="flex flex-wrap items-center gap-1.5">
              <Button
                type="button"
                size="xs"
                :variant="selected.decision === 'remove' ? 'default' : 'outline'"
                :disabled="busy !== null"
                data-action="repeat-remove"
                @click="decideOne(selected, 'remove')"
              >
                <Scissors />떼기
              </Button>
              <Button
                type="button"
                size="xs"
                :variant="selected.decision === 'keep' ? 'default' : 'outline'"
                :disabled="busy !== null"
                data-action="repeat-keep"
                @click="decideOne(selected, 'keep')"
              >
                <Pin />남김
              </Button>
              <Button
                v-if="selected.decision"
                type="button"
                size="xs"
                variant="quiet"
                :disabled="busy !== null"
                data-action="repeat-reset"
                @click="decideOne(selected, null)"
              >
                <RotateCcw />고르기 전
              </Button>
            </dd>
          </dl>
          <div>
            <div class="mb-1.5 flex items-center text-ui font-semibold">
              보기 문서<span class="ml-auto text-meta font-normal text-muted-foreground">
                {{ samples ? fmt(samples.length) : '…' }} / {{ fmt(selected.document_count) }}
              </span>
            </div>
            <div v-if="samples === null" class="flex justify-center py-4 text-muted-foreground"><LoaderCircle class="size-4 animate-spin" /></div>
            <div v-else class="grid gap-2">
              <div
                v-for="sample in samples"
                :key="sample.document_id"
                class="rounded-[9px] border px-2.5 py-2 text-ui leading-[1.65] break-all whitespace-pre-wrap"
                data-repeat-sample
              >
                <div class="mb-0.5 truncate text-meta font-semibold">{{ sample.title || `#${sample.document_id}` }}</div>{{ sample.before }}<mark class="rounded-[3px] bg-accent px-0.5 text-accent-foreground">{{ sample.span }}</mark>{{ sample.after }}
              </div>
            </div>
          </div>
          <div v-if="samples && samples.length">
            <div class="mb-1.5 text-ui font-semibold">학습 글 · 떼면</div>
            <div class="rounded-[9px] border px-2.5 py-2 text-ui leading-[1.65] break-all whitespace-pre-wrap">
              <div class="mb-0.5 truncate text-meta font-semibold">{{ samples[0].title || `#${samples[0].document_id}` }}</div>{{ samples[0].before }}<s class="text-subtle-foreground">{{ samples[0].span }}</s>{{ samples[0].after }}
            </div>
          </div>
        </div>
      </div>

      <div v-if="error" class="flex items-start gap-2 border-t px-[18px] py-2.5 text-ui" role="alert">
        <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger" />
        <span class="font-semibold text-danger-ink">실패</span>
        <span class="text-muted-foreground">{{ error }}</span>
      </div>

      <div v-if="list && items.length" class="flex flex-wrap items-center gap-2 border-t px-[18px] py-2.5">
        <span class="text-meta text-muted-foreground">고름 {{ fmt(checked.length) }} · 본문 그대로 · 학습 글만</span>
        <Button
          type="button"
          variant="outline"
          class="ml-auto"
          :disabled="!checked.length || busy !== null"
          data-action="repeats-remove"
          @click="decideChecked('remove')"
        >
          <LoaderCircle v-if="busy === 'checked-remove'" class="animate-spin" /><Scissors v-else />고른 것 떼기
        </Button>
        <Button
          type="button"
          variant="outline"
          :disabled="!checked.length || busy !== null"
          data-action="repeats-keep"
          @click="decideChecked('keep')"
        >
          <LoaderCircle v-if="busy === 'checked-keep'" class="animate-spin" /><Pin v-else />고른 것 남김
        </Button>
        <Button
          type="button"
          :disabled="!undecided.length || busy !== null"
          data-action="repeats-follow"
          @click="followSuggestions"
        >
          <LoaderCircle v-if="busy === 'follow'" class="animate-spin" /><CheckCheck v-else />제안대로 모두
          <span class="rounded-full bg-primary-foreground/20 px-1.5 text-meta">{{ fmt(undecided.length) }}</span>
        </Button>
      </div>
    </DialogContent>
  </Dialog>
</template>
