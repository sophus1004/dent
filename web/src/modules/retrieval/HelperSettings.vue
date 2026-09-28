<!--
  검색 도우미의 실행 설정 카드. 도우미 창이 처음(실행 없음)에 [시작]과 함께 보이고,
  끝난 뒤 머리의 [설정] · 허락 카드의 [설정]으로 다시 연다. 값을 바꾸면 바로 데이터셋 설정에 저장한다(PATCH /datasets/{id}/settings).
    흐름 전체     학습할 모델 (비우면 연결된 임베딩)
    문서 나누기   청크 크기 256 · 512 · 1,024 · 오버랩 없음 · 32 · 64 · 128 · 머리말 켬 · 끔 → 어림 '긴 문서 n → 청크 약 n · 모두 n'
    질의 만들기   청크마다 1 · 2 · 3 · 5 · 8개 → 어림 '청크 n → 약 n · 후보 2N · 모자라면 한 번 더' ·
                  유형 질문형 % · 검색어형 % → 어림 'LLM 약 n토큰'
  단계 옆 값(summary): chunk '512토큰 · 오버랩 없음' · generate '청크마다 2 · 질문형 60%'.
  학습할 모델 · 청크 크기 · 머리말은 진단(긴 문서 · 학습 글)을 바꾸므로 저장 뒤 진단 · 머리가 다시 읽는다.
-->
<script setup lang="ts">
import { Route, Scissors, WandSparkles } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'

import { fmt } from '@/system/format'
import SettingsCard from '@/system/helper/SettingsCard.vue'
import SettingsField from '@/system/helper/SettingsField.vue'
import SettingsGroup from '@/system/helper/SettingsGroup.vue'
import { notifyHelperChanged } from '@/system/helper/helperSignals'
import { errorMessage, isAbortError } from '@/system/http'
import { Input } from '@/system/ui/input'
import { Segmented, type SegmentedOption } from '@/system/ui/segmented'

import { estimateHelper, getDataset, updateSettings } from '@/modules/retrieval/api'
import type { HelperEstimateRead, SettingsRead, SettingsUpdate } from '@/modules/retrieval/types'

const props = defineProps<{
  datasetId: number
  startable: boolean
  canStart: boolean
  closable: boolean
}>()

const emit = defineEmits<{
  start: []
  close: []
  // 단계 열쇠 → 그 단계의 값 글 (도우미 창의 단계 줄 옆)
  summary: [values: Record<string, string>]
}>()

// 기본값 (백엔드 retrieval/models.py의 DEFAULT_*와 같다)
const DEFAULTS = {
  target_model: '',
  doc_max_tokens: 512,
  chunk_overlap: 0,
  section_header: true,
  queries_per_chunk: 2,
  question_share: 60,
}

// 고를 값 (지금 값이 목록 밖이면 그 값도 한 칸 더 보인다)
const CHUNK_SIZES = [256, 512, 1024]
const OVERLAPS = [0, 32, 64, 128]
const PER_CHUNK = [1, 2, 3, 5, 8]

// 질의 유형 막대의 한 칸 (%)
const SHARE_STEP = 10

// 후보는 청크마다 남길 수의 몇 배 (백엔드 generation.CANDIDATE_FACTOR)
const CANDIDATE_FACTOR = 2

// 이 값들을 바꾸면 진단(긴 문서 · 학습 글 · 분석 모델)이 바뀐다
const DATA_FIELDS = new Set(['target_model', 'doc_max_tokens', 'section_header'])

const form = reactive({ ...DEFAULTS })
const loaded = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const estimate = ref<HelperEstimateRead | null>(null)
let estimateController: AbortController | null = null

function withCurrent(values: number[], current: number): number[] {
  return values.includes(current) ? values : [...values, current].sort((a, b) => a - b)
}

const sizeOptions = computed<SegmentedOption<number>[]>(() =>
  withCurrent(CHUNK_SIZES, form.doc_max_tokens).map((value) => ({ value, label: fmt(value) })),
)
const overlapOptions = computed<SegmentedOption<number>[]>(() =>
  withCurrent(OVERLAPS, form.chunk_overlap).map((value) => ({ value, label: value ? String(value) : '없음' })),
)
const perChunkOptions = computed<SegmentedOption<number>[]>(() =>
  withCurrent(PER_CHUNK, form.queries_per_chunk).map((value) => ({ value, label: String(value) })),
)
const headerOptions: SegmentedOption<string>[] = [
  { value: 'on', label: '켬' },
  { value: 'off', label: '끔' },
]

const docMaxTokens = computed({
  get: () => form.doc_max_tokens,
  set: (value: number) => void save({ doc_max_tokens: value }),
})
const overlap = computed({
  get: () => form.chunk_overlap,
  set: (value: number) => void save({ chunk_overlap: value }),
})
const perChunk = computed({
  get: () => form.queries_per_chunk,
  set: (value: number) => void save({ queries_per_chunk: value }),
})
const header = computed({
  get: () => (form.section_header ? 'on' : 'off'),
  set: (value: string) => void save({ section_header: value === 'on' }),
})

const chunkHint = computed(() => {
  const found = estimate.value
  if (!found) return ''
  if (!found.long_documents) return `긴 문서 0 · 청크 ${fmt(found.chunks)}`
  return `긴 문서 ${fmt(found.long_documents)} → 청크 약 ${fmt(found.long_chunks)} · 모두 ${fmt(found.chunks)}`
})
const queryHint = computed(() => {
  const found = estimate.value
  if (!found) return ''
  return `청크 ${fmt(found.targets)} → 약 ${fmt(found.queries)} · 후보 ${form.queries_per_chunk * CANDIDATE_FACTOR} · 모자라면 한 번 더`
})
const costHint = computed(() => (estimate.value ? `LLM 약 ${fmt(estimate.value.tokens)}토큰` : ''))

function fill(settings: SettingsRead): void {
  Object.assign(form, {
    target_model: settings.target_model ?? '',
    doc_max_tokens: settings.doc_max_tokens,
    chunk_overlap: settings.chunk_overlap,
    section_header: settings.section_header,
    queries_per_chunk: settings.queries_per_chunk,
    question_share: settings.question_share,
  })
  emit('summary', {
    chunk: `${fmt(form.doc_max_tokens)}토큰 · 오버랩 ${form.chunk_overlap ? `${form.chunk_overlap}토큰` : '없음'}`,
    generate: `청크마다 ${form.queries_per_chunk} · 질문형 ${form.question_share}%`,
  })
}

async function load(): Promise<void> {
  loaded.value = false
  error.value = null
  try {
    const dataset = await getDataset(props.datasetId)
    if (dataset.settings) {
      fill(dataset.settings)
      loaded.value = true
      void refreshEstimate()
    }
  } catch (failure) {
    error.value = errorMessage(failure)
  }
}

async function refreshEstimate(): Promise<void> {
  estimateController?.abort()
  const current = new AbortController()
  estimateController = current
  try {
    estimate.value = await estimateHelper(
      props.datasetId,
      { chunk_tokens: form.doc_max_tokens, overlap: form.chunk_overlap, per_chunk: form.queries_per_chunk },
      current.signal,
    )
  } catch (failure) {
    if (!isAbortError(failure)) estimate.value = null
  }
}

async function save(patch: SettingsUpdate): Promise<void> {
  if (!loaded.value) return
  saving.value = true
  error.value = null
  try {
    fill(await updateSettings(props.datasetId, patch))
    void refreshEstimate()
    if (Object.keys(patch).some((name) => DATA_FIELDS.has(name))) notifyHelperChanged()
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    saving.value = false
  }
}

function saveModel(): void {
  const name = form.target_model.trim()
  void save({ target_model: name })
}

function saveShare(event: Event): void {
  void save({ question_share: Number((event.target as HTMLInputElement).value) })
}

/** [기본값]: 기본값과 다른 칸만 되돌린다(같은 값은 보내지 않아 진단을 건드리지 않는다). */
function reset(): void {
  const patch: SettingsUpdate = {}
  for (const [name, value] of Object.entries(DEFAULTS) as [keyof typeof DEFAULTS, string | number | boolean][]) {
    if (form[name] !== value) Object.assign(patch, { [name]: value })
  }
  if (Object.keys(patch).length) void save(patch)
}

watch(() => props.datasetId, load, { immediate: true })
</script>

<template>
  <SettingsCard
    :saving="saving"
    :error="error"
    note="허락은 그 단계에서 따로"
    :startable="startable"
    :can-start="canStart && loaded"
    :closable="closable"
    data-retrieval-helper-settings
    @reset="reset"
    @start="emit('start')"
    @close="emit('close')"
  >
    <SettingsGroup :icon="Route" title="흐름 전체">
      <SettingsField label="학습할 모델">
        <Input
          v-model="form.target_model"
          placeholder="연결된 임베딩"
          class="h-7 w-[200px] font-mono"
          :disabled="!loaded"
          data-field="helper-target-model"
          @change="saveModel"
        />
      </SettingsField>
    </SettingsGroup>
    <SettingsGroup :icon="Scissors" title="문서 나누기 · 허락">
      <SettingsField label="청크 크기">
        <Segmented v-model="docMaxTokens" :options="sizeOptions" label="청크 크기" :disabled="!loaded" data-field="helper-chunk-size" />
        <small class="text-meta text-muted-foreground">토큰</small>
      </SettingsField>
      <SettingsField label="오버랩">
        <Segmented v-model="overlap" :options="overlapOptions" label="오버랩" :disabled="!loaded" data-field="helper-overlap" />
      </SettingsField>
      <SettingsField label="머리말" :hint="chunkHint">
        <Segmented v-model="header" :options="headerOptions" label="머리말" :disabled="!loaded" data-field="helper-header" />
        <small class="text-meta text-muted-foreground">구획 경로</small>
      </SettingsField>
    </SettingsGroup>
    <SettingsGroup :icon="WandSparkles" title="질의 만들기 · 허락">
      <SettingsField label="질의 수" :hint="queryHint">
        <small class="text-meta text-muted-foreground">청크마다</small>
        <Segmented v-model="perChunk" :options="perChunkOptions" label="청크마다 질의 수" :disabled="!loaded" data-field="helper-per-chunk" />
        <small class="text-meta text-muted-foreground">개</small>
      </SettingsField>
      <SettingsField label="유형" :hint="costHint">
        <input
          v-model.number="form.question_share"
          type="range"
          min="0"
          max="100"
          :step="SHARE_STEP"
          class="w-[110px] accent-[var(--primary)]"
          aria-label="질문형 몫"
          :disabled="!loaded"
          data-field="helper-question-share"
          @change="saveShare"
        />
        <span class="text-meta tabular-nums">질문형 {{ form.question_share }}% · 검색어형 {{ 100 - form.question_share }}%</span>
      </SettingsField>
    </SettingsGroup>
  </SettingsCard>
</template>
