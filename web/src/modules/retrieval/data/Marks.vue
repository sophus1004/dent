<!--
  문제 표시 칩들 (질의 · 문서 표의 표시 칸, 패널의 표시 줄): 아이콘 + 이름, 등급 색(빨강 · 호박).
  compact면 이름 없이 아이콘만 보이고 올리면 이름이 뜬다.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { MARKS } from '@/modules/retrieval/data/filters'

const props = defineProps<{
  marks: string[]
  compact?: boolean
}>()

// 심각을 앞에
const sorted = computed(() =>
  [...props.marks]
    .filter((mark) => MARKS[mark])
    .sort((a, b) => (MARKS[a].tone === MARKS[b].tone ? 0 : MARKS[a].tone === 'bad' ? -1 : 1)),
)
</script>

<template>
  <span
    v-for="mark in sorted"
    :key="mark"
    class="inline-flex h-[22px] items-center gap-1 rounded-[5px] text-meta font-semibold whitespace-nowrap"
    :class="[
      MARKS[mark].tone === 'bad' ? 'text-danger-ink' : 'text-warning-ink',
      compact ? 'px-1' : 'px-1.5 shadow-[inset_0_0_0_1px_var(--border)]',
    ]"
    :title="MARKS[mark].name"
    :data-mark="mark"
  >
    <component :is="MARKS[mark].icon" class="size-3.5" :class="MARKS[mark].tone === 'bad' ? 'text-danger' : 'text-warning'" />
    <span v-if="!compact">{{ MARKS[mark].name }}</span>
  </span>
</template>
