<!--
  등급 배지: 아이콘 + 등급 이름(심각 · 주의 · 통과). 바탕 없이 등급색 글자와 얇은 테두리.
  large면 종합 상태 옆처럼 조금 크게 그린다.
-->
<script setup lang="ts">
import { GRADES, type Grade } from '@/system/diagnosis/grades'

defineProps<{
  grade: Grade
  large?: boolean
}>()

// 등급마다 테두리 색
const RING_CLASS: Record<Grade, string> = {
  bad: 'shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--danger)_38%,transparent)]',
  warn: 'shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--warning)_45%,transparent)]',
  good: 'shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--success)_38%,transparent)]',
}
</script>

<template>
  <span
    class="inline-flex items-center font-semibold whitespace-nowrap"
    :class="[
      GRADES[grade].textClass,
      RING_CLASS[grade],
      large ? 'h-[26px] gap-1.5 rounded-[7px] px-2.5 text-ui' : 'h-5 gap-1 rounded-md px-[7px] text-meta',
    ]"
  >
    <component :is="GRADES[grade].icon" :class="[GRADES[grade].iconClass, large ? 'size-3.5' : 'size-3']" :stroke-width="2.2" />
    {{ GRADES[grade].name }}
  </span>
</template>
