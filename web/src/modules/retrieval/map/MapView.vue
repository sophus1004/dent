<!--
  데이터 탭의 지도 보기 (?view=map): 질의와 문서를 한 지도에 WebGL로 그린다(system MapCanvas).
    머리    점 n(질의 · 문서) · 강조 n · 칠하기 [종류 | 문제 | 무리] · [전체 보기]
    몸      지도 · 왼쪽 위 범례(색 · 이름 · 수, 누르면 그 조건으로 거른다) · 점에 올리면 말풍선(글 · 종류 · #번호)
            무리로 칠하면 질의가 없는 무리(문서만 있음)를 범례에 '질의 없음'으로 표시한다(질의 만들기 대상).
    바닥    만들기 칸(질의 · 문서 · 모델 · 만든 시각, 분석 이후 변경 · [다시 만들기]) · 조작 안내
  데이터 탭의 거르기(문제 · 검색어)에 맞는 점은 또렷하게, 나머지는 흐리게. 검색어만 서버에 맞는 번호를 묻는다.
  점을 누르면 부모가 오른쪽 패널(질의 · 문서)을 연다. 뜻 분석이 없으면 [뜻 분석 만들기] 칸을 보인다.
-->
<script setup lang="ts">
import { ChartScatter, LoaderCircle, Plug, Scan, TriangleAlert } from '@lucide/vue'
import { useElementSize, useMutationObserver } from '@vueuse/core'
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { openConnectionSettings } from '@/system/connections/connections'
import { fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import MapCanvas from '@/system/map/MapCanvas.vue'
import { CATEGORY_COLORS, currentMapTheme, NEUTRAL_COLOR, OTHER_COLOR, type MapTheme } from '@/system/map/palette'
import { isConnected } from '@/system/readiness'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { getMapMatches, getMapPoints, getMapText } from '@/modules/retrieval/api'
import { MARKS, type DataFilters, type OpenItem } from '@/modules/retrieval/data/filters'
import { useAnalysis } from '@/modules/retrieval/map/analysis'
import AnalysisAction from '@/modules/retrieval/map/AnalysisAction.vue'
import type { MapPointsRead } from '@/modules/retrieval/types'

const props = defineProps<{
  datasetId: number
  filters: DataFilters
  open: OpenItem | null
}>()

const emit = defineEmits<{
  open: [item: OpenItem]
  // 범례를 눌러 거르기를 바꾼다
  change: [patch: Partial<DataFilters>]
}>()

type ColorMode = 'kind' | 'problem' | 'cluster'
const COLOR_MODES: { key: ColorMode; name: string }[] = [
  { key: 'kind', name: '종류' },
  { key: 'problem', name: '문제' },
  { key: 'cluster', name: '무리' },
]

const MAX_COLORS = CATEGORY_COLORS.light.length

const LARGE_MAP_POINTS = 50_000
const MEDIUM_MAP_POINTS = 10_000
const POINT_SIZE = { large: 2, medium: 3, small: 4 }
const RARE_SHARE = 0.1
const HOVER_DELAY_MS = 120
const TOOLTIP_WIDTH = 320
const TOOLTIP_HEIGHT = 120
const TOOLTIP_OFFSET = 14

const analysis = useAnalysis(() => props.datasetId)
const { state, progress, sending, actionError, loadError: stateError } = analysis

const connected = computed(() => isConnected('embedding'))
const colorMode = ref<ColorMode>('kind')
const canvas = ref<InstanceType<typeof MapCanvas> | null>(null)
const body = ref<HTMLDivElement | null>(null)
const { width: bodyWidth, height: bodyHeight } = useElementSize(body)

// ---------- 점 읽기 ----------

const points = shallowRef<MapPointsRead | null>(null)
const pointsError = ref<string | null>(null)
let pointsController: AbortController | null = null

const analysisId = computed(() => state.value?.done?.id ?? null)
watch(analysisId, loadPoints, { immediate: true })

async function loadPoints(): Promise<void> {
  pointsController?.abort()
  pointsError.value = null
  if (analysisId.value === null) {
    points.value = null
    return
  }
  const controller = new AbortController()
  pointsController = controller
  try {
    points.value = await getMapPoints(props.datasetId, controller.signal)
  } catch (error) {
    if (!isAbortError(error)) pointsError.value = errorMessage(error)
  }
}

const count = computed(() => points.value?.ids.length ?? 0)
const queryCount = computed(() => points.value?.kinds.filter((kind) => kind === 'query').length ?? 0)
const keyOf = (kind: string, id: number) => `${kind[0]}${id}`
const indexByKey = computed(() => {
  const index = new Map<string, number>()
  points.value?.ids.forEach((id, position) => index.set(keyOf(points.value!.kinds[position], id), position))
  return index
})
const selectedIndex = computed(() => (props.open ? (indexByKey.value.get(keyOf(props.open.kind, props.open.id)) ?? null) : null))
const pointSize = computed(() => {
  if (count.value > LARGE_MAP_POINTS) return POINT_SIZE.large
  return count.value > MEDIUM_MAP_POINTS ? POINT_SIZE.medium : POINT_SIZE.small
})
const flagBit = (name: string) => {
  const position = points.value?.flag_names.indexOf(name) ?? -1
  return position < 0 ? 0 : 1 << position
}

// ---------- 검색어 ----------

const matches = shallowRef<Set<string> | null>(null)
const matchesLoading = ref(false)
let matchesController: AbortController | null = null

watch(() => [props.datasetId, props.filters.q] as const, loadMatches, { immediate: true })

async function loadMatches(): Promise<void> {
  matchesController?.abort()
  matches.value = null
  const q = props.filters.q
  if (!q) return
  const controller = new AbortController()
  matchesController = controller
  matchesLoading.value = true
  try {
    const found = await getMapMatches(props.datasetId, q, controller.signal)
    matches.value = new Set([
      ...found.query_ids.map((id) => keyOf('query', id)),
      ...found.document_ids.map((id) => keyOf('document', id)),
    ])
  } catch (error) {
    if (!isAbortError(error)) matches.value = new Set()
  } finally {
    if (!controller.signal.aborted) matchesLoading.value = false
  }
}

// ---------- 색 ----------

const theme = ref<MapTheme>(currentMapTheme())
useMutationObserver(document.documentElement, () => (theme.value = currentMapTheme()), {
  attributes: true,
  attributeFilter: ['data-theme'],
})
const chrome = computed(() => {
  void theme.value
  const style = getComputedStyle(document.documentElement)
  return {
    background: style.getPropertyValue('--card').trim(),
    active: style.getPropertyValue('--primary').trim(),
    hover: style.getPropertyValue('--foreground').trim(),
  }
})

// 문제 칠하기: 점마다 가장 앞의(flag_names 순서) 문제. 없으면 flag_names.length
const problemNames = computed(() => (points.value?.flag_names ?? []).slice(0, MAX_COLORS - 1))
function problemSlot(flags: number): number {
  const names = problemNames.value
  const slot = names.findIndex((_name, position) => flags & (1 << position))
  return slot < 0 ? names.length : slot
}

const categories = computed<number[]>(() => {
  const data = points.value
  if (!data) return []
  if (colorMode.value === 'kind') return data.kinds.map((kind) => (kind === 'query' ? 0 : 1))
  if (colorMode.value === 'problem') return data.flags.map(problemSlot)
  return data.clusters.map((cluster) => (cluster < 0 ? MAX_COLORS : cluster % MAX_COLORS))
})

const colors = computed<string[]>(() => {
  const palette = CATEGORY_COLORS[theme.value]
  if (colorMode.value === 'kind') return [palette[0], palette[1]]
  if (colorMode.value === 'problem') return [...palette.slice(0, problemNames.value.length), NEUTRAL_COLOR[theme.value]]
  return [...palette, OTHER_COLOR[theme.value]]
})

// ---------- 강조 ----------

const hasFilters = computed(() => {
  const filters = props.filters
  return filters.problem !== null || filters.q !== ''
})

const highlight = computed<number[]>(() => {
  const data = points.value
  if (!data) return []
  if (!hasFilters.value) return data.ids.map(() => 1)
  const { problem } = props.filters
  const bit = problem === null ? 0 : flagBit(problem)
  const found = matches.value
  return data.ids.map((id, index) => {
    const isMatch =
      (problem === null || (bit !== 0 && (data.flags[index] & bit) !== 0)) &&
      (found === null || found.has(keyOf(data.kinds[index], id)))
    return isMatch ? 1 : 0
  })
})
const highlightedCount = computed(() => highlight.value.reduce((sum, value) => sum + value, 0))

const levels = computed<number[]>(() => {
  const data = points.value
  if (!data) return []
  const isProblemMode = colorMode.value === 'problem'
  const isRareMatch = hasFilters.value && highlightedCount.value <= count.value * RARE_SHARE
  return highlight.value.map((isMatch, index) => {
    if (!isMatch) return 0
    const hasProblem = isProblemMode && problemSlot(data.flags[index]) < problemNames.value.length
    return isRareMatch || hasProblem ? 2 : 1
  })
})

// ---------- 범례 ----------

interface LegendEntry {
  key: string
  name: string
  color: string
  count: number
  active: boolean
  patch: Partial<DataFilters> | null
  // 질의 없는 무리 표시
  note?: string
}

const legend = computed<LegendEntry[]>(() => {
  const data = points.value
  if (!data) return []
  const palette = colors.value
  const counts = new Map<number, number>()
  categories.value.forEach((category) => counts.set(category, (counts.get(category) ?? 0) + 1))
  const filters = props.filters
  if (colorMode.value === 'kind') {
    return [
      { key: 'query', name: '질의', color: palette[0], count: counts.get(0) ?? 0, active: false, patch: null },
      { key: 'document', name: '문서', color: palette[1], count: counts.get(1) ?? 0, active: false, patch: null },
    ]
  }
  if (colorMode.value === 'problem') {
    const entries: LegendEntry[] = problemNames.value.map((name, index) => ({
      key: name,
      name: MARKS[name]?.name ?? name,
      color: palette[index],
      count: data.flags.filter((flags) => flags & (1 << index)).length,
      active: filters.problem === name,
      patch: { problem: filters.problem === name ? null : name },
    }))
    const clean = counts.get(problemNames.value.length) ?? 0
    entries.push({ key: 'none', name: '문제 없음', color: palette[problemNames.value.length], count: clean, active: false, patch: null })
    return entries
  }
  const empty = new Set(data.empty_clusters)
  const clusterIds = [...new Set(data.clusters.filter((cluster) => cluster >= 0))].sort((a, b) => a - b)
  return clusterIds.map((cluster) => ({
    key: String(cluster),
    name: `무리 ${cluster + 1}`,
    color: palette[cluster % MAX_COLORS],
    count: data.clusters.filter((value) => value === cluster).length,
    active: false,
    patch: null,
    note: empty.has(cluster) ? '질의 없음' : undefined,
  }))
})

// ---------- 말풍선 ----------

const hovered = ref<{ index: number; x: number; y: number } | null>(null)
const hoveredText = ref<{ text: string; title: string } | null>(null)
const textCache = new Map<string, { text: string; title: string }>()
let hoverTimer: ReturnType<typeof setTimeout> | undefined
let hoverController: AbortController | null = null

const hoveredPoint = computed(() => {
  const data = points.value
  const current = hovered.value
  if (!data || !current) return null
  return {
    kind: data.kinds[current.index],
    id: data.ids[current.index],
  }
})

const tooltipStyle = computed(() => {
  const current = hovered.value
  if (!current) return {}
  const flipX = current.x + TOOLTIP_OFFSET + TOOLTIP_WIDTH > bodyWidth.value
  const flipY = current.y + TOOLTIP_OFFSET + TOOLTIP_HEIGHT > bodyHeight.value
  return {
    width: `${TOOLTIP_WIDTH}px`,
    left: `${flipX ? current.x - TOOLTIP_OFFSET - TOOLTIP_WIDTH : current.x + TOOLTIP_OFFSET}px`,
    top: `${flipY ? current.y - TOOLTIP_OFFSET - TOOLTIP_HEIGHT : current.y + TOOLTIP_OFFSET}px`,
  }
})

function onHover(index: number | null, position: { x: number; y: number } | null): void {
  clearTimeout(hoverTimer)
  hoverController?.abort()
  if (index === null || position === null || !points.value) {
    hovered.value = null
    return
  }
  hovered.value = { index, ...position }
  const kind = points.value.kinds[index]
  const id = points.value.ids[index]
  const key = keyOf(kind, id)
  hoveredText.value = textCache.get(key) ?? null
  if (hoveredText.value !== null) return
  hoverTimer = setTimeout(() => void loadText(kind, id), HOVER_DELAY_MS)
}

async function loadText(kind: 'query' | 'document', id: number): Promise<void> {
  const controller = new AbortController()
  hoverController = controller
  try {
    const found = await getMapText(props.datasetId, kind, id, controller.signal)
    textCache.set(keyOf(kind, id), found)
    if (hoveredPoint.value?.id === id && hoveredPoint.value.kind === kind) hoveredText.value = found
  } catch {
    // 말풍선의 글은 없어도 된다.
  }
}

function onOpen(index: number): void {
  const data = points.value
  if (!data) return
  emit('open', { kind: data.kinds[index], id: data.ids[index] })
}

onBeforeUnmount(() => {
  clearTimeout(hoverTimer)
  hoverController?.abort()
  pointsController?.abort()
  matchesController?.abort()
})
</script>

<template>
  <div class="card mt-3 overflow-clip" data-map-view>
    <div class="flex h-11 items-center gap-3 border-b px-4">
      <template v-if="points">
        <span class="text-ui text-muted-foreground" data-map-count>
          질의 <b class="text-title font-semibold text-foreground">{{ fmt(queryCount) }}</b>
          · 문서 <b class="text-title font-semibold text-foreground">{{ fmt(count - queryCount) }}</b>
        </span>
        <span v-if="hasFilters" class="text-ui text-muted-foreground">
          · 강조 <b class="font-semibold text-foreground">{{ fmt(highlightedCount) }}</b>
        </span>
        <LoaderCircle v-if="matchesLoading" class="size-3.5 animate-spin text-muted-foreground" />
      </template>
      <span v-else class="text-ui font-semibold">의미 지도</span>
      <div v-if="points" class="ml-auto flex items-center gap-2">
        <span class="text-ui text-muted-foreground">칠하기</span>
        <div class="inline-flex rounded-md border border-border-strong bg-card p-0.5 shadow-(--shadow-xs)" role="group" aria-label="칠하기">
          <button
            v-for="mode in COLOR_MODES"
            :key="mode.key"
            type="button"
            class="h-6 rounded-[5px] px-2.5 text-ui font-[550] transition-colors"
            :class="colorMode === mode.key ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:text-foreground'"
            :aria-pressed="colorMode === mode.key"
            :data-color-mode="mode.key"
            @click="colorMode = mode.key"
          >
            {{ mode.name }}
          </button>
        </div>
        <Button variant="quiet" size="icon-sm" aria-label="전체 보기" title="전체 보기" @click="canvas?.resetView()">
          <Scan />
        </Button>
      </div>
    </div>

    <div ref="body" class="relative h-[max(420px,calc(100vh-318px))]">
      <template v-if="points">
        <MapCanvas
          ref="canvas"
          :x="points.x"
          :y="points.y"
          :categories="categories"
          :levels="levels"
          :colors="colors"
          :selected="selectedIndex"
          :point-size="pointSize"
          :background="chrome.background"
          :active-color="chrome.active"
          :hover-color="chrome.hover"
          @hover="onHover"
          @open="onOpen"
        />

        <div
          class="absolute top-3 left-3 max-h-[calc(100%-24px)] w-[220px] overflow-y-auto rounded-lg border bg-card/90 p-1.5 shadow-(--shadow-card) backdrop-blur-sm"
          aria-label="범례"
          data-map-legend
        >
          <component
            :is="entry.patch ? 'button' : 'div'"
            v-for="entry in legend"
            :key="entry.key"
            :type="entry.patch ? 'button' : undefined"
            class="flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-ui"
            :class="[entry.active ? 'bg-muted font-semibold' : '', entry.patch ? 'hover:bg-muted' : '']"
            :aria-pressed="entry.patch ? entry.active : undefined"
            @click="entry.patch && emit('change', entry.patch)"
          >
            <span class="size-2.5 shrink-0 rounded-full" :style="{ background: entry.color }" />
            <span class="min-w-0 flex-1 truncate" :title="entry.name">{{ entry.name }}</span>
            <span v-if="entry.note" class="text-meta font-semibold text-warning-ink">{{ entry.note }}</span>
            <span class="text-meta text-muted-foreground">{{ fmt(entry.count) }}</span>
          </component>
        </div>

        <div
          v-if="hovered && hoveredPoint"
          class="pointer-events-none absolute z-10 rounded-lg border bg-popover p-3 text-popover-foreground shadow-(--shadow-pop)"
          :style="tooltipStyle"
          role="tooltip"
          data-map-tooltip
        >
          <template v-if="hoveredText !== null">
            <div v-if="hoveredText.title" class="mb-1 truncate text-ui font-semibold">{{ hoveredText.title }}</div>
            <p class="line-clamp-3 text-ui break-words">{{ hoveredText.text }}</p>
          </template>
          <div v-else class="space-y-1.5">
            <Skeleton class="h-3.5 w-full" />
            <Skeleton class="h-3.5 w-2/3" />
          </div>
          <div class="mt-2 flex items-center gap-2 text-meta">
            <span class="font-semibold">{{ hoveredPoint.kind === 'query' ? '질의' : '문서' }}</span>
            <span class="ml-auto shrink-0 font-mono text-subtle-foreground">#{{ hoveredPoint.id }}</span>
          </div>
        </div>
      </template>

      <div v-else-if="pointsError || stateError" class="flex h-full items-center justify-center gap-3 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="font-medium">의미 지도</span>
        <span class="font-semibold text-danger-ink">오류</span>
        <span class="text-muted-foreground">{{ pointsError ?? stateError }}</span>
        <Button variant="outline" size="sm" @click="pointsError ? loadPoints() : analysis.reload()">다시 불러오기</Button>
      </div>

      <div v-else-if="!state || analysisId !== null" class="flex h-full items-center justify-center gap-2 text-ui text-muted-foreground">
        <LoaderCircle class="size-4 animate-spin" />불러오는 중
      </div>

      <div v-else class="flex h-full flex-col items-center justify-center gap-3 text-center" data-map-empty>
        <span class="grid size-11 place-items-center rounded-xl bg-muted"><ChartScatter class="size-5 text-muted-foreground" /></span>
        <span class="text-title font-semibold">의미 지도</span>
        <span class="text-ui text-muted-foreground">질의 · 문서 임베딩 2차원 배치 · 없음</span>
        <span v-if="!connected && !progress" class="mt-1 inline-flex items-center gap-3">
          <span class="inline-flex items-center gap-1.5 text-meta text-muted-foreground">
            <span class="size-1.5 rounded-full ring-[1.5px] ring-subtle-foreground ring-inset" />임베딩 미연결
          </span>
          <Button type="button" variant="outline" size="sm" @click="openConnectionSettings"><Plug />연결 설정</Button>
        </span>
        <AnalysisAction
          class="mt-1"
          :state="state"
          :progress="progress"
          :sending="sending"
          :action-error="actionError"
          :connected="connected"
          @start="analysis.start"
          @cancel="analysis.cancel"
        />
      </div>
    </div>

    <div v-if="state?.done" class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-t bg-muted/30 px-4 py-1.5 text-ui text-muted-foreground">
      <AnalysisAction
        :state="state"
        :progress="progress"
        :sending="sending"
        :action-error="actionError"
        :connected="connected"
        facts
        @start="analysis.start"
        @cancel="analysis.cancel"
      />
      <span class="ml-auto hidden items-center gap-3 whitespace-nowrap xl:inline-flex">
        <span>휠 확대</span><span>끌어 이동</span><span>점 눌러 열기</span>
      </span>
    </div>
  </div>
</template>
