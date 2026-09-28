<!--
  작업 기록 (#/jobs): 가져오기 · 의미 지도 · 정리 같은 긴 작업의 결과를 한 곳에서 본다.
    결론    기간 안의 판정(실패 n · 고칠 것 / 도는 중 / 문제 없음) · 작업 · 완료 · 실패 · 멈춤 · 도는 중
            (숫자 칸을 누르면 그 상태로 거른다)
    문제    거르기(상태 · 종류 · 데이터셋 · 기간) · 표(상태 · 종류 · 데이터셋 · 시작 · 걸린 시간 · 멈춘 곳 · 까닭 · 번호)
    고치기  줄을 누르면 오른쪽 패널(JobPanel): 단계 · 까닭 · 다시 보내기 · 쓴 연결 · [다시 하기]
  거르기 · 쪽 · 연 작업은 주소 뒤 ?에 둔다(?show=problems&kind=classification.map&dataset=2&hours=24&job=4).
  5초마다 다시 읽고, 읽을 때마다 실패를 확인한 것으로 둔다(사이드바 배지를 비운다). J/K로 이전 · 다음, Esc로 닫는다.
-->
<script setup lang="ts">
import { Check, ChevronDown, ChevronLeft, ChevronRight, CircleCheck, History, LoaderCircle, SearchX, TriangleAlert } from '@lucide/vue'
import { useEventListener } from '@vueuse/core'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import { getJob, listDatasets, listJobs } from '@/system/api'
import { clip, clockText, durationText, fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { useJobAnnouncer } from '@/system/jobAnnounce'
import { markJobsSeen } from '@/system/jobFeed'
import { findJobKind, jobKindName, jobKindOptions, progressText } from '@/system/jobKinds'
import TopBar from '@/system/layout/TopBar.vue'
import { useModules } from '@/system/module'
import JobPanel from '@/system/pages/JobPanel.vue'
import JobStatusTag from '@/system/pages/JobStatusTag.vue'
import type { DatasetRead, JobPageRead, JobRead, JobStatus } from '@/system/types'
import { Button } from '@/system/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/system/ui/dropdown-menu'
import { Skeleton } from '@/system/ui/skeleton'

// 상태 거르기
const SHOWS: { key: string; name: string; statuses: JobStatus[] }[] = [
  { key: 'all', name: '전체', statuses: [] },
  { key: 'problems', name: '실패 · 멈춤', statuses: ['failed', 'canceled'] },
  { key: 'failed', name: '실패', statuses: ['failed'] },
  { key: 'canceled', name: '멈춤', statuses: ['canceled'] },
  { key: 'active', name: '도는 중 · 대기', statuses: ['running', 'queued'] },
  { key: 'done', name: '완료', statuses: ['done'] },
]

// 기간 거르기(넣은 시각). 끝난 작업 기록은 90일 남는다.
const PERIODS = [
  { hours: 24, name: '24시간' },
  { hours: 24 * 7, name: '7일' },
  { hours: 24 * 30, name: '30일' },
  { hours: 24 * 90, name: '90일' },
]

// 한 쪽에 보일 작업 수
const PAGE_SIZE = 50

// 다시 읽는 간격(밀리초). 알림(jobFeed)과 같다.
const REFRESH_MS = 5000

// 결론 줄의 까닭 글자 길이
const REASON_LENGTH = 26

// 표 머리 칸 · 몸 칸 (문장 표와 같은 모양)
const TH_CLASS =
  'sticky top-[52px] z-[5] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'
const TD_CLASS = 'h-11 px-2.5 py-1.5 align-middle whitespace-nowrap'

// 거르기 칸 (데이터 탭의 거르기 칸과 같은 모양)
const TOKEN_CLASS =
  'inline-flex h-7 items-center gap-1.5 rounded-[7px] border px-2.5 text-ui font-[550] whitespace-nowrap transition-colors'
const TOKEN_ON_CLASS = 'border-border-strong bg-card text-foreground shadow-(--shadow-xs)'
const TOKEN_OFF_CLASS = 'border-dashed border-border-strong text-muted-foreground hover:text-foreground'

const route = useRoute()
const router = useRouter()
const modules = useModules()
const { retry } = useJobAnnouncer()

// ---------- 주소의 거르기 ----------

function numberParam(value: unknown): number | null {
  const parsed = typeof value === 'string' ? Number(value) : NaN
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

const show = computed(() => SHOWS.find((item) => item.key === route.query.show) ?? SHOWS[0])
const period = computed(() => PERIODS.find((item) => String(item.hours) === route.query.hours) ?? PERIODS[0])
const kindOptions = computed(() => jobKindOptions(modules))
const kindFilter = computed(() => kindOptions.value.find((item) => item.key === route.query.kind) ?? null)
const datasetFilter = computed(() => numberParam(route.query.dataset))
const page = computed(() => numberParam(route.query.page) ?? 1)
const openId = computed(() => numberParam(route.query.job))
const hasFilters = computed(() => show.value.key !== 'all' || kindFilter.value !== null || datasetFilter.value !== null)

/** 주소의 거르기를 바꾼다. 거르기가 바뀌면 첫 쪽으로 간다. */
function setQuery(patch: LocationQueryRaw, { keepPage = false } = {}): void {
  const next: LocationQueryRaw = { ...route.query, ...patch }
  if (!keepPage) delete next.page
  for (const [key, value] of Object.entries(next)) if (value === null || value === undefined) delete next[key]
  void router.push({ query: next })
}

function clearFilters(): void {
  setQuery({ show: null, kind: null, dataset: null })
}

// ---------- 목록 ----------

const result = ref<JobPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const datasets = ref<DatasetRead[]>([])
let controller: AbortController | null = null
let timer: ReturnType<typeof setInterval> | undefined

const rows = computed(() => result.value?.items ?? null)
const total = computed(() => result.value?.total ?? 0)
const counts = computed(() => result.value?.counts ?? null)
const allCount = computed(() => (counts.value ? Object.values(counts.value).reduce((sum, count) => sum + count, 0) : 0))
const firstNumber = computed(() => (page.value - 1) * PAGE_SIZE + 1)
const lastNumber = computed(() => firstNumber.value + (rows.value?.length ?? 0) - 1)
const hasNextPage = computed(() => page.value * PAGE_SIZE < total.value)
const datasetName = computed(() => datasets.value.find((item) => item.id === datasetFilter.value)?.name ?? null)

// 목록 요청이 바뀌는 값. 연 작업(job)만 바뀌면 다시 읽지 않는다.
const requestKey = computed(() =>
  JSON.stringify([show.value.key, period.value.hours, kindFilter.value?.key, datasetFilter.value, page.value]),
)

watch(requestKey, () => void load(), { immediate: true })

onMounted(async () => {
  timer = setInterval(() => void load({ quiet: true }), REFRESH_MS)
  try {
    datasets.value = await listDatasets()
  } catch {
    // 데이터셋 거르기만 못 쓴다.
  }
})

onBeforeUnmount(() => {
  clearInterval(timer)
  controller?.abort()
})

/** 이 쪽을 읽는다. quiet면 도는 동안 표를 흐리게 하지 않는다(5초마다 다시 읽을 때). */
async function load({ quiet = false } = {}): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  if (!quiet) loading.value = true
  try {
    result.value = await listJobs(
      {
        status: show.value.statuses,
        module: kindFilter.value?.module,
        kind: kindFilter.value?.kind,
        dataset_id: datasetFilter.value ?? undefined,
        hours: period.value.hours,
        limit: PAGE_SIZE,
        offset: (page.value - 1) * PAGE_SIZE,
      },
      current.signal,
    )
    loadError.value = null
    markJobsSeen()
  } catch (error) {
    if (isAbortError(error)) return
    if (!quiet) result.value = null
    loadError.value = errorMessage(error)
  } finally {
    if (!current.signal.aborted) loading.value = false
  }
}

// ---------- 결론 ----------

// 판정: 실패가 있으면 고칠 것, 도는 것이 있으면 도는 중, 아니면 문제 없음
const verdict = computed(() => {
  const current = counts.value
  if (!current) return null
  if (current.failed > 0) return { tone: 'bad', text: `실패 ${fmt(current.failed)} · 고칠 것` } as const
  if (current.running + current.queued > 0) return { tone: 'busy', text: `도는 중 ${fmt(current.running + current.queued)}` } as const
  if (allCount.value === 0) return { tone: 'none', text: '작업 없음' } as const
  return { tone: 'good', text: '문제 없음' } as const
})

// 이 쪽 실패의 가장 흔한 까닭과 그 수. 예: { reason: '임베딩 서버 요청이 실패했습니다(시간 초과)…', count: 2 }
const topReason = computed(() => {
  const tally = new Map<string, number>()
  for (const job of rows.value ?? []) if (job.status === 'failed' && job.error) tally.set(job.error, (tally.get(job.error) ?? 0) + 1)
  const [reason, count] = [...tally.entries()].sort((a, b) => b[1] - a[1])[0] ?? []
  return reason ? { reason: clip(reason, REASON_LENGTH), count: count ?? 0 } : null
})

// 결론 줄의 숫자 칸. 누르면 그 상태로 거른다.
const summaryCells = computed(() => {
  const current = counts.value
  if (!current) return []
  return [
    { key: 'all', name: '작업', value: allCount.value, dot: '' },
    { key: 'done', name: '완료', value: current.done, dot: 'bg-success' },
    { key: 'failed', name: '실패', value: current.failed, dot: 'bg-danger' },
    { key: 'canceled', name: '멈춤', value: current.canceled, dot: 'ring-[1.5px] ring-subtle-foreground ring-inset' },
    { key: 'active', name: '도는 중', value: current.running + current.queued, dot: 'bg-primary' },
  ]
})

// ---------- 오른쪽 패널 ----------

const openJob = ref<JobRead | null>(null)
const openIndex = computed(() => rows.value?.findIndex((job) => job.id === openId.value) ?? -1)
const canPrev = computed(() => openIndex.value > 0)
const canNext = computed(() => rows.value !== null && openIndex.value >= 0 && openIndex.value < rows.value.length - 1)
const openKind = computed(() => (openJob.value ? findJobKind(modules, openJob.value) : null))
// 패널이 열리면 표가 좁아지므로 번호 칸을 숨기고(패널 머리에 번호가 있다) 까닭 칸을 줄인다.
const isNarrow = computed(() => openJob.value !== null)
const columnCount = computed(() => (isNarrow.value ? 6 : 7))

watch([openId, result], () => void syncOpenJob(), { immediate: true })

// 이 쪽에 있으면 그것을, 없으면(주소로 바로 왔거나 알림에서 옴) 번호로 읽는다.
async function syncOpenJob(): Promise<void> {
  const id = openId.value
  if (id === null) {
    openJob.value = null
    return
  }
  const onPage = rows.value?.find((job) => job.id === id)
  if (onPage) {
    openJob.value = onPage
    return
  }
  try {
    const found = await getJob(id)
    if (openId.value === id) openJob.value = found
  } catch {
    if (openId.value === id) openJob.value = null
  }
}

// 여는 것 · 닫는 것은 뒤로 가기 기록을 남기지 않는다(J/K로 옮길 때마다 쌓이지 않게).
function open(job: JobRead): void {
  openJob.value = job
  void router.replace({ query: { ...route.query, job: String(job.id) } })
}

function close(): void {
  const next = { ...route.query }
  delete next.job
  void router.replace({ query: next })
}

function step(delta: number): void {
  const list = rows.value
  if (!list?.length) return
  const index = openIndex.value
  const nextIndex = index === -1 ? 0 : Math.min(Math.max(index + delta, 0), list.length - 1)
  open(list[nextIndex])
}

async function retryOpen(): Promise<void> {
  if (!openJob.value) return
  await retry(openJob.value)
  await load({ quiet: true })
}

// J/K: 이전 · 다음 작업, Esc: 패널 닫기. 펼친 목록 안에서는 가로채지 않는다.
useEventListener(document, 'keydown', (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null
  if (target?.closest('input, textarea, [role="menu"], [role="dialog"]')) return
  if (event.key === 'Escape' && openId.value !== null) close()
  else if (event.key === 'j') step(1)
  else if (event.key === 'k') step(-1)
})

function reasonText(job: JobRead): string {
  if (job.status === 'canceled') return '멈추기 누름'
  return job.error ?? ''
}
</script>

<template>
  <TopBar :crumbs="[{ label: '작업 기록', icon: History }]">
    <span class="text-meta text-muted-foreground">보관 90일 · 5초마다 새로 읽음</span>
  </TopBar>

  <div class="flex flex-1 items-start">
    <div class="min-w-0 flex-1 space-y-4 px-6 py-5 xl:px-8">
      <!-- 결론 -->
      <section class="grid grid-cols-[minmax(0,2.6fr)_repeat(5,minmax(0,1fr))] gap-2.5 max-xl:grid-cols-3" aria-label="요약">
        <div class="card flex items-center gap-3 px-4 py-3 max-xl:col-span-3" :data-verdict="verdict?.tone">
          <span
            class="grid size-9 shrink-0 place-items-center rounded-[9px]"
            :class="{
              'bg-danger/12 text-danger-ink': verdict?.tone === 'bad',
              'bg-accent text-accent-foreground': verdict?.tone === 'busy',
              'bg-success/12 text-success-ink': verdict?.tone === 'good',
              'bg-muted text-muted-foreground': !verdict || verdict.tone === 'none',
            }"
          >
            <TriangleAlert v-if="verdict?.tone === 'bad'" class="size-4" />
            <LoaderCircle v-else-if="verdict?.tone === 'busy'" class="size-4 animate-spin" />
            <CircleCheck v-else class="size-4" />
          </span>
          <div class="min-w-0">
            <div class="text-meta text-muted-foreground">최근 {{ period.name }}</div>
            <Skeleton v-if="!verdict" class="mt-1 h-5 w-32" />
            <b
              v-else
              class="block truncate text-title font-semibold"
              :class="{ 'text-danger-ink': verdict.tone === 'bad', 'text-success-ink': verdict.tone === 'good' }"
            >
              {{ verdict.text }}
            </b>
            <div v-if="verdict?.tone === 'bad' && topReason" class="truncate text-meta text-muted-foreground">
              원인 · {{ topReason.reason }} {{ topReason.count }}
            </div>
          </div>
        </div>
        <button
          v-for="cell in summaryCells"
          :key="cell.key"
          type="button"
          class="card px-4 py-3 text-left transition-shadow hover:shadow-(--shadow-xs)"
          :class="show.key === cell.key ? 'ring-2 ring-primary ring-offset-0' : ''"
          :data-summary="cell.key"
          @click="setQuery({ show: cell.key === 'all' ? null : cell.key })"
        >
          <span class="flex items-center gap-1.5 text-meta text-muted-foreground">
            <span v-if="cell.dot" class="size-1.5 rounded-full" :class="cell.dot" />{{ cell.name }}
          </span>
          <b class="mt-0.5 block text-[22px] leading-tight font-semibold tabular-nums" :class="cell.key === 'failed' && cell.value ? 'text-danger-ink' : ''">
            {{ fmt(cell.value) }}
          </b>
        </button>
      </section>

      <!-- 거르기 -->
      <div class="flex flex-wrap items-center gap-1.5">
        <DropdownMenu>
          <DropdownMenuTrigger as-child>
            <button type="button" :class="[TOKEN_CLASS, show.key !== 'all' ? TOKEN_ON_CLASS : TOKEN_OFF_CLASS]" data-filter="status">
              상태<b v-if="show.key !== 'all'" class="font-semibold">: {{ show.name }}</b><ChevronDown class="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" class="min-w-40">
            <DropdownMenuItem v-for="item in SHOWS" :key="item.key" @select="setQuery({ show: item.key === 'all' ? null : item.key })">
              <span class="grid size-4 place-items-center"><Check v-if="show.key === item.key" class="size-4" /></span>{{ item.name }}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger as-child>
            <button type="button" :class="[TOKEN_CLASS, kindFilter ? TOKEN_ON_CLASS : TOKEN_OFF_CLASS]" data-filter="kind">
              종류<b v-if="kindFilter" class="font-semibold">: {{ kindFilter.name }}</b><ChevronDown class="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" class="min-w-40">
            <DropdownMenuItem @select="setQuery({ kind: null })">
              <span class="grid size-4 place-items-center"><Check v-if="!kindFilter" class="size-4" /></span>전체
            </DropdownMenuItem>
            <DropdownMenuItem v-for="item in kindOptions" :key="item.key" @select="setQuery({ kind: item.key })">
              <span class="grid size-4 place-items-center"><Check v-if="kindFilter?.key === item.key" class="size-4" /></span>{{ item.name }}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger as-child>
            <button type="button" :class="[TOKEN_CLASS, datasetFilter ? TOKEN_ON_CLASS : TOKEN_OFF_CLASS]" data-filter="dataset">
              데이터셋<b v-if="datasetFilter" class="max-w-48 truncate font-semibold">: {{ datasetName ?? `#${datasetFilter}` }}</b><ChevronDown class="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" class="max-h-80 min-w-48 overflow-y-auto">
            <DropdownMenuItem @select="setQuery({ dataset: null })">
              <span class="grid size-4 place-items-center"><Check v-if="!datasetFilter" class="size-4" /></span>전체
            </DropdownMenuItem>
            <DropdownMenuItem v-for="item in datasets" :key="item.id" @select="setQuery({ dataset: String(item.id) })">
              <span class="grid size-4 place-items-center"><Check v-if="datasetFilter === item.id" class="size-4" /></span>
              <span class="truncate">{{ item.name }}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu>
          <DropdownMenuTrigger as-child>
            <button type="button" :class="[TOKEN_CLASS, TOKEN_ON_CLASS]" data-filter="period">
              {{ period.name }}<ChevronDown class="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" class="min-w-32">
            <DropdownMenuItem v-for="item in PERIODS" :key="item.hours" @select="setQuery({ hours: item.hours === PERIODS[0].hours ? null : String(item.hours) })">
              <span class="grid size-4 place-items-center"><Check v-if="period.hours === item.hours" class="size-4" /></span>{{ item.name }}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button v-if="hasFilters" variant="quiet" size="sm" @click="clearFilters">조건 지우기</Button>
        <span v-if="result" class="ml-auto text-ui text-muted-foreground tabular-nums">{{ fmt(total) }} / {{ fmt(allCount) }}건</span>
      </div>

      <!-- 표 -->
      <section class="card overflow-clip" aria-label="작업 목록">
        <table class="w-full border-collapse text-ui">
          <thead>
            <tr>
              <th :class="[TH_CLASS, 'pl-4']">상태</th>
              <th :class="TH_CLASS">종류</th>
              <th :class="TH_CLASS">데이터셋</th>
              <th :class="TH_CLASS">시작 · 걸린 시간</th>
              <th :class="TH_CLASS">멈춘 곳 · 진행</th>
              <th :class="TH_CLASS">까닭</th>
              <th v-if="!isNarrow" :class="[TH_CLASS, 'pr-4 text-right']"><span class="sr-only">번호</span></th>
            </tr>
          </thead>
          <tbody v-if="loadError && !rows">
            <tr>
              <td :colspan="columnCount" class="py-14 text-center">
                <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted"><TriangleAlert class="size-5 text-danger" /></span>
                <div class="mt-3 text-body font-semibold">불러오기 실패</div>
                <div class="mt-1 text-ui text-muted-foreground">{{ loadError }}</div>
                <Button variant="outline" size="sm" class="mt-4" @click="load()">다시 불러오기</Button>
              </td>
            </tr>
          </tbody>
          <tbody v-else-if="!rows" aria-busy="true">
            <tr v-for="index in 6" :key="index" class="[&>td]:border-b">
              <td :class="[TD_CLASS, 'pl-4']"><Skeleton class="h-5 w-10" /></td>
              <td :class="TD_CLASS"><Skeleton class="h-3.5 w-16" /></td>
              <td :class="TD_CLASS"><Skeleton class="h-3.5 w-24" /></td>
              <td :class="TD_CLASS"><Skeleton class="h-3.5 w-20" /></td>
              <td :class="TD_CLASS"><Skeleton class="h-3.5 w-24" /></td>
              <td :class="TD_CLASS"><Skeleton class="h-3.5 w-40" /></td>
              <td v-if="!isNarrow" :class="[TD_CLASS, 'pr-4']" />
            </tr>
          </tbody>
          <tbody v-else-if="rows.length === 0">
            <tr>
              <td :colspan="columnCount" class="py-14 text-center" data-empty="jobs">
                <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted"><SearchX class="size-5 text-muted-foreground" /></span>
                <div class="mt-3 text-body font-semibold">작업 없음</div>
                <Button v-if="hasFilters" variant="outline" size="sm" class="mt-4" @click="clearFilters">조건 지우기</Button>
              </td>
            </tr>
          </tbody>
          <tbody v-else class="transition-opacity" :class="{ 'opacity-60': loading }">
            <tr
              v-for="job in rows"
              :key="job.id"
              :data-job-id="job.id"
              class="cursor-pointer [&>td]:border-b [&>td]:transition-colors"
              :class="job.id === openId ? '[&>td]:bg-accent' : 'hover:[&>td]:bg-muted/60'"
              :aria-current="job.id === openId ? 'true' : undefined"
              @click="open(job)"
            >
              <td :class="[TD_CLASS, 'pl-4']"><JobStatusTag :status="job.status" /></td>
              <td :class="[TD_CLASS, 'font-semibold']">{{ jobKindName(modules, job) }}</td>
              <td :class="[TD_CLASS, 'max-w-48 truncate']">
                <template v-if="job.dataset_name">
                  {{ job.dataset_name }}
                  <span v-if="job.dataset_id === null" class="ml-1 rounded-md px-1.5 text-meta font-semibold text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]">지움</span>
                </template>
                <span v-else class="text-subtle-foreground">—</span>
              </td>
              <td :class="[TD_CLASS, 'tabular-nums']">
                {{ clockText(job.started_at ?? job.created_at) }}
                <div class="text-meta text-muted-foreground">{{ durationText(job.started_at, job.finished_at) || (job.status === 'running' ? '도는 중' : '—') }}</div>
              </td>
              <td :class="[TD_CLASS, 'tabular-nums text-muted-foreground']">{{ progressText(findJobKind(modules, job), job) || '—' }}</td>
              <td
                :class="[TD_CLASS, 'truncate', isNarrow ? 'max-w-52' : 'max-w-72', job.status === 'failed' ? '' : 'text-subtle-foreground']"
                :title="reasonText(job) || undefined"
              >
                {{ reasonText(job) || '—' }}
              </td>
              <td v-if="!isNarrow" :class="[TD_CLASS, 'pr-4 text-right font-mono text-meta text-subtle-foreground']">#{{ job.id }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="result && total > 0" class="flex h-11 items-center gap-3 border-t bg-muted/30 px-4 text-ui text-muted-foreground">
          <span class="tabular-nums">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }} / 전체 {{ fmt(total) }}</span>
          <div class="ml-auto flex items-center gap-1">
            <Button variant="quiet" size="xs" :disabled="page <= 1" @click="setQuery({ page: page > 2 ? String(page - 1) : null }, { keepPage: true })">
              <ChevronLeft />이전
            </Button>
            <span class="text-subtle-foreground">·</span>
            <Button variant="quiet" size="xs" :disabled="!hasNextPage" @click="setQuery({ page: String(page + 1) }, { keepPage: true })">
              다음<ChevronRight />
            </Button>
          </div>
        </div>
      </section>
    </div>

    <JobPanel
      v-if="openJob"
      :key="openJob.id"
      :job="openJob"
      :kind="openKind"
      :kind-name="jobKindName(modules, openJob)"
      :can-prev="canPrev"
      :can-next="canNext"
      @close="close"
      @step="step"
      @retry="retryOpen"
    />
  </div>
</template>
