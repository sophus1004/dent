<!--
  진단 탭 (#/retrieval/<id>/diagnosis): 보고서처럼 낱말 · 숫자로만 보인다.
    종합 상태   StatusCard. 등급 수 · 기준 검색 점수 · 흐름 줄 한 줄 · 뜻 분석 줄 · 도우미 줄
    검사 카드   한 장: 고른 단계(처음엔 지금 단계). 흐름 줄의 단계 칩을 누르면 바뀐다(주소 ?stage=).
    분포        첫 정답 순위 · 질의당 오답 · 질의 유형 · 문서 길이 · 판정 출처
  숫자는 GET …/overview 한 번(뜻 분석이 있으면 그 결과를 함께 센다). 도우미 · 작업이 데이터를 바꾸면 조용히 다시 읽는다.
  이 탭은 데이터를 바꾸지 않는다. 데이터 · 제안 탭으로 가는 버튼, [뜻 분석 만들기], [도우미 열기]만 움직인다.
-->
<script setup lang="ts">
import { Clock, FileText, Layers, LoaderCircle, MessageCircleQuestion, Pickaxe, RefreshCw, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import CheckGroup from '@/system/diagnosis/CheckGroup.vue'
import { helperChanged } from '@/system/helper/helperSignals'
import { clockText, fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { pushToast } from '@/system/toasts'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Skeleton } from '@/system/ui/skeleton'

import { getOverview, mine, remine } from '@/modules/retrieval/api'
import BinCard from '@/modules/retrieval/diagnosis/BinCard.vue'
import { CHECK_WORDS, toCheck } from '@/modules/retrieval/diagnosis/checks'
import StatusCard from '@/modules/retrieval/diagnosis/StatusCard.vue'
import { jobFinished } from '@/modules/retrieval/jobEvents'
import { shortModelName, useAnalysis } from '@/modules/retrieval/map/analysis'
import StageNo from '@/modules/retrieval/StageNo.vue'
import type { DatasetRead, OverviewRead } from '@/modules/retrieval/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

// 읽는 동안 보일 회색 줄 수
const SKELETON_ROWS = 5

// 넘어가기가 '지금'일 때 보일 단계: 질의 만들기 → 2, 오답 찾기 · 내보내기 → 3
const STAGE_OF_MOVE = { generate: 2, mine: 3, export: 3 } as const

// 단계 카드 머리의 아이콘 (1 문서 · 2 질의 · 3 하드 네거티브)
const STAGE_ICONS: Record<number, typeof Clock> = { 1: FileText, 2: MessageCircleQuestion, 3: Layers }

// 검사가 없을 때 단계 카드의 빈 줄
const EMPTY_TEXT: Record<number, string> = { 1: '문서 없음', 2: '질의 뒤', 3: '오답 뒤' }

const route = useRoute()
const router = useRouter()

const datasetId = computed(() => Number(route.params.datasetId))
const analysis = useAnalysis(datasetId)
const overview = ref<OverviewRead | null>(null)
const loadError = ref<string | null>(null)
let controller: AbortController | null = null

const checks = computed(() => (overview.value ? overview.value.checks.map((check) => toCheck(check, datasetId.value)) : []))

// 고른 단계: 주소 ?stage= → 없으면 지금 단계(넘어가기면 그 다음 단계) → 입구
const selected = computed(() => {
  const asked = Number(route.query.stage)
  if ([1, 2, 3].includes(asked)) return asked
  const current = overview.value?.flow.current
  if (typeof current === 'number') return current
  if (typeof current === 'string') return STAGE_OF_MOVE[current]
  return overview.value?.flow.entry ?? 1
})
const stage = computed(() => overview.value?.flow.stages.find((item) => item.no === selected.value) ?? null)
const stageChecks = computed(() =>
  checks.value.filter((_check, index) => overview.value!.checks[index].stage === selected.value),
)
const stageUnit = computed(() => (selected.value === 1 ? '문서' : selected.value === 2 ? '질의' : '오답'))

watch(datasetId, loadOverview, { immediate: true })
// 도우미 · 작업이 데이터를 바꾸면 조용히 다시 읽는다(회색 막대 없이 숫자만 바꿔 끼운다).
watch([helperChanged, jobFinished], () => {
  void refreshOverview()
  void analysis.reload()
})
// 뜻 분석이 끝나면 진단(기준 검색 점수 · 뜻 검사)이 바뀐다.
watch(
  () => analysis.state.value?.done?.id,
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

// ---------- 오답 찾기 · 다시 찾기 (3단계 카드) ----------

const mining = ref(false)
const remineOpen = ref(false)

/** 오답 찾기(모자란 질의만) 또는 다시 찾기(찾은 오답을 떼고 처음부터) 작업을 넣는다. 끝나면 알림이 온다. */
async function startMining(again: boolean): Promise<void> {
  mining.value = true
  try {
    await (again ? remine(datasetId.value) : mine(datasetId.value))
    remineOpen.value = false
    pushToast({ tone: 'done', title: again ? '다시 찾기' : '오답 찾기', detail: '작업 넣음 · 끝나면 알림', actions: [] })
  } catch (error) {
    pushToast({ tone: 'fail', title: '오답 찾기', detail: errorMessage(error), actions: [] })
  } finally {
    mining.value = false
  }
}

// 분포 카드의 사실
const firstRankTotal = computed(() => overview.value?.first_rank.reduce((sum, bin) => sum + bin.count, 0) ?? 0)
const targetModel = computed(() => props.dataset.settings?.target_model ?? analysis.state.value?.model ?? null)
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
        :dataset-id="datasetId"
        :overview="overview"
        :checks="checks"
        :analysis="analysis"
        :selected="selected"
        @select="selectStage"
      />

      <CheckGroup
        v-if="stageChecks.length && stage"
        :icon="STAGE_ICONS[selected]"
        :name="stage.name"
        :checks="stageChecks"
        :words="CHECK_WORDS"
        :data-stage-card="selected"
      >
        <template v-if="selected === 3 && overview.query_count" #actions>
          <Button
            type="button"
            variant="outline"
            size="sm"
            :disabled="mining"
            data-action="mine"
            @click="startMining(false)"
          >
            <Pickaxe />오답 찾기
          </Button>
          <Button
            v-if="overview.negative_count"
            type="button"
            variant="quiet"
            size="sm"
            :disabled="mining"
            data-action="remine"
            @click="remineOpen = true"
          >
            <RefreshCw />다시 찾기
          </Button>
        </template>
        <template #facts>
          <StageNo :no="selected" size="xs" />
          <span>{{ stageUnit }} <b class="font-semibold text-foreground">{{ fmt(stage.count) }}</b></span>
          <span class="text-border-strong">·</span>
          <span>{{ stageChecks.length }}종</span>
          <span class="text-border-strong">·</span>
          <span class="whitespace-nowrap">진단 <b class="font-semibold text-foreground">{{ clockText(overview.computed_at) }}</b></span>
          <template v-if="selected === 3 && targetModel">
            <span class="text-border-strong">·</span>
            <span class="whitespace-nowrap">학습할 모델 <b class="font-semibold text-foreground">{{ shortModelName(targetModel) }}</b></span>
          </template>
        </template>
      </CheckGroup>
      <section v-else-if="stage" class="card overflow-hidden" :aria-label="stage.name" :data-stage-card="selected">
        <div class="flex min-h-11 items-center gap-2.5 border-b px-[18px]">
          <StageNo :no="selected" size="xs" />
          <h2 class="text-body font-semibold">{{ stage.name }}</h2>
          <span class="text-meta text-muted-foreground">{{ stageUnit }} {{ fmt(stage.count) }}</span>
        </div>
        <div class="flex h-14 items-center justify-center gap-2 text-ui text-subtle-foreground">
          <Clock class="size-4" />{{ EMPTY_TEXT[selected] }}
        </div>
      </section>

      <div class="pt-3">
        <div class="mb-3 flex items-center gap-2">
          <span class="caps">분포</span>
          <span class="h-px flex-1 bg-border" />
        </div>
        <div class="@container">
          <div class="grid gap-4 @min-[880px]:grid-cols-2">
            <BinCard v-if="overview.query_count" title="첫 정답 순위" :bins="overview.first_rank">
              <template #facts>
                <template v-if="overview.kpi">
                  기준 검색 <b class="font-semibold text-foreground">{{ shortModelName(analysis.state.value?.model ?? null) }}</b>
                  · 질의 <b class="font-semibold text-foreground">{{ fmt(firstRankTotal) }}</b>
                </template>
                <template v-else>뜻 분석 뒤</template>
                <span
                  v-if="analysis.state.value?.outdated"
                  class="ml-1 inline-flex h-5 items-center rounded-md px-1.5 text-meta font-semibold text-warning-ink shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--warning)_45%,transparent)]"
                  data-first-rank-outdated
                >분석 이후 변경</span>
              </template>
            </BinCard>
            <BinCard v-if="overview.query_count" title="질의당 오답" :bins="overview.negatives_per_query">
              <template #facts>
                목표 <b class="font-semibold text-foreground">{{ dataset.settings?.negatives ?? 7 }}</b>
              </template>
            </BinCard>
            <BinCard v-if="overview.query_count" title="질의 유형" :bins="overview.query_types">
              <template #facts>질의 <b class="font-semibold text-foreground">{{ fmt(overview.query_count) }}</b></template>
            </BinCard>
            <BinCard title="문서 길이" :bins="overview.document_lengths">
              <template #facts>
                문서 <b class="font-semibold text-foreground">{{ fmt(overview.document_count) }}</b>
                · 최대 <b class="font-semibold text-foreground">{{ dataset.settings?.doc_max_tokens ?? 512 }}토큰</b>
              </template>
            </BinCard>
            <BinCard v-if="overview.query_count" title="판정 출처" :bins="overview.judgment_sources">
              <template #facts>
                정답 <b class="font-semibold text-foreground">{{ fmt(overview.positive_count) }}</b>
                · 오답 <b class="font-semibold text-foreground">{{ fmt(overview.negative_count) }}</b>
              </template>
            </BinCard>
          </div>
        </div>
      </div>
    </template>
    <Dialog v-model:open="remineOpen">
      <DialogContent class="w-[min(420px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none">
        <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
          <RefreshCw class="size-4 text-muted-foreground" />
          <DialogTitle class="text-title font-semibold">오답 다시 찾기</DialogTitle>
          <DialogDescription class="sr-only">찾은 오답을 떼고 지금 설정으로 다시 찾는다</DialogDescription>
        </DialogHeader>
        <div class="space-y-4 px-5 py-4">
          <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">떼기</dt>
            <dd>찾기로 붙인 오답 (원본 · 사람 판정은 그대로)</dd>
            <dt class="text-muted-foreground">다시</dt>
            <dd>
              학습할 모델 · {{ dataset.settings?.mine_rank_from ?? 10 }}~{{ dataset.settings?.mine_rank_to ?? 100 }}위
              · 질의마다 {{ dataset.settings?.negatives ?? 7 }}
            </dd>
          </dl>
          <div class="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" @click="remineOpen = false">취소</Button>
            <Button type="button" :disabled="mining" data-action="remine-go" @click="startMining(true)">
              <LoaderCircle v-if="mining" class="animate-spin" /><RefreshCw v-else />다시 찾기
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
