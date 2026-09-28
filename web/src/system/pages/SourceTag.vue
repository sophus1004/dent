<!--
  출처 태그: 내장(보라, 상자 아이콘) 또는 외부 공급자 이름(회색, 구름 아이콘). 홈의 모델 · 시스템 칸이 쓴다.
-->
<script setup lang="ts">
import { Cloud, Package } from '@lucide/vue'

defineProps<{
  // 태그 글자. 예: '내장', 'OpenAI'
  label: string
  // 내장이면 강조 색
  embedded: boolean
}>()
</script>

<template>
  <span
    class="inline-flex h-5 shrink-0 items-center gap-1 rounded-md px-1.5 text-meta font-semibold whitespace-nowrap"
    :class="
      embedded
        ? 'bg-accent text-accent-foreground shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--primary)_35%,transparent)]'
        : 'text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]'
    "
  >
    <component :is="embedded ? Package : Cloud" class="size-3" />{{ label }}
  </span>
</template>
