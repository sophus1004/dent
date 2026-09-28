<!--
  의미 지도 캔버스: regl-scatterplot(WebGL)으로 점 수십만 개를 그린다. 휠 확대 · 끌어 옮기기는 라이브러리가 한다.
  부모가 좌표 · 점마다 색 번호 · 눈에 띄는 정도 · 색 목록 · 연 점을 주면 그리기만 한다. 이 부품은 문장 · 주소를 모른다.
    올리기   hover(점 번호, 캔버스 안 위치) · 벗어나거나 화면을 옮기면 hover(null)
    누르기   open(점 번호)
  눈에 띄는 정도(level): 0 흐리게 · 1 보통 · 2 크게. 큰 점일수록 나중에(위에) 그린다.
  연 점은 보라, 올린 점은 글자색으로 칠한다.
  크기는 부모 칸을 따라간다(ResizeObserver). 끌어서 여러 점 고르기(lasso)는 쓰지 않는다.
-->
<script setup lang="ts">
import { useResizeObserver } from '@vueuse/core'
import createScatterplot from 'regl-scatterplot'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  // 가로 · 세로 좌표 [-1, 1]
  x: number[]
  y: number[]
  // 점마다 색 번호 (colors의 자리)
  categories: number[]
  // 점마다 눈에 띄는 정도: 0 흐리게(거르기에 안 맞음) · 1 보통 · 2 크게(거르기에 맞음 · 문제 있음)
  levels: number[]
  // 색 번호 → 색
  colors: string[]
  // 연 점의 번호. 없으면 null
  selected: number | null
  // 보통 점의 크기(px)
  pointSize: number
  // 캔버스 뒤 바탕색. 연 점의 테두리 안쪽을 이 색으로 채운다.
  background: string
  // 연 점 · 올린 점의 색
  activeColor: string
  hoverColor: string
}>()

const emit = defineEmits<{
  hover: [index: number | null, position: { x: number; y: number } | null]
  open: [index: number]
}>()

// 눈에 띄는 정도(0 흐리게 · 1 보통 · 2 크게)별 불투명도와 더하는 크기(px).
// 점이 많이 겹치므로 보통 점도 조금 비친다. 큰 점은 드문 점(검색 결과 · 라벨 충돌)도 눈에 띄게 한다.
const OPACITY_BY_LEVEL = [0.08, 0.85, 0.95]
const SIZE_BOOST_BY_LEVEL = [0, 0, 2]

// 처음 볼 때 점 무리가 캔버스 끝에 닿지 않게 조금 물러서서 본다(1이면 [-1, 1]이 꽉 찬다).
const CAMERA_DISTANCE = 1.06

// 연 점은 이만큼 더 크게, 이 굵기의 테두리로 그린다.
const SELECTED_EXTRA_SIZE = 4
const OUTLINE_WIDTH = 2

type Scatterplot = ReturnType<typeof createScatterplot>
type ScatterplotOptions = NonNullable<Parameters<typeof createScatterplot>[0]>

const container = ref<HTMLDivElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
let plot: Scatterplot | null = null
// 좌표가 같으면 점 찾기 색인을 다시 만들지 않는다(색 · 강조만 바뀔 때 빨리 다시 그리려고).
let spatialIndex: ArrayBuffer | undefined

onMounted(() => {
  const box = container.value?.getBoundingClientRect()
  if (!canvas.value || !box) return
  const options: ScatterplotOptions = {
    canvas: canvas.value,
    width: Math.max(1, box.width),
    height: Math.max(1, box.height),
    backgroundColor: props.background,
    pointSizeSelected: SELECTED_EXTRA_SIZE,
    pointOutlineWidth: OUTLINE_WIDTH,
    colorBy: 'valueA',
    opacityBy: 'valueB',
    sizeBy: 'valueB',
    opacity: OPACITY_BY_LEVEL,
    cameraDistance: CAMERA_DISTANCE,
    deselectOnEscape: false,
    deselectOnDblClick: false,
  }
  // 끌어서 여러 점 고르기(lasso) · 돌리기를 끈다. 이 판의 타입 정의에는 새 이름(actionKeyMap)이 빠져 있고,
  // 옛 이름(keyMap)은 콘솔 경고를 내서 따로 넣는다.
  Object.assign(options, { actionKeyMap: {} })
  plot = createScatterplot(options)
  plot.subscribe('pointOver', (index: number) => emit('hover', index, positionOf(index)))
  plot.subscribe('pointOut', () => emit('hover', null, null))
  plot.subscribe('view', () => emit('hover', null, null))
  plot.subscribe('select', ({ points }: { points: number[] }) => {
    const [index] = points
    const isNewPoint = points.length === 1 && index !== props.selected
    if (isNewPoint) emit('open', index)
  })
  void render()
})

onBeforeUnmount(() => {
  plot?.destroy()
  plot = null
})

useResizeObserver(container, (entries) => {
  const { width, height } = entries[0].contentRect
  if (plot && width > 0 && height > 0) plot.set({ width, height })
})

watch(
  () => props.x,
  () => {
    spatialIndex = undefined
    void render()
  },
)
watch(
  [
    () => props.categories,
    () => props.levels,
    () => props.colors,
    () => props.pointSize,
    () => props.activeColor,
    () => props.hoverColor,
  ],
  () => void render(),
)
watch(() => props.selected, syncSelection)
watch(
  () => props.background,
  (background) => plot?.set({ backgroundColor: background }),
)

async function render(): Promise<void> {
  const current = plot
  if (!current) return
  current.set({
    pointColor: props.colors,
    pointSize: SIZE_BOOST_BY_LEVEL.map((boost) => props.pointSize + boost),
  })
  // 연 점 · 올린 점의 색은 색 수만큼 같은 값을 준다. 하나만 주면 라이브러리가 점 자기 색으로 칠한다.
  // 이 판의 타입 정의에는 색 하나만 적혀 있지만 라이브러리는 색 목록도 받는다.
  const stateColors = {
    pointColorActive: props.colors.map(() => props.activeColor),
    pointColorHover: props.colors.map(() => props.hoverColor),
  }
  current.set(stateColors as unknown as Parameters<Scatterplot['set']>[0])
  await current.draw(
    // 라이브러리가 칸마다 .map을 부르므로 보통 배열로 준다(형식 배열은 안 된다).
    { x: props.x, y: props.y, z: props.categories, w: props.levels },
    { zDataType: 'categorical', wDataType: 'categorical', spatialIndex },
  )
  if (current !== plot) return
  spatialIndex ??= current.get('spatialIndex')
  current.set({ pointOrder: bigOnTop() })
  syncSelection()
}

// 눈에 띄는 점을 나중에(위에) 그리는 순서. 모두 같은 정도면 원래 순서(null).
function bigOnTop(): number[] | null {
  const byLevel: number[][] = OPACITY_BY_LEVEL.map(() => [])
  props.levels.forEach((level, index) => byLevel[level].push(index))
  const usedLevels = byLevel.filter((indices) => indices.length > 0).length
  return usedLevels > 1 ? byLevel.flat() : null
}

function syncSelection(): void {
  if (!plot) return
  if (props.selected === null) plot.deselect({ preventEvent: true })
  else plot.select([props.selected], { preventEvent: true })
}

function positionOf(index: number): { x: number; y: number } | null {
  const position = plot?.getScreenPosition(index)
  return position ? { x: position[0], y: position[1] } : null
}

/** 처음 보던 자리로 돌아간다. */
function resetView(): void {
  plot?.reset()
}

defineExpose({ resetView })
</script>

<template>
  <div ref="container" class="absolute inset-0">
    <canvas ref="canvas" class="block size-full" aria-label="의미 지도" />
  </div>
</template>
