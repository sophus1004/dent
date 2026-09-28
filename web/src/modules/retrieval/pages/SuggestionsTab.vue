<!--
  제안 탭 (#/retrieval/<id>/suggestions): 뜻 분석이 사람에게 묻는 판정 바꾸기.
    머리    [거짓 오답 | 빠진 정답 | 정답 의심] (수 = 대기) · 상태 [대기 | 판단함] · [Jev 확인 모두 수락 n]
    표      질의 · 문서 · 지금 → 제안 · 순위 · 유사도 · Jev(확인 표시) · [수락] [유지]
  한 줄 = (질의, 문서) 판정 하나. 수락 전에는 데이터가 바뀌지 않는다. [유지]한 판단은 다음 뜻 분석에서도 다시 묻지 않는다.
  Jev 확인 = 예 ≥ 0.8(거짓 오답 · 빠진 정답) 또는 아니오 ≥ 0.8(정답 의심). 주소 ?kind= 로 종류를 고른다(진단의 → 버튼).
-->
<script setup lang="ts">
import { ArrowRight, Check, CheckCheck, ChevronLeft, ChevronRight, CircleMinus, LoaderCircle, ShieldCheck, Sparkles, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Skeleton } from '@/system/ui/skeleton'

import { acceptConfirmedSuggestions, acceptSuggestion, keepSuggestion, listSuggestions } from '@/modules/retrieval/api'
import { MARKS, NO_FILTERS, writeDataQuery } from '@/modules/retrieval/data/filters'
import { useDatasetRefresh } from '@/modules/retrieval/datasetRefresh'
import GradeChip from '@/modules/retrieval/GradeChip.vue'
import { jobFinished } from '@/modules/retrieval/jobEvents'
import type { DatasetRead, SuggestionKind, SuggestionPageRead, SuggestionRead } from '@/modules/retrieval/types'

defineProps<{
  dataset: DatasetRead
}>()

const KINDS: { key: SuggestionKind; mark: string; after: number | null }[] = [
  { key: 'false_negative', mark: 'false_negative', after: 1 },
  { key: 'missing_positive', mark: 'missing', after: 1 },
  { key: 'suspect_positive', mark: 'suspect', after: null },
]

const PAGE_SIZE = 50
const TH_CLASS =
  'sticky top-[52px] z-[5] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'
const TD_CLASS = 'h-12 border-b px-2.5 py-1.5 align-middle'

const route = useRoute()
const router = useRouter()
const refreshDataset = useDatasetRefresh()

const datasetId = computed(() => Number(route.params.datasetId))
const kind = computed<SuggestionKind>(() => {
  const asked = route.query.kind
  return KINDS.some((item) => item.key === asked) ? (asked as SuggestionKind) : 'false_negative'
})
const decided = computed(() => route.query.state === 'decided')
const page = computed(() => {
  const value = Number(route.query.page ?? 1)
  return Number.isInteger(value) && value > 0 ? value : 1
})
const current = computed(() => KINDS.find((item) => item.key === kind.value)!)

const result = ref<SuggestionPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const busy = ref<number | 'all' | null>(null)
const actionError = ref<string | null>(null)
const confirmOpen = ref(false)
let controller: AbortController | null = null

const rows = computed(() => result.value?.items ?? null)
const total = computed(() => result.value?.total ?? 0)
const hasNextPage = computed(() => page.value * PAGE_SIZE < total.value)
const noAnalysis = computed(() => loadError.value !== null && loadError.value.includes('뜻 분석'))

watch([datasetId, kind, decided, page], load, { immediate: true })
watch(jobFinished, load)
onBeforeUnmount(() => controller?.abort())

async function load(): Promise<void> {
  controller?.abort()
  const active = new AbortController()
  controller = active
  loading.value = true
  loadError.value = null
  try {
    result.value = await listSuggestions(
      datasetId.value,
      { kind: kind.value, decided: decided.value, limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE },
      active.signal,
    )
  } catch (error) {
    if (isAbortError(error)) return
    result.value = null
    loadError.value = errorMessage(error)
  } finally {
    if (!active.signal.aborted) loading.value = false
  }
}

function setQuery(patch: Record<string, string | undefined>): void {
  const query = { kind: kind.value as string, state: decided.value ? 'decided' : undefined, ...patch }
  void router.push({ query: Object.fromEntries(Object.entries(query).filter(([, value]) => value !== undefined)) })
}

async function decide(item: SuggestionRead, accept: boolean): Promise<void> {
  busy.value = item.id
  actionError.value = null
  try {
    await (accept ? acceptSuggestion(item.id) : keepSuggestion(item.id))
    void refreshDataset()
    await load()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busy.value = null
  }
}

async function acceptAll(): Promise<void> {
  busy.value = 'all'
  actionError.value = null
  try {
    await acceptConfirmedSuggestions(datasetId.value)
    confirmOpen.value = false
    void refreshDataset()
    await load()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busy.value = null
  }
}

function openQuery(queryId: number): void {
  void router.push({
    name: 'retrieval-data',
    params: { datasetId: route.params.datasetId },
    query: writeDataQuery({ ...NO_FILTERS, view: 'queries', page: 1, open: { kind: 'query', id: queryId } }),
  })
}

function decisionText(item: SuggestionRead): string {
  return item.decision === 'accepted' ? '수락' : item.decision === 'kept' ? '유지' : ''
}
</script>

<template>
  <div class="max-w-[1320px] space-y-3 px-6 py-5 xl:px-8">
    <div class="flex flex-wrap items-center gap-2">
      <div class="inline-flex shrink-0 rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)" role="group" aria-label="제안 종류">
        <button
          v-for="item in KINDS"
          :key="item.key"
          type="button"
          class="inline-flex h-[28px] items-center gap-1.5 rounded-[5px] px-2.5 text-ui font-[550] transition-colors [&>svg]:size-3.5"
          :class="kind === item.key ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
          :aria-pressed="kind === item.key"
          :data-kind="item.key"
          @click="setQuery({ kind: item.key, page: undefined })"
        >
          <component :is="MARKS[item.mark].icon" />{{ MARKS[item.mark].name }}
          <span class="count-pill">{{ fmt(result?.pending_counts[item.key] ?? 0) }}</span>
        </button>
      </div>
      <div class="inline-flex shrink-0 rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)" role="group" aria-label="상태">
        <button
          v-for="state in [{ key: false, name: '대기' }, { key: true, name: '판단함' }]"
          :key="state.name"
          type="button"
          class="inline-flex h-[28px] items-center rounded-[5px] px-2.5 text-ui font-[550] transition-colors"
          :class="decided === state.key ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
          :aria-pressed="decided === state.key"
          @click="setQuery({ state: state.key ? 'decided' : undefined, page: undefined })"
        >
          {{ state.name }}
        </button>
      </div>
      <Button
        v-if="result?.confirmed_pending"
        type="button"
        class="ml-auto"
        :disabled="busy !== null"
        data-action="accept-confirmed"
        @click="confirmOpen = true"
      >
        <CheckCheck />Jev 확인 모두 수락<span class="count-pill bg-primary-foreground/20 text-primary-foreground">{{ fmt(result.confirmed_pending) }}</span>
      </Button>
    </div>

    <section class="card overflow-clip" aria-label="제안">
      <div class="flex h-11 items-center gap-2 border-b px-4">
        <Skeleton v-if="!result && !loadError" class="h-5 w-24" />
        <template v-else-if="result">
          <b class="text-title font-semibold">{{ fmt(total) }}쌍</b>
          <span class="text-ui text-muted-foreground">{{ decided ? '판단함' : '대기' }}</span>
        </template>
        <div v-if="total > PAGE_SIZE" class="ml-auto flex items-center gap-0.5">
          <Button variant="quiet" size="icon-sm" :disabled="page <= 1" aria-label="이전 쪽" @click="setQuery({ page: String(page - 1) })"><ChevronLeft /></Button>
          <Button variant="quiet" size="icon-sm" :disabled="!hasNextPage" aria-label="다음 쪽" @click="setQuery({ page: String(page + 1) })"><ChevronRight /></Button>
        </div>
      </div>

      <div v-if="actionError" class="flex items-center gap-2 border-b px-4 py-2 text-ui" role="alert">
        <TriangleAlert class="size-4 shrink-0 text-danger" />
        <span class="font-semibold text-danger-ink">실패</span>
        <span class="text-muted-foreground">{{ actionError }}</span>
      </div>

      <div v-if="noAnalysis" class="py-14 text-center" data-empty="no-analysis">
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted"><Sparkles class="size-5 text-muted-foreground" /></span>
        <div class="mt-3 text-body font-semibold">뜻 분석 뒤</div>
        <Button variant="outline" size="sm" class="mt-4" as-child>
          <RouterLink :to="{ name: 'retrieval-diagnosis', params: { datasetId } }">진단<ArrowRight /></RouterLink>
        </Button>
      </div>
      <div v-else-if="loadError" class="flex h-28 items-center justify-center gap-3 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="font-semibold text-danger-ink">오류</span>
        <span class="text-muted-foreground">{{ loadError }}</span>
        <Button variant="outline" size="sm" @click="load">다시 불러오기</Button>
      </div>
      <div v-else-if="rows && rows.length === 0" class="py-14 text-center">
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted"><Check class="size-5 text-muted-foreground" /></span>
        <div class="mt-3 text-body font-semibold">{{ decided ? '판단한 제안 없음' : '대기 없음' }}</div>
      </div>

      <table v-else class="w-full table-fixed border-separate border-spacing-0 text-ui">
        <colgroup>
          <col class="w-[26%]" />
          <col />
          <col class="w-[150px]" />
          <col class="w-[56px]" />
          <col class="w-[72px]" />
          <col class="w-[96px]" />
          <col class="w-[150px]" />
        </colgroup>
        <thead>
          <tr>
            <th :class="[TH_CLASS, 'pl-4']">질의</th>
            <th :class="TH_CLASS">문서</th>
            <th :class="TH_CLASS">지금 → 제안</th>
            <th :class="[TH_CLASS, 'text-right']">순위</th>
            <th :class="[TH_CLASS, 'text-right']">유사도</th>
            <th :class="TH_CLASS">Jev</th>
            <th :class="[TH_CLASS, 'pr-4']"><span class="sr-only">동작</span></th>
          </tr>
        </thead>
        <tbody v-if="!rows" aria-busy="true">
          <tr v-for="index in 8" :key="index">
            <td v-for="cell in 7" :key="cell" :class="TD_CLASS"><Skeleton v-if="cell < 3" class="h-3.5 w-3/4" /></td>
          </tr>
        </tbody>
        <tbody v-else class="transition-opacity" :class="{ 'opacity-60': loading }">
          <tr v-for="item in rows" :key="item.id" :data-suggestion="item.id" class="hover:[&>td]:bg-muted/50">
            <td :class="[TD_CLASS, 'pl-4']">
              <button type="button" class="line-clamp-2 text-left hover:underline" @click="openQuery(item.query_id)">{{ item.query_text }}</button>
            </td>
            <td :class="TD_CLASS">
              <div class="line-clamp-2 text-muted-foreground" :title="item.document.snippet">
                <b v-if="item.document.title" class="font-semibold text-foreground">{{ item.document.title }} · </b>{{ item.document.snippet }}
              </div>
            </td>
            <td :class="TD_CLASS">
              <span class="inline-flex items-center gap-1">
                <GradeChip :grade="item.grade" /><ArrowRight class="size-3.5 text-subtle-foreground" /><GradeChip :grade="current.after" />
              </span>
            </td>
            <td :class="[TD_CLASS, 'text-right font-mono text-meta']">{{ item.rank }}</td>
            <td :class="[TD_CLASS, 'text-right font-mono text-meta']">{{ item.similarity.toFixed(3) }}</td>
            <td :class="TD_CLASS">
              <span v-if="item.jev_probability !== null" class="font-mono text-meta font-semibold">{{ item.jev_probability.toFixed(2) }}</span>
              <span v-else class="text-subtle-foreground">—</span>
              <span v-if="item.is_confirmed" class="ml-1 inline-flex items-center gap-0.5 text-[11px] font-[650] text-muted-foreground">
                <ShieldCheck class="size-3" />확인
              </span>
            </td>
            <td :class="[TD_CLASS, 'pr-4']">
              <span v-if="item.decision" class="font-semibold text-muted-foreground">{{ decisionText(item) }}</span>
              <span v-else class="flex justify-end gap-1">
                <LoaderCircle v-if="busy === item.id" class="size-4 animate-spin self-center text-muted-foreground" />
                <Button
                  type="button"
                  size="xs"
                  :variant="item.is_confirmed ? 'default' : 'outline'"
                  :disabled="busy !== null"
                  data-action="accept-suggestion"
                  @click="decide(item, true)"
                >
                  <Check />수락
                </Button>
                <Button type="button" size="xs" variant="outline" :disabled="busy !== null" data-action="keep-suggestion" @click="decide(item, false)">
                  <CircleMinus />유지
                </Button>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <Dialog v-model:open="confirmOpen">
      <DialogContent class="w-[min(420px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none">
        <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
          <CheckCheck class="size-4 text-primary" />
          <DialogTitle class="text-title font-semibold">Jev 확인 모두 수락</DialogTitle>
          <DialogDescription class="sr-only">Jev가 확인한 제안을 모두 수락한다</DialogDescription>
        </DialogHeader>
        <div class="space-y-4 px-5 py-4">
          <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">대상</dt>
            <dd><b class="font-semibold">{{ fmt(result?.confirmed_pending ?? 0) }}</b> · 세 종류 모두</dd>
            <dt class="text-muted-foreground">바뀜</dt>
            <dd>거짓 오답 · 빠진 정답 → 정답 · 정답 의심 → 떼기</dd>
            <dt class="text-muted-foreground">되돌리기</dt>
            <dd>질의 패널의 판정</dd>
          </dl>
          <div class="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" @click="confirmOpen = false">취소</Button>
            <Button type="button" :disabled="busy === 'all'" data-action="accept-confirmed-go" @click="acceptAll">
              <LoaderCircle v-if="busy === 'all'" class="animate-spin" /><CheckCheck v-else />수락
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
