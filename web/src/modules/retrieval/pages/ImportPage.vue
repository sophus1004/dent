<!--
  새 검색 데이터셋 가져오기 (#/retrieval/new) · 데이터 추가 (#/retrieval/<id>/add): 네 단계를 한 화면에서 차례로 보인다(분류와 같은 짜임).
    1 파일 고르기   데이터셋 이름(추가면 더할 곳) + 가져올 곳 두 칸(시스템 SourcePicker) · 새 데이터셋이면 예시 목록
    2 형식 확인     서버가 읽은 형식과 앞쪽 줄(시스템 SourceCheck)
    3 필드 맞추기   원본 모양 6장 · 칸 맞추기 · 들어갈 모양(이 모듈의 MappingStep)
    4 가져오기      검색 가져오기 API를 부르고 진행률 · 결과(질의 · 문서 · 합침 · 판정 · 충돌)를 보인다(시스템 ImportRun)
  단계 사이의 값(원본, 칸 맞춤, 이름)은 모두 이 화면이 가진다. 가져오기를 시작하거나 끝내면 사이드바와 홈의 목록을 다시 읽는다.
  데이터 추가는 있던 데이터셋에 넣는다(같은 질의 · 문서는 합치고 판정을 모은다). 입구는 처음 가져온 모양 그대로다. 끝나면 [데이터셋으로].
-->
<script setup lang="ts">
import { Database, TextSearch } from '@lucide/vue'
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
import { Input } from '@/system/ui/input'

import { createImport, getDataset, installExample, listExamples } from '@/modules/retrieval/api'
import { loadDatasets } from '@/modules/retrieval/datasets'
import MappingStep from '@/modules/retrieval/MappingStep.vue'
import { SHAPES } from '@/modules/retrieval/shapes'
import StageNo from '@/modules/retrieval/StageNo.vue'
import type { DatasetRead, FieldMapping } from '@/modules/retrieval/types'

const STEPS = ['파일 고르기', '형식 확인', '필드 맞추기', '가져오기']

// 데이터셋 이름의 최대 길이. 백엔드(system/models.py의 DATASET_NAME_MAX_LENGTH)와 같다.
const DATASET_NAME_MAX_LENGTH = 200

const LOCKED_PHASES: ImportPhase[] = ['starting', 'running', 'done']

// 가져오기 결과의 수 (importing.RESULT_KEYS)
const RESULT_WORDS: [string, string][] = [
  ['queries_added', '질의'],
  ['documents_added', '문서'],
  ['documents_merged', '같은 문서 합침'],
  ['judgments_added', '판정'],
  ['conflicts', '판정 충돌'],
]

const route = useRoute()

const step = ref(1)
const datasetName = ref('')
const repo = ref('')
const source = ref<LoadedSource | null>(null)
const mapping = ref<FieldMapping>(emptyMapping())
const createdDataset = ref<{ id: number; name: string } | null>(null)
let autoName = ''

// 데이터 추가면 더할 데이터셋 번호 (주소의 번호). 새 데이터셋이면 null
const targetId = computed(() => (route.params.datasetId ? Number(route.params.datasetId) : null))
const target = ref<DatasetRead | null>(null)
const targetError = ref<string | null>(null)
const isAdding = computed(() => targetId.value !== null)

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
const canStart = computed(
  () => source.value !== null && (isAdding.value ? target.value !== null : !nameProblem.value),
)
const rowCount = computed(() => (source.value ? sourceRowCount(source.value) : null))
const shape = computed(() => SHAPES[mapping.value.shape])

const crumbs = computed(() => {
  if (!isAdding.value) return [{ label: '검색', icon: TextSearch }, { label: '새 데이터셋' }]
  const home = { label: '검색', icon: TextSearch, to: '/' }
  return [home, { label: target.value?.name ?? '…', to: `/retrieval/${targetId.value}` }, { label: '데이터 추가' }]
})
const doneTo = computed(() => (isAdding.value ? { label: '데이터셋으로', to: `/retrieval/${targetId.value}` } : null))

// 새 데이터셋 ↔ 데이터 추가, 다른 데이터셋으로 옮기면 처음부터 다시 고른다.
watch(
  targetId,
  () => {
    restart()
    void loadTarget()
  },
  { immediate: true },
)

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

function emptyMapping(): FieldMapping {
  return {
    shape: 'pair',
    text: null,
    query: null,
    positive: null,
    negatives: [],
    document: null,
    score: null,
    answer: null,
    title: null,
    doc_key: null,
    header_columns: [],
    group_column: null,
  }
}

watch(source, (next, previous) => {
  if (!next) return
  const suggestedName = defaultDatasetName(next)
  const isNameUntouched = trimmedName.value === '' || datasetName.value === autoName
  if (isNameUntouched) datasetName.value = suggestedName
  autoName = suggestedName
  const isNewSource = !previous || sourceKey(previous) !== sourceKey(next)
  if (isNewSource) mapping.value = emptyMapping()
})

watch(step, () => window.scrollTo({ top: 0 }))

function onLoaded(loaded: LoadedSource): void {
  source.value = loaded
  step.value = 2
}

async function startImport(): Promise<ImportRead> {
  const loaded = source.value
  if (!loaded) throw new Error('가져올 원본을 먼저 고르세요.')
  if (targetId.value !== null) {
    return createImport({ ...toSourceCreate(loaded), dataset_id: targetId.value, new_dataset_name: null, mapping: mapping.value })
  }
  const created = createdDataset.value
  const isRetryIntoCreated = created !== null && created.name === trimmedName.value
  return createImport({
    ...toSourceCreate(loaded),
    dataset_id: isRetryIntoCreated ? created.id : null,
    new_dataset_name: isRetryIntoCreated ? null : trimmedName.value,
    mapping: mapping.value,
  })
}

function onStarted(read: ImportRead): void {
  if (!isAdding.value) createdDataset.value = { id: read.dataset_id, name: trimmedName.value }
  void loadDatasets()
}

function onFinished(): void {
  void loadDatasets()
}

function resultCount(read: ImportRead, key: string): number {
  const value = read.result[key]
  return typeof value === 'number' ? value : 0
}

function reupload(): void {
  source.value = null
  mapping.value = emptyMapping()
  autoName = ''
  step.value = 1
}

function restart(): void {
  source.value = null
  mapping.value = emptyMapping()
  datasetName.value = ''
  autoName = ''
  repo.value = ''
  createdDataset.value = null
  step.value = 1
}

// 맞춘 칸을 한 줄로 (4단계 요약)
const mappedColumns = computed(() => {
  const m = mapping.value
  const parts: [string, string | null][] = [
    ['본문', m.text],
    ['질의', m.query],
    ['정답', m.positive],
    ['문서', m.document],
    ['점수', m.score],
    ['답', m.answer],
    ['제목', m.title],
  ]
  const shown = parts.filter(([, column]) => column).map(([name, column]) => `${name} ${column}`)
  if (m.negatives.length) shown.push(`오답 ${m.negatives.join(', ')}`)
  return shown.join(' · ')
})
</script>

<template>
  <TopBar :crumbs="crumbs" />

  <div class="mx-auto w-full max-w-[1160px] space-y-6 px-5 py-7 md:px-8">
    <ImportSteps :steps="STEPS" :current="step" />

    <template v-if="step === 1">
      <section v-if="isAdding" aria-label="더할 곳" class="flex flex-wrap items-center gap-x-2 gap-y-1 text-ui" data-add-target>
        <span class="font-[550]">더할 곳</span>
        <Database class="ml-1 size-4 text-muted-foreground" />
        <template v-if="target">
          <b class="font-semibold">{{ target.name }}</b>
          <span class="text-border-strong">/</span>
          <span class="text-muted-foreground">질의 <b class="font-semibold text-foreground">{{ fmt(target.query_count) }}</b></span>
          <span class="text-border-strong">/</span>
          <span class="text-muted-foreground">문서 <b class="font-semibold text-foreground">{{ fmt(target.document_count) }}</b></span>
          <template v-if="target.entry_stage">
            <span class="text-border-strong">/</span>
            <span class="inline-flex items-center gap-1 text-muted-foreground">입구 <StageNo :no="target.entry_stage" size="xs" /></span>
          </template>
          <span class="text-muted-foreground">· 같은 질의 · 문서 합침</span>
        </template>
        <span v-else-if="targetError" class="font-semibold text-danger-ink">{{ targetError }}</span>
        <span v-else class="text-muted-foreground">…</span>
      </section>
      <section v-else aria-label="데이터셋 이름">
        <label for="dataset-name" class="mb-1.5 block text-ui font-[550]">데이터셋 이름</label>
        <Input
          id="dataset-name"
          v-model="datasetName"
          placeholder="예: 사내 규정 검색"
          class="max-w-[420px]"
          :maxlength="DATASET_NAME_MAX_LENGTH"
        />
      </section>
      <SourcePicker v-model:repo="repo" :picked="pickedSource" :loaded="source" @loaded="onLoaded" @next="step = 2" />
      <ExampleList
        v-if="!isAdding"
        :load="listExamples"
        :install="installExample"
        :dataset-route="(id: number) => `/retrieval/${id}`"
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
            <dt class="text-muted-foreground">모양</dt>
            <dd class="flex items-center gap-1.5 font-medium">
              <template v-if="isAdding && target?.entry_stage">
                {{ shape.name }} · 입구 <StageNo :no="target.entry_stage" size="xs" /> <span class="font-normal text-muted-foreground">그대로</span>
              </template>
              <template v-else>{{ shape.name }} · 입구 <StageNo :no="shape.entry" size="xs" /></template>
            </dd>
            <dt class="text-muted-foreground">칸</dt>
            <dd class="truncate font-mono text-meta" :title="mappedColumns">{{ mappedColumns }}</dd>
          </dl>
        </div>
      </template>

      <template #result="{ read }">
        <dl class="grid grid-cols-2 border-t sm:grid-cols-5 sm:divide-x">
          <div v-for="[key, name] in RESULT_WORDS" :key="key" class="px-4 py-3">
            <dt class="text-meta text-muted-foreground">{{ name }}</dt>
            <dd
              class="mt-0.5 text-title font-semibold"
              :class="key === 'conflicts' && resultCount(read, key) ? 'text-warning-ink' : ''"
            >
              {{ fmt(resultCount(read, key)) }}
            </dd>
          </div>
        </dl>
      </template>
    </ImportRun>
  </div>
</template>
