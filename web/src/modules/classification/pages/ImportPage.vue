<!--
  새 데이터셋 가져오기 (#/classification/new) · 데이터 추가 (#/classification/<id>/add): 네 단계를 한 화면에서 차례로 보인다.
    1 파일 고르기   데이터셋 이름(추가면 더할 곳) + 가져올 곳 두 칸(시스템 SourcePicker) · 새 데이터셋이면 예시 목록.
                    불러오면 2단계로 넘어간다.
    2 형식 확인     서버가 읽은 형식과 앞쪽 줄(시스템 SourceCheck)
    3 필드 맞추기   문장 · 라벨 칸 고르기(이 모듈의 MappingStep)
    4 가져오기      분류 가져오기 API를 부르고 진행률 · 결과를 보인다(시스템 ImportRun에 이 모듈의 가져오기를 넘긴다)
  단계 사이의 값(원본, 칸 맞춤, 이름)은 모두 이 화면이 가진다. 가져오기를 시작하거나 끝내면 사이드바와 홈의 목록을 다시 읽는다.
  파일 가져오기가 실패하면 서버가 원본을 지우므로 [다시 올리기]로 1단계에 돌아간다. 이름은 그대로 두어 만든 데이터셋에 다시 넣는다.
  주소의 ?source=file 또는 ?source=huggingface면 그 칸을 보라 테두리로 미리 골라 둔다.
  데이터 추가는 있던 데이터셋에 넣는다(같은 문장은 가져오기가 합치고, 새 라벨은 더한다). 끝나면 [데이터셋으로].
-->
<script setup lang="ts">
import { Database, Tags } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import TopBar from '@/system/layout/TopBar.vue'
import ImportRun from '@/system/sources/ImportRun.vue'
import ImportSteps from '@/system/sources/ImportSteps.vue'
import SourceCheck from '@/system/sources/SourceCheck.vue'
import ExampleList from '@/system/sources/ExampleList.vue'
import SourcePicker from '@/system/sources/SourcePicker.vue'
import {
  defaultDatasetName,
  sourceKey,
  sourceName,
  sourceRowCount,
  toSourceCreate,
  type ImportPhase,
  type LoadedSource,
} from '@/system/sources/source'
import type { ImportRead, ImportSource } from '@/system/types'
import { Badge } from '@/system/ui/badge'
import { Input } from '@/system/ui/input'

import { createImport, getDataset, installExample, listExamples } from '@/modules/classification/api'
import { loadDatasets } from '@/modules/classification/datasets'
import MappingStep from '@/modules/classification/MappingStep.vue'
import { NEW_LABELS_KEY, type DatasetRead, type MappingDraft } from '@/modules/classification/types'

const STEPS = ['파일 고르기', '형식 확인', '필드 맞추기', '가져오기']

// 데이터셋 이름의 최대 길이. 백엔드(system/models.py의 DATASET_NAME_MAX_LENGTH)와 같다.
const DATASET_NAME_MAX_LENGTH = 200

// 4단계에서 이름을 더는 고칠 수 없는 상태
const LOCKED_PHASES: ImportPhase[] = ['starting', 'running', 'done']

const route = useRoute()

const step = ref(1)
const datasetName = ref('')
const repo = ref('')
const source = ref<LoadedSource | null>(null)
const mapping = ref<MappingDraft>(emptyMapping())
// 가져오기를 시작하며 만든 데이터셋. 실패 뒤 같은 이름으로 다시 시도하면 새로 만들지 않고 여기에 넣는다.
const createdDataset = ref<{ id: number; name: string } | null>(null)
// 원본 이름으로 채워 준 이름. 사용자가 고치지 않았으면 원본이 바뀔 때 새 이름으로 바꾼다.
let autoName = ''

// 데이터 추가면 더할 데이터셋 번호 (주소의 번호). 새 데이터셋이면 null
const targetId = computed(() => (route.params.datasetId ? Number(route.params.datasetId) : null))
const target = ref<DatasetRead | null>(null)
const targetError = ref<string | null>(null)
const isAdding = computed(() => targetId.value !== null)

// 주소로 미리 고른 가져올 곳. 없으면 null
const pickedSource = computed<ImportSource | null>(() => {
  const picked = route.query.source
  return picked === 'file' || picked === 'huggingface' ? picked : null
})

const trimmedName = computed(() => datasetName.value.trim())
const nameProblem = computed(() => {
  if (!trimmedName.value) return '이름 없음'
  if (trimmedName.value.length > DATASET_NAME_MAX_LENGTH) return `${DATASET_NAME_MAX_LENGTH}자 넘음`
  return null
})
const isMapped = computed(() => mapping.value.text !== null && mapping.value.label !== null)
const canStart = computed(
  () => source.value !== null && isMapped.value && (isAdding.value ? target.value !== null : !nameProblem.value),
)
const rowCount = computed(() => (source.value ? sourceRowCount(source.value) : null))

function emptyMapping(): MappingDraft {
  return { text: null, label: null }
}

const crumbs = computed(() => {
  const home = { label: '분류', icon: Tags, to: '/' }
  if (!isAdding.value) return [{ label: '분류', icon: Tags }, { label: '새 데이터셋' }]
  return [home, { label: target.value?.name ?? '…', to: `/classification/${targetId.value}` }, { label: '데이터 추가' }]
})
const doneTo = computed(() =>
  isAdding.value ? { label: '데이터셋으로', to: `/classification/${targetId.value}` } : null,
)

// 새 데이터셋 ↔ 데이터 추가, 다른 데이터셋으로 옮기면 처음부터 다시 고른다.
watch(targetId, () => {
  restart()
  void loadTarget()
}, { immediate: true })

async function loadTarget(): Promise<void> {
  target.value = null
  targetError.value = null
  const id = targetId.value
  if (id === null) return
  try {
    const read = await getDataset(id)
    if (id === targetId.value) target.value = read
  } catch (error) {
    if (id === targetId.value) targetError.value = errorMessage(error)
  }
}

watch(source, (next, previous) => {
  if (!next) return
  const suggestedName = defaultDatasetName(next)
  const isNameUntouched = trimmedName.value === '' || datasetName.value === autoName
  if (isNameUntouched) datasetName.value = suggestedName
  autoName = suggestedName
  // 다른 파일·시트·구성이면 칸 이름이 바뀔 수 있어 필드 맞춤을 처음부터 다시 짐작한다.
  const isNewSource = !previous || sourceKey(previous) !== sourceKey(next)
  if (isNewSource) mapping.value = emptyMapping()
})

// 단계를 옮기면 맨 위부터 보인다.
watch(step, () => window.scrollTo({ top: 0 }))

function onLoaded(loaded: LoadedSource): void {
  source.value = loaded
  step.value = 2
}

/** 4단계가 부르는 가져오기 시작. 분류 가져오기 API에 원본 · 칸 맞춤 · 데이터셋을 넘긴다. */
async function startImport(): Promise<ImportRead> {
  const loaded = source.value
  const { text, label } = mapping.value
  if (!loaded || !text || !label) throw new Error('가져올 원본과 칸을 먼저 고르세요.')
  if (targetId.value !== null) {
    return createImport({ ...toSourceCreate(loaded), dataset_id: targetId.value, new_dataset_name: null, mapping: { text, label } })
  }
  const created = createdDataset.value
  const isRetryIntoCreated = created !== null && created.name === trimmedName.value
  return createImport({
    ...toSourceCreate(loaded),
    dataset_id: isRetryIntoCreated ? created.id : null,
    new_dataset_name: isRetryIntoCreated ? null : trimmedName.value,
    mapping: { text, label },
  })
}

function onStarted(read: ImportRead): void {
  if (!isAdding.value) createdDataset.value = { id: read.dataset_id, name: trimmedName.value }
  // 새 데이터셋이 사이드바와 홈 목록에 바로 보이게 한다.
  void loadDatasets()
}

function onFinished(): void {
  void loadDatasets()
}

/** 이번 가져오기에서 새로 만든 라벨 이름들. */
function newLabels(read: ImportRead): string[] {
  const value = read.result[NEW_LABELS_KEY]
  return Array.isArray(value) ? value.filter((name): name is string => typeof name === 'string') : []
}

/** 파일을 다시 올리러 1단계로. 이름과 만든 데이터셋은 그대로 두어 같은 데이터셋에 다시 넣는다. */
function reupload(): void {
  source.value = null
  mapping.value = emptyMapping()
  // 사용자가 정한 이름으로 두어, 새 파일을 올려도 파일 이름으로 바뀌지 않게 한다.
  autoName = ''
  step.value = 1
}

/** 처음(1단계)부터 다시. */
function restart(): void {
  source.value = null
  mapping.value = emptyMapping()
  datasetName.value = ''
  autoName = ''
  repo.value = ''
  createdDataset.value = null
  step.value = 1
}
</script>

<template>
  <TopBar :crumbs="crumbs" />

  <div class="mx-auto w-full max-w-[960px] space-y-6 px-5 py-7 md:px-8">
    <ImportSteps :steps="STEPS" :current="step" />

    <template v-if="step === 1">
      <section v-if="isAdding" aria-label="더할 곳" class="flex flex-wrap items-center gap-x-2 gap-y-1 text-ui" data-add-target>
        <span class="font-[550]">더할 곳</span>
        <Database class="ml-1 size-4 text-muted-foreground" />
        <template v-if="target">
          <b class="font-semibold">{{ target.name }}</b>
          <span class="text-border-strong">/</span>
          <span class="text-muted-foreground">문장 <b class="font-semibold text-foreground">{{ fmt(target.record_count) }}</b></span>
          <span class="text-border-strong">/</span>
          <span class="text-muted-foreground">라벨 <b class="font-semibold text-foreground">{{ fmt(target.label_count) }}</b></span>
          <span class="text-muted-foreground">· 같은 문장 합침 · 새 라벨 추가</span>
        </template>
        <span v-else-if="targetError" class="font-semibold text-danger-ink">{{ targetError }}</span>
        <span v-else class="text-muted-foreground">…</span>
      </section>
      <section v-else aria-label="데이터셋 이름">
        <label for="dataset-name" class="mb-1.5 block text-ui font-[550]">데이터셋 이름</label>
        <Input
          id="dataset-name"
          v-model="datasetName"
          placeholder="예: 고객 문의 의도"
          class="max-w-[420px]"
          :maxlength="DATASET_NAME_MAX_LENGTH"
        />
      </section>
      <SourcePicker v-model:repo="repo" :picked="pickedSource" :loaded="source" @loaded="onLoaded" @next="step = 2" />
      <ExampleList
        v-if="!isAdding"
        :load="listExamples"
        :install="installExample"
        :dataset-route="(id: number) => `/classification/${id}`"
        @installed="loadDatasets"
      />
    </template>

    <SourceCheck v-else-if="step === 2 && source" v-model:source="source" @back="step = 1" @next="step = 3" />

    <MappingStep
      v-else-if="step === 3 && source"
      v-model:mapping="mapping"
      :source="source"
      @back="step = 2"
      @next="step = 4"
    />

    <ImportRun
      v-else-if="step === 4 && source"
      :start="startImport"
      :can-start="canStart"
      :source="source.source"
      :done-to="doneTo"
      @back="step = 3"
      @restart="restart"
      @reupload="reupload"
      @started="onStarted"
      @finished="onFinished"
    >
      <template #default="{ phase }">
        <div class="grid gap-x-8 gap-y-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <div v-if="isAdding">
            <div class="mb-1.5 text-ui font-[550]">더할 곳</div>
            <div class="flex h-9 items-center gap-2 rounded-md border bg-muted/40 px-3 text-ui">
              <Database class="size-4 text-muted-foreground" />
              <span class="truncate font-medium">{{ target?.name ?? '…' }}</span>
            </div>
          </div>
          <div v-else>
            <label for="import-name" class="mb-1.5 flex items-center gap-2 text-ui font-[550]">
              새 데이터셋 이름
              <span v-if="nameProblem" class="text-meta font-semibold text-danger-ink">{{ nameProblem }}</span>
            </label>
            <Input
              id="import-name"
              v-model="datasetName"
              :maxlength="DATASET_NAME_MAX_LENGTH"
              :disabled="LOCKED_PHASES.includes(phase)"
              :aria-invalid="nameProblem ? true : undefined"
            />
          </div>
          <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-ui">
            <dt class="text-muted-foreground">원본</dt>
            <dd class="truncate font-medium">{{ sourceName(source) }}</dd>
            <template v-if="source.source === 'huggingface'">
              <dt class="text-muted-foreground">가져올 분할</dt>
              <dd class="truncate font-mono">{{ source.splits.join(' · ') }} <span class="font-sans text-muted-foreground">→ 하나로</span></dd>
            </template>
            <dt class="text-muted-foreground">줄 수</dt>
            <dd class="font-medium">{{ rowCount === null ? '—' : fmt(rowCount) }}</dd>
            <dt class="text-muted-foreground">문장 칸</dt>
            <dd class="truncate font-mono">{{ mapping.text }}</dd>
            <dt class="text-muted-foreground">라벨 칸</dt>
            <dd class="truncate font-mono">{{ mapping.label }}</dd>
          </dl>
        </div>
      </template>

      <template #result="{ read }">
        <div class="border-t px-4 py-3">
          <div class="mb-2 flex items-center gap-2">
            <span class="caps">새 라벨</span>
            <span class="rounded-full bg-muted px-1.5 text-caps font-semibold text-muted-foreground">
              {{ newLabels(read).length }}
            </span>
          </div>
          <div class="flex flex-wrap gap-1.5">
            <Badge v-for="name in newLabels(read)" :key="name">{{ name }}</Badge>
            <span v-if="newLabels(read).length === 0" class="text-ui text-subtle-foreground">없음</span>
          </div>
        </div>
      </template>
    </ImportRun>
  </div>
</template>
