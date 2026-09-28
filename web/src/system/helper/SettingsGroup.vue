<!-- 실행 설정 카드의 무리 하나: 아이콘 · 이름(예: 문서 나누기 · 허락) 머리 줄과 그 아래 칸들. 무리 사이에 선을 긋는다. -->
<script setup lang="ts">
import type { LucideIcon } from '@lucide/vue'

defineProps<{
  icon: LucideIcon
  title: string
}>()
</script>

<template>
  <div class="border-b px-3 pt-2 pb-2.5 last:border-b-0" data-settings-group>
    <div class="mb-1.5 flex items-center gap-1.5 text-meta font-semibold text-muted-foreground">
      <component :is="icon" class="size-3.5" />{{ title }}
    </div>
    <div class="grid gap-2">
      <slot />
    </div>
  </div>
</template>
