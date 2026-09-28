<!--
  종합 상태 카드 (진단 탭 맨 위):
    머리   종합 상태(준비 안 됨 · 확인 필요 · 준비됨) · 심각 n · 주의 n · 통과 n · 검사 n종(글자 5 · 뜻 3) · 대상 · 진단 시각
           [<가장 급한 검사> n건 보기] · [모든 문장 보기]
    잠김   한 줄: 다음 할 일(LLM 미연결) · jev 평가(라벨 불일치 · 뜻 모호, 준비 중) · [연결 설정]
  종합 상태는 새 점수를 만들지 않고 검사 등급만 센다: 심각이 하나라도 있으면 준비 안 됨, 주의만 있으면 확인 필요.
  뜻 검사는 뜻 분석이 있을 때만 센다(없으면 검사 5종).
  검사 표는 이 카드 아래의 글자 검사 · 뜻 검사 카드(CheckGroup)에 있다.
  카드가 좁으면(도우미 창을 옆에 둘 때) 머리가 두 줄이 되고 주 버튼 줄이 한 줄 전체를 쓴다. 폭은 카드 폭으로 잰다(@container).
-->
<script setup lang="ts">
import { ArrowRight, Gavel, ListChecks, Lock, Plug } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { openConnectionSettings } from '@/system/connections/connections'
import { dateTimeText, fmt } from '@/system/format'
import { isConnected } from '@/system/readiness'
import { Button } from '@/system/ui/button'

import { CHECK_WORDS, GRADE_ORDER, GRADES, overallStatus, type Check } from '@/modules/classification/diagnosis/checks'
import type { OverviewRead } from '@/modules/classification/types'

const props = defineProps<{
  overview: OverviewRead
  // 글자 검사 + 뜻 검사 (심각 → 주의 → 통과로 섞지 않아도 된다)
  checks: Check[]
  // 그 가운데 뜻 검사 수 (뜻 분석이 없으면 0)
  meaningCount: number
}>()

const route = useRoute()

// Jev가 연결됐는지 (/readyz). 연결돼도 jev 평가는 아직 만들지 않아 잠겨 있다.
const jevConnected = computed(() => isConnected('jev'))

const overall = computed(() => overallStatus(props.checks))
const textCount = computed(() => props.checks.length - props.meaningCount)

// 주 버튼: 통과하지 못한 검사 가운데 가장 급한 것 (심각 먼저, 같으면 처음 순서)
const firstProblem = computed(() => {
  const open = props.checks.filter((check) => check.grade !== 'good' && check.view)
  return open.sort((a, b) => GRADES[a.grade].rank - GRADES[b.grade].rank)[0] ?? null
})
</script>

<template>
  <section class="@container card overflow-hidden" aria-label="종합 상태">
    <div class="flex flex-wrap items-center gap-x-8 gap-y-4 border-b px-6 py-5">
      <div class="min-w-0">
        <div class="caps">종합 상태</div>
        <div class="mt-1 flex items-center gap-2.5" :class="GRADES[overall.grade].textClass">
          <component :is="GRADES[overall.grade].icon" class="size-7" :class="GRADES[overall.grade].iconClass" />
          <span class="text-kpi font-bold tracking-[-0.03em] whitespace-nowrap" data-overall>{{ overall.label }}</span>
        </div>
      </div>

      <div class="flex divide-x" aria-label="등급별 검사 수">
        <div v-for="grade in GRADE_ORDER" :key="grade" class="px-5 first:pl-0">
          <div
            class="flex items-center gap-1.5 text-ui"
            :class="overall.counts[grade] ? 'text-foreground' : 'text-muted-foreground'"
          >
            <component
              :is="GRADES[grade].icon"
              class="size-3.5"
              :class="overall.counts[grade] ? GRADES[grade].iconClass : 'text-subtle-foreground'"
            />
            {{ GRADES[grade].name }}
          </div>
          <div
            class="text-kpi font-semibold tracking-[-0.02em]"
            :class="overall.counts[grade] ? GRADES[grade].textClass : 'text-subtle-foreground'"
          >
            {{ overall.counts[grade] }}
          </div>
        </div>
      </div>

      <div class="hidden w-px self-stretch bg-border @min-[56rem]:block" />
      <dl class="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-ui">
        <dt class="text-muted-foreground">검사</dt>
        <dd class="font-semibold" data-check-count>
          {{ checks.length }}종<template v-if="meaningCount"> · 글자 {{ textCount }} · 뜻 {{ meaningCount }}</template>
        </dd>
        <dt class="text-muted-foreground">대상</dt>
        <dd class="font-semibold">학습 포함 {{ fmt(overview.included) }}건</dd>
        <dt class="text-muted-foreground">진단</dt>
        <dd class="font-semibold">{{ dateTimeText(overview.computed_at) }}</dd>
      </dl>

      <div class="ml-auto flex flex-wrap gap-2 @max-[51.25rem]:ml-0 @max-[51.25rem]:w-full">
        <Button v-if="firstProblem?.view" size="lg" class="@max-[51.25rem]:flex-1" as-child>
          <RouterLink :to="firstProblem.view.to" data-view="primary">
            <component :is="CHECK_WORDS[firstProblem.key].icon" />
            {{ CHECK_WORDS[firstProblem.key].name }} {{ fmt(firstProblem.view.count) }}건 보기<ArrowRight />
          </RouterLink>
        </Button>
        <Button :variant="firstProblem ? 'ghost' : 'outline'" size="lg" as-child>
          <RouterLink :to="{ name: 'classification-data', params: { datasetId: route.params.datasetId } }">
            모든 문장 보기
          </RouterLink>
        </Button>
      </div>
    </div>

    <div
      class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1.5 bg-[color-mix(in_oklab,var(--muted)_45%,var(--card))] py-1.5 pr-3.5 pl-6 text-meta text-muted-foreground"
      data-locked="next-and-jev"
    >
      <span class="inline-flex items-center gap-1"><Lock class="size-3.5" />잠김</span>
      <span class="h-4 w-px bg-border" />
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap">
        <ListChecks class="size-3.5" /><b class="text-ui font-semibold text-foreground">다음 할 일</b>
        · LLM
        <span class="size-1.5 rounded-full ring-[1.5px] ring-subtle-foreground ring-inset" />미연결
      </span>
      <span class="text-border-strong">·</span>
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap">
        <Gavel class="size-3.5" /><b class="text-ui font-semibold text-foreground">jev 평가</b>
        · 라벨 불일치 · 뜻 모호 · Jev
        <span
          class="size-1.5 rounded-full"
          :class="jevConnected ? 'bg-success' : 'ring-[1.5px] ring-subtle-foreground ring-inset'"
        />{{ jevConnected ? '연결됨' : '미연결' }} · 준비 중
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        class="ml-auto"
        data-action="connection-settings"
        @click="openConnectionSettings"
      >
        <Plug />연결 설정
      </Button>
    </div>
  </section>
</template>
