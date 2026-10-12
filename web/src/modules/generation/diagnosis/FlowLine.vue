<!--
  흐름 줄 한 줄 (진단 종합 상태 카드 안): 1 지시 ─ 응답 만들기 ─ 2 응답 ─ 비교 만들기 ─ 3 선호 ─ 내보내기.
    단계 칩   지나간 단계 '끝' · 지금 단계 '지금 · 문제 n' · 뒤 단계는 이름만. 누르면 그 단계 검사 카드를 고른다(select).
              3 선호는 아직 없다(다음 판): 흐리게 '예정'이고 누를 수 없다.
    넘어가기  가는 줄 위 이름. 응답 만들기 · 비교 만들기는 다음 판이라 '예정'. 지금 받는 데이터는 응답이 있어 입구가 2다.
  '지금'은 서버(overview.now_stage: 1 · 2 · 3 = 내보내기)가 정한다. 선호는 건너뛰고 응답 다음이 내보내기다.
  카드가 51.25rem보다 좁으면(도우미 창을 옆에 둘 때) 지금 칸만 이름을 보이고 나머지는 번호 · 아이콘만(올리면 이름),
  줄 아래 한 줄에 다음 칸의 이름을 둔다(@container, 카드 폭으로 잰다).
-->
<script setup lang="ts">
import { Check, Download, GitCompare, Sparkles, type LucideIcon } from '@lucide/vue'
import { computed } from 'vue'

const props = defineProps<{
  // 흐름에서 지금 단계: 1 지시 · 2 응답 · 3 내보내기
  now: number
  // 단계마다 문제가 남은 검사 수
  openCounts: Record<number, number>
  // 고른 단계 (검사 카드가 보이는 단계)
  selected: number
}>()

const emit = defineEmits<{
  select: [stage: number]
}>()

type When = 'done' | 'now' | 'next' | 'soon'

interface StagePart {
  kind: 'stage'
  key: number
  name: string
  // 아직 없는 단계 (다음 판)
  soon: boolean
}

interface MovePart {
  kind: 'move'
  key: string
  name: string
  icon: LucideIcon
  soon: boolean
}

// 흐름 순서: 단계와 넘어가기가 번갈아 선다. 서버의 now_stage 3은 '내보내기'다.
const FLOW: (StagePart | MovePart)[] = [
  { kind: 'stage', key: 1, name: '지시', soon: false },
  { kind: 'move', key: 'answer', name: '응답 만들기', icon: Sparkles, soon: true },
  { kind: 'stage', key: 2, name: '응답', soon: false },
  { kind: 'move', key: 'compare', name: '비교 만들기', icon: GitCompare, soon: true },
  { kind: 'stage', key: 3, name: '선호', soon: true },
  { kind: 'move', key: 'export', name: '내보내기', icon: Download, soon: false },
]

// 서버의 지금 단계가 흐름의 몇 번째 칸인지 (3 = 내보내기)
const nowIndex = computed(() =>
  props.now >= 3 ? FLOW.findIndex((part) => part.key === 'export') : FLOW.findIndex((part) => part.key === props.now),
)

const parts = computed(() =>
  FLOW.map((part, index) => {
    const when: When = part.soon ? 'soon' : index < nowIndex.value ? 'done' : index === nowIndex.value ? 'now' : 'next'
    if (part.kind === 'move') return { ...part, when }
    const open = props.openCounts[part.key] ?? 0
    const note = when === 'done' ? '끝' : when === 'now' ? `지금${open ? ` · 문제 ${open}` : ''}` : when === 'soon' ? '예정' : ''
    return { ...part, when, note }
  }),
)

// 단계 칩 모양
const CHIP: Record<When, string> = {
  done: 'border-border bg-card text-foreground',
  now: 'border-[color-mix(in_oklab,var(--primary)_55%,var(--border))] bg-accent text-foreground',
  next: 'border-border bg-card text-muted-foreground',
  soon: 'border-dashed border-border bg-card text-subtle-foreground',
}
const CHIP_NO: Record<When, string> = {
  done: 'bg-[color-mix(in_oklab,var(--success)_16%,var(--card))] text-success-ink',
  now: 'bg-primary text-primary-foreground',
  next: 'bg-muted text-muted-foreground',
  soon: 'bg-muted text-subtle-foreground',
}
const CHIP_NOTE: Record<When, string> = { done: 'text-success-ink', now: 'font-semibold text-accent-foreground', next: '', soon: '' }
const MOVE_TONE: Record<When, string> = {
  done: 'text-success-ink',
  now: 'text-foreground',
  next: 'text-muted-foreground',
  soon: 'text-subtle-foreground',
}

// 좁을 때 숨기는 것 (지금 칸이 아닌 칸의 이름)
const NARROW_HIDDEN = '@max-[51.25rem]:hidden'

// 좁을 때 흐름 줄 아래 한 줄: 지금 다음의 (예정이 아닌) 칸 이름
const narrowNote = computed(() => {
  const next = parts.value.slice(nowIndex.value + 1).find((part) => part.when !== 'soon')
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
          :disabled="part.soon"
          :title="part.when === 'now' ? undefined : part.name"
          :aria-pressed="part.key === selected"
          :aria-label="'note' in part && part.note ? `${part.name} ${part.note}` : part.name"
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
            v-if="'note' in part && part.note"
            class="text-meta"
            :class="[CHIP_NOTE[part.when], part.when === 'now' ? '' : NARROW_HIDDEN]"
          >{{ part.note }}</span>
        </button>
        <span
          v-else
          class="flex min-w-8 flex-1 items-center gap-2 px-2 text-meta @min-[45rem]:min-w-[7.5rem]"
          :class="MOVE_TONE[part.when]"
          :title="part.name"
          :data-move="part.key"
          :data-when="part.when"
        >
          <span
            class="h-px min-w-3 flex-1"
            :class="part.when === 'done' ? 'bg-[color-mix(in_oklab,var(--success)_45%,var(--border))]' : 'bg-border-strong'"
          />
          <span class="inline-flex items-center gap-1 font-[550] whitespace-nowrap" :class="{ 'font-[650]': part.when === 'now' }">
            <component :is="part.icon" class="size-[13px]" /><span :class="part.when === 'now' ? '' : NARROW_HIDDEN">{{ part.name }}</span>
            <span v-if="part.soon" class="ml-0.5 hidden text-[10.5px] font-[650] @min-[51.25rem]:inline">예정</span>
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
