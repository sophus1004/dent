<!--
  고치는 쪽 태그 한 개: 규칙(초록) · Jev(보라) · LLM(호박) · 사람(회색). 검토의 결정 · 결정 기록이 쓴다.
-->
<script setup lang="ts">
import { WHO_WORDS } from '@/modules/generation/checks'
import type { Who } from '@/modules/generation/types'

defineProps<{
  who: Who
}>()
</script>

<template>
  <span
    class="inline-flex h-[19px] items-center gap-[3px] rounded-[5px] px-1.5 text-[11px] font-[650] whitespace-nowrap"
    :class="WHO_WORDS[who].tone"
    :data-who="who"
  >
    <component :is="WHO_WORDS[who].icon" class="size-[11px]" />{{ WHO_WORDS[who].name }}
  </span>
</template>
