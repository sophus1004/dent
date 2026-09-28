<!--
  길이 분포: 학습 포함 문장의 글자 수 막대 + y축 눈금(건) + x축 눈금(자) + 짧은 문장 기준선.
    머리   최소 n자 · 평균 n자 · 최대 n자 · 5자 미만 n건
    막대   진단 API의 length_histogram. 마지막 막대는 끝이 열려 있다(480+).
           기준(5자)보다 짧은 막대만 호박색, 나머지는 한 가지 색(--chart-bar).
    기준선 짧은 문장 기준 글자 수 자리에 호박색 점선과 '5자 기준'. 막대 안에서는 비례로 놓고,
           모든 문장이 기준보다 길면 왼쪽 끝에 선다.
  막대에 올리면 글자 수 범위 · 건수 · 비율이 뜬다.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { fmt } from '@/system/format'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { percentText } from '@/modules/classification/diagnosis/checks'
import type { HistogramBin, OverviewRead } from '@/modules/classification/types'

const props = defineProps<{
  overview: OverviewRead
}>()

// y축 눈금 수의 목표. 1 · 2 · 2.5 · 5 · 10 단위로 이만큼 되게 나눈다.
const TARGET_TICKS = 5
const NICE_STEPS = [1, 2, 2.5, 5, 10]

const bins = computed(() => props.overview.length_histogram)
const threshold = computed(() => props.overview.short.threshold)

// y축: 가장 높은 막대를 덮는 보기 좋은 눈금
const scale = computed(() => {
  const highest = Math.max(1, ...bins.value.map((bin) => bin.count))
  const rough = highest / TARGET_TICKS
  const power = 10 ** Math.floor(Math.log10(rough))
  const step = NICE_STEPS.map((multiple) => multiple * power).find((value) => value >= rough) ?? rough
  const top = Math.ceil(highest / step) * step
  const ticks: number[] = []
  for (let tick = 0; tick <= top + step / 1000; tick += step) ticks.push(tick)
  return { top, ticks }
})

// 머리의 최소 · 평균 · 최대 (라벨별 값을 모은다)
const stats = computed(() => {
  const withRecords = props.overview.labels.filter((label) => label.included > 0)
  if (!withRecords.length) return null
  const lengthSum = withRecords.reduce((sum, label) => sum + (label.avg_length ?? 0) * label.included, 0)
  return {
    min: Math.min(...withRecords.map((label) => label.min_length ?? 0)),
    max: Math.max(...withRecords.map((label) => label.max_length ?? 0)),
    average: lengthSum / props.overview.included,
  }
})

// 기준선 자리(%): 기준이 든 막대 안에서 비례로. 모든 막대가 기준보다 뒤면 0
const thresholdX = computed(() => {
  const list = bins.value
  const index = list.findIndex((bin) => bin.end === null || bin.end > threshold.value)
  if (index < 0) return 100
  const bin = list[index]
  const width = bin.end === null ? 0 : bin.end - bin.start
  const fraction = width ? Math.min(1, Math.max(0, (threshold.value - bin.start) / width)) : 0
  return ((index + fraction) / list.length) * 100
})

function height(count: number): string {
  return `${(count / scale.value.top) * 100}%`
}

function isShort(bin: HistogramBin): boolean {
  return bin.start < threshold.value
}

function rangeText(bin: HistogramBin): string {
  return bin.end === null ? `${bin.start}자 이상` : `${bin.start}–${bin.end - 1}자`
}
</script>

<template>
  <section class="card" aria-label="길이 분포">
    <div class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
      <h2 class="text-body font-semibold">길이 분포</h2>
      <div v-if="stats" class="ml-auto flex flex-wrap justify-end gap-x-3 text-ui text-muted-foreground">
        <span>최소 <b class="font-semibold text-foreground">{{ fmt(stats.min) }}자</b></span>
        <span>평균 <b class="font-semibold text-foreground">{{ stats.average.toFixed(1) }}자</b></span>
        <span>최대 <b class="font-semibold text-foreground">{{ fmt(stats.max) }}자</b></span>
        <span>
          {{ threshold }}자 미만
          <b class="font-semibold" :class="overview.short.records ? 'text-warning-ink' : 'text-foreground'">
            {{ fmt(overview.short.records) }}건
          </b>
        </span>
      </div>
    </div>

    <div v-if="bins.length" class="px-4 pt-7 pb-3">
      <div class="grid grid-cols-[44px_minmax(0,1fr)] grid-rows-[200px_22px_auto]">
        <div class="relative">
          <span
            v-for="tick in scale.ticks"
            :key="tick"
            class="absolute right-2 translate-y-1/2 text-caps whitespace-nowrap text-subtle-foreground"
            :style="{ bottom: height(tick) }"
          >
            {{ fmt(tick) }}
          </span>
          <span class="absolute -top-[22px] right-2 text-caps text-subtle-foreground">건</span>
        </div>

        <div class="relative border-b border-axis" data-histogram>
          <span
            v-for="tick in scale.ticks.slice(1)"
            :key="tick"
            class="absolute inset-x-0 h-px bg-grid"
            :style="{ bottom: height(tick) }"
          />
          <div class="absolute inset-0 flex items-end gap-0.5">
            <Tooltip v-for="bin in bins" :key="bin.start">
              <TooltipTrigger as-child>
                <div class="relative flex h-full flex-1 items-end rounded-t-[4px] hover:bg-muted/70">
                  <span
                    class="w-full rounded-t-[4px]"
                    :class="isShort(bin) ? 'bg-warning' : 'bg-chart-bar'"
                    :style="{ height: height(bin.count) }"
                  />
                </div>
              </TooltipTrigger>
              <TooltipContent>
                {{ rangeText(bin) }} · {{ fmt(bin.count) }}건 ·
                {{ percentText(overview.included ? bin.count / overview.included : 0) }}
              </TooltipContent>
            </Tooltip>
          </div>
          <span
            class="pointer-events-none absolute -top-1.5 bottom-0 w-0 border-l-[1.5px] border-dashed border-warning"
            :style="{ left: `${thresholdX}%` }"
            data-threshold-marker
          >
            <span
              class="absolute -top-[9px] left-1.5 rounded bg-card px-1 text-meta font-semibold whitespace-nowrap text-warning-ink"
            >
              {{ threshold }}자 기준
            </span>
          </span>
        </div>

        <div />
        <div class="flex gap-0.5">
          <span
            v-for="bin in bins"
            :key="bin.start"
            class="min-w-0 flex-1 pt-1 text-center text-caps whitespace-nowrap text-subtle-foreground"
          >
            {{ bin.end === null ? `${bin.start}+` : bin.start }}
          </span>
        </div>
        <div />
        <div class="pt-0.5 text-right text-caps text-subtle-foreground">글자 수 (자)</div>
      </div>
    </div>
    <div v-else class="flex h-40 items-center justify-center text-ui text-subtle-foreground">학습 포함 문장 0건</div>
  </section>
</template>
