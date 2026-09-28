<!--
  제안 탭 (#/classification/<id>/suggestions): 고칠 거리를 사람이 수락하는 곳.
  지금 있는 제안은 뜻 분석의 오라벨 의심(라벨 변경)뿐이다. LLM 제안(새 문장 · 문장 다듬기)은 잠겨 있다.
    머리   상태(대기 · 수락 · 유지) · 출처(전체 · Jev 확인) 거르기, [Jev 확인 모두 수락 n] (확인 창)
    표     대상 문장 · 지금 → 제안 · 분류기(지금 라벨 확률 → 제안 확률) · Jev(고른 라벨 · 확신도) · 동작
    동작   [수락](제안 라벨로) · [유지](그대로, 다음 뜻 분석에서도 다시 묻지 않음) · ▾ 다른 라벨로 수락
  수락은 같은 문장 · 같은 라벨인 휴지통 밖 문장을 모두 바꾼다(×n). 수락 전에는 데이터가 바뀌지 않는다.
  목록은 GET …/suspects(Jev 확인 → 지금 라벨 확률 낮은 순), 수는 뜻 분석 상태(useSemanticMap)의 checks에서 읽는다.
  뜻 분석이 없으면 '뜻 분석 없음'과 [진단 열기]만 보인다.
-->
<script setup lang="ts">
import {
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Inbox,
  LoaderCircle,
  Lock,
  Pin,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { dateTimeText, fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { isConnected } from '@/system/readiness'
import { pushToast } from '@/system/toasts'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/system/ui/dropdown-menu'
import { Skeleton } from '@/system/ui/skeleton'

import { acceptConfirmedSuspects, acceptSuspect, keepSuspect, listSuspects } from '@/modules/classification/api'
import { useDatasetRefresh } from '@/modules/classification/datasetRefresh'
import LabelChip from '@/modules/classification/LabelChip.vue'
import { useSemanticMap } from '@/modules/classification/map/useSemanticMap'
import type {
  DatasetRead,
  LabelRead,
  SuspectDecision,
  SuspectPageRead,
  SuspectRead,
} from '@/modules/classification/types'

const props = defineProps<{
  // 머리(DatasetLayout)가 읽은 데이터셋. 다른 라벨로 수락할 때 라벨 목록을 쓴다.
  dataset: DatasetRead
}>()

// 한 쪽에 보이는 의심 수
const PAGE_SIZE = 50

// 읽는 동안 보일 회색 줄 수
const SKELETON_ROWS = 4

// 상태 거르기: 대기(아직 판단 안 함) · 수락 · 유지
type DecisionView = 'open' | SuspectDecision
const DECISION_NAMES: Record<DecisionView, string> = { open: '대기', accepted: '수락', kept: '유지' }
const DECISION_VIEWS: DecisionView[] = ['open', 'accepted', 'kept']

// 머리 칸 (데이터 표와 같은 모양)
const TH_CLASS =
  'h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'
const TD_CLASS = 'h-11 border-b px-2.5 group-last:border-b-0'
const SEGMENT_CLASS = 'inline-flex gap-0.5 rounded-lg bg-muted p-0.5 shadow-[inset_0_0_0_1px_var(--border)]'
const SEGMENT_BUTTON_CLASS =
  'inline-flex h-[26px] items-center gap-[5px] rounded-md px-2.5 text-ui font-[550] text-muted-foreground hover:text-foreground aria-pressed:bg-card aria-pressed:text-foreground aria-pressed:shadow-[0_1px_2px_rgba(0,0,0,0.08),0_0_0_1px_var(--border)]'
const TAG_CLASS =
  'inline-flex h-5 shrink-0 items-center gap-[3px] rounded-md px-[7px] text-meta font-semibold whitespace-nowrap shadow-[inset_0_0_0_1px_var(--border-strong)] [&>svg]:size-3'

const route = useRoute()
const refreshDataset = useDatasetRefresh()

const datasetId = computed(() => Number(route.params.datasetId))
const semantic = useSemanticMap(datasetId)
const { state, checks } = semantic

// Jev가 연결됐는지 (/readyz)
const jevConnected = computed(() => isConnected('jev'))

const decision = ref<DecisionView>('open')
const confirmedOnly = ref(false)
const page = ref(1)

const result = ref<SuspectPageRead | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
let controller: AbortController | null = null

// 수락 · 유지를 보내는 중인 문장 해시와 실패한 까닭
const busyHash = ref<string | null>(null)
const actionError = ref<string | null>(null)

// Jev 확인 모두 수락 확인 창
const acceptAllOpen = ref(false)
const acceptingAll = ref(false)
const acceptAllError = ref<string | null>(null)

// 다 만든 지도(뜻 분석)의 번호. 새로 만들면 목록을 다시 읽는다.
const mapId = computed(() => state.value?.map?.id ?? null)
const hasAnalysis = computed(() => checks.value !== null)
const isStateLoaded = computed(() => state.value !== null)

const rows = computed(() => result.value?.items ?? null)
const total = computed(() => result.value?.total ?? 0)
const firstNumber = computed(() => (page.value - 1) * PAGE_SIZE + 1)
const lastNumber = computed(() => firstNumber.value + (rows.value?.length ?? 0) - 1)
const hasNextPage = computed(() => page.value * PAGE_SIZE < total.value)
const isOpenView = computed(() => decision.value === 'open')

const counts = computed(() => {
  const suspects = checks.value?.suspects
  return {
    open: suspects?.open ?? 0,
    accepted: suspects?.accepted ?? 0,
    kept: suspects?.kept ?? 0,
    confirmed: suspects?.confirmed_open ?? 0,
  }
})

watch([datasetId, mapId, hasAnalysis, decision, confirmedOnly, page], loadSuspects, { immediate: true })
watch([decision, confirmedOnly], () => {
  page.value = 1
})
onBeforeUnmount(() => controller?.abort())

async function loadSuspects(): Promise<void> {
  controller?.abort()
  if (!hasAnalysis.value) {
    result.value = null
    return
  }
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    result.value = await listSuspects(
      datasetId.value,
      {
        decision: decision.value,
        confirmed: isOpenView.value && confirmedOnly.value ? true : undefined,
        limit: PAGE_SIZE,
        offset: (page.value - 1) * PAGE_SIZE,
      },
      current.signal,
    )
  } catch (error) {
    if (!isAbortError(error)) loadError.value = errorMessage(error)
  } finally {
    if (controller === current) loading.value = false
  }
}

// 판단한 뒤: 목록 · 수(checks) · 머리의 라벨 건수를 다시 읽는다. 쪽이 비면 앞 쪽으로.
async function afterDecision(): Promise<void> {
  void semantic.reload()
  void refreshDataset()
  await loadSuspects()
  const isPageEmpty = rows.value?.length === 0 && page.value > 1
  if (isPageEmpty) page.value -= 1
}

/** 수락: 제안 라벨(label을 주면 그 라벨)로 바꾼다. */
async function accept(item: SuspectRead, label?: LabelRead): Promise<void> {
  if (busyHash.value) return
  busyHash.value = item.text_hash
  actionError.value = null
  try {
    const { changed } = await acceptSuspect(datasetId.value, item.text_hash, label ? { label_id: label.id } : {})
    pushToast({
      tone: 'done',
      title: '수락 · 라벨 변경',
      detail: `${item.label.name} → ${label?.name ?? item.suggested_label.name} · ${fmt(changed)}건`,
      actions: [],
    })
    await afterDecision()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busyHash.value = null
  }
}

/** 유지: 라벨을 그대로 둔다. */
async function keep(item: SuspectRead): Promise<void> {
  if (busyHash.value) return
  busyHash.value = item.text_hash
  actionError.value = null
  try {
    await keepSuspect(datasetId.value, item.text_hash)
    await afterDecision()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busyHash.value = null
  }
}

function openAcceptAll(): void {
  acceptAllError.value = null
  acceptAllOpen.value = true
}

/** Jev도 확인한 의심을 모두 제안 라벨로 수락한다. */
async function acceptAll(): Promise<void> {
  acceptingAll.value = true
  acceptAllError.value = null
  try {
    const { changed } = await acceptConfirmedSuspects(datasetId.value)
    acceptAllOpen.value = false
    pushToast({ tone: 'done', title: '수락 · Jev 확인', detail: `라벨 변경 ${fmt(changed)}`, actions: [] })
    await afterDecision()
  } catch (error) {
    acceptAllError.value = errorMessage(error)
  } finally {
    acceptingAll.value = false
  }
}

// 다른 라벨로 수락할 때 고를 라벨: 지금 라벨과 제안 라벨을 뺀 나머지
function otherLabels(item: SuspectRead): LabelRead[] {
  return props.dataset.labels.filter(
    (label) => label.id !== item.label.id && label.id !== item.suggested_label.id,
  )
}

function probabilityText(value: number | null): string {
  return value === null ? '—' : value.toFixed(2)
}
</script>

<template>
  <div class="max-w-[1320px] space-y-3 px-6 py-5 xl:px-8">
    <div class="flex flex-wrap items-center gap-x-5 gap-y-2">
      <span class="inline-flex items-center gap-2">
        <span class="text-meta text-muted-foreground">상태</span>
        <span :class="SEGMENT_CLASS" role="radiogroup" aria-label="상태 거르기">
          <button
            v-for="view in DECISION_VIEWS"
            :key="view"
            type="button"
            :aria-pressed="decision === view"
            :disabled="!hasAnalysis"
            :class="SEGMENT_BUTTON_CLASS"
            :data-decision="view"
            @click="decision = view"
          >
            {{ DECISION_NAMES[view] }}<span class="font-medium text-muted-foreground">{{ fmt(counts[view]) }}</span>
          </button>
        </span>
      </span>
      <span v-if="isOpenView" class="inline-flex items-center gap-2">
        <span class="text-meta text-muted-foreground">출처</span>
        <span :class="SEGMENT_CLASS" role="radiogroup" aria-label="출처 거르기">
          <button
            type="button"
            :aria-pressed="!confirmedOnly"
            :disabled="!hasAnalysis"
            :class="SEGMENT_BUTTON_CLASS"
            @click="confirmedOnly = false"
          >
            전체
          </button>
          <button
            type="button"
            :aria-pressed="confirmedOnly"
            :disabled="!hasAnalysis"
            :class="SEGMENT_BUTTON_CLASS"
            data-source="jev"
            @click="confirmedOnly = true"
          >
            Jev 확인<span class="font-medium text-muted-foreground">{{ fmt(counts.confirmed) }}</span>
          </button>
        </span>
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        class="ml-auto"
        :disabled="!counts.confirmed"
        data-action="open-accept-confirmed"
        @click="openAcceptAll"
      >
        <CheckCheck />Jev 확인 모두 수락<span class="count-pill">{{ fmt(counts.confirmed) }}</span>
      </Button>
    </div>

    <section class="card overflow-clip" aria-label="제안">
      <div class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2">
        <span class="flex items-baseline gap-2">
          <Skeleton v-if="hasAnalysis && !result && !loadError" class="h-5 w-16" />
          <b v-else class="text-title font-semibold">{{ fmt(total) }}건</b>
          <span class="text-ui text-muted-foreground">{{ DECISION_NAMES[decision] }} · 오라벨 의심</span>
        </span>
        <div class="ml-auto flex flex-wrap items-center justify-end gap-x-2 text-ui text-muted-foreground">
          <template v-if="checks">
            <span>
              기준 · 지금 라벨 <b class="font-semibold text-foreground">&lt; {{ checks.thresholds.suspect_label_probability }}</b>
              · 제안 <b class="font-semibold text-foreground">≥ {{ checks.thresholds.suspect_suggested_probability }}</b>
            </span>
            <span class="text-border-strong">/</span>
          </template>
          <span>
            출처 · 분류기 · Jev <b class="font-semibold text-foreground">{{ jevConnected ? '연결됨' : '미연결' }}</b>
          </span>
          <span class="text-border-strong">/</span>
          <span class="inline-flex items-center gap-1">LLM 제안 <Lock class="size-3" /><b class="font-semibold text-foreground">잠김</b></span>
        </div>
      </div>
      <div v-if="actionError" class="flex items-center gap-2 border-b px-4 py-2 text-ui" role="alert">
        <TriangleAlert class="size-4 shrink-0 text-danger" />
        <span class="font-semibold text-danger-ink">실패</span>
        <span class="text-muted-foreground">{{ actionError }}</span>
      </div>

      <div v-if="isStateLoaded && !hasAnalysis" class="py-14 text-center" data-empty="no-analysis">
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
          <Sparkles class="size-5 text-muted-foreground" />
        </span>
        <div class="mt-3 text-body font-semibold">뜻 분석 없음</div>
        <dl class="mt-2 inline-grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-left text-ui">
          <dt class="text-muted-foreground">만드는 곳</dt>
          <dd>진단 · 뜻 분석</dd>
          <dt class="text-muted-foreground">필요</dt>
          <dd>임베딩 · Jev(선택)</dd>
        </dl>
        <div class="mt-4">
          <Button as-child variant="outline" size="sm">
            <RouterLink :to="{ name: 'classification-diagnosis', params: { datasetId } }">진단 열기</RouterLink>
          </Button>
        </div>
      </div>

      <div v-else-if="loadError" class="flex h-28 items-center justify-center gap-3 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="font-semibold text-danger-ink">오류</span>
        <span class="text-muted-foreground">{{ loadError }}</span>
        <Button variant="outline" size="sm" @click="loadSuspects">다시 불러오기</Button>
      </div>

      <div v-else-if="result && total === 0" class="py-14 text-center" data-empty="suggestions">
        <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
          <Inbox class="size-5 text-muted-foreground" />
        </span>
        <div class="mt-3 text-body font-semibold">{{ DECISION_NAMES[decision] }} 0</div>
        <dl class="mt-2 inline-grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-left text-ui">
          <dt class="text-muted-foreground">찾음</dt>
          <dd>{{ fmt(checks?.suspects.found ?? 0) }}</dd>
          <dt class="text-muted-foreground">재지 못한 라벨</dt>
          <dd>{{ fmt(checks?.suspects.unjudged_labels ?? 0) }}</dd>
        </dl>
      </div>

      <table v-else class="w-full table-fixed border-separate border-spacing-0 text-ui">
        <colgroup>
          <col />
          <col class="w-[26%]" />
          <col class="w-[104px]" />
          <col class="w-[15%]" />
          <col class="w-[216px]" />
        </colgroup>
        <thead>
          <tr>
            <th :class="TH_CLASS" class="pl-4">대상 문장</th>
            <th :class="TH_CLASS">지금 → 제안</th>
            <th :class="TH_CLASS" class="text-right">분류기</th>
            <th :class="TH_CLASS">Jev</th>
            <th :class="TH_CLASS" class="pr-4 text-right">동작</th>
          </tr>
        </thead>
        <tbody v-if="!rows">
          <tr v-for="index in SKELETON_ROWS" :key="index">
            <td :class="TD_CLASS" class="pl-4"><Skeleton class="h-4 w-4/5" /></td>
            <td :class="TD_CLASS"><Skeleton class="h-5 w-36" /></td>
            <td :class="TD_CLASS"><Skeleton class="ml-auto h-4 w-16" /></td>
            <td :class="TD_CLASS"><Skeleton class="h-4 w-20" /></td>
            <td :class="TD_CLASS" class="pr-4"><Skeleton class="ml-auto h-7 w-32" /></td>
          </tr>
        </tbody>
        <tbody v-else :class="loading && 'opacity-60'">
          <tr v-for="item in rows" :key="item.text_hash" class="group" :data-suspect="item.text_hash">
            <td :class="TD_CLASS" class="pl-4">
              <span class="flex min-w-0 items-center gap-1.5">
                <span class="truncate" :title="item.text">“{{ item.text }}”</span>
                <span v-if="item.record_count > 1" class="count-pill shrink-0">×{{ fmt(item.record_count) }}</span>
              </span>
            </td>
            <td :class="TD_CLASS">
              <span class="flex min-w-0 items-center gap-1.5">
                <span class="min-w-0"><LabelChip :name="item.label.name" /></span>
                <span class="shrink-0 text-muted-foreground">→</span>
                <span class="min-w-0"><LabelChip :name="item.suggested_label.name" /></span>
              </span>
            </td>
            <td :class="TD_CLASS" class="text-right whitespace-nowrap tabular-nums">
              <span class="text-muted-foreground">{{ probabilityText(item.label_probability) }}</span>
              <span class="px-1 text-subtle-foreground">→</span>
              <b class="font-semibold">{{ probabilityText(item.suggested_probability) }}</b>
            </td>
            <td :class="TD_CLASS">
              <span v-if="item.jev_label" class="flex min-w-0 items-center gap-1.5 whitespace-nowrap">
                <span v-if="item.is_confirmed" :class="TAG_CLASS" class="text-success-ink">
                  <ShieldCheck class="text-success" />확인
                </span>
                <span class="min-w-0 truncate" :title="item.jev_label.name">{{ item.jev_label.name }}</span>
                <span class="text-meta text-muted-foreground tabular-nums">{{ probabilityText(item.jev_confidence) }}</span>
              </span>
              <span v-else class="text-subtle-foreground">—</span>
            </td>
            <td :class="TD_CLASS" class="pr-4">
              <span v-if="item.decision" class="flex items-center justify-end gap-2 whitespace-nowrap">
                <span :class="TAG_CLASS">
                  <Check v-if="item.decision === 'accepted'" class="text-success" /><Pin v-else class="text-muted-foreground" />
                  {{ DECISION_NAMES[item.decision] }}
                </span>
                <span class="text-meta text-muted-foreground">{{ dateTimeText(item.decided_at) }}</span>
              </span>
              <span v-else class="flex items-center justify-end gap-1">
                <Button
                  type="button"
                  size="xs"
                  :disabled="busyHash !== null"
                  data-action="suspect-accept"
                  @click="accept(item)"
                >
                  <LoaderCircle v-if="busyHash === item.text_hash" class="animate-spin" /><Check v-else />수락
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  :disabled="busyHash !== null"
                  data-action="suspect-keep"
                  @click="keep(item)"
                >
                  <Pin />유지
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger as-child>
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      :disabled="busyHash !== null || !otherLabels(item).length"
                      aria-label="다른 라벨로 수락"
                      data-action="suspect-accept-other"
                    >
                      <ChevronDown />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" class="max-h-72 min-w-44 overflow-y-auto">
                    <DropdownMenuLabel>다른 라벨로 수락</DropdownMenuLabel>
                    <DropdownMenuItem v-for="label in otherLabels(item)" :key="label.id" @select="accept(item, label)">
                      <span class="truncate">{{ label.name }}</span>
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      <div
        v-if="hasAnalysis && result && total > 0"
        class="flex h-11 items-center gap-3 border-t bg-muted/30 px-4 text-ui text-muted-foreground"
      >
        <span v-if="rows?.length">{{ fmt(firstNumber) }}–{{ fmt(lastNumber) }} / {{ fmt(total) }}건</span>
        <span class="text-border-strong">·</span>
        <span>정렬 · Jev 확인 → 지금 라벨 확률</span>
        <span class="ml-auto inline-flex items-center gap-1.5">
          <ShieldCheck class="size-3.5" />수락 전 데이터 변경 없음
        </span>
        <div v-if="total > PAGE_SIZE" class="flex items-center gap-1">
          <Button variant="quiet" size="xs" :disabled="page <= 1" aria-label="이전 쪽" @click="page -= 1">
            <ChevronLeft />이전
          </Button>
          <span class="text-subtle-foreground">·</span>
          <Button variant="quiet" size="xs" :disabled="!hasNextPage" aria-label="다음 쪽" @click="page += 1">
            다음<ChevronRight />
          </Button>
        </div>
      </div>
    </section>

    <Dialog v-model:open="acceptAllOpen">
      <DialogContent
        class="w-[min(420px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
        data-dialog="accept-confirmed"
      >
        <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
          <CheckCheck class="size-4 text-primary" />
          <DialogTitle class="text-title font-semibold">Jev 확인 모두 수락</DialogTitle>
          <DialogDescription class="sr-only">Jev도 확인한 오라벨 의심을 모두 제안 라벨로 바꾼다</DialogDescription>
        </DialogHeader>
        <div class="space-y-4 px-5 py-4">
          <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">의심</dt>
            <dd><b class="font-semibold">{{ fmt(counts.confirmed) }}</b> · 대기 중 Jev 확인 전부</dd>
            <dt class="text-muted-foreground">바뀜</dt>
            <dd>라벨 → 제안 라벨 (같은 문장 모두)</dd>
            <dt class="text-muted-foreground">그대로</dt>
            <dd>Jev 미확인 의심 {{ fmt(counts.open - counts.confirmed) }}</dd>
          </dl>
          <div v-if="acceptAllError" class="flex items-start gap-2 text-ui" role="alert">
            <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger" />
            <span class="font-semibold text-danger-ink">실패</span>
            <span class="text-muted-foreground">{{ acceptAllError }}</span>
          </div>
          <div class="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" @click="acceptAllOpen = false">취소</Button>
            <Button type="button" :disabled="acceptingAll" data-action="accept-confirmed" @click="acceptAll">
              <LoaderCircle v-if="acceptingAll" class="animate-spin" /><CheckCheck v-else />모두 수락
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
