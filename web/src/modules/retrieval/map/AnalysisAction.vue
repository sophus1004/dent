<!--
  뜻 분석 만들기 칸: 진단 탭의 뜻 분석 줄 끝과 데이터 탭의 지도 보기가 같이 쓴다. 상태마다 보이는 것이 다르다.
  뜻 분석 한 번이 지도 · 근접 중복 · 기준 검색 순위 · 제안(거짓 오답 · 빠진 정답 · 정답 의심)을 함께 만든다.
    없음             [뜻 분석 만들기]
    만드는 중        임베딩 1,234 / 9,107 · 진행 막대 · 남은 약 3분 · [취소]
    다 만듦          질의 n · 문서 n · 모델 · 만든 시각   (facts를 주면)
    분석 이후 변경   태그 '분석 이후 변경' · [다시 만들기]
    실패             태그 '실패'(올리면 까닭) · [다시 만들기]
    최신             rebuild를 주면 흐린 [다시 만들기]
  임베딩이 연결되지 않았으면 만들기 버튼을 그리지 않는다(부모가 [연결 설정]을 둔다).
-->
<script setup lang="ts">
import { LoaderCircle, RefreshCw, Sparkles, X } from '@lucide/vue'
import { computed } from 'vue'

import { dateTimeText, fmt } from '@/system/format'
import { Button } from '@/system/ui/button'
import { Progress } from '@/system/ui/progress'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import {
  ANALYSIS_PHASE_NAMES,
  remainingText,
  shortModelName,
  type AnalysisProgress,
} from '@/modules/retrieval/map/analysis'
import type { AnalysisStateRead } from '@/modules/retrieval/types'

const props = defineProps<{
  state: AnalysisStateRead | null
  progress: AnalysisProgress | null
  sending: boolean
  actionError: string | null
  // 임베딩 서버가 연결됐는지
  connected: boolean
  // 다 만든 분석의 사실을 보일지
  facts?: boolean
  // 최신이어도 흐린 [다시 만들기]를 둘지
  rebuild?: boolean
}>()

const emit = defineEmits<{
  start: []
  cancel: []
}>()

const TAG_CLASS =
  'inline-flex h-5 items-center rounded-md px-[7px] text-meta font-semibold whitespace-nowrap shadow-[inset_0_0_0_1px_var(--border-strong)]'

const done = computed(() => props.state?.done ?? null)
const failed = computed(() => (props.state?.run?.status === 'failed' ? props.state.run : null))
const outdated = computed(() => done.value !== null && props.state?.outdated === true)
const canBuild = computed(() => props.connected && props.state !== null)
const needsRebuild = computed(() => outdated.value || failed.value !== null)
</script>

<template>
  <span class="inline-flex flex-wrap items-center gap-x-3 gap-y-2" data-analysis-action>
    <template v-if="progress">
      <span class="inline-flex items-center gap-1.5 text-ui whitespace-nowrap text-muted-foreground" data-analysis-phase>
        <LoaderCircle class="size-3.5 animate-spin" />
        <b class="font-semibold text-foreground">{{ ANALYSIS_PHASE_NAMES[progress.phase] }}</b>
        <span v-if="progress.total !== null">{{ fmt(progress.done) }} / {{ fmt(progress.total) }}</span>
      </span>
      <span class="w-28 shrink-0"><Progress :value="progress.ratio" label="뜻 분석 만들기" /></span>
      <span v-if="progress.remainingSeconds !== null" class="text-ui whitespace-nowrap text-muted-foreground">
        남은 <b class="font-semibold text-foreground">{{ remainingText(progress.remainingSeconds) }}</b>
      </span>
      <Button type="button" variant="outline" size="sm" :disabled="sending" data-action="analysis-cancel" @click="emit('cancel')">
        <X />취소
      </Button>
    </template>

    <template v-else>
      <template v-if="facts && done">
        <span class="text-ui whitespace-nowrap text-muted-foreground">
          질의 <b class="font-semibold text-foreground">{{ fmt(done.query_count) }}</b>
          · 문서 <b class="font-semibold text-foreground">{{ fmt(done.document_count) }}</b>
        </span>
        <span class="max-w-[220px] truncate text-ui whitespace-nowrap text-muted-foreground" :title="state?.model ?? ''">
          모델 <b class="font-semibold text-foreground">{{ shortModelName(state?.model ?? null) }}</b>
        </span>
        <span class="text-ui whitespace-nowrap text-muted-foreground">
          만듦 <b class="font-semibold text-foreground">{{ dateTimeText(done.finished_at) }}</b>
        </span>
      </template>
      <span v-if="outdated" :class="[TAG_CLASS, 'text-warning-ink']" data-analysis-outdated>분석 이후 변경</span>
      <Tooltip v-if="failed">
        <TooltipTrigger as-child>
          <span :class="[TAG_CLASS, 'cursor-default text-danger-ink']" tabindex="0">실패</span>
        </TooltipTrigger>
        <TooltipContent class="max-w-[320px]">{{ failed.error }}</TooltipContent>
      </Tooltip>
      <template v-if="canBuild">
        <Button v-if="!done" type="button" size="sm" :disabled="sending" data-action="analysis-build" @click="emit('start')">
          <Sparkles />뜻 분석 만들기
        </Button>
        <Button
          v-else-if="needsRebuild || rebuild"
          type="button"
          :variant="needsRebuild ? 'outline' : 'quiet'"
          size="sm"
          :disabled="sending"
          data-action="analysis-rebuild"
          @click="emit('start')"
        >
          <RefreshCw />다시 만들기
        </Button>
      </template>
    </template>
    <span v-if="actionError" class="text-ui text-danger-ink">{{ actionError }}</span>
  </span>
</template>
