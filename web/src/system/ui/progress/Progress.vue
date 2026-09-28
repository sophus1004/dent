<!--
  진행 막대. value는 0~1이고, null이면 얼마나 남았는지 모르는 상태라 막대 전체가 천천히 깜빡인다.
  막대 색은 푸른 회색(--chart-bar) 한 가지다.
-->
<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  // 0~1. 모르면 null
  value: number | null
  // 화면 읽기 프로그램이 읽을 이름. 예: '올리기'
  label: string
}>()

const percent = computed(() => (props.value === null ? null : Math.round(props.value * 100)))
</script>

<template>
  <div
    class="relative h-1.5 w-full overflow-hidden rounded-full bg-muted"
    role="progressbar"
    :aria-label="label"
    aria-valuemin="0"
    aria-valuemax="100"
    :aria-valuenow="percent ?? undefined"
  >
    <div v-if="percent === null" class="h-full w-full animate-pulse rounded-full bg-chart-bar/45" />
    <div
      v-else
      class="h-full rounded-full bg-chart-bar transition-[width] duration-200 ease-linear"
      :style="{ width: `${percent}%` }"
    />
  </div>
</template>
