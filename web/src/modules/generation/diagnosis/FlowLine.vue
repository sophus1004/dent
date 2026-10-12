<!--
  흐름 줄 한 줄 (진단 종합 상태 카드 안): 1 지시 ─ 응답 만들기 ─ 2 응답 ─ 비교 만들기 ─ 3 선호 ─ 내보내기.
    단계 칩   지나간 단계 '끝' · 지금 단계 '지금 · 문제 n' · 뒤 단계는 이름만. 누르면 그 단계 검사 카드를 고른다(select).
              만들 데이터가 SFT이고 쌍이 없으면 3 선호는 흐리게 'SFT'이고 누를 수 없다(설정에서 선호를 고르면 붙는다).
    넘어가기  가는 줄 위 이름. 응답 만들기는 응답 없는 지시가 있으면 '지시 n · 허락', 비교 만들기는 선호인데 쌍 없는
              지시가 있으면 '지시 n · 허락'(도우미가 시험 · 비용을 보이고 묻는다). SFT면 비교 만들기도 흐리다.
  '지금'은 서버(overview.now_stage: 1 · 2 · 3 · 4 = 내보내기)가 정한다.
  카드가 51.25rem보다 좁으면(도우미 창을 옆에 둘 때) 지금 칸만 이름을 보이고 나머지는 번호 · 아이콘만(올리면 이름),
  줄 아래 한 줄에 다음 칸의 이름을 둔다(@container, 카드 폭으로 잰다).
-->
<script setup lang="ts">
import { Check, Download, GitCompare, Sparkles, type LucideIcon } from '@lucide/vue'
import { computed } from 'vue'

import { fmt } from '@/system/format'

const props = defineProps<{
  // 흐름에서 지금 단계: 1 지시 · 2 응답 · 3 선호 · 4 내보내기
  now: number
  // 단계마다 문제가 남은 검사 수
  openCounts: Record<number, number>
  // 고른 단계 (검사 카드가 보이는 단계)
  selected: number
  // 3 선호가 있는지 (만들 데이터가 선호이거나 쌍이 있다)
  withPairs: boolean
  // 응답 없는 지시 수 (응답 만들기의 할 일) · 쌍 없는 지시 수 (비교 만들기의 할 일)
  unanswered: number
  unpaired: number
}>()

const emit = defineEmits<{
  select: [stage: number]
}>()

// skip: 이 데이터셋에는 없는 칸 (SFT의 3 선호 · 비교 만들기)
type When = 'done' | 'now' | 'next' | 'skip'

interface StagePart {
  kind: 'stage'
  key: number
  name: string
}

interface MovePart {
  kind: 'move'
  key: string
  name: string
  icon: LucideIcon
}

// 흐름 순서: 단계와 넘어가기가 번갈아 선다. 서버의 now_stage 4는 '내보내기'다.
const FLOW: (StagePart | MovePart)[] = [
  { kind: 'stage', key: 1, name: '지시' },
  { kind: 'move', key: 'answer', name: '응답 만들기', icon: Sparkles },
  { kind: 'stage', key: 2, name: '응답' },
  { kind: 'move', key: 'compare', name: '비교 만들기', icon: GitCompare },
  { kind: 'stage', key: 3, name: '선호' },
  { kind: 'move', key: 'export', name: '내보내기', icon: Download },
]

// SFT면 없는 칸
const PAIR_PARTS = new Set<string | number>(['compare', 3])

// 서버의 지금 단계가 흐름의 몇 번째 칸인지 (4 = 내보내기)
const nowIndex = computed(() =>
  props.now >= 4 ? FLOW.findIndex((part) => part.key === 'export') : FLOW.findIndex((part) => part.key === props.now),
)

// 넘어가기 칸의 할 일 수 (허락을 받아 도우미가 하는 일)
const moveTodo = computed<Record<string, number>>(() => ({
  answer: props.unanswered,
  compare: props.withPairs ? props.unpaired : 0,
}))

const parts = computed(() =>
  FLOW.map((part, index) => {
    const isSkipped = !props.withPairs && PAIR_PARTS.has(part.key)
    const when: When = isSkipped ? 'skip' : index < nowIndex.value ? 'done' : index === nowIndex.value ? 'now' : 'next'
    if (part.kind === 'move') {
      const todo = moveTodo.value[part.key] ?? 0
      // 할 일이 남았으면 지나간 칸으로 그리지 않는다(허락을 기다리는 일).
      const moveWhen: When = when === 'done' && todo ? 'next' : when
      return { ...part, when: moveWhen, todo, note: todo ? `지시 ${fmt(todo)} · 허락` : '' }
    }
    const open = props.openCounts[part.key] ?? 0
    const note = when === 'done' ? '끝' : when === 'now' ? `지금${open ? ` · 문제 ${open}` : ''}` : when === 'skip' ? 'SFT' : ''
    return { ...part, when, todo: 0, note }
  }),
)

// 단계 칩 모양
const CHIP: Record<When, string> = {
  done: 'border-border bg-card text-foreground',
  now: 'border-[color-mix(in_oklab,var(--primary)_55%,var(--border))] bg-accent text-foreground',
  next: 'border-border bg-card text-muted-foreground',
  skip: 'border-dashed border-border bg-card text-subtle-foreground',
}
const CHIP_NO: Record<When, string> = {
  done: 'bg-[color-mix(in_oklab,var(--success)_16%,var(--card))] text-success-ink',
  now: 'bg-primary text-primary-foreground',
  next: 'bg-muted text-muted-foreground',
  skip: 'bg-muted text-subtle-foreground',
}
const CHIP_NOTE: Record<When, string> = { done: 'text-success-ink', now: 'font-semibold text-accent-foreground', next: '', skip: '' }
const MOVE_TONE: Record<When, string> = {
  done: 'text-success-ink',
  now: 'text-foreground',
  next: 'text-muted-foreground',
  skip: 'text-subtle-foreground',
}

// 좁을 때 숨기는 것 (지금 칸이 아닌 칸의 이름)
const NARROW_HIDDEN = '@max-[51.25rem]:hidden'

// 좁을 때 흐름 줄 아래 한 줄: 할 일이 남은 넘어가기, 없으면 지금 다음의 (없는 칸이 아닌) 칸 이름
const narrowNote = computed(() => {
  const pending = parts.value.find((part) => part.kind === 'move' && part.todo)
  if (pending) return `${pending.name} · ${pending.note}`
  const next = parts.value.slice(nowIndex.value + 1).find((part) => part.when !== 'skip')
  return next ? next.name : null
})
</script>

<template>
  <div class="border-t" data-flow-line>
    <div class="flex flex-wrap items-center gap-y-2 px-[18px] py-3">
      <template v-for="part in parts" :key="part.key">
        <button
          v-if="part.kind === 'stage'"
          type="button"
          class="inline-flex h-[34px] items-center gap-[7px] rounded-full border pr-[13px] pl-1.5 text-ui whitespace-nowrap transition-shadow disabled:cursor-default"
          :class="[
            CHIP[part.when],
            part.key === selected ? 'shadow-[0_0_0_2px_color-mix(in_oklab,var(--primary)_30%,transparent)]' : '',
            part.when === 'now' ? '' : '@max-[51.25rem]:pr-1.5',
          ]"
          :disabled="part.when === 'skip'"
          :title="part.when === 'now' ? undefined : part.name"
          :aria-pressed="part.key === selected"
          :aria-label="part.note ? `${part.name} ${part.note}` : part.name"
          :data-stage="part.key"
          :data-when="part.when"
          @click="emit('select', part.key)"
        >
          <span class="inline-grid size-[22px] place-items-center rounded-full text-[11.5px] font-bold" :class="CHIP_NO[part.when]">
            <Check v-if="part.when === 'done'" class="size-3" :stroke-width="3" />
            <template v-else>{{ part.key }}</template>
          </span>
          <b class="font-[650]" :class="part.when === 'now' ? '' : NARROW_HIDDEN">{{ part.name }}</b>
          <span
            v-if="part.note"
            class="text-meta"
            :class="[CHIP_NOTE[part.when], part.when === 'now' ? '' : NARROW_HIDDEN]"
          >{{ part.note }}</span>
        </button>
        <span
          v-else
          class="flex min-w-8 flex-1 items-center gap-2 px-2 text-meta @min-[45rem]:min-w-[7.5rem]"
          :class="MOVE_TONE[part.when]"
          :title="part.note ? `${part.name} · ${part.note}` : part.name"
          :data-move="part.key"
          :data-when="part.when"
        >
          <span
            class="h-px min-w-3 flex-1"
            :class="part.when === 'done' ? 'bg-[color-mix(in_oklab,var(--success)_45%,var(--border))]' : 'bg-border-strong'"
          />
          <span class="inline-flex items-center gap-1 font-[550] whitespace-nowrap" :class="{ 'font-[650]': part.when === 'now' }">
            <component :is="part.icon" class="size-[13px]" /><span :class="part.when === 'now' ? '' : NARROW_HIDDEN">{{ part.name }}</span>
            <span v-if="part.todo" class="ml-0.5 hidden text-[10.5px] font-[650] text-warning-ink @min-[51.25rem]:inline" data-move-todo>
              {{ part.note }}
            </span>
          </span>
          <span
            class="h-px min-w-3 flex-1"
            :class="part.when === 'done' ? 'bg-[color-mix(in_oklab,var(--success)_45%,var(--border))]' : 'bg-border-strong'"
          />
        </span>
      </template>
    </div>
    <div
      v-if="narrowNote"
      class="-mt-1 hidden items-center gap-1.5 px-[18px] pb-3 text-meta text-muted-foreground @max-[51.25rem]:flex"
      data-flow-note
    >
      다음 <b class="font-semibold text-accent-foreground">{{ narrowNote }}</b>
    </div>
  </div>
</template>
