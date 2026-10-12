<!--
  종합 상태 카드 (생성 진단 탭 맨 위, 분류 · 검색과 같은 틀):
    머리     종합 상태(준비 안 됨 · 확인 필요 · 준비됨) · 심각 n · 주의 n · 통과 n · 대화 n · 휴지통 n
             · 검사 n종 · 입구 n단계 · 진단 시각 · [<가장 급한 검사> n건 검토]
    흐름 줄  FlowLine 한 줄. 단계 칩을 누르면 아래 검사 카드가 그 단계로 바뀐다.
    도우미   LLM 연결 · 다음 할 일(지금 단계의 남은 검사 이름) · [도우미 열기](오른쪽 창을 연다)
  종합 상태는 새 점수를 만들지 않고 검사 등급만 센다(분류 · 검색과 같다).
  카드가 좁으면(도우미 창을 옆에 둘 때) 머리가 두 줄이 되고 주 버튼이 한 줄 전체를 쓴다. 폭은 카드 폭으로 잰다(@container).
-->
<script setup lang="ts">
import { ArrowRight, Bot, Plug } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { openConnectionSettings } from '@/system/connections/connections'
import { GRADE_ORDER, GRADES, overallStatus, type Check } from '@/system/diagnosis/grades'
import { isHelperOpen, setHelperOpen } from '@/system/helper/helperSignals'
import { isConnected } from '@/system/readiness'
import { dateTimeText, fmt } from '@/system/format'
import { Button } from '@/system/ui/button'

import { checkWords } from '@/modules/generation/checks'
import FlowLine from '@/modules/generation/diagnosis/FlowLine.vue'
import type { OverviewRead } from '@/modules/generation/types'

const props = defineProps<{
  overview: OverviewRead
  checks: Check[]
  // 흐름의 입구 단계
  entry: number
  // 고른 단계
  selected: number
}>()

const emit = defineEmits<{
  select: [stage: number]
}>()

const overall = computed(() => overallStatus(props.checks))
const llmConnected = computed(() => isConnected('llm'))

// 단계마다 통과하지 못한 검사 수 (흐름 줄의 '지금 · 문제 n')
const openCounts = computed(() => {
  const counts: Record<number, number> = {}
  for (const check of props.overview.checks) {
    if (check.grade !== 'good') counts[check.stage] = (counts[check.stage] ?? 0) + 1
  }
  return counts
})

// 주 버튼: 통과하지 못한 검사 가운데 가장 급한 것 (심각 먼저, 같으면 흐름 순서)
const firstProblem = computed(() => {
  const open = props.checks.filter((check) => check.grade !== 'good' && check.view)
  return [...open].sort((a, b) => GRADES[a.grade].rank - GRADES[b.grade].rank)[0] ?? null
})

// 3 선호가 있는지 (만들 데이터가 선호이거나 쌍이 있다)
const withPairs = computed(() => props.overview.goal === 'preference' || props.overview.pairs > 0)

// 다음 할 일: 지금 단계의 남은 검사 이름(둘까지). 응답 · 비교 만들기가 남았으면 그것, 남은 것이 없으면 내보내기
const nextTodo = computed(() => {
  const { now_stage: now, unanswered, unpaired } = props.overview
  if (now <= 2 && unanswered) return `응답 만들기 · 지시 ${fmt(unanswered)}`
  if (now === 3 && withPairs.value && unpaired && !props.overview.checks.some((c) => c.stage === 3 && c.grade !== 'good')) {
    return `비교 만들기 · 지시 ${fmt(unpaired)}`
  }
  if (now >= 4) return '내보내기'
  const names = props.overview.checks
    .filter((check) => check.stage === props.overview.now_stage && check.grade !== 'good')
    .map((check) => checkWords(check.key).name)
  return names.length ? names.slice(0, 2).join(' · ') + (names.length > 2 ? ` 외 ${names.length - 2}` : '') : '—'
})
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
      <div class="grid grid-cols-[auto_auto] gap-x-6" aria-label="대화 수">
        <div>
          <div class="text-ui text-muted-foreground">대화</div>
          <div class="text-kpi font-semibold tracking-[-0.02em]">{{ fmt(overview.conversations) }}</div>
        </div>
        <div>
          <div class="text-ui text-muted-foreground">휴지통</div>
          <div class="text-kpi font-semibold tracking-[-0.02em]" :class="overview.trashed ? '' : 'text-subtle-foreground'">
            {{ fmt(overview.trashed) }}
          </div>
        </div>
      </div>

      <div class="hidden w-px self-stretch bg-border @min-[56rem]:block" />
      <dl class="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-ui">
        <dt class="text-muted-foreground">검사</dt>
        <dd class="font-semibold" data-check-count>{{ checks.length }}종</dd>
        <dt class="text-muted-foreground">입구</dt>
        <dd class="font-semibold">{{ entry }}단계</dd>
        <dt class="text-muted-foreground">진단</dt>
        <dd class="font-semibold">{{ dateTimeText(overview.computed_at) }}</dd>
      </dl>

      <div class="ml-auto flex flex-wrap gap-2 @max-[51.25rem]:ml-0 @max-[51.25rem]:w-full">
        <Button v-if="firstProblem?.view" size="lg" class="@max-[51.25rem]:flex-1" as-child>
          <RouterLink :to="firstProblem.view.to" data-view="primary">
            <component :is="checkWords(firstProblem.key).icon" />
            {{ checkWords(firstProblem.key).name }} {{ fmt(firstProblem.view.count) }}건 검토<ArrowRight />
          </RouterLink>
        </Button>
      </div>
    </div>

    <FlowLine
      :now="overview.now_stage"
      :open-counts="openCounts"
      :selected="selected"
      :with-pairs="withPairs"
      :unanswered="overview.unanswered"
      :unpaired="overview.unpaired"
      @select="emit('select', $event)"
    />

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
