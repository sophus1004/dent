<!--
  새 데이터셋 가져오기 (#/generation/new) · 데이터 추가 (#/generation/<id>/add): 네 단계를 한 화면에서 차례로 보인다.
    1 파일 고르기   데이터셋 이름(추가면 더할 곳) + 가져올 곳 두 칸(시스템 SourcePicker) · 새 데이터셋이면 예시 목록.
                    불러오면 2단계로 넘어간다.
    2 형식 확인     서버가 읽은 형식과 앞쪽 줄(시스템 SourceCheck)
    3 필드 맞추기   원본 모양 · 칸 고르기(이 모듈의 MappingStep)
    4 가져오기      생성 가져오기 API를 부르고 진행률 · 결과를 보인다(시스템 ImportRun에 이 모듈의 가져오기를 넘긴다)
  단계 사이의 값(원본, 칸 맞춤, 이름)은 모두 이 화면이 가진다. 가져오기를 시작하거나 끝내면 사이드바와 홈의 목록을 다시 읽는다.
  파일 가져오기가 실패하면 서버가 원본을 지우므로 [다시 올리기]로 1단계에 돌아간다. 이름은 그대로 두어 만든 데이터셋에 다시 넣는다.
  주소의 ?source=file 또는 ?source=huggingface면 그 칸을 보라 테두리로 미리 골라 둔다.
  데이터 추가는 있던 데이터셋에 넣는다(같은 지시는 합치지 않고 진단의 '같은 지시'가 잡는다). 끝나면 [데이터셋으로].
  새 데이터셋은 1 · 4단계에 '평가셋' 켬 · 끔을 둔다(주소 ?eval=1이면 켬: 진단의 [평가셋 가져오기]). 켜면 가져오기를 시작한 뒤
  그 데이터셋을 평가셋으로 표시해 다른 데이터셋의 오염 대조가 들어간다.
-->
<script setup lang="ts">
import { Database, MessageSquareText } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import TopBar from '@/system/layout/TopBar.vue'
import ImportRun from '@/system/sources/ImportRun.vue'
import ImportSteps from '@/system/sources/ImportSteps.vue'
import ExampleList from '@/system/sources/ExampleList.vue'
import SourceCheck from '@/system/sources/SourceCheck.vue'
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

import { Segmented, type SegmentedOption } from '@/system/ui/segmented'
import { pushToast } from '@/system/toasts'

import { createImport, getDataset, installExample, listExamples, updateSettings } from '@/modules/generation/api'
import { loadDatasets } from '@/modules/generation/datasets'
import MappingStep from '@/modules/generation/MappingStep.vue'
import type { DatasetRead, FieldMapping, Shape } from '@/modules/generation/types'

const STEPS = ['파일 고르기', '형식 확인', '필드 맞추기', '가져오기']

// 데이터셋 이름의 최대 길이. 백엔드(system/models.py의 DATASET_NAME_MAX_LENGTH)와 같다.
const DATASET_NAME_MAX_LENGTH = 200

// 4단계에서 이름을 더는 고칠 수 없는 상태
const LOCKED_PHASES: ImportPhase[] = ['starting', 'running', 'done']

const SHAPE_NAMES: Record<Shape, string> = {
  messages: 'messages',
  sharegpt: 'ShareGPT',
  alpaca: 'Alpaca',
  turns: '턴마다 칸',
  preference: '선호 쌍',
  pairwise: '두 응답 + 점수',
}

const route = useRoute()

// 평가셋 켬 · 끔 (새 데이터셋만)
const EVAL_OPTIONS: SegmentedOption<string>[] = [
  { value: 'off', label: '학습 데이터' },
  { value: 'on', label: '평가셋' },
]

const step = ref(1)
const evalChoice = ref(route.query.eval === '1' ? 'on' : 'off')
const datasetName = ref('')
const repo = ref('')
const source = ref<LoadedSource | null>(null)
const mapping = ref<FieldMapping>(emptyMapping())
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
const isMapped = computed(() => {
  const value = mapping.value
  if (value.shape === 'alpaca') return Boolean(value.instruction)
  if (value.shape === 'turns') return value.turns.length > 0 && value.turns.every((turn) => turn.user && turn.assistant)
  if (value.shape === 'preference') return Boolean(value.chosen && value.rejected)
  if (value.shape === 'pairwise') return Boolean(value.prompt && value.response1 && value.response2 && value.preference)
  return Boolean(value.messages)
})
const canStart = computed(
  () => source.value !== null && isMapped.value && (isAdding.value ? target.value !== null : !nameProblem.value),
)
const rowCount = computed(() => (source.value ? sourceRowCount(source.value) : null))

// 4단계 요약에 보일 칸: [이름, 칸]
const mappedColumns = computed<[string, string][]>(() => {
  const value = mapping.value
  const byShape: Record<Shape, [string, string | null | undefined][]> = {
    messages: [['대화 칸', value.messages], ['도구 칸', value.tools]],
    sharegpt: [['대화 칸', value.messages], ['도구 칸', value.tools]],
    alpaca: [['지시 칸', value.instruction], ['입력 칸', value.input], ['응답 칸', value.output], ['시스템 칸', value.system]],
    turns: [['턴 칸', value.turns.map((turn) => `${turn.user} → ${turn.assistant}`).join(' · ')], ['시스템 칸', value.system]],
    preference: [['지시 칸', value.prompt], ['고른 응답 칸', value.chosen], ['버린 응답 칸', value.rejected]],
    pairwise: [['지시 칸', value.prompt], ['응답 1 칸', value.response1], ['응답 2 칸', value.response2], ['선호 점수 칸', value.preference]],
  }
  const rows = byShape[value.shape]
  return rows.filter((row): row is [string, string] => Boolean(row[1]))
})

function emptyMapping(): FieldMapping {
  return { shape: 'messages', messages: null, instruction: null, input: null, output: null, system: null, turns: [] }
}

const crumbs = computed(() => {
  const home = { label: '생성', icon: MessageSquareText, to: '/' }
  if (!isAdding.value) return [{ label: '생성', icon: MessageSquareText }, { label: '새 데이터셋' }]
  return [home, { label: target.value?.name ?? '…', to: `/generation/${targetId.value}` }, { label: '데이터 추가' }]
})
const doneTo = computed(() =>
  isAdding.value ? { label: '데이터셋으로', to: `/generation/${targetId.value}` } : null,
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

/** 4단계가 부르는 가져오기 시작. 생성 가져오기 API에 원본 · 칸 맞춤 · 데이터셋을 넘긴다. */
async function startImport(): Promise<ImportRead> {
  const loaded = source.value
  if (!loaded || !isMapped.value) throw new Error('가져올 원본과 칸을 먼저 고르세요.')
  const body = { ...toSourceCreate(loaded), mapping: mapping.value }
  if (targetId.value !== null) return createImport({ ...body, dataset_id: targetId.value, new_dataset_name: null })
  const created = createdDataset.value
  const isRetryIntoCreated = created !== null && created.name === trimmedName.value
  return createImport({
    ...body,
    dataset_id: isRetryIntoCreated ? created.id : null,
    new_dataset_name: isRetryIntoCreated ? null : trimmedName.value,
  })
}

function onStarted(read: ImportRead): void {
  if (!isAdding.value) createdDataset.value = { id: read.dataset_id, name: trimmedName.value }
  // 평가셋이면 표시한다(다른 데이터셋의 오염 대조가 들어간다). 가져오기가 끝날 때도 다시 대조한다.
  if (!isAdding.value && evalChoice.value === 'on') {
    updateSettings(read.dataset_id, { is_eval: true }).catch((error) =>
      pushToast({ tone: 'fail', title: '평가셋 표시', detail: errorMessage(error), actions: [] }),
    )
  }
  // 새 데이터셋이 사이드바와 홈 목록에 바로 보이게 한다.
  void loadDatasets()
}

function onFinished(): void {
  void loadDatasets()
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
          <span class="text-muted-foreground">대화 <b class="font-semibold text-foreground">{{ fmt(target.conversation_count) }}</b></span>
          <span class="text-muted-foreground">· 같은 지시는 진단에서</span>
        </template>
        <span v-else-if="targetError" class="font-semibold text-danger-ink">{{ targetError }}</span>
        <span v-else class="text-muted-foreground">…</span>
      </section>
      <section v-else aria-label="데이터셋 이름">
        <label for="dataset-name" class="mb-1.5 block text-ui font-[550]">데이터셋 이름</label>
        <div class="flex flex-wrap items-center gap-3">
          <Input
            id="dataset-name"
            v-model="datasetName"
            placeholder="예: 상담 응답 SFT"
            class="max-w-[420px]"
            :maxlength="DATASET_NAME_MAX_LENGTH"
          />
          <Segmented v-model="evalChoice" :options="EVAL_OPTIONS" label="쓰임" data-field="import-eval" />
        </div>
      </section>
      <SourcePicker v-model:repo="repo" :picked="pickedSource" :loaded="source" @loaded="onLoaded" @next="step = 2" />
      <ExampleList
        v-if="!isAdding"
        :load="listExamples"
        :install="installExample"
        :dataset-route="(id: number) => `/generation/${id}`"
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
            <dd class="font-medium">{{ SHAPE_NAMES[mapping.shape] }}</dd>
            <template v-if="!isAdding">
              <dt class="text-muted-foreground">쓰임</dt>
              <dd class="font-medium" data-import-eval>{{ evalChoice === 'on' ? '평가셋 · 오염 대조에 쓰임' : '학습 데이터' }}</dd>
            </template>
            <template v-for="[name, column] in mappedColumns" :key="name">
              <dt class="text-muted-foreground">{{ name }}</dt>
              <dd class="truncate font-mono">{{ column }}</dd>
            </template>
          </dl>
        </div>
      </template>
    </ImportRun>
  </div>
</template>
