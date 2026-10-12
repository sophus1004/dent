<!--
  데이터셋 화면의 틀 (#/generation/<id>/…, 분류 · 검색과 같은 짜임):
    상단 바   생성 › 데이터셋 이름 › 지금 탭 · [도우미 ⌘J] · [+ 데이터 추가](#/generation/<id>/add) · [내보내기]
    머리      이름 · 선호 · 평가셋 태그 / 입구 n단계 / 대화 n / 문제 n / 수정 시각
              · [⋯] → [평가셋으로 표시 | 평가셋 표시 해제] · [데이터셋 지우기]
              머리 칸이 51.25rem보다 좁으면 수는 이름 아래 줄로 내리고 [⋯]는 이름 줄 끝에 둔다(@container).
    탭        진단(검토 포함, 문제가 남은 대화 수 보라 알약) · 데이터(대화 수) · 휴지통 · 가져온 기록
    본문      지금 탭(RouterView). 데이터셋을 읽은 뒤에 그리고, 읽은 데이터셋을 dataset으로 넘긴다.
  탭이 데이터를 바꾸면 datasetRefresh로 머리를 다시 읽는다. 작업이 끝나거나(jobEvents) 도우미가 바꾸면(helperSignals)도 다시 읽는다.
  오른쪽 끝의 빈 자리(#dataset-side)는 데이터 탭이 대화 패널을 그리는 곳이다. 그 오른쪽에 도우미 창(HelperPanel)이 붙는다.
-->
<script setup lang="ts">
import {
  Database,
  Download,
  Ellipsis,
  FlaskConical,
  House,
  MessageSquareText,
  Plus,
  SearchX,
  Sparkles,
  Trash2,
  TriangleAlert,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { helperChanged, isHelperOpen, setHelperOpen, toggleHelper } from '@/system/helper/helperSignals'
import HelperPanel from '@/system/helper/HelperPanel.vue'
import { helperEnter, helperLeave } from '@/system/helper/helperMotion'
import { dateTimeText, fmt } from '@/system/format'
import { ApiError, errorMessage, isAbortError } from '@/system/http'
import TopBar from '@/system/layout/TopBar.vue'
import { Button } from '@/system/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/system/ui/dropdown-menu'
import { Skeleton } from '@/system/ui/skeleton'

import { pushToast } from '@/system/toasts'

import { getDataset, updateSettings } from '@/modules/generation/api'
import DatasetDeleteDialog from '@/modules/generation/DatasetDeleteDialog.vue'
import { provideDatasetRefresh } from '@/modules/generation/datasetRefresh'
import { loadDatasets } from '@/modules/generation/datasets'
import ExportDialog from '@/modules/generation/ExportDialog.vue'
import { generationHelperApi, generationPlanSteps } from '@/modules/generation/helperApi'
import { jobFinished } from '@/modules/generation/jobEvents'
import StageNo from '@/modules/generation/StageNo.vue'
import type { DatasetRead } from '@/modules/generation/types'

interface DatasetTab {
  key: string
  name: string
  routeName: string
  // 이 탭 아래의 화면 (탭을 켠 채로 보인다). 예: 진단 → 검토
  also?: string[]
  count?: (dataset: DatasetRead) => number
}

const TABS: DatasetTab[] = [
  { key: 'diagnosis', name: '진단', routeName: 'generation-diagnosis', also: ['generation-review'] },
  { key: 'data', name: '데이터', routeName: 'generation-data', count: (dataset) => dataset.conversation_count },
  { key: 'trash', name: '휴지통', routeName: 'generation-trash', count: (dataset) => dataset.trash_count },
  { key: 'imports', name: '가져온 기록', routeName: 'generation-imports' },
]

// 탭 아래 화면의 이름 (상단 바 끝 칸)
const SUB_PAGE_NAMES: Record<string, string> = { 'generation-review': '검토' }

const TAB_CLASS =
  'relative inline-flex h-10 items-center gap-1.5 px-2.5 text-body font-[550] whitespace-nowrap transition-colors'
const ACTIVE_TAB_CLASS =
  'text-foreground after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full after:bg-primary'

const route = useRoute()
const router = useRouter()

const datasetId = computed(() => Number(route.params.datasetId))
const dataset = ref<DatasetRead | null>(null)
const notFound = ref(false)
const loadError = ref<string | null>(null)
const deleteOpen = ref(false)
const exportOpen = ref(false)
let controller: AbortController | null = null

function isActive(tab: DatasetTab): boolean {
  const name = String(route.name)
  return tab.routeName === name || (tab.also?.includes(name) ?? false)
}

const currentTab = computed(() => TABS.find(isActive) ?? null)

const crumbs = computed(() => {
  const home = { label: '생성', icon: MessageSquareText, to: '/' }
  if (notFound.value) return [home, { label: '없는 데이터셋' }]
  const name = dataset.value?.name ?? '…'
  const tab = currentTab.value
  const datasetCrumb = { label: name, to: tab ? `/generation/${datasetId.value}` : undefined }
  if (!tab) return [home, datasetCrumb]
  const subName = SUB_PAGE_NAMES[String(route.name)]
  if (!subName) return [home, datasetCrumb, { label: tab.name }]
  return [home, datasetCrumb, { label: tab.name, to: `/generation/${datasetId.value}/${tab.key}` }, { label: subName }]
})

watch(datasetId, loadDataset, { immediate: true })
watch([helperChanged, jobFinished], () => void refreshDataset())
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  controller?.abort()
  window.removeEventListener('keydown', onKeydown)
})

/** ⌘J(맥) · Ctrl+J: 도우미 창을 열고 닫는다. */
function onKeydown(event: KeyboardEvent): void {
  const isToggle = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'j'
  if (!isToggle || notFound.value) return
  event.preventDefault()
  toggleHelper()
}

async function loadDataset(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  dataset.value = null
  notFound.value = false
  loadError.value = null
  try {
    dataset.value = await getDataset(datasetId.value, current.signal)
  } catch (error) {
    if (isAbortError(error)) return
    const isMissing = error instanceof ApiError && error.status === 404
    if (isMissing) notFound.value = true
    else loadError.value = errorMessage(error)
  }
}

/** 머리 · 탭의 수와 사이드바 목록을 다시 읽는다. 보이는 것은 그대로 두고 바꿔 끼운다. */
async function refreshDataset(): Promise<void> {
  const id = datasetId.value
  void loadDatasets()
  try {
    const fresh = await getDataset(id)
    if (id === datasetId.value) dataset.value = fresh
  } catch {
    // 수만 못 바꾼다. 다음에 머리를 읽을 때 맞춰진다.
  }
}

provideDatasetRefresh(refreshDataset)

const planSteps = computed(() => generationPlanSteps(dataset.value?.settings.goal === 'preference'))

/** [⋯] → 평가셋으로 표시 · 해제. 다른 데이터셋의 오염 대조가 다시 들어간다. */
async function toggleEval(): Promise<void> {
  if (!dataset.value) return
  const next = !dataset.value.settings.is_eval
  try {
    await updateSettings(datasetId.value, { is_eval: next })
    await refreshDataset()
  } catch (error) {
    pushToast({ tone: 'fail', title: '평가셋 표시', detail: errorMessage(error), actions: [] })
  }
}

function onDeleted(): void {
  void loadDatasets()
  void router.push('/')
}
</script>

<template>
  <TopBar :crumbs="crumbs">
    <Button
      v-if="!notFound"
      type="button"
      variant="outline"
      size="sm"
      :aria-pressed="isHelperOpen"
      :class="isHelperOpen ? 'border-primary/40 bg-accent text-accent-foreground hover:bg-accent' : ''"
      data-action="helper-toggle"
      @click="toggleHelper"
    >
      <Sparkles />도우미<span class="kbd ml-0.5">⌘J</span>
    </Button>
    <Button v-if="!notFound" variant="outline" size="sm" as-child>
      <RouterLink :to="`/generation/${datasetId}/add`" data-action="add-data"><Plus />데이터 추가</RouterLink>
    </Button>
    <Button v-if="dataset" type="button" variant="outline" size="sm" data-action="open-export" @click="exportOpen = true">
      <Download />내보내기
    </Button>
  </TopBar>

  <div v-if="notFound" class="page">
    <section class="card flex flex-col items-center px-6 py-12 text-center" aria-label="없는 데이터셋">
      <span class="grid size-11 place-items-center rounded-xl bg-muted">
        <SearchX class="size-5 text-muted-foreground" />
      </span>
      <div class="mt-3 text-title font-semibold">없는 데이터셋</div>
      <div class="mt-1 font-mono text-meta text-muted-foreground">#{{ route.params.datasetId }}</div>
      <Button variant="outline" class="mt-5" as-child>
        <RouterLink to="/"><House />홈으로</RouterLink>
      </Button>
    </section>
  </div>

  <div v-else-if="loadError" class="page">
    <section class="card flex h-28 items-center justify-center gap-3 text-ui" aria-label="데이터셋 오류">
      <TriangleAlert class="size-4 text-danger" />
      <span class="font-medium">데이터셋</span>
      <span class="font-semibold text-danger-ink">오류</span>
      <span class="text-muted-foreground">{{ loadError }}</span>
      <Button variant="outline" size="sm" @click="loadDataset">다시 불러오기</Button>
    </section>
  </div>

  <div v-else class="flex flex-1 items-start" data-helper-region>
    <div class="min-w-0 flex-1">
      <div class="border-b bg-card/60">
        <div class="@container px-6 pt-4 xl:px-8">
          <div class="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1">
            <span class="grid size-8 shrink-0 place-items-center rounded-lg border bg-card">
              <Database class="size-4 text-muted-foreground" />
            </span>
            <template v-if="dataset">
              <h1 class="text-title font-semibold tracking-[-0.01em]">{{ dataset.name }}</h1>
              <span v-if="dataset.importing" class="rounded-md bg-muted px-1.5 text-meta font-semibold text-muted-foreground">
                가져오는 중
              </span>
              <span
                v-if="dataset.settings.goal === 'preference'"
                class="rounded-md bg-accent px-1.5 text-meta font-semibold text-accent-foreground"
                data-goal-tag
              >
                선호
              </span>
              <span
                v-if="dataset.settings.is_eval"
                class="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 text-meta font-semibold text-foreground"
                data-eval-tag
              >
                <FlaskConical class="size-3" />평가셋
              </span>
              <div
                class="order-2 flex flex-wrap items-center gap-x-2 text-ui text-muted-foreground @max-[51.25rem]:order-4 @max-[51.25rem]:basis-full @max-[51.25rem]:pl-11 @max-[51.25rem]:[&>span:first-child]:hidden"
              >
                <span class="text-border-strong">/</span>
                <span class="inline-flex items-center gap-1">입구 <StageNo :no="dataset.entry_stage" size="xs" /></span>
                <span class="text-border-strong">/</span>
                <span>대화 <b class="font-semibold text-foreground">{{ fmt(dataset.conversation_count) }}</b></span>
                <span class="text-border-strong">/</span>
                <span>문제 <b class="font-semibold text-foreground">{{ fmt(dataset.problem_count) }}</b></span>
                <span class="text-border-strong">/</span>
                <span>수정 <b class="font-semibold text-foreground">{{ dateTimeText(dataset.updated_at) }}</b></span>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger as-child>
                  <Button variant="quiet" size="icon-sm" class="order-3 ml-auto" aria-label="데이터셋 메뉴" data-action="dataset-menu">
                    <Ellipsis />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" class="min-w-44">
                  <DropdownMenuItem data-action="toggle-eval" @select="toggleEval">
                    <FlaskConical />{{ dataset.settings.is_eval ? '평가셋 표시 해제' : '평가셋으로 표시' }}
                  </DropdownMenuItem>
                  <DropdownMenuItem class="text-danger-ink" data-action="open-dataset-delete" @select="deleteOpen = true">
                    <Trash2 />데이터셋 지우기
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <DatasetDeleteDialog v-model:open="deleteOpen" :dataset="dataset" @deleted="onDeleted" />
              <ExportDialog v-model:open="exportOpen" :dataset="dataset" />
            </template>
            <template v-else>
              <Skeleton class="h-5 w-48" />
              <Skeleton class="h-4 w-80" />
            </template>
          </div>

          <nav class="mt-2 -mb-px -ml-2.5 flex gap-0.5 overflow-x-auto" aria-label="데이터셋 화면">
            <RouterLink
              v-for="tab in TABS"
              :key="tab.key"
              :to="{ name: tab.routeName, params: { datasetId: route.params.datasetId } }"
              :class="[TAB_CLASS, isActive(tab) ? ACTIVE_TAB_CLASS : 'text-muted-foreground hover:text-foreground']"
              :aria-current="isActive(tab) ? 'page' : undefined"
              :data-tab="tab.key"
            >
              {{ tab.name }}
              <span v-if="dataset && tab.count" class="count-pill">{{ fmt(tab.count(dataset)) }}</span>
              <span
                v-if="dataset && tab.key === 'diagnosis' && dataset.problem_count"
                class="count-pill bg-accent text-accent-foreground"
                data-open-problems
              >
                {{ fmt(dataset.problem_count) }}
              </span>
            </RouterLink>
          </nav>
        </div>
      </div>

      <RouterView v-if="dataset" v-slot="{ Component }">
        <component :is="Component" :dataset="dataset" />
      </RouterView>
    </div>
    <div id="dataset-side" class="sticky top-[52px] z-20 self-start" />
    <!-- 사람이 열고 닫으면 창이 부드럽게 붙고 떨어진다(system/helper/helperMotion.ts) -->
    <Transition :css="false" @enter="helperEnter" @leave="helperLeave">
      <HelperPanel
        v-if="isHelperOpen && dataset"
        :dataset-id="datasetId"
        :api="generationHelperApi"
        :plan-steps="planSteps"
        @close="setHelperOpen(false)"
      />
    </Transition>
  </div>
</template>
