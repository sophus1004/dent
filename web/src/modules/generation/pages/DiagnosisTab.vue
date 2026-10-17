<!--
  진단 탭 (#/generation/<id>/diagnosis): 보고서처럼 낱말 · 숫자로만 보인다(검색 진단과 같은 틀).
    종합 상태   StatusCard. 등급 수 · 대화 · 휴지통 · 흐름 줄 한 줄 · 다음 할 일
    검사 카드   한 장: 고른 단계(처음엔 지금 단계). 흐름 줄의 단계 칩을 누르면 바뀐다(주소 ?stage=).
                검사 줄의 [→]는 그 검사의 검토 큐(#/generation/<id>/review?check=)로 간다.
    뜻 검사     고른 단계의 뜻 · 대조 · 판정 검사(1 근접 중복 · 과제 쏠림 · 평가셋 오염, 2 지시 불일치 · 안전,
                3 판정 반대 · 작은 마진). 재지 못한 것은 흐린 줄(—)과 까닭. 평가셋이 없으면 [평가셋 가져오기]
                (새 데이터셋 가져오기를 '평가셋' 켬으로 연다).
                머리에 뜻 분석 만들기 칸(AnalysisAction) · [의미 지도] · 임베딩이 없으면 [연결 설정].
    분포        턴 수(응답 수)별 대화 수
  3 선호는 만들 데이터가 선호이거나 쌍이 있을 때만 있다(설정에서 고른다).
  숫자는 GET …/overview 한 번. 작업이 데이터를 바꾸거나 뜻 분석이 끝나면 조용히 다시 읽는다.
  이 탭은 데이터를 바꾸지 않는다([뜻 분석 만들기]만 작업을 넣는다).
-->
<script setup lang="ts">
import {
  ArrowRight,
  FlaskConical,
  GitCompare,
  LoaderCircle,
  MessageSquareText,
  Plug,
  Reply,
  Sparkles,
  TriangleAlert,
  type LucideIcon,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { openConnectionSettings } from '@/system/connections/connections'
import CheckGroup from '@/system/diagnosis/CheckGroup.vue'
import { helperChanged } from '@/system/helper/helperSignals'
import { clockText, fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { isConnected } from '@/system/readiness'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { getOverview } from '@/modules/generation/api'
import { CHECK_WORDS, SEMANTIC_KEYS, checkWords, toCheck } from '@/modules/generation/checks'
import BinCard from '@/modules/generation/diagnosis/BinCard.vue'
import StatusCard from '@/modules/generation/diagnosis/StatusCard.vue'
import { jobFinished } from '@/modules/generation/jobEvents'
import AnalysisAction from '@/modules/generation/map/AnalysisAction.vue'
import { useAnalysis } from '@/modules/generation/map/analysis'
import StageNo from '@/modules/generation/StageNo.vue'
import type { BinRead, DatasetRead, OverviewRead } from '@/modules/generation/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

// 읽는 동안 보일 회색 줄 수
const SKELETON_ROWS = 5

// 단계 카드 머리 (1 지시 · 2 응답 · 3 선호)
const STAGES: Record<number, { name: string; icon: LucideIcon }> = {
  1: { name: '지시', icon: MessageSquareText },
  2: { name: '응답', icon: Reply },
  3: { name: '선호', icon: GitCompare },
}

// 평가셋이 없어 오염을 재지 못한 까닭 (서버의 기다림 글)
const NO_EVAL_REASON = '평가셋 없음'

// 턴 수 분포에서 한 칸으로 묶는 턴 수 (이 수 이상은 'n턴+')
const TURN_BINS_MAX = 5

const route = useRoute()
const router = useRouter()

const datasetId = computed(() => Number(route.params.datasetId))
const analysis = useAnalysis(datasetId)
const embeddingConnected = computed(() => isConnected('embedding'))
const overview = ref<OverviewRead | null>(null)
const loadError = ref<string | null>(null)
let controller: AbortController | null = null

const checks = computed(() =>
  overview.value
    ? overview.value.checks.map((check) =>
        toCheck(check, datasetId.value, overview.value!.conversations, overview.value!.contamination),
      )
    : [],
)

// 3 선호가 있는지: 만들 데이터가 선호이거나 쌍이 있다
const withPairs = computed(() => overview.value !== null && (overview.value.goal === 'preference' || overview.value.pairs > 0))
const stageNos = computed(() => (withPairs.value ? [1, 2, 3] : [1, 2]))

// 고른 단계: 주소 ?stage= → 없으면 지금 단계(내보내기면 마지막 단계) → 입구
const selected = computed(() => {
  const asked = Number(route.query.stage)
  if (stageNos.value.includes(asked)) return asked
  const now = overview.value?.now_stage ?? props.dataset.entry_stage
  return stageNos.value.includes(now) ? now : stageNos.value[stageNos.value.length - 1]
})
const isSemantic = (key: string) => (SEMANTIC_KEYS as string[]).includes(key)
const stageChecks = computed(() =>
  checks.value.filter((check, index) => overview.value!.checks[index].stage === selected.value && !isSemantic(check.key)),
)
// 뜻 검사: 고른 단계의 잰 것 + 재지 못한 것(서버의 기다림 · 늘 흐린 줄)
const semanticChecks = computed(() =>
  checks.value.filter((check, index) => overview.value!.checks[index].stage === selected.value && isSemantic(check.key)),
)
const stageWaiting = computed(() => (overview.value?.waiting ?? []).filter((item) => item.stage === selected.value))
const pending = computed(() => stageWaiting.value.map((item) => item.key))
// 뜻 분석 전이 아닌 까닭(Jev 미연결 · 평가셋 없음 등)은 카드 머리에 한 줄로
const waitingNotes = computed(() => stageWaiting.value.filter((item) => item.reason !== '뜻 분석 전'))
const needsEvalSet = computed(() => stageWaiting.value.some((item) => item.reason === NO_EVAL_REASON))

// 턴 수 분포: 1턴 · 2턴 … · n턴+
const turnBins = computed<BinRead[]>(() => {
  const turns = overview.value?.turns ?? {}
  const bins: BinRead[] = []
  for (let count = 1; count <= TURN_BINS_MAX; count += 1) {
    const isLast = count === TURN_BINS_MAX
    const total = Object.entries(turns)
      .filter(([turn]) => (isLast ? Number(turn) >= count : Number(turn) === count))
      .reduce((sum, [, value]) => sum + value, 0)
    bins.push({ label: isLast ? `${count}턴+` : `${count}턴`, count: total, tone: 'normal' })
  }
  return bins
})
const multiTurn = computed(() =>
  Object.entries(overview.value?.turns ?? {})
    .filter(([turn]) => Number(turn) > 1)
    .reduce((sum, [, value]) => sum + value, 0),
)

watch(datasetId, loadOverview, { immediate: true })
// 뜻 분석이 끝나면 뜻 검사가 바뀐다.
watch(
  () => analysis.state.value?.done?.id,
  (next, previous) => {
    if (previous !== undefined && next !== previous) void refreshOverview()
  },
)
// 작업이 데이터를 바꾸면 조용히 다시 읽는다(회색 막대 없이 숫자만 바꿔 끼운다).
watch([() => jobFinished(datasetId.value), helperChanged], () => {
  void refreshOverview()
  void analysis.reload()
})
// 머리가 다시 읽히면(검토 · 휴지통에서 바꿈) 진단도 맞춘다.
watch(
  () => props.dataset.updated_at,
  (next, previous) => {
    if (previous !== undefined && next !== previous) void refreshOverview()
  },
)
onBeforeUnmount(() => controller?.abort())

function selectStage(no: number): void {
  void router.replace({ query: { ...route.query, stage: String(no) } })
}

async function refreshOverview(): Promise<void> {
  try {
    overview.value = await getOverview(datasetId.value)
  } catch {
    // 못 읽으면 앞의 값을 그대로 둔다.
  }
}

async function loadOverview(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  overview.value = null
  loadError.value = null
  try {
    overview.value = await getOverview(datasetId.value, current.signal)
  } catch (error) {
    if (isAbortError(error)) return
    loadError.value = errorMessage(error)
  }
}
</script>

<template>
  <div class="max-w-[1320px] space-y-3 px-6 py-6 xl:px-8">
    <section v-if="loadError" class="card flex h-28 items-center justify-center gap-3 text-ui" aria-label="진단 오류">
      <TriangleAlert class="size-4 text-danger" />
      <span class="font-medium">진단</span>
      <span class="font-semibold text-danger-ink">오류</span>
      <span class="text-muted-foreground">{{ loadError }}</span>
      <Button variant="outline" size="sm" @click="loadOverview">다시 불러오기</Button>
    </section>

    <template v-else-if="!overview">
      <section class="card overflow-hidden" aria-busy="true" aria-label="진단 계산 중">
        <div class="flex items-center gap-8 border-b px-6 py-5">
          <div>
            <div class="caps">종합 상태</div>
            <div class="mt-1.5 flex items-center gap-2.5 text-muted-foreground">
              <LoaderCircle class="size-7 animate-spin" />
              <span class="text-kpi font-bold tracking-[-0.03em]">진단 중</span>
            </div>
          </div>
        </div>
        <div class="h-14" />
      </section>
      <section class="card overflow-hidden" aria-busy="true">
        <div v-for="index in SKELETON_ROWS" :key="index" class="flex h-[54px] items-center gap-6 border-b px-5 last:border-b-0">
          <Skeleton class="h-5 w-12" />
          <Skeleton class="h-4 w-24" />
          <Skeleton class="ml-auto h-6 w-14" />
          <Skeleton class="h-5 w-28" />
        </div>
      </section>
    </template>

    <template v-else>
      <StatusCard
        :overview="overview"
        :checks="checks"
        :entry="dataset.entry_stage"
        :selected="selected"
        @select="selectStage"
      />

      <CheckGroup
        :icon="STAGES[selected].icon"
        :name="STAGES[selected].name"
        :checks="stageChecks"
        :words="CHECK_WORDS"
        :data-stage-card="selected"
      >
        <template #facts>
          <StageNo :no="selected" size="xs" />
          <span>대화 <b class="font-semibold text-foreground">{{ fmt(overview.conversations) }}</b></span>
          <span class="text-border-strong">·</span>
          <span>{{ stageChecks.length }}종</span>
          <span class="text-border-strong">·</span>
          <span>규칙 계산</span>
          <span class="text-border-strong">·</span>
          <span class="whitespace-nowrap">진단 <b class="font-semibold text-foreground">{{ clockText(overview.computed_at) }}</b></span>
        </template>
      </CheckGroup>

      <CheckGroup
        :icon="Sparkles"
        name="뜻 검사"
        :checks="semanticChecks"
        :words="CHECK_WORDS"
        :pending="pending"
        data-semantic-card
      >
        <template #facts>
          <StageNo :no="selected" size="xs" />
          <span>{{ semanticChecks.length + pending.length }}종</span>
          <span class="text-border-strong">·</span>
          <span>임베딩 · Jev</span>
          <template v-for="item in waitingNotes" :key="item.key">
            <span class="text-border-strong">·</span>
            <span class="font-semibold text-warning-ink">{{ checkWords(item.key).name }} {{ item.reason }}</span>
          </template>
        </template>
        <template #actions>
          <AnalysisAction
            :state="analysis.state.value"
            :progress="analysis.progress.value"
            :sending="analysis.sending.value"
            :action-error="analysis.actionError.value"
            :connected="embeddingConnected"
            facts
            rebuild
            @start="analysis.start"
            @cancel="analysis.cancel"
          />
          <Button v-if="analysis.done.value" variant="outline" size="sm" as-child>
            <RouterLink :to="{ name: 'generation-data', params: { datasetId }, query: { view: 'map' } }" data-action="open-map">
              의미 지도<ArrowRight />
            </RouterLink>
          </Button>
          <Button v-if="!embeddingConnected" type="button" variant="outline" size="sm" @click="openConnectionSettings">
            <Plug />연결 설정
          </Button>
          <Button v-if="needsEvalSet" variant="outline" size="sm" as-child>
            <RouterLink :to="{ name: 'generation-new', query: { eval: '1' } }" data-action="import-eval">
              <FlaskConical />평가셋 가져오기
            </RouterLink>
          </Button>
        </template>
      </CheckGroup>

      <div class="pt-3">
        <div class="mb-3 flex items-center gap-2">
          <span class="caps">분포</span>
          <span class="h-px flex-1 bg-border" />
        </div>
        <div class="@container">
          <div class="grid gap-4 @min-[880px]:grid-cols-2">
            <BinCard title="턴 수" :bins="turnBins">
              <template #facts>
                대화 <b class="font-semibold text-foreground">{{ fmt(overview.conversations) }}</b>
                · 여러 턴 <b class="font-semibold text-foreground">{{ fmt(multiTurn) }}</b>
              </template>
            </BinCard>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
