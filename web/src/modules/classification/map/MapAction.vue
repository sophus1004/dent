<!--
  뜻 분석 만들기 칸: 진단 탭의 뜻 분석 줄 끝과 데이터 탭의 지도 보기가 같이 쓴다. 상태마다 보이는 것이 다르다.
  뜻 분석 한 번이 의미 지도 · 근접 중복 · 오라벨 의심 · 의미 쏠림을 함께 만든다.
    없음             [뜻 분석 만들기]
    만드는 중        임베딩 1,234 / 9,107 · 진행 막대 · 남은 약 3분 · [취소]
                     (좌표 계산 · 분석 · Jev 판정 · 저장 단계도 같은 자리. 양을 모르는 단계는 막대만 흐른다)
    다 만듦          점 9,107 · 모델 bge-m3 · 만듦 2026-09-25 14:02        (facts를 주면)
    분석 이후 변경   태그 '분석 이후 변경' · [다시 만들기]
    지도만 있음      태그 '분석 없음'(뜻 분석 전에 만든 지도) · [다시 만들기]
    실패             태그 '실패'(올리면 까닭) · [다시 만들기]
    최신             rebuild를 주면 흐린 [다시 만들기] (모델을 바꿨을 때 등 사람이 다시 만들 수 있게)
  임베딩이 연결되지 않았으면 만들기 버튼을 그리지 않는다(부모가 [연결 설정]을 둔다).
  누르면 start · cancel을 알리기만 한다. 요청과 진행률 묻기는 부모의 useSemanticMap이 한다.
-->
<script setup lang="ts">
import { LoaderCircle, RefreshCw, Sparkles, X } from '@lucide/vue'
import { computed } from 'vue'

import { dateTimeText, fmt } from '@/system/format'
import { Button } from '@/system/ui/button'
import { Progress } from '@/system/ui/progress'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import {
  PHASE_NAMES,
  remainingText,
  shortModelName,
  type MapProgress,
} from '@/modules/classification/map/useSemanticMap'
import type { MapStateRead } from '@/modules/classification/types'

const props = defineProps<{
  // 지도 상태. 아직 못 읽었으면 null
  state: MapStateRead | null
  // 만드는 중이면 진행, 아니면 null
  progress: MapProgress | null
  // 만들기 · 멈추기를 보내는 중
  sending: boolean
  // 만들기 · 멈추기가 실패한 까닭
  actionError: string | null
  // 임베딩 서버가 연결됐는지
  connected: boolean
  // 다 만든 지도의 사실(점 · 모델 · 만든 시각)을 보일지
  facts?: boolean
  // 최신이어도 흐린 [다시 만들기]를 둘지
  rebuild?: boolean
}>()

const emit = defineEmits<{
  start: []
  cancel: []
}>()

// 태그 모양 (검사 묶음 머리의 '최신' 태그와 같다)
const TAG_CLASS =
  'inline-flex h-5 items-center rounded-md px-[7px] text-meta font-semibold whitespace-nowrap shadow-[inset_0_0_0_1px_var(--border-strong)]'

const map = computed(() => props.state?.map ?? null)
const failed = computed(() => (props.state?.run?.status === 'failed' ? props.state.run : null))
const outdated = computed(() => map.value !== null && props.state?.outdated === true)
// 뜻 분석 전에 만든 지도: 점은 있지만 근접 중복 · 오라벨 의심 · 의미 쏠림이 없다.
const lacksChecks = computed(() => map.value !== null && props.state?.checks === null)
const canBuild = computed(() => props.connected && props.state !== null)
const needsRebuild = computed(() => outdated.value || lacksChecks.value || failed.value !== null)
</script>

<template>
  <span class="inline-flex flex-wrap items-center gap-x-3 gap-y-2" data-map-action>
    <template v-if="progress">
      <span class="inline-flex items-center gap-1.5 text-ui whitespace-nowrap text-muted-foreground" data-map-phase>
        <LoaderCircle class="size-3.5 animate-spin" />
        <b class="font-semibold text-foreground">{{ PHASE_NAMES[progress.phase] }}</b>
        <span v-if="progress.total !== null">
          {{ fmt(progress.done) }} / {{ fmt(progress.total) }}
        </span>
      </span>
      <!-- 진행 막대는 늘 칸을 꽉 채우므로(w-full) 폭은 감싼 칸으로 정한다. -->
      <span class="w-28 shrink-0">
        <Progress :value="progress.ratio" label="뜻 분석 만들기" />
      </span>
      <span v-if="progress.remainingSeconds !== null" class="text-ui whitespace-nowrap text-muted-foreground">
        남은 <b class="font-semibold text-foreground">{{ remainingText(progress.remainingSeconds) }}</b>
      </span>
      <Button type="button" variant="outline" size="sm" :disabled="sending" data-action="map-cancel" @click="emit('cancel')">
        <X />취소
      </Button>
    </template>

    <template v-else>
      <template v-if="facts && map">
        <span class="text-ui whitespace-nowrap text-muted-foreground">
          점 <b class="font-semibold text-foreground">{{ fmt(map.point_count) }}</b>
        </span>
        <span class="max-w-[220px] truncate text-ui whitespace-nowrap text-muted-foreground" :title="map.model_name ?? ''">
          모델 <b class="font-semibold text-foreground">{{ shortModelName(map.model_name) }}</b>
        </span>
        <span class="text-ui whitespace-nowrap text-muted-foreground">
          만듦 <b class="font-semibold text-foreground">{{ dateTimeText(map.finished_at) }}</b>
        </span>
      </template>
      <span v-if="outdated" :class="[TAG_CLASS, 'text-warning-ink']" data-map-outdated>분석 이후 변경</span>
      <span v-else-if="lacksChecks" :class="[TAG_CLASS, 'text-warning-ink']" data-map-no-checks>분석 없음</span>
      <Tooltip v-if="failed">
        <TooltipTrigger as-child>
          <span :class="[TAG_CLASS, 'cursor-default text-danger-ink']" tabindex="0">실패</span>
        </TooltipTrigger>
        <TooltipContent class="max-w-[320px]">{{ failed.error }}</TooltipContent>
      </Tooltip>
      <template v-if="canBuild">
        <Button v-if="!map" type="button" size="sm" :disabled="sending" data-action="map-build" @click="emit('start')">
          <Sparkles />뜻 분석 만들기
        </Button>
        <Button
          v-else-if="needsRebuild || rebuild"
          type="button"
          :variant="needsRebuild ? 'outline' : 'quiet'"
          size="sm"
          :disabled="sending"
          data-action="map-rebuild"
          @click="emit('start')"
        >
          <RefreshCw />다시 만들기
        </Button>
      </template>
    </template>
    <span v-if="actionError" class="text-ui text-danger-ink">{{ actionError }}</span>
  </span>
</template>
