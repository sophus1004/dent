<!--
  생성 도우미의 실행 설정 카드. 도우미 창이 처음(실행 없음)에 [시작]과 함께 보이고,
  끝난 뒤 머리의 [설정] · 허락 카드의 [설정]으로 다시 연다. 값을 바꾸면 바로 데이터셋 설정에 저장한다(PATCH /datasets/{id}/settings).
    흐름 전체   만들 데이터 SFT · 선호 (선호면 흐름에 3 선호 · 비교 만들기가 붙는다)
    길이        학습할 모델(허깅페이스 이름, 비우면 글자 수 어림) · 최대 길이 2,048 · 4,096 · 8,192 · 32,768토큰
                → 바꾸면 작업이 토크나이저 파일만 받아 모든 대화를 다시 잰다('다시 재는 중' · '잰 모델 n')
    평가        이 데이터셋을 평가셋으로 (다른 데이터셋의 오염 대조에 쓰인다)
  단계 옆 값(summary): no_answer '시험 5 · 비용 어림' · pairs 'Jev가 고르기'.
  만들 데이터 · 길이 · 평가셋은 진단을 바꾸므로 저장 뒤 진단 · 머리가 다시 읽는다.
-->
<script setup lang="ts">
import { FlaskConical, Route, Ruler } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'

import { fmt } from '@/system/format'
import SettingsCard from '@/system/helper/SettingsCard.vue'
import SettingsField from '@/system/helper/SettingsField.vue'
import SettingsGroup from '@/system/helper/SettingsGroup.vue'
import { notifyHelperChanged } from '@/system/helper/helperSignals'
import { errorMessage } from '@/system/http'
import { Input } from '@/system/ui/input'
import { Segmented, type SegmentedOption } from '@/system/ui/segmented'

import { getDataset, updateSettings } from '@/modules/generation/api'
import type { Goal, SettingsRead, SettingsUpdate } from '@/modules/generation/types'

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

// 기본값 (백엔드 generation/models.py의 DEFAULT_MAX_TOKENS · Goal.SFT와 같다)
const DEFAULTS = { goal: 'sft' as Goal, tokenizer_model: '', max_tokens: 4096, is_eval: false }

// 고를 최대 길이 (지금 값이 목록 밖이면 그 값도 한 칸 더 보인다)
const MAX_TOKENS = [2048, 4096, 8192, 32768]

// 허락 단계 옆의 값
const PERMISSION_SUMMARY = { no_answer: '시험 5 · 비용 어림', pairs: 'Jev가 고르기' }

const form = reactive({ ...DEFAULTS })
const measured = ref<{ model: string | null; maxTokens: number | null }>({ model: null, maxTokens: null })
const loaded = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const goalOptions: SegmentedOption<Goal>[] = [
  { value: 'sft', label: 'SFT' },
  { value: 'preference', label: '선호' },
]
const evalOptions: SegmentedOption<string>[] = [
  { value: 'off', label: '아님' },
  { value: 'on', label: '평가셋' },
]
const lengthOptions = computed<SegmentedOption<number>[]>(() => {
  const values = MAX_TOKENS.includes(form.max_tokens) ? MAX_TOKENS : [...MAX_TOKENS, form.max_tokens].sort((a, b) => a - b)
  return values.map((value) => ({ value, label: fmt(value) }))
})

const goal = computed({
  get: () => form.goal,
  set: (value: Goal) => void save({ goal: value }),
})
const maxTokens = computed({
  get: () => form.max_tokens,
  set: (value: number) => void save({ max_tokens: value }),
})
const isEval = computed({
  get: () => (form.is_eval ? 'on' : 'off'),
  set: (value: string) => void save({ is_eval: value === 'on' }),
})

// 길이를 잰 상태: 설정과 잰 것이 다르면 다시 재는 중
const lengthHint = computed(() => {
  const wanted = form.tokenizer_model.trim() || null
  const isMeasuring = wanted !== measured.value.model || (wanted !== null && measured.value.maxTokens !== form.max_tokens)
  if (isMeasuring) return '다시 재는 중'
  return measured.value.model ? `잰 모델 ${measured.value.model}` : '글자 수 어림'
})

function fill(settings: SettingsRead): void {
  Object.assign(form, {
    goal: settings.goal,
    tokenizer_model: settings.tokenizer_model ?? '',
    max_tokens: settings.max_tokens,
    is_eval: settings.is_eval,
  })
  measured.value = { model: settings.measured_model, maxTokens: settings.measured_max_tokens }
  emit('summary', { ...PERMISSION_SUMMARY })
}

async function load(): Promise<void> {
  loaded.value = false
  error.value = null
  try {
    fill((await getDataset(props.datasetId)).settings)
    loaded.value = true
  } catch (failure) {
    error.value = errorMessage(failure)
  }
}

async function save(patch: SettingsUpdate): Promise<void> {
  if (!loaded.value) return
  saving.value = true
  error.value = null
  try {
    fill(await updateSettings(props.datasetId, patch))
    notifyHelperChanged()
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    saving.value = false
  }
}

function saveModel(): void {
  void save({ tokenizer_model: form.tokenizer_model.trim() })
}

/** [기본값]: 기본값과 다른 칸만 되돌린다(같은 값은 보내지 않아 다시 재지 않는다). */
function reset(): void {
  const patch: SettingsUpdate = {}
  if (form.goal !== DEFAULTS.goal) patch.goal = DEFAULTS.goal
  if (form.tokenizer_model.trim() !== DEFAULTS.tokenizer_model) patch.tokenizer_model = DEFAULTS.tokenizer_model
  if (form.max_tokens !== DEFAULTS.max_tokens) patch.max_tokens = DEFAULTS.max_tokens
  if (form.is_eval !== DEFAULTS.is_eval) patch.is_eval = DEFAULTS.is_eval
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
    data-generation-helper-settings
    @reset="reset"
    @start="emit('start')"
    @close="emit('close')"
  >
    <SettingsGroup :icon="Route" title="흐름 전체">
      <SettingsField label="만들 데이터">
        <Segmented v-model="goal" :options="goalOptions" label="만들 데이터" :disabled="!loaded" data-field="helper-goal" />
        <small class="text-meta text-muted-foreground">{{ form.goal === 'preference' ? '3 선호 · 비교 만들기' : '응답까지' }}</small>
      </SettingsField>
    </SettingsGroup>
    <SettingsGroup :icon="Ruler" title="길이">
      <SettingsField label="학습할 모델">
        <Input
          v-model="form.tokenizer_model"
          placeholder="Qwen/Qwen3-8B"
          class="h-7 w-[200px] font-mono"
          :disabled="!loaded"
          data-field="helper-tokenizer-model"
          @change="saveModel"
        />
      </SettingsField>
      <SettingsField label="최대 길이" :hint="lengthHint">
        <Segmented v-model="maxTokens" :options="lengthOptions" label="최대 길이" :disabled="!loaded" data-field="helper-max-tokens" />
        <small class="text-meta text-muted-foreground">토큰</small>
      </SettingsField>
    </SettingsGroup>
    <SettingsGroup :icon="FlaskConical" title="평가">
      <SettingsField label="이 데이터셋" hint="평가셋 → 다른 데이터셋의 오염 대조">
        <Segmented v-model="isEval" :options="evalOptions" label="평가셋" :disabled="!loaded" data-field="helper-eval" />
      </SettingsField>
    </SettingsGroup>
  </SettingsCard>
</template>
