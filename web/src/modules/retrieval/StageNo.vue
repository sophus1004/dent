<!--
  흐름 단계 번호 동그라미(1 문서 · 2 질의 · 3 하드 네거티브). 흐름 줄 · 입구 칩 · 홈 목록이 같이 쓴다.
  state가 done이면 채운 보라, now면 보라 테두리, 그 밖은 회색.
-->
<script setup lang="ts">
defineProps<{
  no: number
  size?: 'xs' | 'sm'
  state?: 'done' | 'now' | 'idle'
}>()
</script>

<template>
  <span
    class="inline-grid shrink-0 place-items-center rounded-full leading-none font-semibold tabular-nums"
    :class="[
      size === 'xs' ? 'size-4 text-[10px]' : 'size-5 text-meta',
      state === 'done'
        ? 'bg-primary text-primary-foreground'
        : state === 'now'
          ? 'bg-accent text-accent-foreground shadow-[inset_0_0_0_1.5px_var(--primary)]'
          : 'bg-muted text-muted-foreground shadow-[inset_0_0_0_1px_var(--border)]',
    ]"
  >{{ no }}</span>
</template>
