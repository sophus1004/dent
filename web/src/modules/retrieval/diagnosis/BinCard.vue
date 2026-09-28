<!--
  분포 카드 한 장 (진단 탭 아래): 이름 · 사실 한 줄, 칸마다 이름 · 막대 · 수 · 몫.
  막대 색은 칸의 tone(보통 · 주의 · 심각 · 흐림). 모든 칸이 0이면 흐린 '없음'.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { fmt } from '@/system/format'

import type { BinRead } from '@/modules/retrieval/types'

const props = defineProps<{
  title: string
  bins: BinRead[]
}>()

defineSlots<{
  facts?(): unknown
}>()

const TONE_CLASS = { normal: 'bg-primary/70', warn: 'bg-warning', bad: 'bg-danger', muted: 'bg-subtle-foreground/45' }
const COUNT_TONE = { normal: '', warn: 'text-warning-ink', bad: 'text-danger-ink', muted: 'text-muted-foreground' }

const total = computed(() => props.bins.reduce((sum, bin) => sum + bin.count, 0))
const top = computed(() => Math.max(1, ...props.bins.map((bin) => bin.count)))

function share(count: number): string {
  if (!total.value) return '—'
  const percent = (count / total.value) * 100
  return `${percent < 10 && percent > 0 ? percent.toFixed(1) : Math.round(percent)}%`
}
</script>

<template>
  <section class="card overflow-hidden" :aria-label="title">
    <div class="flex min-h-11 flex-wrap items-center gap-x-2.5 gap-y-1 border-b px-4 py-1.5">
      <h2 class="text-body font-semibold whitespace-nowrap">{{ title }}</h2>
      <span class="ml-auto flex flex-wrap items-center gap-x-1.5 text-meta text-muted-foreground"><slot name="facts" /></span>
    </div>
    <div v-if="total" class="space-y-1.5 px-4 py-3">
      <div
        v-for="bin in bins"
        :key="bin.label"
        class="grid grid-cols-[88px_minmax(0,1fr)_56px_48px] items-center gap-2.5 text-ui"
      >
        <span class="truncate text-muted-foreground" :title="bin.label">{{ bin.label }}</span>
        <span class="h-2 overflow-hidden rounded-full bg-muted">
          <span
            class="block h-full rounded-full"
            :class="TONE_CLASS[bin.tone]"
            :style="{ width: `${bin.count ? Math.max(1.5, (bin.count / top) * 100) : 0}%` }"
          />
        </span>
        <span class="text-right font-semibold tabular-nums" :class="COUNT_TONE[bin.tone]">{{ fmt(bin.count) }}</span>
        <span class="text-right text-meta text-muted-foreground tabular-nums">{{ share(bin.count) }}</span>
      </div>
    </div>
    <div v-else class="flex h-16 items-center justify-center text-ui text-subtle-foreground">없음</div>
  </section>
</template>
