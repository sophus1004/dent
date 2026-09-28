<!--
  데이터셋 화면의 틀 (#/retrieval/<id>/…, 분류와 같은 짜임):
    상단 바   검색 › 데이터셋 이름 › 지금 탭 · [도우미 ⌘J] · [+ 데이터 추가](#/retrieval/<id>/add) · [내보내기]
    머리      이름 / 입구 n단계 / 질의 n / 문서 n / 정답 · 오답 / 수정 시각 · [⋯] → [설정] · [반복 구간] · [데이터셋 지우기]
              머리 칸이 51.25rem보다 좁으면(도우미 창을 옆에 둘 때) 수는 이름 아래 줄로 내리고 [⋯]는 이름 줄 끝에 둔다(@container).
    탭        진단 · 데이터(질의 수) · 제안(대기 수, 보라 알약) · 휴지통 · 가져온 기록
    본문      지금 탭(RouterView). 데이터셋을 읽은 뒤에 그리고, 읽은 데이터셋을 dataset으로 넘긴다.
  탭이 데이터를 바꾸면 datasetRefresh로 머리를 다시 읽는다. 작업이 끝나거나(jobEvents) 도우미가 바꾸면(helperSignals)도 다시 읽는다.
  오른쪽 끝의 빈 자리(#dataset-side)는 탭이 질의 · 문서 패널을 그리는 곳이다. 그 오른쪽에 도우미 창(HelperPanel)이 붙는다.
  반복 구간 고르기 창(RepeatsDialog)은 주소의 ?repeats=1로 연다(진단의 '반복 구간' 줄 [→]가 이 주소로 온다). 닫으면 주소에서 뺀다.
-->
<script setup lang="ts">
import {
  Database,
  Download,
  Ellipsis,
  House,
  Plus,
  SearchX,
  Settings2,
  Sparkles,
  Stamp,
  TextSearch,
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

import { getDataset, listSuggestions } from '@/modules/retrieval/api'
import DatasetDeleteDialog from '@/modules/retrieval/DatasetDeleteDialog.vue'
import { provideDatasetRefresh } from '@/modules/retrieval/datasetRefresh'
import { loadDatasets } from '@/modules/retrieval/datasets'
import ExportDialog from '@/modules/retrieval/ExportDialog.vue'
import { RETRIEVAL_PLAN_STEPS, retrievalHelperApi } from '@/modules/retrieval/helperApi'
import { jobFinished } from '@/modules/retrieval/jobEvents'
import RepeatsDialog from '@/modules/retrieval/RepeatsDialog.vue'
import SettingsDialog from '@/modules/retrieval/SettingsDialog.vue'
import StageNo from '@/modules/retrieval/StageNo.vue'
import type { DatasetRead } from '@/modules/retrieval/types'

interface DatasetTab {
  key: string
  name: string
  routeName: string
  count?: (dataset: DatasetRead) => number
}

const TABS: DatasetTab[] = [
  { key: 'diagnosis', name: '진단', routeName: 'retrieval-diagnosis' },
  { key: 'data', name: '데이터', routeName: 'retrieval-data', count: (dataset) => dataset.query_count },
  { key: 'suggestions', name: '제안', routeName: 'retrieval-suggestions' },
  { key: 'trash', name: '휴지통', routeName: 'retrieval-trash', count: (dataset) => dataset.trash_count },
  { key: 'imports', name: '가져온 기록', routeName: 'retrieval-imports' },
]

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
const settingsOpen = ref(false)
const exportOpen = ref(false)
// 제안 탭 옆: 판단을 기다리는 제안 수. 모르면 null
const openSuggestions = ref<number | null>(null)
let controller: AbortController | null = null

// 반복 구간 고르기 창: 주소의 ?repeats=1이 열림이다(진단의 검사 줄이 이 주소로 온다).
const repeatsOpen = computed({
  get: () => route.query.repeats === '1',
  set: (isOpen: boolean) => {
    const { repeats: _repeats, ...rest } = route.query
    void router.replace({ query: isOpen ? { ...rest, repeats: '1' } : rest })
  },
})

const currentTab = computed(() => TABS.find((tab) => tab.routeName === route.name) ?? null)

const crumbs = computed(() => {
  const home = { label: '검색', icon: TextSearch, to: '/' }
  if (notFound.value) return [home, { label: '없는 데이터셋' }]
  const name = dataset.value?.name ?? '…'
  const tabName = currentTab.value?.name
  const datasetCrumb = { label: name, to: tabName ? `/retrieval/${datasetId.value}` : undefined }
  return tabName ? [home, datasetCrumb, { label: tabName }] : [home, datasetCrumb]
})

watch(datasetId, loadDataset, { immediate: true })
watch([datasetId, jobFinished], loadCounts, { immediate: true })
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

/** 제안 대기 수. 못 읽으면 배지를 뗀다. */
async function loadCounts(): Promise<void> {
  const id = datasetId.value
  try {
    const page = await listSuggestions(id, { limit: 1 })
    if (id === datasetId.value) openSuggestions.value = Object.values(page.pending_counts).reduce((a, b) => a + b, 0)
  } catch {
    if (id === datasetId.value) openSuggestions.value = null
  }
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
  void loadCounts()
}

provideDatasetRefresh(refreshDataset)

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
      <RouterLink :to="`/retrieval/${datasetId}/add`" data-action="add-data"><Plus />데이터 추가</RouterLink>
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
              <div
                class="order-2 flex flex-wrap items-center gap-x-2 text-ui text-muted-foreground @max-[51.25rem]:order-4 @max-[51.25rem]:basis-full @max-[51.25rem]:pl-11 @max-[51.25rem]:[&>span:first-child]:hidden"
              >
                <template v-if="dataset.entry_stage">
                  <span class="text-border-strong">/</span>
                  <span class="inline-flex items-center gap-1">입구 <StageNo :no="dataset.entry_stage" size="xs" /></span>
                </template>
                <span class="text-border-strong">/</span>
                <span>질의 <b class="font-semibold text-foreground">{{ fmt(dataset.query_count) }}</b></span>
                <span class="text-border-strong">/</span>
                <span>문서 <b class="font-semibold text-foreground">{{ fmt(dataset.document_count) }}</b></span>
                <span class="text-border-strong">/</span>
                <span>
                  정답 <b class="font-semibold text-foreground">{{ fmt(dataset.positive_count) }}</b>
                  · 오답 <b class="font-semibold text-foreground">{{ fmt(dataset.negative_count) }}</b>
                </span>
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
                  <DropdownMenuItem v-if="dataset.settings" data-action="open-settings" @select="settingsOpen = true">
                    <Settings2 />설정
                  </DropdownMenuItem>
                  <DropdownMenuItem v-if="dataset.document_count" data-action="open-repeats" @select="repeatsOpen = true">
                    <Stamp />반복 구간
                  </DropdownMenuItem>
                  <DropdownMenuItem class="text-danger-ink" data-action="open-dataset-delete" @select="deleteOpen = true">
                    <Trash2 />데이터셋 지우기
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <DatasetDeleteDialog v-model:open="deleteOpen" :dataset="dataset" @deleted="onDeleted" />
              <SettingsDialog
                v-if="dataset.settings"
                v-model:open="settingsOpen"
                :dataset-id="dataset.id"
                :settings="dataset.settings"
                @saved="refreshDataset"
              />
              <ExportDialog v-model:open="exportOpen" :dataset="dataset" />
              <RepeatsDialog v-model:open="repeatsOpen" :dataset-id="dataset.id" @changed="refreshDataset" />
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
              :class="[TAB_CLASS, tab.routeName === route.name ? ACTIVE_TAB_CLASS : 'text-muted-foreground hover:text-foreground']"
              :aria-current="tab.routeName === route.name ? 'page' : undefined"
              :data-tab="tab.key"
            >
              {{ tab.name }}
              <span v-if="dataset && tab.count" class="count-pill">{{ fmt(tab.count(dataset)) }}</span>
              <span
                v-if="tab.key === 'suggestions' && openSuggestions"
                class="count-pill bg-accent text-accent-foreground"
                data-open-suggestions
              >
                {{ fmt(openSuggestions) }}
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
        :api="retrievalHelperApi"
        :plan-steps="RETRIEVAL_PLAN_STEPS"
        @close="setHelperOpen(false)"
      />
    </Transition>
  </div>
</template>
