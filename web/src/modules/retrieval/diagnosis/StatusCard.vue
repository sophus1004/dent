<!--
  종합 상태 카드 (검색 진단 탭 맨 위):
    머리     종합 상태(준비 안 됨 · 확인 필요 · 준비됨) · 심각 n · 주의 n · 통과 n · Recall@10 · MRR@10(전 값)
             · 검사 n종 · 입구 n단계 · 진단 시각 · [<가장 급한 검사> n건 보기]
    흐름 줄  FlowLine 한 줄. 단계 칩을 누르면 아래 검사 카드가 그 단계로 바뀐다.
    뜻 분석  임베딩 · 질의 n · 문서 n · 모델 · 만든 시각 · [의미 지도] · 만들기 칸(AnalysisAction)
    도우미   LLM 연결 · 다음 할 일(지금 단계의 남은 검사만) · [도우미 시작](오른쪽 창을 연다)
  종합 상태는 새 점수를 만들지 않고 검사 등급만 센다(분류와 같다).
  카드가 좁으면(도우미 창을 옆에 둘 때) 머리가 두 줄이 되고 주 버튼이 한 줄 전체를 쓴다. 폭은 카드 폭으로 잰다(@container).
-->
<script setup lang="ts">
import { ArrowRight, Bot, ListOrdered, Plug, Sparkles, Target } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { openConnectionSettings } from '@/system/connections/connections'
import { GRADE_ORDER, GRADES, overallStatus, type Check } from '@/system/diagnosis/grades'
import { isHelperOpen, setHelperOpen } from '@/system/helper/helperSignals'
import { dateTimeText, fmt } from '@/system/format'
import { isConnected } from '@/system/readiness'
import { Button } from '@/system/ui/button'

import { checkWords } from '@/modules/retrieval/diagnosis/checks'
import FlowLine from '@/modules/retrieval/diagnosis/FlowLine.vue'
import type { Analysis } from '@/modules/retrieval/map/analysis'
import AnalysisAction from '@/modules/retrieval/map/AnalysisAction.vue'
import type { OverviewRead } from '@/modules/retrieval/types'

const props = defineProps<{
  datasetId: number
  overview: OverviewRead
  checks: Check[]
  analysis: Analysis
  // 고른 단계
  selected: number
}>()

const emit = defineEmits<{
  select: [stage: number]
}>()

// 넘어가기 이름 (다음 할 일에 쓴다)
const MOVE_NAMES = { generate: '질의 만들기', mine: '오답 찾기', export: '내보내기' } as const

const { state, progress, sending, actionError } = props.analysis

const overall = computed(() => overallStatus(props.checks))
const embeddingConnected = computed(() => isConnected('embedding'))
const llmConnected = computed(() => isConnected('llm'))

const kpi = computed(() => props.overview.kpi)

// 주 버튼: 통과하지 못한 검사 가운데 가장 급한 것 (심각 먼저, 같으면 흐름 순서)
const firstProblem = computed(() => {
  const open = props.checks.filter((check) => check.grade !== 'good' && check.view)
  return [...open].sort((a, b) => GRADES[a.grade].rank - GRADES[b.grade].rank)[0] ?? null
})

// 다음 할 일: 지금이 단계면 그 단계의 남은 검사 이름(둘까지), 넘어가기면 그 이름과 서버의 짧은 글
const nextTodo = computed(() => {
  const current = props.overview.flow.current
  if (current === null) return '없음 · 내보낼 수 있음'
  if (typeof current === 'string') {
    const move = props.overview.flow.moves.find((item) => item.key === current)
    return `${MOVE_NAMES[current]}${move ? ` · ${move.text}` : ''}`
  }
  const stageKeys = props.overview.checks
    .filter((check) => check.stage === current && check.grade !== 'good')
    .map((check) => checkWords(check.key).name)
  return stageKeys.length ? stageKeys.slice(0, 2).join(' · ') + (stageKeys.length > 2 ? ` 외 ${stageKeys.length - 2}` : '') : '—'
})

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(3)
}
</script>

<template>
  <section class="@container card overflow-hidden" aria-label="종합 상태">
    <div class="flex flex-wrap items-center gap-x-8 gap-y-4 px-6 py-5">
      <div class="min-w-0">
        <div class="caps">종합 상태</div>
        <div class="mt-1 flex items-center gap-2.5" :class="GRADES[overall.grade].textClass">
          <component :is="GRADES[overall.grade].icon" class="size-7" :class="GRADES[overall.grade].iconClass" />
          <span class="text-kpi font-bold tracking-[-0.03em] whitespace-nowrap" data-overall>{{ overall.label }}</span>
        </div>
      </div>

      <div class="flex divide-x" aria-label="등급별 검사 수">
        <div v-for="grade in GRADE_ORDER" :key="grade" class="px-5 first:pl-0">
          <div class="flex items-center gap-1.5 text-ui" :class="overall.counts[grade] ? 'text-foreground' : 'text-muted-foreground'">
            <component
              :is="GRADES[grade].icon"
              class="size-3.5"
              :class="overall.counts[grade] ? GRADES[grade].iconClass : 'text-subtle-foreground'"
            />
            {{ GRADES[grade].name }}
          </div>
          <div class="text-kpi font-semibold tracking-[-0.02em]" :class="overall.counts[grade] ? GRADES[grade].textClass : 'text-subtle-foreground'">
            {{ overall.counts[grade] }}
          </div>
        </div>
      </div>

      <div class="hidden w-px self-stretch bg-border @min-[56rem]:block" />
      <div class="grid grid-cols-[auto_auto] gap-x-6" aria-label="기준 검색 점수" data-kpi>
        <div>
          <div class="flex items-center gap-1.5 text-ui text-muted-foreground">
            <Target class="size-3.5" />Recall@10
            <span v-if="kpi?.baseline_recall_at_10 !== null && kpi?.baseline_recall_at_10 !== undefined" class="text-meta">
              · 전 {{ percent(kpi.baseline_recall_at_10) }}
            </span>
          </div>
          <div class="text-kpi font-semibold tracking-[-0.02em]" :class="kpi ? '' : 'text-subtle-foreground'">
            {{ percent(kpi?.recall_at_10) }}
          </div>
        </div>
        <div>
          <div class="flex items-center gap-1.5 text-ui text-muted-foreground">
            <ListOrdered class="size-3.5" />MRR@10
            <span v-if="kpi?.baseline_mrr_at_10 !== null && kpi?.baseline_mrr_at_10 !== undefined" class="text-meta">
              · 전 {{ percent(kpi.baseline_mrr_at_10) }}
            </span>
          </div>
          <div class="text-kpi font-semibold tracking-[-0.02em]" :class="kpi ? '' : 'text-subtle-foreground'">
            {{ percent(kpi?.mrr_at_10) }}
          </div>
        </div>
      </div>

      <div class="hidden w-px self-stretch bg-border @min-[56rem]:block" />
      <dl class="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-ui">
        <dt class="text-muted-foreground">검사</dt>
        <dd class="font-semibold" data-check-count>{{ checks.length }}종</dd>
        <dt class="text-muted-foreground">입구</dt>
        <dd class="font-semibold">{{ overview.flow.entry ? `${overview.flow.entry}단계` : '—' }}</dd>
        <dt class="text-muted-foreground">진단</dt>
        <dd class="font-semibold">{{ dateTimeText(overview.computed_at) }}</dd>
      </dl>

      <div class="ml-auto flex flex-wrap gap-2 @max-[51.25rem]:ml-0 @max-[51.25rem]:w-full">
        <Button v-if="firstProblem?.view" size="lg" class="@max-[51.25rem]:flex-1" as-child>
          <RouterLink :to="firstProblem.view.to" data-view="primary">
            <component :is="checkWords(firstProblem.key).icon" />
            {{ checkWords(firstProblem.key).name }} {{ fmt(firstProblem.view.count) }}건 보기<ArrowRight />
          </RouterLink>
        </Button>
      </div>
    </div>

    <FlowLine :flow="overview.flow" :selected="selected" @select="emit('select', $event)" />

    <div
      class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1.5 border-t bg-[color-mix(in_oklab,var(--muted)_45%,var(--card))] py-1.5 pr-3.5 pl-6 text-meta text-muted-foreground"
      data-analysis-line
    >
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap">
        <Sparkles class="size-3.5" /><b class="text-ui font-semibold text-foreground">뜻 분석</b>
        · 임베딩
        <span class="size-1.5 rounded-full" :class="embeddingConnected ? 'bg-success' : 'ring-[1.5px] ring-subtle-foreground ring-inset'" />
        {{ embeddingConnected ? '연결됨' : '미연결' }}
      </span>
      <span class="ml-auto flex flex-wrap items-center gap-2">
        <AnalysisAction
          :state="state"
          :progress="progress"
          :sending="sending"
          :action-error="actionError"
          :connected="embeddingConnected"
          facts
          rebuild
          @start="analysis.start"
          @cancel="analysis.cancel"
        />
        <Button v-if="state?.done" variant="outline" size="sm" as-child>
          <RouterLink :to="{ name: 'retrieval-data', params: { datasetId }, query: { view: 'map' } }">
            의미 지도<ArrowRight />
          </RouterLink>
        </Button>
        <Button v-if="!embeddingConnected" type="button" variant="outline" size="sm" @click="openConnectionSettings">
          <Plug />연결 설정
        </Button>
      </span>
    </div>

    <div
      class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1.5 border-t bg-[color-mix(in_oklab,var(--muted)_45%,var(--card))] py-1.5 pr-3.5 pl-6 text-meta text-muted-foreground"
      data-helper-line
    >
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap">
        <Bot class="size-3.5" /><b class="text-ui font-semibold text-foreground">도우미</b>
        · LLM
        <span class="size-1.5 rounded-full" :class="llmConnected ? 'bg-success' : 'ring-[1.5px] ring-subtle-foreground ring-inset'" />
        {{ llmConnected ? '연결됨' : '미연결' }}
      </span>
      <span class="text-border-strong">·</span>
      <span class="min-w-0 truncate">다음 할 일 · <b class="font-semibold text-foreground">{{ nextTodo }}</b></span>
      <Button
        v-if="llmConnected"
        type="button"
        variant="outline"
        size="sm"
        class="ml-auto"
        :aria-pressed="isHelperOpen"
        data-action="open-helper"
        @click="setHelperOpen(true)"
      >
        <Bot />도우미 열기
      </Button>
      <Button v-else type="button" variant="outline" size="sm" class="ml-auto" @click="openConnectionSettings">
        <Plug />연결 설정
      </Button>
    </div>
  </section>
</template>
