<!--
  흐름 줄 한 줄 (진단 종합 상태 카드 안): 1 문서 ─ 질의 만들기 ─ 2 질의 ─ 오답 찾기 ─ 3 하드 네거티브 ─ 내보내기.
    단계 칩   지나간 단계 '끝' · 지금 단계 '지금 · 문제 n' · 뒤 단계는 이름만. 누르면 그 단계 검사 카드를 고른다(select).
    넘어가기  가는 줄 위 이름. 질의 만들기는 끝나기 전까지 '허락' 표시, 지금이면 서버의 짧은 글(예: 질의 0 · 허락).
  지나간 단계의 문제 기록은 보이지 않는다. '지금'은 서버(overview._flow)가 정한 current다.
  카드가 51.25rem보다 좁으면(도우미 창을 옆에 둘 때) 지금 칸만 이름을 보이고 나머지는 번호 · 아이콘만(올리면 이름),
  줄 아래 한 줄에 다음 칸(지금이 넘어가기면 그 이름과 글)을 둔다(@container, 카드 폭으로 잰다).
-->
<script setup lang="ts">
import { Check, Download, Pickaxe, Sparkles, type LucideIcon } from '@lucide/vue'
import { computed } from 'vue'

import type { FlowMoveKey, FlowRead } from '@/modules/retrieval/types'

const props = defineProps<{
  flow: FlowRead
  // 고른 단계 (검사 카드가 보이는 단계)
  selected: number
}>()

const emit = defineEmits<{
  select: [stage: number]
}>()

// 흐름 순서: 단계와 넘어가기가 번갈아 선다(백엔드 overview.FLOW_ORDER와 같다).
const FLOW_ORDER: (number | FlowMoveKey)[] = [1, 'generate', 2, 'mine', 3, 'export']

const MOVES: Record<FlowMoveKey, { name: string; icon: LucideIcon }> = {
  generate: { name: '질의 만들기', icon: Sparkles },
  mine: { name: '오답 찾기', icon: Pickaxe },
  export: { name: '내보내기', icon: Download },
}

type When = 'done' | 'now' | 'next'

const nowIndex = computed(() => {
  const current = props.flow.current
  // 남은 일이 없으면 모두 지나간 것으로 본다.
  return current === null ? FLOW_ORDER.length : FLOW_ORDER.indexOf(current)
})

const parts = computed(() =>
  FLOW_ORDER.map((key, index) => {
    const when: When = index < nowIndex.value ? 'done' : index === nowIndex.value ? 'now' : 'next'
    if (typeof key === 'number') {
      const stage = props.flow.stages.find((item) => item.no === key)!
      const note = when === 'done' ? '끝' : when === 'now' ? `지금${stage.open_count ? ` · 문제 ${stage.open_count}` : ''}` : ''
      return { kind: 'stage' as const, key, when, name: stage.name, note }
    }
    const move = props.flow.moves.find((item) => item.key === key)!
    return {
      kind: 'move' as const,
      key,
      when,
      name: MOVES[key].name,
      icon: MOVES[key].icon,
      permission: key === 'generate' && when !== 'done' && move.state !== 'skip',
      text: when === 'now' ? move.text : '',
    }
  }),
)

// 단계 칩 모양
const CHIP = {
  done: 'border-border bg-card text-foreground',
  now: 'border-[color-mix(in_oklab,var(--primary)_55%,var(--border))] bg-accent text-foreground',
  next: 'border-border bg-card text-muted-foreground',
}
const CHIP_NO = {
  done: 'bg-[color-mix(in_oklab,var(--success)_16%,var(--card))] text-success-ink',
  now: 'bg-primary text-primary-foreground',
  next: 'bg-muted text-muted-foreground',
}
const CHIP_NOTE = { done: 'text-success-ink', now: 'font-semibold text-accent-foreground', next: '' }

// 좁을 때 숨기는 것 (지금 칸이 아닌 칸의 이름)
const NARROW_HIDDEN = '@max-[51.25rem]:hidden'

// 좁을 때 흐름 줄 아래 한 줄: 지금이 넘어가기면 그 이름과 글, 아니면 다음 칸의 이름
const narrowNote = computed(() => {
  const now = parts.value[nowIndex.value]
  if (!now) return null
  if (now.kind === 'move') {
    const move = props.flow.moves.find((item) => item.key === now.key)
    return { label: '지금', name: now.name, text: move?.text ?? '' }
  }
  const next = parts.value[nowIndex.value + 1]
  return next ? { label: '다음', name: next.name, text: '' } : null
})
</script>

<template>
  <div class="border-t" data-flow-line>
    <div class="flex flex-wrap items-center gap-y-2 px-[18px] py-3">
      <template v-for="part in parts" :key="part.key">
        <button
          v-if="part.kind === 'stage'"
          type="button"
          class="inline-flex h-[34px] items-center gap-[7px] rounded-full border pr-[13px] pl-1.5 text-ui whitespace-nowrap transition-shadow"
          :class="[
            CHIP[part.when],
            part.key === selected ? 'shadow-[0_0_0_2px_color-mix(in_oklab,var(--primary)_30%,transparent)]' : '',
            part.when === 'now' ? '' : '@max-[51.25rem]:pr-1.5',
          ]"
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
          <span v-if="part.note" class="text-meta" :class="[CHIP_NOTE[part.when], part.when === 'now' ? '' : NARROW_HIDDEN]">{{ part.note }}</span>
        </button>
        <span
          v-else
          class="flex min-w-8 flex-1 items-center gap-2 px-2 text-meta @min-[45rem]:min-w-[7.5rem]"
          :class="part.when === 'done' ? 'text-success-ink' : part.when === 'now' ? 'text-foreground' : 'text-muted-foreground'"
          :title="part.name"
          :data-move="part.key"
        >
          <span class="h-px min-w-3 flex-1" :class="part.when === 'done' ? 'bg-[color-mix(in_oklab,var(--success)_45%,var(--border))]' : 'bg-border-strong'" />
          <span class="inline-flex items-center gap-1 font-[550] whitespace-nowrap" :class="{ 'font-[650]': part.when === 'now' }">
            <component :is="part.icon" class="size-[13px]" /><span :class="part.when === 'now' ? '' : NARROW_HIDDEN">{{ part.name }}</span>
            <span v-if="part.permission" class="ml-0.5 hidden text-[10.5px] font-[650] text-warning-ink @min-[51.25rem]:inline">허락</span>
            <span v-if="part.text" class="ml-1 hidden font-[650] text-warning-ink @min-[51.25rem]:inline">{{ part.text }}</span>
          </span>
          <span class="h-px min-w-3 flex-1" :class="part.when === 'done' ? 'bg-[color-mix(in_oklab,var(--success)_45%,var(--border))]' : 'bg-border-strong'" />
        </span>
      </template>
    </div>
    <div
      v-if="narrowNote"
      class="hidden items-center gap-1.5 px-[18px] pb-3 -mt-1 text-meta text-muted-foreground @max-[51.25rem]:flex"
      data-flow-note
    >
      {{ narrowNote.label }} <b class="font-semibold text-accent-foreground">{{ narrowNote.name }}</b>
      <template v-if="narrowNote.text">· <span class="font-semibold text-warning-ink">{{ narrowNote.text }}</span></template>
    </div>
  </div>
</template>
