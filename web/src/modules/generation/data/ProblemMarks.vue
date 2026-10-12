<!--
  대화 한 건의 문제 표시(데이터 탭 표 · 휴지통 표): 남은 문제마다 아이콘 하나(진단 등급 색), 올리면 검사 이름.
  같은 검사가 여러 자리에 있어도 한 번만 보인다. 너무 많으면 앞의 것만 보이고 +n. 문제가 없으면 흐린 —.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { GRADES } from '@/system/diagnosis/grades'

import { checkWords } from '@/modules/generation/checks'
import type { CheckKey, ConversationRead, Grade } from '@/modules/generation/types'

const props = defineProps<{
  conversation: ConversationRead
  // 검사마다 등급 (색). 모르면 주의 색
  grades: Partial<Record<CheckKey, Grade>>
}>()

// 한 줄에 보이는 아이콘 수
const MAX_ICONS = 4

const keys = computed(() => [...new Set(props.conversation.problems.map((problem) => problem.key))])

function tone(key: CheckKey): string {
  return GRADES[props.grades[key] === 'bad' ? 'bad' : 'warn'].textClass
}
</script>

<template>
  <span class="flex flex-wrap items-center gap-1" :aria-label="keys.map((key) => checkWords(key).name).join(' · ') || '문제 없음'">
    <span
      v-for="key in keys.slice(0, MAX_ICONS)"
      :key="key"
      class="inline-grid size-[22px] place-items-center rounded-md shadow-[inset_0_0_0_1px_var(--border-strong)]"
      :class="tone(key)"
      :title="checkWords(key).name"
      :data-problem="key"
    >
      <component :is="checkWords(key).icon" class="size-3.5" />
    </span>
    <span v-if="keys.length > MAX_ICONS" class="text-meta font-semibold text-muted-foreground">+{{ keys.length - MAX_ICONS }}</span>
    <span v-if="!keys.length" class="text-subtle-foreground">—</span>
  </span>
</template>
