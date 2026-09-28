<!--
  데이터 탭의 지도 보기 (?view=map): 의미 지도의 점을 WebGL로 그린다(MapCanvas). 점 하나가 문장 한 건이다.
    머리    점 n · 강조 n · 칠하기 [라벨 | 문제] · [전체 보기]
    몸      지도 · 왼쪽 위 범례(색 · 이름 · 수, 누르면 그 조건으로 거른다) · 점에 올리면 말풍선(문장 · 라벨 · #번호)
    바닥    만들기 칸(점 · 모델 · 만든 시각, 분석 이후 변경 · [다시 만들기], 만드는 중이면 진행) · 조작 안내
  데이터 탭의 거르기(라벨 · 상태 · 문제 · 검색어)에 맞는 점은 크고 또렷하게, 나머지는 흐리게 그린다.
  문제로 칠할 때는 문제 있는 점을 크게 위에 그린다(수천 개 가운데 몇십 개라도 보이게).
  라벨 · 상태 · 문제는 점 목록에 든 값으로 가리고, 검색어만 서버에 맞는 문장 번호를 묻는다(getMapMatches).
  점을 누르면 부모가 오른쪽 문장 패널(?record=)을 연다. 지도가 없으면 표 대신 [뜻 분석 만들기] 칸을 보인다.
  색은 지도에서만 쓰는 범주 색(palette.ts)이고, 어두운 테마로 바꾸면 그 테마의 색으로 다시 칠한다.
-->
<script setup lang="ts">
import { ChartScatter, LoaderCircle, Plug, Scan, TriangleAlert } from '@lucide/vue'
import { useElementSize, useMutationObserver } from '@vueuse/core'
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import { openConnectionSettings } from '@/system/connections/connections'
import { fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { isConnected } from '@/system/readiness'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { getMapMatches, getMapPoints, getRecord } from '@/modules/classification/api'
import { PROBLEM_NAMES, PROBLEMS, type DataFilters } from '@/modules/classification/data/filters'
import LabelChip from '@/modules/classification/LabelChip.vue'
import MapAction from '@/modules/classification/map/MapAction.vue'
import MapCanvas from '@/system/map/MapCanvas.vue'
import { CATEGORY_COLORS, currentMapTheme, NEUTRAL_COLOR, OTHER_COLOR, type MapTheme } from '@/system/map/palette'

import { FLAG, PROBLEM_PRIORITY, PROBLEM_SLOTS } from '@/modules/classification/map/flags'
import { useSemanticMap } from '@/modules/classification/map/useSemanticMap'
import type { MapPointsRead, RecordProblem } from '@/modules/classification/types'

const props = defineProps<{
  datasetId: number
  // 데이터 탭의 거르기 (주소에서 읽은 것)
  filters: DataFilters
  // 오른쪽 패널에 연 문장 번호
  openId: number | null
}>()

const emit = defineEmits<{
  // 점을 눌러 문장을 연다
  open: [recordId: number]
  // 범례를 눌러 거르기를 바꾼다
  change: [patch: Partial<DataFilters>]
}>()

// 칠하는 기준
type ColorMode = 'label' | 'problem'
const COLOR_MODES: { key: ColorMode; name: string }[] = [
  { key: 'label', name: '라벨' },
  { key: 'problem', name: '문제' },
]

// 라벨에 줄 수 있는 범주 색 수. 넘는 라벨은 '기타' 하나로 묶는다.
const MAX_LABEL_COLORS = CATEGORY_COLORS.light.length

// 점 수에 따른 점 크기(px). 많을수록 작게 그려 무리의 모양이 뭉개지지 않게 한다.
const LARGE_MAP_POINTS = 50_000
const MEDIUM_MAP_POINTS = 10_000
const POINT_SIZE = { large: 2, medium: 3, small: 4 }

// 거르기에 맞는 점이 전체의 이 몫 이하일 때만 크게 그린다. 흔한 점까지 키우면 흐린 점을 모두 덮는다.
const RARE_SHARE = 0.1

// 점에 올린 뒤 문장을 읽기까지 기다리는 시간(밀리초). 지나가며 스치는 점마다 묻지 않게.
const HOVER_DELAY_MS = 120

// 말풍선 폭(px)과 점에서 떨어진 거리(px). 오른쪽 · 아래 끝에 닿으면 반대쪽으로 뒤집는다.
const TOOLTIP_WIDTH = 320
const TOOLTIP_HEIGHT = 120
const TOOLTIP_OFFSET = 14

const semantic = useSemanticMap(() => props.datasetId)
const { state, progress, sending, actionError, loadError: stateError } = semantic

const connected = computed(() => isConnected('embedding'))
const colorMode = ref<ColorMode>('label')
const canvas = ref<InstanceType<typeof MapCanvas> | null>(null)
const body = ref<HTMLDivElement | null>(null)
const { width: bodyWidth, height: bodyHeight } = useElementSize(body)

// ---------- 점 읽기 ----------

// 점이 수십만 개라 깊게 반응하지 않게 shallowRef로 둔다.
const points = shallowRef<MapPointsRead | null>(null)
const pointsError = ref<string | null>(null)
let pointsController: AbortController | null = null

const mapId = computed(() => state.value?.map?.id ?? null)
watch(mapId, loadPoints, { immediate: true })

async function loadPoints(): Promise<void> {
  pointsController?.abort()
  pointsError.value = null
  if (mapId.value === null) {
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

const count = computed(() => points.value?.record_ids.length ?? 0)
const indexById = computed(() => {
  const index = new Map<number, number>()
  points.value?.record_ids.forEach((recordId, position) => index.set(recordId, position))
  return index
})
const selectedIndex = computed(() =>
  props.openId === null ? null : (indexById.value.get(props.openId) ?? null),
)
const pointSize = computed(() => {
  if (count.value > LARGE_MAP_POINTS) return POINT_SIZE.large
  return count.value > MEDIUM_MAP_POINTS ? POINT_SIZE.medium : POINT_SIZE.small
})

// ---------- 검색어에 맞는 문장 ----------

// 검색어에 맞는 문장 번호. 검색어가 없거나 아직 못 받았으면 null
const matches = shallowRef<Set<number> | null>(null)
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
    matches.value = new Set(found.record_ids)
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
// 캔버스의 바탕 · 연 점 · 올린 점 색. 테마가 바뀌면 CSS 변수에서 다시 읽는다.
const chrome = computed(() => {
  void theme.value
  const style = getComputedStyle(document.documentElement)
  return {
    background: style.getPropertyValue('--card').trim(),
    active: style.getPropertyValue('--primary').trim(),
    hover: style.getPropertyValue('--foreground').trim(),
  }
})

// 라벨 번호 → 범주 자리 (이름 순서대로). 넘는 라벨과 라벨 없음은 MAX_LABEL_COLORS(기타)
const labelSlots = computed(() => {
  const slots = new Map<number, number>()
  points.value?.labels.forEach((label, position) => slots.set(label.id, Math.min(position, MAX_LABEL_COLORS)))
  return slots
})

// 점 하나의 문제 가운데 먼저 칠할 것. 없으면 null
function problemOf(flags: number): (typeof PROBLEM_PRIORITY)[number] | null {
  return PROBLEM_PRIORITY.find((problem) => flags & FLAG[problem]) ?? null
}

const categories = computed<number[]>(() => {
  const data = points.value
  if (!data) return []
  if (colorMode.value === 'problem') {
    return data.flags.map((flags) => {
      const problem = problemOf(flags)
      return problem === null ? PROBLEM_PRIORITY.length : PROBLEM_SLOTS[problem]
    })
  }
  return data.label_ids.map((labelId) => (labelId === null ? MAX_LABEL_COLORS : (labelSlots.value.get(labelId) ?? MAX_LABEL_COLORS)))
})

const colors = computed<string[]>(() => {
  const palette = CATEGORY_COLORS[theme.value]
  if (colorMode.value === 'problem') return [...palette.slice(0, PROBLEM_PRIORITY.length), NEUTRAL_COLOR[theme.value]]
  return [...palette, OTHER_COLOR[theme.value]]
})

// ---------- 강조 (거르기) ----------

const hasFilters = computed(() => {
  const filters = props.filters
  return (
    filters.labelId !== null ||
    filters.problem !== null ||
    filters.status !== 'active' ||
    filters.q !== ''
  )
})

const highlight = computed<number[]>(() => {
  const data = points.value
  if (!data) return []
  if (!hasFilters.value) return data.record_ids.map(() => 1)
  const { labelId, problem, status } = props.filters
  const problemBit = problem === null ? 0 : FLAG[problem]
  const found = matches.value
  return data.record_ids.map((recordId, index) => {
    const flags = data.flags[index]
    const isExcluded = (flags & FLAG.excluded) !== 0
    const isMatch =
      (labelId === null || data.label_ids[index] === labelId) &&
      (problemBit === 0 || (flags & problemBit) !== 0) &&
      (status === 'active' || (status === 'included' && !isExcluded) || (status === 'excluded' && isExcluded)) &&
      (found === null || found.has(recordId))
    return isMatch ? 1 : 0
  })
})

const highlightedCount = computed(() => highlight.value.reduce((sum, value) => sum + value, 0))

// 점마다 눈에 띄는 정도 (MapCanvas의 level): 0 거르기에 안 맞음 · 1 보통 ·
// 2 크게(거르기에 맞는 점이 드물 때, 문제로 칠할 때 문제 있는 점)
const levels = computed<number[]>(() => {
  const data = points.value
  if (!data) return []
  const isProblemMode = colorMode.value === 'problem'
  const isRareMatch = hasFilters.value && highlightedCount.value <= count.value * RARE_SHARE
  return highlight.value.map((isMatch, index) => {
    if (!isMatch) return 0
    const hasProblem = isProblemMode && problemOf(data.flags[index]) !== null
    return isRareMatch || hasProblem ? 2 : 1
  })
})

// ---------- 범례 ----------

interface LegendEntry {
  key: string
  name: string
  color: string
  count: number
  // 지금 이 조건으로 거르고 있는지
  active: boolean
  // 누르면 바꿀 거르기. 없으면 누를 수 없다(기타 · 문제 없음)
  patch: Partial<DataFilters> | null
}

const legend = computed<LegendEntry[]>(() => {
  const data = points.value
  if (!data) return []
  const palette = colors.value
  const counts = new Map<number, number>()
  categories.value.forEach((category) => counts.set(category, (counts.get(category) ?? 0) + 1))
  const filters = props.filters

  if (colorMode.value === 'problem') {
    const entries: LegendEntry[] = PROBLEMS.map((problem: RecordProblem) => ({
      key: problem,
      name: PROBLEM_NAMES[problem],
      color: palette[PROBLEM_SLOTS[problem]],
      count: data.flags.filter((flags) => flags & FLAG[problem]).length,
      active: filters.problem === problem,
      patch: { problem: filters.problem === problem ? null : problem },
    }))
    const clean = counts.get(PROBLEM_PRIORITY.length) ?? 0
    return [...entries, { key: 'none', name: '문제 없음', color: palette[PROBLEM_PRIORITY.length], count: clean, active: false, patch: null }]
  }
  const entries: LegendEntry[] = data.labels.slice(0, MAX_LABEL_COLORS).map((label, index) => ({
    key: String(label.id),
    name: label.name,
    color: palette[index],
    count: counts.get(index) ?? 0,
    active: filters.labelId === label.id,
    patch: { labelId: filters.labelId === label.id ? null : label.id },
  }))
  const others = counts.get(MAX_LABEL_COLORS) ?? 0
  if (others) {
    entries.push({ key: 'other', name: '기타', color: palette[MAX_LABEL_COLORS], count: others, active: false, patch: null })
  }
  return entries
})

// ---------- 말풍선 ----------

const hovered = ref<{ index: number; x: number; y: number } | null>(null)
const hoveredText = ref<string | null>(null)
const textCache = new Map<number, string>()
let hoverTimer: ReturnType<typeof setTimeout> | undefined
let hoverController: AbortController | null = null

const hoveredPoint = computed(() => {
  const data = points.value
  const current = hovered.value
  if (!data || !current) return null
  const labelId = data.label_ids[current.index]
  const label = data.labels.find((item) => item.id === labelId)
  return {
    recordId: data.record_ids[current.index],
    labelName: label?.name ?? null,
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
  if (index === null || position === null) {
    hovered.value = null
    return
  }
  hovered.value = { index, ...position }
  const recordId = points.value?.record_ids[index]
  if (recordId === undefined) return
  hoveredText.value = textCache.get(recordId) ?? null
  if (hoveredText.value !== null) return
  hoverTimer = setTimeout(() => void loadText(recordId), HOVER_DELAY_MS)
}

async function loadText(recordId: number): Promise<void> {
  const controller = new AbortController()
  hoverController = controller
  try {
    const record = await getRecord(recordId, controller.signal)
    textCache.set(recordId, record.text)
    if (hoveredPoint.value?.recordId === recordId) hoveredText.value = record.text
  } catch {
    // 말풍선의 문장은 없어도 된다. 라벨 · 번호는 이미 보인다.
  }
}

function onOpen(index: number): void {
  const recordId = points.value?.record_ids[index]
  if (recordId !== undefined) emit('open', recordId)
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
          점 <b class="text-title font-semibold text-foreground">{{ fmt(count) }}</b>
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
          <p v-if="hoveredText !== null" class="line-clamp-3 text-ui break-words">{{ hoveredText }}</p>
          <div v-else class="space-y-1.5">
            <Skeleton class="h-3.5 w-full" />
            <Skeleton class="h-3.5 w-2/3" />
          </div>
          <div class="mt-2 flex items-center gap-2">
            <span class="min-w-0"><LabelChip :name="hoveredPoint.labelName" /></span>
            <span class="ml-auto shrink-0 font-mono text-meta text-subtle-foreground">#{{ hoveredPoint.recordId }}</span>
          </div>
        </div>
      </template>

      <div v-else-if="pointsError || stateError" class="flex h-full items-center justify-center gap-3 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="font-medium">의미 지도</span>
        <span class="font-semibold text-danger-ink">오류</span>
        <span class="text-muted-foreground">{{ pointsError ?? stateError }}</span>
        <Button variant="outline" size="sm" @click="pointsError ? loadPoints() : semantic.reload()">다시 불러오기</Button>
      </div>

      <div v-else-if="!state || mapId !== null" class="flex h-full items-center justify-center gap-2 text-ui text-muted-foreground">
        <LoaderCircle class="size-4 animate-spin" />불러오는 중
      </div>

      <div v-else class="flex h-full flex-col items-center justify-center gap-3 text-center" data-map-empty>
        <span class="grid size-11 place-items-center rounded-xl bg-muted">
          <ChartScatter class="size-5 text-muted-foreground" />
        </span>
        <span class="text-title font-semibold">의미 지도</span>
        <span class="text-ui text-muted-foreground">문장 임베딩 2차원 배치 · 없음</span>
        <span v-if="!connected && !progress" class="mt-1 inline-flex items-center gap-3">
          <span class="inline-flex items-center gap-1.5 text-meta text-muted-foreground">
            <span class="size-1.5 rounded-full ring-[1.5px] ring-subtle-foreground ring-inset" />임베딩 미연결
          </span>
          <Button type="button" variant="outline" size="sm" @click="openConnectionSettings"><Plug />연결 설정</Button>
        </span>
        <MapAction
          class="mt-1"
          :state="state"
          :progress="progress"
          :sending="sending"
          :action-error="actionError"
          :connected="connected"
          @start="semantic.start"
          @cancel="semantic.cancel"
        />
      </div>
    </div>

    <div
      v-if="state?.map"
      class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-t bg-muted/30 px-4 py-1.5 text-ui text-muted-foreground"
    >
      <MapAction
        :state="state"
        :progress="progress"
        :sending="sending"
        :action-error="actionError"
        :connected="connected"
        facts
        @start="semantic.start"
        @cancel="semantic.cancel"
      />
      <span class="ml-auto hidden items-center gap-3 whitespace-nowrap xl:inline-flex">
        <span>휠 확대</span><span>끌어 이동</span><span>점 눌러 열기</span>
        <span class="inline-flex items-center gap-1"><span class="kbd">J</span><span class="kbd">K</span>이동</span>
      </span>
    </div>
  </div>
</template>
