<!--
  도우미 창: 데이터셋 화면 오른쪽에 붙는다(상단 바 [도우미] · ⌘J).
  도우미는 채팅이 아니다. 진단을 읽고, 스스로 묻고 답하며, 규칙 · Jev · LLM으로 데이터를 고친다. 이 창은 그 일을 실시간으로 보인다.
    머리     도우미 · 상태(돌리는 중 n/7 · 허락 기다림 · 끝남 · 막힘 · 멈춤 · 실패) · [멈추기] 또는 [시작] · 닫기
             막힘 = 문에 걸림: 앞 단계에 심각이 남아 허락 · 다음 흐름으로 가지 않았다(그 단계는 빨강 막힘, 남은 문제 카드)
             바꿈 · 보류 · LLM 토큰 · Jev · 걸린 시간 · 모델
    진행 점  0~7 (끝남 ✓ · 지금 보라 · 허락 호박 · 막힘 빨강)
    단계     한 줄: 상태 · 번호 · 이름 · 사실(바꿈 · 보류 · 유지 · 규칙/Jev/LLM · 시간) · 처음 → 지금
             지금 단계와 허락 단계는 펼쳐 에이전트의 말과 카드를 보인다. 끝난 단계는 눌러서 다시 본다.
    보고     실행 끝(과 AI로 고치기 끝)의 보고 카드. 끝난 뒤에는 가장 최근 보고를 펼친다.
    남은 것  끝난 뒤 단계 줄 아래(HelperLeft): 세 상자 · 고른 것을 [AI로 고치기] → 같은 실행에 'AI로 고치기' 무리가 붙는다
    바닥     바뀐 것 n · [이번 실행 전부 되돌리기] (도는 중에는 막힘)
  처음(실행 없음)에는 한 번 실행의 단계와 필요한 연결(LLM · Jev), [시작]을 보인다. 모듈이 실행 설정 카드(api.settingsCard)를 주면 처음에는 그 카드와 [시작], 단계 옆에 그 단계의 값을 보인다.
  끝난 뒤에는 머리의 [설정] · 허락 카드의 [설정]으로 카드를 다시 연다(도는 동안은 닫는다).
  단계에 무리(stage)가 있으면 무리 머리 줄을 넣고, 무리 안 단계가 모두 끝나면 '끝 · 바꿈 n' 한 줄로 접는다(누르면 펼침).
  상태 읽기와 1초마다 이어 읽기는 useHelper가 한다. 시작 · 되돌리기 · 허락은 모듈이 api로 넘긴다(모든 모듈이 같이 쓴다).
  놓는 자리(Material 3의 보조 창과 같은 틀):
    나란히  창이 놓인 줄(본문 · 상세 패널 · 이 창)에서 본문에 35rem을 남기고도 창을 23rem 넣을 수 있으면 본문 옆에 붙인다.
            창 폭은 남는 자리를 두 최소의 비율(약 60 : 40)로 나눠 23~28.75rem 사이로 맞춘다(함께 줄어든다). 본문 부품은 자기 폭을 보고 접는다(@container).
    겹침    둘 다 최소(본문 35rem · 창 23rem)에 닿아 더 줄일 수 없으면 겹친다. 본문은 그 35rem에서 멈추고(다시 넓어지거나
            창을 따라 바뀌지 않는다), 줄의 나머지는 이 창의 자리다. 창은 넘어갈 때의 23rem 그대로 오른쪽 끝에 붙어 본문 위로 들어온다.
    사이드바 사이드바는 제 규칙(창 73.75rem 이하)으로만 접힌다. 두 최소의 합을 그 직전 자리보다 작게 두어, 겹치기는 사이드바가 접힌 뒤에만 온다.
            [접기] · Esc · 본문 누르기로 레일(3.25rem, 단계 점 · 허락 표시, 자리의 오른쪽 끝)로 접고, 레일을 누르면 편다. 접힘은 브라우저에 기억한다.
  폭은 rem으로 재서 브라우저 확대 · 글꼴 크기 · 해상도가 달라도 같은 규칙으로 넘어간다(줄의 실제 폭을 ResizeObserver로 잰다).
-->
<script setup lang="ts">
import {
  ChevronRight,
  Circle,
  CircleCheck,
  CircleMinus,
  Hand,
  LoaderCircle,
  Lock,
  OctagonX,
  PanelRightClose,
  Pause,
  Pencil,
  Pin,
  Play,
  Plug,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  TriangleAlert,
  Undo2,
  X,
  type LucideIcon,
} from '@lucide/vue'
import { onKeyStroke, useEventListener, useResizeObserver } from '@vueuse/core'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'

import { openConnectionSettings } from '@/system/connections/connections'
import { fmt } from '@/system/format'
import { isConnected } from '@/system/readiness'
import type { HelperEventRead, HelperStep } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { GRADES, type Grade } from '@/system/diagnosis/grades'
import HelperEvent from '@/system/helper/HelperEvent.vue'
import HelperLeft from '@/system/helper/HelperLeft.vue'
import { isHelperFolded, isHelperOverlaid, setHelperFolded } from '@/system/helper/helperSignals'
import { remPx } from '@/system/layout/rem'
import { useHelper, type HelperApi, type PlanStep } from '@/system/helper/useHelper'

const props = defineProps<{
  datasetId: number
  // 모듈의 도우미 API (시작 · 되돌리기 · 허락 · 보류의 [정하기] 주소)
  api: HelperApi
  // 처음 화면에 보일 한 번 실행의 단계 (백엔드의 initial_steps와 같은 순서)
  planSteps: PlanStep[]
}>()

const emit = defineEmits<{
  close: []
}>()

// 걸린 시간을 다시 그리는 간격(밀리초)
const CLOCK_MS = 1_000

// 지금 단계의 말을 따라 내려가는 거리(px). 이보다 위로 올려 읽는 중이면 따라가지 않는다.
const FOLLOW_GAP_PX = 120

// 놓는 자리 (rem): 나란히 둘 때 본문에 남길 폭 · 창 폭 범위. 본문 35rem은 부품의 접힌 모양이 검사 표의 모든 칸을 보이는 끝이다.
// 두 최소의 합(58rem)은 사이드바가 제 규칙대로 접히기 직전에 남는 자리(73.75 − 15.5 = 58.25rem, system/layout/rail.ts)보다
// 작게 둔다. 그래서 사이드바가 펼쳐진 동안은 늘 나란히이고, 겹치기는 사이드바가 접힌 뒤에만 온다(나란히 ↔ 겹침이 오가지 않는다).
const MAIN_MIN_REM = 35
const PANEL_MIN_REM = 23
const PANEL_MAX_REM = 28.75
// 남는 자리 가운데 창의 몫: 두 최소의 비율과 같아서 줄어들 때 둘이 동시에 최소에 닿는다.
const PANEL_SHARE = PANEL_MIN_REM / (MAIN_MIN_REM + PANEL_MIN_REM)
// 겹쳐서 접었을 때의 레일 폭 (rem, 클래스 w-13과 같다)
const RAIL_REM = 3.25

const STATE_WORDS: Record<string, { word: string; tone: string }> = {
  running: { word: '돌리는 중', tone: 'text-accent-foreground bg-accent' },
  asking: { word: '허락 기다림', tone: 'text-warning-ink bg-warning/10' },
  done: { word: '끝남', tone: 'text-success-ink bg-success/10' },
  stopped: { word: '멈춤', tone: 'text-muted-foreground bg-muted' },
  failed: { word: '실패', tone: 'text-danger-ink bg-danger/10' },
}

// 문에 걸려 끝난 실행의 상태 (실행은 done이고 단계 하나가 blocked다)
const BLOCKED_WORD = { word: '막힘', tone: 'text-danger-ink bg-danger/10' }

const helper = useHelper(() => props.datasetId, props.api)
// 사람이 펼친 끝난 무리
const openStages = ref<Set<number>>(new Set())

// 단계 무리마다: 끝났는지(모두 끝 · 건너뜀) · 바꾼 수
const stageSummary = computed(() => {
  const summary = new Map<number, { title: string; done: boolean; changed: number }>()
  for (const step of run.value?.steps ?? []) {
    if (step.stage === undefined) continue
    const entry = summary.get(step.stage) ?? { title: step.stage_title ?? '', done: true, changed: 0 }
    entry.done = entry.done && ['done', 'skipped'].includes(step.status)
    entry.changed += Number(step.facts?.changed ?? 0)
    summary.set(step.stage, entry)
  }
  return summary
})

/** 이 단계가 무리의 첫 단계인지 (무리 머리 줄을 그 앞에 넣는다) */
function startsStage(index: number): boolean {
  const steps = run.value?.steps ?? []
  const stage = steps[index]?.stage
  return stage !== undefined && steps[index - 1]?.stage !== stage
}

/** 끝나서 접힌 무리의 단계인지 */
function isFolded(step: HelperStep): boolean {
  if (step.stage === undefined) return false
  return Boolean(stageSummary.value.get(step.stage)?.done) && !openStages.value.has(step.stage)
}

function toggleStage(stage: number): void {
  const next = new Set(openStages.value)
  if (next.has(stage)) next.delete(stage)
  else next.add(stage)
  openStages.value = next
}
const { run, events, undone, loading, loadError, actionError, busy, isRunning } = helper

// ----- 놓는 자리 -----

const rootEl = useTemplateRef<HTMLElement>('root')
const panelEl = useTemplateRef<HTMLElement>('panel')
const railEl = useTemplateRef<HTMLElement>('rail')
// 이 창이 놓인 줄 (본문 · 상세 패널 #dataset-side · 이 창). 뿌리는 display: contents라 그 부모가 줄이다.
const region = computed(() => rootEl.value?.parentElement ?? null)
const sidePanel = computed(() => region.value?.querySelector<HTMLElement>(':scope > #dataset-side') ?? null)
const regionWidth = ref(0)
const sideWidth = ref(0)
const oneRem = ref(remPx())

function measure(row: HTMLElement | null = region.value): void {
  if (!row) return
  // 브라우저 글꼴 크기를 바꾸면 rem도 바뀐다.
  oneRem.value = remPx()
  regionWidth.value = row.clientWidth
  sideWidth.value = row.querySelector<HTMLElement>(':scope > #dataset-side')?.offsetWidth ?? 0
}

// 사람이 열 때는 줄(data-helper-region)이 이미 있으므로 처음 그릴 때부터 제자리 · 제 폭으로 그린다
// (열리는 움직임이 제 폭에서 시작하게. 화면을 처음 열 때는 붙은 뒤에 잰다).
measure(document.querySelector<HTMLElement>('[data-helper-region]'))
onMounted(() => measure())
useResizeObserver(region, () => measure())
// 데이터 탭의 상세 패널이 열리고 닫히면 본문 자리가 바뀐다.
useResizeObserver(sidePanel, () => measure())

// 본문 · 이 창이 나눠 쓸 폭 (상세 패널을 뺀 것)
const room = computed(() => regionWidth.value - sideWidth.value)
// 나란히: 본문에 35rem을 남기고 창을 23rem 넣을 수 있다. 아직 못 쟀으면 나란히로 둔다(겹침으로 번쩍이지 않게).
const isBeside = computed(() => regionWidth.value === 0 || room.value >= (MAIN_MIN_REM + PANEL_MIN_REM) * oneRem.value)
const panelWidth = computed(() => {
  const share = room.value * PANEL_SHARE
  return Math.min(PANEL_MAX_REM * oneRem.value, Math.max(PANEL_MIN_REM * oneRem.value, share))
})
// 겹칠 때 펼쳐 보이는지 (접으면 레일만)
const isOverlayOpen = computed(() => !isBeside.value && !isHelperFolded.value)
// 겹칠 때 이 창의 자리: 본문을 35rem에 멈추고 남는 폭(레일보다 좁아지면 레일 폭). 레일은 이 자리의 오른쪽 끝에 선다.
const overlaySlot = computed(() => Math.max(RAIL_REM * oneRem.value, room.value - MAIN_MIN_REM * oneRem.value))

// 상단 바 [도우미] · ⌘J가 레일을 펴게 겹쳐 있는지 알린다.
watch(isBeside, (beside) => (isHelperOverlaid.value = !beside), { immediate: true })
onBeforeUnmount(() => (isHelperOverlaid.value = false))

// 겹쳐 펼친 창: Esc나 본문 누르기로 레일로 접는다(창 안 · 레일은 빼고).
// 대화상자가 떠 있으면 Esc는 그 대화상자를 닫는 데 쓴다.
onKeyStroke('Escape', () => {
  const hasDialog = document.querySelector('[role="dialog"], [role="alertdialog"]') !== null
  if (isOverlayOpen.value && !hasDialog) setHelperFolded(true)
})
useEventListener(region, 'pointerdown', (event: PointerEvent) => {
  const target = event.target as Node | null
  const isInside = Boolean(target && (panelEl.value?.contains(target) || railEl.value?.contains(target)))
  if (isOverlayOpen.value && !isInside) setHelperFolded(true)
})

// 레일의 단계 점: 실행이 있으면 그 단계, 없으면 처음 단계(모두 할 것)
const railSteps = computed(() =>
  run.value
    ? run.value.steps.map((step) => ({ key: step.key, status: step.status }))
    : props.planSteps.map((step, index) => ({ key: step.key ?? String(index), status: 'todo' })),
)

function railDotTone(status: string): string {
  if (status === 'done') return 'bg-success'
  if (status === 'running') return 'bg-primary shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_22%,transparent)]'
  if (status === 'ask') return 'bg-warning shadow-[0_0_0_3px_color-mix(in_oklab,var(--warning)_25%,transparent)]'
  if (status === 'blocked') return 'bg-danger'
  return 'bg-muted shadow-[inset_0_0_0_1px_var(--border-strong)]'
}
const body = useTemplateRef<HTMLElement>('body')
// 실행 설정 카드를 다시 열었는지 (끝난 뒤 · 허락 중) · 카드가 알려 준 단계마다의 값 (처음 화면의 단계 옆)
const settingsOpen = ref(false)
const planValues = ref<Record<string, string>>({})

// 도는 동안에는 설정을 바꾸지 않는다(다음 단계가 읽는 값이 흔들리지 않게).
watch(isRunning, (running) => {
  if (running) settingsOpen.value = false
})

/** 허락 카드의 [설정] · 머리의 [설정]: 카드를 창 맨 위에 연다. */
async function openSettings(): Promise<void> {
  settingsOpen.value = true
  await nextTick()
  if (body.value) body.value.scrollTop = 0
}
const openSteps = ref<Set<string>>(new Set())
const now = ref(Date.now())
const clock = setInterval(() => {
  if (isRunning.value) now.value = Date.now()
}, CLOCK_MS)
onBeforeUnmount(() => clearInterval(clock))

const llmConnected = computed(() => isConnected('llm'))
const jevConnected = computed(() => isConnected('jev'))
// 문에 걸려 끝난 실행 (AI로 고치기를 붙여 끝나면 까닭이 지워져 끝남으로 돌아간다)
const isBlocked = computed(
  () => run.value?.status === 'done' && Boolean(run.value.error) && run.value.steps.some((step) => step.status === 'blocked'),
)
// 남은 것을 보일 때: 실행이 끝났을 때(돌거나 허락을 기다리는 동안은 숨긴다)
const showsLeft = computed(() => Boolean(run.value) && !['running', 'asking'].includes(run.value?.status ?? ''))
const stateWord = computed(() => {
  if (!run.value) return null
  return isBlocked.value ? BLOCKED_WORD : STATE_WORDS[run.value.status]
})
const eventsByStep = computed(() => {
  const grouped = new Map<number, HelperEventRead[]>()
  for (const event of events.value) {
    const list = grouped.get(event.step) ?? []
    list.push(event)
    grouped.set(event.step, list)
  }
  return grouped
})
const elapsed = computed(() => {
  if (!run.value) return ''
  const end = run.value.finished_at ? Date.parse(run.value.finished_at) : now.value
  const seconds = Math.max(0, Math.round((end - Date.parse(run.value.created_at)) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
})
const stepCount = computed(() => Math.max(0, (run.value?.steps.length ?? 1) - 1))
// 마지막 허락 카드 (그 카드만 버튼을 켠다)
const lastPermissionId = computed(() => [...events.value].reverse().find((event) => event.kind === 'permission')?.id ?? null)

// 지금 단계(도는 중 · 허락 · 막힘)는 펼친다. 사람이 접은 끝난 단계는 그대로 둔다.
watch(
  () => run.value?.steps.find((step) => ['running', 'ask', 'blocked'].includes(step.status))?.key,
  (key) => {
    if (key) openSteps.value = new Set([...openSteps.value, key])
  },
  { immediate: true },
)

// 끝나면 가장 최근 보고를 펼친다.
watch(
  () => [...(run.value?.steps ?? [])].reverse().find((step) => step.key.startsWith('report') && step.status === 'done')?.key,
  (key) => {
    if (key) openSteps.value = new Set([...openSteps.value, key])
  },
  { immediate: true },
)

// 새 말이 오면 아래에 붙어 따라간다(위로 올려 읽는 중이면 두고).
watch(
  () => events.value.length,
  async () => {
    const box = body.value
    if (!box) return
    const isNearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < FOLLOW_GAP_PX
    await nextTick()
    if (isNearBottom) box.scrollTop = box.scrollHeight
  },
)

function toggle(step: HelperStep): void {
  if (!eventsByStep.value.get(step.no)?.length) return
  const next = new Set(openSteps.value)
  if (next.has(step.key)) next.delete(step.key)
  else next.add(step.key)
  openSteps.value = next
}

function statusIcon(status: string): { icon: LucideIcon; tone: string } {
  if (status === 'done') return { icon: CircleCheck, tone: 'text-success' }
  if (status === 'running') return { icon: LoaderCircle, tone: 'animate-spin text-primary' }
  if (status === 'ask') return { icon: Hand, tone: 'text-warning' }
  if (status === 'blocked') return { icon: OctagonX, tone: 'text-danger' }
  if (status === 'locked') return { icon: Lock, tone: 'text-subtle-foreground' }
  if (status === 'skipped') return { icon: CircleMinus, tone: 'text-subtle-foreground' }
  return { icon: Circle, tone: 'text-subtle-foreground' }
}

function dotTone(step: HelperStep): string {
  if (step.status === 'done') return 'bg-success/16 text-success-ink'
  if (step.status === 'running') return 'bg-primary text-primary-foreground shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_25%,transparent)]'
  if (step.status === 'ask') return 'bg-warning text-white shadow-[0_0_0_3px_color-mix(in_oklab,var(--warning)_25%,transparent)]'
  if (step.status === 'blocked') return 'bg-danger text-white'
  return 'bg-muted text-muted-foreground'
}

// 단계 사실 칩 (바꿈 · 보류 · 유지 · 규칙 · Jev · LLM · 시간)
function factChips(step: HelperStep): { key: string; icon?: LucideIcon; text: string; tone?: string }[] {
  const facts = step.facts ?? {}
  const chips = []
  if (facts.changed) chips.push({ key: 'changed', icon: Pencil, text: fmt(Number(facts.changed)) })
  if (facts.held) chips.push({ key: 'held', icon: Hand, text: fmt(Number(facts.held)), tone: 'text-warning-ink bg-warning/12' })
  if (facts.kept) chips.push({ key: 'kept', icon: Pin, text: fmt(Number(facts.kept)) })
  if (facts.rule) chips.push({ key: 'rule', text: '규칙' })
  if (facts.jev) chips.push({ key: 'jev', text: `Jev ${fmt(Number(facts.jev))}` })
  if (facts.llm) chips.push({ key: 'llm', text: `LLM ${fmt(Number(facts.llm))}` })
  if (facts.time) chips.push({ key: 'time', text: String(facts.time), tone: 'bg-transparent font-medium text-subtle-foreground' })
  return chips
}

function deltaGrade(step: HelperStep): Grade {
  return (step.delta?.[2] ?? 'good') as Grade
}
</script>

<template>
  <!-- 뿌리 하나(display: contents): 데이터셋 화면의 Transition이 이 창을 한 덩어리로 붙이고 뗀다(helperMotion.ts) -->
  <div ref="root" class="contents" data-helper-root>
  <!-- 겹칠 때 줄에 남는 이 창의 자리(본문은 35rem에 멈춘다)와 그 오른쪽 끝의 레일: 펼치기 · 상태 · 단계 점 · 허락 · 닫기 -->
  <aside
    v-if="!isBeside"
    ref="rail"
    class="sticky top-[52px] z-20 flex h-[calc(100vh-52px)] shrink-0 justify-end transition-[width] duration-200 ease-out motion-reduce:transition-none"
    :style="{ width: `${overlaySlot}px` }"
    aria-label="도우미 (접힘)"
    data-helper-rail
  >
    <div class="flex h-full w-13 flex-col items-center gap-2 border-l bg-card py-2.5">
      <Tooltip>
        <TooltipTrigger as-child>
          <Button
            type="button"
            variant="quiet"
            size="icon"
            class="bg-accent text-accent-foreground hover:bg-accent"
            aria-label="도우미 펼치기"
            :aria-expanded="isOverlayOpen"
            data-action="helper-unfold"
            @click="setHelperFolded(!isHelperFolded)"
          >
            <Sparkles />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="left">{{ isOverlayOpen ? '접기 · Esc' : '도우미 펼치기' }}</TooltipContent>
      </Tooltip>
      <LoaderCircle v-if="run?.status === 'running'" class="size-3.5 animate-spin text-primary" aria-label="돌리는 중" />
      <Hand v-else-if="run?.status === 'asking'" class="size-3.5 text-warning" aria-label="허락 기다림" />
      <div class="mt-1 grid justify-items-center gap-1.5" aria-hidden="true">
        <span v-for="step in railSteps" :key="step.key" class="size-2 rounded-full" :class="railDotTone(step.status)" />
      </div>
      <span v-if="run?.status === 'asking'" class="mt-1 text-meta font-[650] tracking-[0.1em] text-warning-ink [writing-mode:vertical-rl]">허락</span>
      <Tooltip>
        <TooltipTrigger as-child>
          <Button type="button" variant="quiet" size="icon-sm" class="mt-auto" aria-label="닫기" @click="emit('close')"><X /></Button>
        </TooltipTrigger>
        <TooltipContent side="left">닫기 · ⌘J</TooltipContent>
      </Tooltip>
    </div>
  </aside>

  <!-- 겹친 창은 오른쪽에서 밀려 들어오고 나간다(옆에 붙인 창은 본문이 튀지 않게 움직이지 않는다) -->
  <Transition
    :css="!isBeside"
    enter-active-class="transition duration-200 ease-out motion-reduce:transition-none"
    enter-from-class="translate-x-full opacity-0"
    leave-active-class="transition duration-150 ease-in motion-reduce:transition-none"
    leave-to-class="translate-x-full opacity-0"
  >
  <aside
    v-if="isBeside || isOverlayOpen"
    ref="panel"
    class="flex flex-col border-l bg-card"
    :class="
      isBeside
        ? 'sticky top-[52px] h-[calc(100vh-52px)] shrink-0 shadow-(--shadow-panel) transition-[width] duration-200 ease-out motion-reduce:transition-none'
        : 'fixed top-[52px] right-0 bottom-0 z-30 w-[min(23rem,calc(100vw-4rem))] shadow-(--shadow-pop)'
    "
    :style="isBeside ? { width: `${panelWidth}px` } : undefined"
    aria-label="도우미"
    :data-helper-panel="isBeside ? 'beside' : 'over'"
  >
    <div class="grid gap-2 border-b px-4 pt-3 pb-2.5">
      <div class="flex items-center gap-2">
        <span class="inline-flex items-center gap-1.5 text-[15px] font-[650]"><Sparkles class="size-4 text-primary" />도우미</span>
        <Tooltip v-if="stateWord && run">
          <TooltipTrigger as-child>
            <span
              class="inline-flex h-5 cursor-default items-center gap-1 rounded-md px-[7px] text-meta font-semibold whitespace-nowrap"
              :class="stateWord.tone"
              tabindex="0"
              data-helper-status
            >
              <LoaderCircle v-if="run.status === 'running'" class="size-3 animate-spin" />
              <Hand v-else-if="run.status === 'asking'" class="size-3" />
              {{ stateWord.word }}<template v-if="run.status === 'running'"> · {{ run.step_now }}/{{ stepCount }}</template>
            </span>
          </TooltipTrigger>
          <TooltipContent v-if="run.error" class="max-w-[320px]">{{ run.error }}</TooltipContent>
        </Tooltip>
        <span class="ml-auto flex items-center gap-1">
          <Tooltip v-if="run && !isRunning && api.settingsCard">
            <TooltipTrigger as-child>
              <Button
                type="button"
                variant="quiet"
                size="icon-sm"
                aria-label="실행 설정"
                :aria-pressed="settingsOpen"
                data-action="helper-settings"
                @click="settingsOpen ? (settingsOpen = false) : openSettings()"
              >
                <SlidersHorizontal />
              </Button>
            </TooltipTrigger>
            <TooltipContent>실행 설정</TooltipContent>
          </Tooltip>
          <Button
            v-if="isRunning"
            type="button"
            variant="outline"
            size="sm"
            :disabled="busy !== null || run?.stop_requested"
            data-action="helper-stop"
            @click="helper.stop"
          >
            <Pause />{{ run?.stop_requested ? '멈추는 중' : '멈추기' }}
          </Button>
          <Button
            v-else-if="!loading && llmConnected && (run || !api.settingsCard)"
            type="button"
            :variant="run ? 'outline' : 'default'"
            size="sm"
            :disabled="busy !== null"
            data-action="helper-start"
            @click="helper.start"
          >
            <LoaderCircle v-if="busy === 'start'" class="animate-spin" />
            <RotateCcw v-else-if="run" /><Play v-else />{{ run ? '다시 시작' : '시작' }}
          </Button>
          <Button
            v-else-if="!loading && !llmConnected"
            type="button"
            variant="outline"
            size="sm"
            data-action="connection-settings"
            @click="openConnectionSettings"
          >
            <Plug />연결 설정
          </Button>
          <Tooltip v-if="!isBeside">
            <TooltipTrigger as-child>
              <Button type="button" variant="quiet" size="icon-sm" aria-label="레일로 접기" data-action="helper-fold" @click="setHelperFolded(true)">
                <PanelRightClose />
              </Button>
            </TooltipTrigger>
            <TooltipContent>레일로 접기 · Esc</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger as-child>
              <Button type="button" variant="quiet" size="icon-sm" aria-label="닫기" @click="emit('close')"><X /></Button>
            </TooltipTrigger>
            <TooltipContent>닫기 · ⌘J</TooltipContent>
          </Tooltip>
        </span>
      </div>
      <div v-if="run" class="flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-muted-foreground" data-helper-facts>
        <span class="inline-flex items-center gap-1"><Pencil class="size-3" />바꿈 <b class="font-semibold text-foreground">{{ fmt(run.changed) }}</b></span>
        <span class="inline-flex items-center gap-1"><Hand class="size-3" />보류 <b class="font-semibold" :class="run.held ? 'text-warning-ink' : 'text-foreground'">{{ fmt(run.held) }}</b></span>
        <span>LLM <b class="font-semibold text-foreground">{{ fmt(run.llm_tokens) }}</b></span>
        <span>Jev <b class="font-semibold text-foreground">{{ fmt(run.jev_calls) }}</b></span>
        <span class="tabular-nums">{{ elapsed }}</span>
        <span class="ml-auto truncate font-mono text-[11px]" :title="run.model ?? ''">{{ run.model }}</span>
      </div>
      <div v-if="actionError" class="flex items-start gap-1.5 text-ui" role="alert">
        <TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-danger" />
        <span class="text-danger-ink">{{ actionError }}</span>
      </div>
    </div>

    <div v-if="run" class="flex items-center px-4 pt-2.5 pb-0.5" aria-label="진행">
      <template v-for="(step, index) in run.steps" :key="step.key">
        <span
          class="grid size-[22px] shrink-0 place-items-center rounded-full text-[11px] font-bold"
          :class="dotTone(step)"
          :title="step.title"
        >
          <template v-if="step.status === 'done'">✓</template><template v-else>{{ step.no }}</template>
        </span>
        <span
          v-if="index < run.steps.length - 1"
          class="h-0.5 flex-1"
          :class="step.status === 'done' ? 'bg-success/45' : 'bg-border'"
        />
      </template>
    </div>

    <div ref="body" class="flex-1 overflow-y-auto px-4 pt-2 pb-5">
      <div v-if="loading" class="space-y-2 pt-2" aria-busy="true">
        <Skeleton v-for="index in 4" :key="index" class="h-10 w-full rounded-[10px]" />
      </div>

      <div v-else-if="loadError" class="flex items-center gap-2 pt-4 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="text-danger-ink">{{ loadError }}</span>
        <Button variant="outline" size="sm" @click="helper.load">다시 불러오기</Button>
      </div>

      <div v-else-if="!run" class="pt-3" data-helper-empty>
        <component
          :is="api.settingsCard"
          v-if="api.settingsCard"
          class="mb-4"
          :dataset-id="datasetId"
          :startable="true"
          :can-start="llmConnected && busy === null"
          :closable="false"
          @start="helper.start"
          @summary="planValues = $event"
        />
        <dl class="m-0 grid grid-cols-[52px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-ui">
          <dt class="text-muted-foreground">하는 일</dt>
          <dd class="m-0">진단 읽기 → 규칙 · Jev · LLM으로 고치기</dd>
          <dt class="text-muted-foreground">반영</dt>
          <dd class="m-0">바로 · 단계마다 되돌리기 · 영구 삭제 없음</dd>
          <dt class="text-muted-foreground">연결</dt>
          <dd class="m-0 flex items-center gap-3">
            <span class="inline-flex items-center gap-1.5">
              <span class="size-1.5 rounded-full" :class="llmConnected ? 'bg-success' : 'ring-[1.5px] ring-subtle-foreground ring-inset'" />LLM
            </span>
            <span class="inline-flex items-center gap-1.5">
              <span class="size-1.5 rounded-full" :class="jevConnected ? 'bg-success' : 'ring-[1.5px] ring-subtle-foreground ring-inset'" />Jev
            </span>
          </dd>
        </dl>
        <ol class="m-0 mt-4 grid list-none gap-1 p-0">
          <li
            v-for="(planStep, index) in planSteps"
            :key="planStep.title"
            class="flex h-8 items-center gap-2 rounded-lg px-2.5 text-ui"
            :class="planStep.permission ? 'text-warning-ink' : 'text-muted-foreground'"
          >
            <span class="w-4 text-meta font-bold text-subtle-foreground">{{ index }}</span>
            <Lock v-if="planStep.permission" class="size-3.5" />{{ planStep.title }}
            <span
              v-if="planStep.key && planValues[planStep.key]"
              class="ml-auto truncate text-meta text-muted-foreground"
              :data-plan-value="planStep.key"
            >{{ planValues[planStep.key] }}</span>
          </li>
        </ol>
      </div>

      <template v-else>
        <component
          :is="api.settingsCard"
          v-if="api.settingsCard && settingsOpen && !isRunning"
          class="mt-1 mb-3"
          :dataset-id="datasetId"
          :startable="false"
          :can-start="false"
          :closable="true"
          @close="settingsOpen = false"
          @summary="planValues = $event"
        />
        <template v-for="(step, stepIndex) in run.steps" :key="step.key">
        <button
          v-if="startsStage(stepIndex) && step.stage !== undefined"
          type="button"
          class="mt-3.5 mb-1 flex w-full items-center gap-2 text-left text-ui font-[650]"
          :class="stageSummary.get(step.stage)?.done ? 'cursor-pointer' : 'cursor-default'"
          :data-helper-stage="step.stage"
          @click="stageSummary.get(step.stage)?.done && toggleStage(step.stage)"
        >
          <span
            class="grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-bold"
            :class="stageSummary.get(step.stage)?.done ? 'bg-success/16 text-success-ink' : 'bg-foreground text-background'"
          >
            <template v-if="stageSummary.get(step.stage)?.done">✓</template>
            <Sparkles v-else-if="step.stage_icon" class="size-3" />
            <template v-else>{{ step.stage }}</template>
          </span>
          {{ step.stage_title }}
          <span v-if="stageSummary.get(step.stage)?.done" class="text-meta font-medium text-success-ink">
            끝<template v-if="stageSummary.get(step.stage)?.changed"> · 바꿈 {{ fmt(stageSummary.get(step.stage)?.changed ?? 0) }}</template>
          </span>
          <span class="h-px flex-1 bg-border" />
          <ChevronRight
            v-if="stageSummary.get(step.stage)?.done"
            class="size-3.5 text-subtle-foreground transition-transform"
            :class="openStages.has(step.stage) && 'rotate-90'"
          />
        </button>
        <div
          v-if="!isFolded(step)"
          class="my-2 overflow-hidden rounded-[10px] border"
          :class="[
            step.status === 'running' && 'border-primary/45 shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_10%,transparent)]',
            step.status === 'ask' && 'border-warning/60',
            step.status === 'blocked' && 'border-danger/50',
            ['todo', 'locked', 'skipped'].includes(step.status) ? 'border-dashed bg-transparent' : 'bg-card',
          ]"
          :data-helper-step="step.key"
        >
          <button
            type="button"
            class="grid min-h-10 w-full grid-cols-[16px_14px_minmax(0,1fr)_auto_14px] items-center gap-2 px-3 py-1 text-left"
            :class="eventsByStep.get(step.no)?.length ? 'cursor-pointer' : 'cursor-default'"
            @click="toggle(step)"
          >
            <component :is="statusIcon(step.status).icon" class="size-[15px]" :class="statusIcon(step.status).tone" />
            <span class="text-[11px] font-bold text-muted-foreground">{{ step.no }}</span>
            <span class="flex min-w-0 flex-wrap items-center gap-1.5">
              <span
                class="font-semibold whitespace-nowrap"
                :class="['todo', 'locked', 'skipped'].includes(step.status) && 'font-medium text-muted-foreground'"
              >{{ step.title }}</span>
              <span
                v-for="chip in factChips(step)"
                :key="chip.key"
                class="inline-flex h-[18px] items-center gap-[3px] rounded-[5px] bg-muted px-1.5 text-[11px] font-semibold whitespace-nowrap text-muted-foreground"
                :class="chip.tone"
              >
                <component :is="chip.icon" v-if="chip.icon" class="size-[11px]" />{{ chip.text }}
              </span>
            </span>
            <span v-if="step.delta" class="inline-flex items-center gap-1 text-meta whitespace-nowrap">
              <s class="text-subtle-foreground">{{ step.delta[0] }}</s>→<b class="font-semibold" :class="GRADES[deltaGrade(step)].textClass">{{ step.delta[1] }}</b>
            </span>
            <span
              v-else-if="step.plan"
              class="inline-flex h-[18px] items-center rounded-[5px] px-1.5 text-[11px] font-semibold whitespace-nowrap"
              :class="step.status === 'blocked' ? 'bg-danger/10 text-danger-ink' : 'bg-muted text-muted-foreground'"
            >{{ step.plan }}</span>
            <span v-else />
            <ChevronRight
              v-if="eventsByStep.get(step.no)?.length"
              class="size-3.5 text-subtle-foreground transition-transform"
              :class="openSteps.has(step.key) && 'rotate-90'"
            />
            <span v-else />
          </button>
          <div v-if="openSteps.has(step.key) && eventsByStep.get(step.no)?.length" class="border-t px-3 pt-1 pb-2.5">
            <HelperEvent
              v-for="event in eventsByStep.get(step.no)"
              :key="event.id"
              :event="event"
              :dataset-id="datasetId"
              :hold-route="api.holdRoute"
              :undone="undone.has(event.id)"
              :can-undo="!isRunning && busy === null"
              :asking="run.status === 'asking' && event.id === lastPermissionId"
              :busy="busy !== null"
              @undo="helper.undo"
              @answer="helper.answer"
              @settings="openSettings"
            />
            <div v-if="step.status === 'running'" class="mt-2 ml-6 flex items-center gap-2 text-ui text-muted-foreground">
              <span class="inline-flex gap-[3px]">
                <span class="size-[5px] animate-pulse rounded-full bg-primary/60" />
                <span class="size-[5px] animate-pulse rounded-full bg-primary/60 [animation-delay:0.2s]" />
                <span class="size-[5px] animate-pulse rounded-full bg-primary/60 [animation-delay:0.4s]" />
              </span>
              생각 중
            </div>
          </div>
        </div>
        </template>
        <HelperLeft
          v-if="showsLeft"
          :dataset-id="datasetId"
          :api="api"
          :disabled="isRunning || busy !== null"
          @fix="helper.fix"
        />
      </template>
    </div>

    <div v-if="run" class="flex min-h-11 items-center gap-2 border-t bg-muted/35 py-1.5 pr-3 pl-4 text-ui text-muted-foreground">
      <span>바뀐 것 <b class="font-semibold text-foreground">{{ fmt(run.changed) }}</b></span>
      <Button
        type="button"
        variant="quiet"
        size="sm"
        class="ml-auto"
        :disabled="isRunning || busy !== null || run.changed === 0"
        data-action="helper-undo-all"
        @click="helper.undo()"
      >
        <Undo2 />이번 실행 전부 되돌리기
      </Button>
    </div>
  </aside>
  </Transition>
  </div>
</template>
