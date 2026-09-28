<!--
  분류 도우미의 실행 설정 카드. 도우미 창이 처음(실행 없음)에 [시작]과 함께 보이고,
  끝난 뒤 머리의 [설정]으로 다시 연다. 값을 바꾸면 바로 데이터셋 설정에 저장한다(PATCH /datasets/{id}/settings).
    새 문장(남은 것 · 라벨 균형)   목표 1.2배 · 1.5배 · 2배 (가장 많은 라벨 대비 · 라벨당 최대 100)
  라벨마다 더할 수는 끝난 뒤 남은 것의 '라벨 균형' 줄에서 고친다. 목표를 바꾸면 남은 것이 다시 센다.
-->
<script setup lang="ts">
import { FilePlus2 } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import SettingsCard from '@/system/helper/SettingsCard.vue'
import SettingsField from '@/system/helper/SettingsField.vue'
import SettingsGroup from '@/system/helper/SettingsGroup.vue'
import { notifyHelperChanged } from '@/system/helper/helperSignals'
import { errorMessage } from '@/system/http'
import { Segmented, type SegmentedOption } from '@/system/ui/segmented'

import { getDataset, updateSettings } from '@/modules/classification/api'

const props = defineProps<{
  datasetId: number
  startable: boolean
  canStart: boolean
  closable: boolean
}>()

const emit = defineEmits<{
  start: []
  close: []
  summary: [values: Record<string, string>]
}>()

// 기본 목표 배율 (백엔드 classification/models.py의 DEFAULT_BALANCE_TARGET) · 고를 배율
const DEFAULT_TARGET = 1.5
const TARGETS = [1.2, 1.5, 2]

// 라벨 하나에 한 번에 더하는 최대 수 (백엔드 helper_fixes.MAX_GENERATE_PER_LABEL)
const MAX_PER_LABEL = 100

const target = ref(DEFAULT_TARGET)
const loaded = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const options = computed<SegmentedOption<number>[]>(() =>
  (TARGETS.includes(target.value) ? TARGETS : [...TARGETS, target.value].sort((a, b) => a - b)).map((value) => ({
    value,
    label: `${value}배`,
  })),
)

const chosen = computed({
  get: () => target.value,
  set: (value: number) => void save(value),
})

async function load(): Promise<void> {
  loaded.value = false
  error.value = null
  try {
    const dataset = await getDataset(props.datasetId)
    target.value = dataset.settings.balance_target
    loaded.value = true
    emit('summary', {})
  } catch (failure) {
    error.value = errorMessage(failure)
  }
}

async function save(value: number): Promise<void> {
  if (!loaded.value || value === target.value) return
  saving.value = true
  error.value = null
  try {
    target.value = (await updateSettings(props.datasetId, { balance_target: value })).balance_target
    // 남은 것의 라벨 균형 계획이 이 배율로 다시 세어진다.
    notifyHelperChanged()
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    saving.value = false
  }
}

watch(() => props.datasetId, load, { immediate: true })
</script>

<template>
  <SettingsCard
    :saving="saving"
    :error="error"
    note="새 문장은 끝난 뒤 골라서"
    :startable="startable"
    :can-start="canStart && loaded"
    :closable="closable"
    data-classification-helper-settings
    @reset="save(DEFAULT_TARGET)"
    @start="emit('start')"
    @close="emit('close')"
  >
    <SettingsGroup :icon="FilePlus2" title="새 문장 (남은 것 · 라벨 균형)">
      <SettingsField label="목표" :hint="`가장 많은 라벨 대비 · 라벨당 최대 ${MAX_PER_LABEL}`">
        <Segmented v-model="chosen" :options="options" label="목표 배율" :disabled="!loaded" data-field="helper-balance-target" />
      </SettingsField>
    </SettingsGroup>
  </SettingsCard>
</template>
