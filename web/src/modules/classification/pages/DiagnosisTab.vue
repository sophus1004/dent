<!--
  진단 탭 (#/classification/<id>/diagnosis): 데이터셋 화면의 첫 탭. 보고서처럼 낱말 · 숫자로만 보인다.
    종합 상태     StatusCard. 검사 7종(글자 4 · 뜻 3)의 등급을 센다 · 가장 급한 검사로 가는 주 버튼 · 잠긴 줄 한 줄
    글자 검사     CheckGroup. 라벨 충돌 · 짧은 문장 · 중복 · 라벨 균형
    뜻 검사       MeaningCard. 오라벨 의심 · 근접 중복 · 의미 쏠림. 머리에 모델 · 만든 시각 · Jev · [의미 지도] · 만들기 칸
    분포          라벨 분포(뜻 쏠림 칸 · 문제 아이콘 + 수) · 길이 분포
  글자 검사 숫자는 GET …/overview 한 번(학습 포함 문장 기준), 뜻 검사는 뜻 분석 상태(useSemanticMap)의 checks에서 온다.
  뜻 분석이 없으면 뜻 검사는 흐린 줄(—)이고 종합 상태는 글자 검사만 센다.
  도우미(오른쪽 창)가 데이터를 바꾸면 조용히 다시 읽어 실시간으로 숫자가 바뀐다.
  읽는 동안은 '진단 중'과 회색 막대, 실패하면 오류 칸과 [다시 불러오기].
  이 탭은 데이터를 바꾸지 않는다. 데이터 · 제안 탭으로 가는 버튼, [연결 설정], [뜻 분석 만들기](작업을 넣는다)만 움직인다.
-->
<script setup lang="ts">
import { ListChecks, LoaderCircle, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import CheckGroup from '@/system/diagnosis/CheckGroup.vue'
import { helperChanged } from '@/system/helper/helperSignals'
import { clockText } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import { getOverview } from '@/modules/classification/api'
import { buildChecks, buildMeaningChecks, CHECK_WORDS } from '@/modules/classification/diagnosis/checks'
import LabelDistribution from '@/modules/classification/diagnosis/LabelDistribution.vue'
import LengthHistogram from '@/modules/classification/diagnosis/LengthHistogram.vue'
import MeaningCard from '@/modules/classification/diagnosis/MeaningCard.vue'
import StatusCard from '@/modules/classification/diagnosis/StatusCard.vue'
import { useSemanticMap } from '@/modules/classification/map/useSemanticMap'
import type { DatasetRead, OverviewRead } from '@/modules/classification/types'

defineProps<{
  // 머리(DatasetLayout)가 읽은 데이터셋. 진단은 따로 읽으므로 쓰지 않는다.
  dataset: DatasetRead
}>()

// 읽는 동안 보일 회색 줄 수 (글자 검사 네 개)
const SKELETON_ROWS = 4

const route = useRoute()

const datasetId = computed(() => Number(route.params.datasetId))
const semantic = useSemanticMap(datasetId)
const summary = semantic.checks
const overview = ref<OverviewRead | null>(null)
const loadError = ref<string | null>(null)
let controller: AbortController | null = null

const textChecks = computed(() => (overview.value ? buildChecks(overview.value) : []))
const meaningChecks = computed(() => (summary.value ? buildMeaningChecks(summary.value, datasetId.value) : []))
const allChecks = computed(() => [...textChecks.value, ...meaningChecks.value])

watch(datasetId, loadOverview, { immediate: true })
// 도우미가 데이터를 바꾸면 진단을 조용히 다시 읽는다(회색 막대 없이 숫자만 바꿔 끼운다).
watch(helperChanged, () => {
  void refreshOverview()
  void semantic.reload()
})
onBeforeUnmount(() => controller?.abort())

async function refreshOverview(): Promise<void> {
  try {
    overview.value = await getOverview(datasetId.value)
  } catch {
    // 못 읽으면 앞의 값을 그대로 둔다. 다음 바뀜에서 다시 읽는다.
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
          <div class="flex gap-8">
            <div v-for="name in ['심각', '주의', '통과']" :key="name">
              <div class="text-ui text-muted-foreground">{{ name }}</div>
              <Skeleton class="mt-2 h-6 w-7" />
            </div>
          </div>
        </div>
        <div class="h-11" />
      </section>
      <section class="card overflow-hidden" aria-busy="true">
        <div v-for="index in SKELETON_ROWS" :key="index" class="flex h-[54px] items-center gap-6 border-b px-5 last:border-b-0">
          <Skeleton class="h-5 w-12" />
          <Skeleton class="h-4 w-24" />
          <Skeleton class="ml-auto h-6 w-14" />
          <Skeleton class="h-4 w-12" />
          <Skeleton class="h-5 w-28" />
          <Skeleton class="h-7 w-7" />
        </div>
      </section>
    </template>

    <template v-else>
      <StatusCard :overview="overview" :checks="allChecks" :meaning-count="meaningChecks.length" />

      <CheckGroup :icon="ListChecks" name="글자 검사" :checks="textChecks" :words="CHECK_WORDS" data-text-card>
        <template #facts>
          <span>{{ textChecks.length }}종</span>
          <span class="text-border-strong">·</span>
          <span>규칙 계산</span>
          <span class="text-border-strong">·</span>
          <span class="whitespace-nowrap">진단 <b class="font-semibold text-foreground">{{ clockText(overview.computed_at) }}</b></span>
        </template>
      </CheckGroup>

      <MeaningCard :dataset-id="datasetId" :semantic="semantic" :checks="meaningChecks" />

      <div class="pt-3">
        <div class="mb-3 flex items-center gap-2">
          <span class="caps">분포</span>
          <span class="h-px flex-1 bg-border" />
        </div>
        <div class="space-y-4">
          <LabelDistribution :overview="overview" :summary="summary" />
          <LengthHistogram :overview="overview" />
        </div>
      </div>
    </template>
  </div>
</template>
