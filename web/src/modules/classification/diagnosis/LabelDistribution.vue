<!--
  라벨 분포 표: 라벨 · 건수 · 비율 · 평균 길이 · 뜻 쏠림 · 문제.
  - 라벨 칸이 남는 폭을 갖고, 숫자 칸은 오른쪽 맞춤으로 고르게 선다. 건수 막대는 두지 않는다(숫자로 견준다).
  - 라벨 균형 값은 머리에 등급색으로 (최다 · 최소 라벨은 건수로 본다).
  - 뜻 쏠림(뜻 분석이 있을 때만): 라벨 안 문장을 뜻으로 무리 지었을 때 가장 큰 무리의 비율. 작은 막대와 점선(기준),
    쏠린 라벨은 호박색. 올리면 무리 수 · 고르기 · 대표 문장. 문장이 적어 재지 못한 라벨은 —.
  - 문제: 통과하지 못한 검사의 이 라벨 몫을 아이콘 + 수로 (검사 표와 같은 아이콘 · 등급색, 올리면 이름).
    라벨 충돌 · 짧은 문장 · 중복 여분 · 오라벨 의심(대기)
  - 줄을 누르면 데이터 탭을 그 라벨(학습 포함)로 거른다.
  학습 포함 문장만 센다(진단 API의 labels). 뜻 쏠림 · 오라벨 의심은 다 만든 뜻 분석의 값이다.
-->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fmt } from '@/system/format'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import {
  CHECK_WORDS,
  dataQuery,
  GRADES,
  percentText,
  type CheckKey,
} from '@/modules/classification/diagnosis/checks'
import type { Grade, MapChecksRead, OverviewLabel, OverviewRead, SkewLabel } from '@/modules/classification/types'

const props = defineProps<{
  overview: OverviewRead
  // 다 만든 뜻 분석의 요약. 없으면 뜻 쏠림 칸을 빼고 오라벨 의심을 세지 않는다.
  summary: MapChecksRead | null
}>()

// 머리 칸 · 몸 칸. 숫자 칸 사이를 넉넉히 띄우고, 뜻 쏠림 · 문제 앞은 한 칸 더 띄워 숫자 묶음과 나눈다.
const TH_CLASS =
  'h-[34px] border-b bg-[color-mix(in_oklab,var(--muted)_60%,var(--card))] px-3 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground first:pl-[18px] last:pr-4'
const TD_CLASS = 'h-10 overflow-hidden border-b px-3 whitespace-nowrap group-last:border-b-0 first:pl-[18px] last:pr-4'
const GAP_LEFT = '!pl-7'

// 문제 칸의 한 조각
interface LabelMark {
  key: CheckKey
  grade: Grade
  name: string
  count: number
}

const route = useRoute()
const router = useRouter()

const labels = computed(() => props.overview.labels)
const hasRatio = computed(() => props.overview.balance.ratio !== null)
const mean = computed(() => (labels.value.length ? props.overview.included / labels.value.length : 0))

const skewByLabel = computed(() => new Map((props.summary?.skews ?? []).map((skew) => [skew.label_id, skew])))
const skewedCount = computed(() => (props.summary?.skews ?? []).filter((skew) => skew.is_skewed).length)
const skewLimit = computed(() => props.summary?.thresholds.skew_largest_share ?? 0)

// 통과하지 못한 검사마다 이 라벨의 몫
function marksOf(label: OverviewLabel): LabelMark[] {
  const { conflicts, short, duplicates } = props.overview
  const candidates: LabelMark[] = [
    { key: 'conflict', grade: conflicts.grade, name: CHECK_WORDS.conflict.name, count: label.conflict_records },
    { key: 'short', grade: short.grade, name: CHECK_WORDS.short.name, count: label.short_records },
    { key: 'duplicate', grade: duplicates.grade, name: '중복 여분', count: label.duplicate_extra },
  ]
  const summary = props.summary
  if (summary) {
    const open = summary.suspects.open_by_label[String(label.label_id)] ?? 0
    candidates.push({ key: 'suspect', grade: summary.grades.suspect, name: CHECK_WORDS.suspect.name, count: open })
  }
  return candidates.filter((mark) => mark.grade !== 'good' && mark.count > 0)
}

function skewOf(label: OverviewLabel): SkewLabel | null {
  return skewByLabel.value.get(label.label_id) ?? null
}

function openLabel(label: OverviewLabel): void {
  void router.push({
    name: 'classification-data',
    params: { datasetId: route.params.datasetId },
    query: dataQuery({ labelId: label.label_id }),
  })
}
</script>

<template>
  <section class="card overflow-hidden" aria-label="라벨 분포">
    <div class="flex min-h-11 flex-wrap items-center gap-x-2.5 gap-y-1 border-b py-2 pr-4 pl-[18px]">
      <h2 class="text-body font-semibold">라벨 분포</h2>
      <div class="flex flex-wrap items-center gap-x-2 text-ui text-muted-foreground">
        <span>학습 포함 <b class="font-semibold text-foreground">{{ fmt(overview.included) }}</b></span>
        <span class="text-border-strong">·</span>
        <span>라벨 <b class="font-semibold text-foreground">{{ labels.length }}</b></span>
        <span class="text-border-strong">·</span>
        <span>라벨당 평균 <b class="font-semibold text-foreground">{{ fmt(mean) }}건</b></span>
        <template v-if="hasRatio">
          <span class="text-border-strong">·</span>
          <span>
            라벨 균형
            <b class="font-semibold" :class="GRADES[overview.balance.grade].textClass">
              {{ (overview.balance.ratio ?? 0).toFixed(1) }}배
            </b>
          </span>
        </template>
        <template v-if="summary">
          <span class="text-border-strong">·</span>
          <span data-skew-summary>
            뜻 쏠림
            <b class="font-semibold" :class="GRADES[summary.grades.skew].textClass">
              {{ skewedCount }} / {{ summary.skews.length }}
            </b>
            <span class="ml-1 text-subtle-foreground">(가장 큰 무리 ≥ {{ percentText(skewLimit) }})</span>
          </span>
        </template>
      </div>
    </div>

    <div class="overflow-x-auto">
      <table class="w-full min-w-[860px] table-fixed border-separate border-spacing-0 text-ui">
        <colgroup>
          <col />
          <col class="w-[84px]" />
          <col class="w-[84px]" />
          <col class="w-[96px]" />
          <col v-if="summary" class="w-[200px]" />
          <col class="w-[236px]" />
        </colgroup>
        <thead>
          <tr>
            <th :class="TH_CLASS">라벨</th>
            <th :class="TH_CLASS" class="text-right">건수</th>
            <th :class="TH_CLASS" class="text-right">비율</th>
            <th :class="TH_CLASS" class="text-right">평균 길이</th>
            <th v-if="summary" :class="[TH_CLASS, GAP_LEFT]">뜻 쏠림</th>
            <th :class="[TH_CLASS, GAP_LEFT]">문제</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="label in labels"
            :key="label.label_id"
            class="group cursor-pointer [&>td]:transition-colors hover:[&>td]:bg-muted/65"
            tabindex="0"
            :data-label-id="label.label_id"
            @click="openLabel(label)"
            @keydown.enter="openLabel(label)"
          >
            <td :class="TD_CLASS">
              <span class="block truncate font-medium" :title="label.name">{{ label.name }}</span>
            </td>
            <td :class="TD_CLASS" class="text-right font-semibold">{{ fmt(label.included) }}</td>
            <td :class="TD_CLASS" class="text-right">{{ percentText(label.share) }}</td>
            <td :class="TD_CLASS" class="text-right">
              {{ label.avg_length === null ? '—' : `${label.avg_length.toFixed(1)}자` }}
            </td>

            <td v-if="summary" :class="[TD_CLASS, GAP_LEFT]">
              <Tooltip v-if="skewOf(label)">
                <TooltipTrigger as-child>
                  <span class="flex items-center gap-2.5" :data-skew="label.label_id">
                    <span class="relative h-2 min-w-0 flex-1 rounded-full bg-muted">
                      <span
                        class="absolute inset-y-0 left-0 rounded-full"
                        :class="skewOf(label)?.is_skewed ? 'bg-warning' : 'bg-chart-bar'"
                        :style="{ width: `${((skewOf(label)?.largest_share ?? 0) * 100).toFixed(2)}%` }"
                      />
                      <span
                        class="absolute -inset-y-[3px] w-0 border-l border-dashed border-muted-foreground opacity-70"
                        :style="{ left: `${(skewLimit * 100).toFixed(2)}%` }"
                      />
                    </span>
                    <span
                      class="w-12 shrink-0 text-right tabular-nums"
                      :class="skewOf(label)?.is_skewed && 'font-semibold text-warning-ink'"
                    >
                      {{ percentText(skewOf(label)?.largest_share ?? 0) }}
                    </span>
                  </span>
                </TooltipTrigger>
                <TooltipContent class="max-w-[360px]">
                  <div>
                    가장 큰 무리 {{ percentText(skewOf(label)?.largest_share ?? 0) }} · 무리
                    {{ skewOf(label)?.cluster_count }} · 고르기 {{ skewOf(label)?.evenness.toFixed(2) }}
                  </div>
                  <div v-for="(example, index) in skewOf(label)?.examples ?? []" :key="index" class="truncate">
                    “{{ example }}”
                  </div>
                </TooltipContent>
              </Tooltip>
              <span v-else class="text-subtle-foreground">—</span>
            </td>

            <td :class="[TD_CLASS, GAP_LEFT]">
              <span v-if="marksOf(label).length" class="flex items-center gap-3">
                <Tooltip v-for="mark in marksOf(label)" :key="mark.key">
                  <TooltipTrigger as-child>
                    <span class="inline-flex items-center gap-[3px] tabular-nums" :data-mark="mark.key">
                      <component
                        :is="CHECK_WORDS[mark.key].icon"
                        class="size-3.5"
                        :class="GRADES[mark.grade].iconClass"
                        :stroke-width="2.1"
                      />
                      <b class="font-[650]" :class="GRADES[mark.grade].textClass">{{ fmt(mark.count) }}</b>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{{ mark.name }} {{ fmt(mark.count) }}건</TooltipContent>
                </Tooltip>
              </span>
              <span v-else class="text-subtle-foreground">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
