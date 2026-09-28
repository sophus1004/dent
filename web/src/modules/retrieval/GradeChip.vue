<!--
  판정 칩: 정답(등급 1~3, 초록 · 등급 2 이상이면 숫자) · 오답(등급 0, 회색) · 모름(판정 없음, 점선).
-->
<script setup lang="ts">
defineProps<{
  grade: number | null
}>()
</script>

<template>
  <span
    v-if="grade === null"
    class="inline-flex h-[22px] items-center rounded-[5px] px-1.5 text-meta font-semibold whitespace-nowrap text-subtle-foreground shadow-[inset_0_0_0_1px_var(--border)] [box-shadow:inset_0_0_0_1px_var(--border)]"
    data-grade="none"
  >
    모름
  </span>
  <span
    v-else-if="grade >= 1"
    class="inline-flex h-[22px] items-center gap-0.5 rounded-[5px] bg-[color-mix(in_oklab,var(--success)_14%,var(--card))] px-1.5 text-meta font-semibold whitespace-nowrap text-success-ink"
    :data-grade="grade"
  >
    정답<template v-if="grade > 1"> {{ grade }}</template>
  </span>
  <span
    v-else
    class="inline-flex h-[22px] items-center rounded-[5px] bg-muted px-1.5 text-meta font-semibold whitespace-nowrap text-muted-foreground"
    data-grade="0"
  >
    오답
  </span>
</template>
