<!-- 실행 설정 카드의 칸 하나: 이름 · 고르기(모듈이 넣는다) · 아래 작은 어림 글(hint, 예: 청크 약 1,045). -->
<script setup lang="ts">
defineProps<{
  label: string
  hint?: string
}>()
</script>

<template>
  <div class="grid grid-cols-[76px_minmax(0,1fr)] items-center gap-x-2.5 gap-y-0.5 text-ui" data-settings-field>
    <span class="text-muted-foreground">{{ label }}</span>
    <span class="flex min-w-0 flex-wrap items-center gap-1.5"><slot /></span>
    <span v-if="hint" class="col-start-2 text-meta text-muted-foreground tabular-nums">{{ hint }}</span>
  </div>
</template>
