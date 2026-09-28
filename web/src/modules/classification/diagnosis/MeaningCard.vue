<!--
  뜻 검사 카드 (진단 탭, 글자 검사 카드 아래): 뜻 분석 한 번이 만드는 오라벨 의심 · 근접 중복 · 의미 쏠림 세 줄.
    머리   뜻 검사 · 3종 · ● 임베딩 bge-m3 · 점 1,000 · 만듦 2026-09-25 19:18 · Jev 확인 13 / 16
           오른쪽: 등급별 수 · 최신 · [의미 지도 →] · 만들기 칸(MapAction)
    몸     세 줄 (CheckTable). 분석 전이면 흐린 줄(—)
  만들기 칸은 상태마다 [뜻 분석 만들기] · 진행(단계 n / m · 남은 시간 · [취소]) · 분석 이후 변경 · 분석 없음 · 실패와
  [다시 만들기]를 보인다. 최신이어도 흐린 [다시 만들기]를 둔다. 임베딩이 끊겼으면 '임베딩 미연결' · [연결 설정].
  상태 읽기 · 만들기 · 진행률 묻기는 부모(DiagnosisTab)의 useSemanticMap이 한다(라벨 분포의 뜻 쏠림 칸과 같이 쓴다).
-->
<script setup lang="ts">
import { ArrowRight, Plug, Sparkles } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import CheckGroup from '@/system/diagnosis/CheckGroup.vue'
import { openConnectionSettings } from '@/system/connections/connections'
import { dateTimeText, fmt } from '@/system/format'
import { isConnected } from '@/system/readiness'
import { Button } from '@/system/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { CHECK_WORDS, MEANING_CHECK_KEYS, type Check } from '@/modules/classification/diagnosis/checks'
import MapAction from '@/modules/classification/map/MapAction.vue'
import { shortModelName, type SemanticMap } from '@/modules/classification/map/useSemanticMap'

const props = defineProps<{
  datasetId: number
  // 부모가 만든 뜻 분석 상태
  semantic: SemanticMap
  // 뜻 검사 세 줄. 분석 전이면 빈 목록
  checks: Check[]
}>()

// 머리의 '최신' 태그 (MapAction의 태그와 같은 모양)
const TAG_CLASS =
  'inline-flex h-5 items-center rounded-md px-[7px] text-meta font-semibold whitespace-nowrap text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]'

const { state, checks: summary, progress, sending, actionError, building } = props.semantic

// 임베딩 서버가 연결됐는지 (/readyz)
const connected = computed(() => isConnected('embedding'))
const map = computed(() => state.value?.map ?? null)
const isFresh = computed(
  () => map.value !== null && summary.value !== null && state.value?.outdated !== true && building.value === null,
)

// Jev 한 조각: 판정했으면 '확인 n / 물은 수', 아니면 상태 낱말
const jev = computed(() => {
  const found = summary.value
  if (!found) return null
  if (found.jev.status === 'judged') {
    if (!found.suspects.judged) return { text: 'Jev 후보 없음', detail: null, tone: '' }
    return {
      text: `Jev 확인 ${fmt(found.suspects.confirmed_open)} / ${fmt(found.suspects.judged)}`,
      detail: null,
      tone: '',
    }
  }
  if (found.jev.status === 'failed') return { text: 'Jev 실패', detail: found.jev.detail, tone: 'text-danger-ink' }
  if (found.jev.status === 'canceled') return { text: 'Jev 멈춤', detail: found.jev.detail, tone: '' }
  return { text: 'Jev 미연결', detail: null, tone: '' }
})
</script>

<template>
  <CheckGroup
    :icon="Sparkles"
    name="뜻 검사"
    :checks="checks"
    :words="CHECK_WORDS"
    :pending="MEANING_CHECK_KEYS"
    data-meaning-card
  >
    <template #facts>
      <span>3종</span>
      <span class="text-border-strong">·</span>
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap">
        <span
          class="size-1.5 shrink-0 rounded-full"
          :class="connected ? 'bg-success' : 'ring-[1.5px] ring-subtle-foreground ring-inset'"
        />
        <template v-if="map">임베딩 <b class="font-semibold text-foreground">{{ shortModelName(map.model_name) }}</b></template>
        <template v-else>임베딩 {{ connected ? '연결됨' : '미연결' }}</template>
      </span>
      <template v-if="map">
        <span class="text-border-strong">·</span>
        <span class="whitespace-nowrap">점 <b class="font-semibold text-foreground">{{ fmt(map.point_count) }}</b></span>
        <span class="text-border-strong">·</span>
        <span class="whitespace-nowrap">만듦 <b class="font-semibold text-foreground">{{ dateTimeText(map.finished_at) }}</b></span>
      </template>
      <template v-if="jev">
        <span class="text-border-strong">·</span>
        <Tooltip v-if="jev.detail">
          <TooltipTrigger as-child>
            <span class="cursor-default font-semibold whitespace-nowrap" :class="jev.tone" tabindex="0" data-jev>{{ jev.text }}</span>
          </TooltipTrigger>
          <TooltipContent class="max-w-[320px]">{{ jev.detail }}</TooltipContent>
        </Tooltip>
        <span v-else class="whitespace-nowrap" data-jev>{{ jev.text }}</span>
      </template>
    </template>

    <template #actions>
      <span v-if="isFresh" :class="TAG_CLASS" data-map-fresh>최신</span>
      <Button v-if="map" variant="quiet" size="sm" as-child>
        <RouterLink :to="{ name: 'classification-data', params: { datasetId }, query: { view: 'map' } }" data-map-link>
          의미 지도<ArrowRight />
        </RouterLink>
      </Button>
      <Button
        v-if="!connected"
        type="button"
        variant="outline"
        size="sm"
        data-action="connection-settings"
        @click="openConnectionSettings"
      >
        <Plug />연결 설정
      </Button>
      <MapAction
        :state="state"
        :progress="progress"
        :sending="sending"
        :action-error="actionError"
        :connected="connected"
        rebuild
        @start="semantic.start"
        @cancel="semantic.cancel"
      />
    </template>
  </CheckGroup>
</template>
