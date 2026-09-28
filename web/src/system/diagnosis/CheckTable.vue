<!--
  검사 표(모든 모듈): 검사 한 줄마다 같은 칸 자리. 검사 카드들이 같이 써서 위아래로 칸이 맞는다. 낱말은 모듈이 words로 준다.
    등급 · 검사(ⓘ 정의) · 값 · 영향 · 조치 · →
  - 검사 이름 칸이 남는 폭을 갖고, 값 · 영향 · 조치 · →는 오른쪽에 고른 간격으로 모인다.
  - 기준(등급 경계)은 칸으로 두지 않고 ⓘ의 등급 사다리에서 본다.
  - 값은 오른쪽 맞춤의 큰 숫자 + 작은 단위(건 · 배 · % · 쌍)이고, 아래 작은 글자는 묶음 · 여분 · 최다/최소 같은 사실이다.
    작은 글자가 값 칸보다 길면 왼쪽(검사 이름 칸의 빈자리)으로 넘친다. 오른쪽의 영향 태그를 덮지 않게 하려는 것이다.
  - ⓘ에는 정의 · 영향 · 조치 · 분모와 등급 사다리 전체.
  - 통과한 줄은 낮게 두고 영향 · 조치를 비운다(할 일이 아니므로).
  - → 는 글자 없는 작은 버튼이다(올리면 '문장 16건 보기'). 데이터 탭 거르기 · 제안 탭 · 의미 지도로 간다.
  - 표가 62.5rem보다 좁으면 영향 칸을 빼고, 40rem보다 좁으면(도우미 창을 옆에 둘 때) 조치는 아이콘만 둔다(올리면 이름).
    폭은 표 자신의 폭을 rem으로 잰다(@container) — 브라우저 확대 · 글꼴 크기가 달라도 같은 규칙.
  pending에 검사 이름을 주면 아직 값이 없는 흐린 줄(— )을 그린다(뜻 분석 전).
-->
<script setup lang="ts">
import { ArrowRight, Info, Wrench } from '@lucide/vue'
import { RouterLink } from 'vue-router'

import { Button } from '@/system/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import GradeBadge from '@/system/diagnosis/GradeBadge.vue'
import { GRADES, type Check, type CheckWords } from '@/system/diagnosis/grades'

defineProps<{
  checks: Check[]
  // 검사 이름 → 고정 낱말 (모듈이 준다)
  words: Record<string, CheckWords>
  // 값이 없는 흐린 줄로 그릴 검사 (checks가 비었을 때)
  pending?: string[]
}>()

// 칸 격자. 넓으면 영향 칸이 하나 더 생긴다.
// 영향 칸(150px)은 '평가 점수 부풀림' 태그가, 조치 칸(168px)은 '라벨 하나로 통일' 태그가 한 줄에 들어가는 너비다.
// 값(숫자, 오른쪽 맞춤)과 태그 칸 사이는 조금 더 띄운다(TAG_START_CLASS).
const GRID_CLASS =
  'grid grid-cols-[64px_minmax(0,1fr)_84px_160px_28px] items-center gap-x-4 pr-3.5 pl-[18px] @max-[40rem]:grid-cols-[64px_minmax(0,1fr)_84px_28px_28px] @min-[62.5rem]:grid-cols-[72px_minmax(0,1fr)_84px_150px_168px_28px] @min-[62.5rem]:gap-x-[18px]'

// 값 다음 첫 태그 칸의 왼쪽 여백 (좁으면 조치 칸, 넓으면 영향 칸)
const TAG_START_CLASS = 'pl-4 @max-[40rem]:pl-0 @min-[62.5rem]:pl-0'
const IMPACT_START_CLASS = '@min-[62.5rem]:pl-4'

// 영향 칸: 좁으면 숨긴다
const IMPACT_CELL_CLASS = 'hidden min-w-0 @min-[62.5rem]:block'

// 누를 수 없는 납작한 태그
const TAG_CLASS =
  'inline-flex h-[22px] max-w-full cursor-default items-center gap-1 overflow-hidden rounded-[5px] bg-muted px-[7px] text-meta font-[550] whitespace-nowrap text-muted-foreground [&>svg]:size-3 [&>svg]:shrink-0'
</script>

<template>
  <div class="@container" role="table" aria-label="검사 결과">
    <div
      role="row"
      :class="GRID_CLASS"
      class="h-[34px] border-b bg-[color-mix(in_oklab,var(--muted)_60%,var(--card))] text-meta font-semibold text-muted-foreground"
    >
      <span role="columnheader">등급</span>
      <span role="columnheader">검사</span>
      <span role="columnheader" class="text-right">값</span>
      <span role="columnheader" :class="[IMPACT_CELL_CLASS, IMPACT_START_CLASS]">영향</span>
      <span role="columnheader" :class="TAG_START_CLASS">조치</span>
      <span role="columnheader"><span class="sr-only">보기</span></span>
    </div>

    <div
      v-for="check in checks"
      :key="check.key"
      role="row"
      :data-check="check.key"
      :class="[GRID_CLASS, check.grade === 'good' ? 'min-h-[46px] py-[5px]' : 'min-h-[54px] py-2']"
      class="border-b transition-colors last:border-b-0 hover:bg-[color-mix(in_oklab,var(--muted)_45%,var(--card))]"
    >
      <span role="cell"><GradeBadge :grade="check.grade" /></span>

      <span role="cell" class="flex min-w-0 items-center gap-1.5">
        <component :is="words[check.key].icon" class="size-4 shrink-0 text-muted-foreground" />
        <span class="truncate text-body font-semibold">{{ words[check.key].name }}</span>
        <Tooltip>
          <TooltipTrigger as-child>
            <button
              type="button"
              class="inline-grid size-5 shrink-0 place-items-center rounded-[5px] text-subtle-foreground hover:bg-muted hover:text-foreground"
              :aria-label="`${words[check.key].name} 정의`"
            >
              <Info class="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent
            side="bottom"
            align="start"
            :side-offset="8"
            class="w-[360px] max-w-[380px] rounded-[10px] border bg-popover px-3 py-2.5 text-ui font-normal text-popover-foreground"
          >
            <div class="flex items-center gap-2 font-semibold">
              <component :is="words[check.key].icon" class="size-4 text-muted-foreground" />
              {{ words[check.key].name }}
              <GradeBadge :grade="check.grade" class="ml-auto" />
            </div>
            <dl class="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-[5px] border-t pt-2">
              <dt class="text-muted-foreground">정의</dt>
              <dd class="font-[550]">{{ check.definition }}</dd>
              <dt class="text-muted-foreground">영향</dt>
              <dd class="font-[550]">{{ words[check.key].impact }}</dd>
              <dt class="text-muted-foreground">조치</dt>
              <dd class="font-[550]">{{ words[check.key].fix }}</dd>
              <template v-if="check.scope">
                <dt class="text-muted-foreground">{{ check.scope[0] }}</dt>
                <dd class="font-[550]">{{ check.scope[1] }}</dd>
              </template>
              <dt class="text-muted-foreground">등급</dt>
              <dd class="flex flex-wrap gap-1">
                <span
                  v-for="step in check.ladder"
                  :key="step.grade"
                  class="inline-flex h-5 items-center gap-[3px] rounded-[5px] px-1.5 text-meta font-semibold shadow-[inset_0_0_0_1px_var(--border)]"
                  :class="GRADES[step.grade].textClass"
                >
                  <component :is="GRADES[step.grade].icon" class="size-3" :class="GRADES[step.grade].iconClass" />
                  {{ GRADES[step.grade].name }} {{ step.text }}
                </span>
              </dd>
            </dl>
          </TooltipContent>
        </Tooltip>
      </span>

      <!-- 값 아래 작은 글이 칸보다 길면 오른쪽(영향 태그)이 아니라 왼쪽의 빈 검사 이름 칸으로 자라게 오른쪽 끝에 붙인다 -->
      <span role="cell" class="flex min-w-0 flex-col items-end text-right">
        <span
          class="block leading-[26px] font-[650] tracking-[-0.02em] whitespace-nowrap"
          :class="check.grade === 'good' ? 'text-[20px] text-muted-foreground' : ['text-[22px]', GRADES[check.grade].textClass]"
        >
          {{ check.value }}<span class="ml-0.5 text-body font-[550] tracking-normal opacity-80">{{ check.unit }}</span>
        </span>
        <span v-if="check.sub" class="block text-meta whitespace-nowrap text-muted-foreground">{{ check.sub }}</span>
      </span>

      <span role="cell" :class="[IMPACT_CELL_CLASS, IMPACT_START_CLASS]">
        <span v-if="check.grade !== 'good'" :class="TAG_CLASS">{{ words[check.key].impact }}</span>
      </span>

      <span role="cell" class="min-w-0" :class="TAG_START_CLASS">
        <span v-if="check.grade !== 'good'" :class="TAG_CLASS" class="text-foreground" :title="words[check.key].fix">
          <Wrench /><span class="@max-[40rem]:hidden">{{ words[check.key].fix }}</span>
        </span>
      </span>

      <span role="cell" class="flex justify-end">
        <Tooltip v-if="check.view">
          <TooltipTrigger as-child>
            <Button :variant="check.grade === 'good' ? 'quiet' : 'outline'" size="icon-sm" as-child>
              <RouterLink :to="check.view.to" :aria-label="check.view.label" :data-view="check.key">
                <ArrowRight />
              </RouterLink>
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ check.view.label }}</TooltipContent>
        </Tooltip>
      </span>
    </div>

    <div
      v-for="key in checks.length ? [] : (pending ?? [])"
      :key="key"
      role="row"
      :data-check-pending="key"
      :class="GRID_CLASS"
      class="min-h-[46px] border-b py-[5px] text-muted-foreground last:border-b-0"
    >
      <span role="cell">
        <span
          class="inline-flex h-5 items-center rounded-md px-[7px] text-meta font-semibold text-subtle-foreground shadow-[inset_0_0_0_1px_var(--border)]"
        >—</span>
      </span>
      <span role="cell" class="flex min-w-0 items-center gap-1.5">
        <component :is="words[key].icon" class="size-4 shrink-0" />
        <span class="truncate text-body font-semibold">{{ words[key].name }}</span>
        <span class="ml-1 hidden truncate text-meta text-subtle-foreground @min-[43.75rem]:inline">{{ words[key].definition }}</span>
      </span>
      <span role="cell" class="text-right text-[20px] font-[650] text-subtle-foreground">—</span>
      <span role="cell" :class="IMPACT_CELL_CLASS" />
      <span role="cell" />
      <span role="cell" />
    </div>
  </div>
</template>
