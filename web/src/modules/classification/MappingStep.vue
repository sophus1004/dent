<!--
  분류 가져오기 3단계 '필드 맞추기': 원본의 칸을 분류의 문장 · 라벨에 맞춘다. 원본이 train · test처럼 나뉘어 있어도 한 데이터로 가져온다.
  - 처음 열면 칸 이름으로 짐작한 값(POST /classification/mapping/suggest)을 채운다.
  - 문장 칸 · 라벨 칸은 꼭 고른다.
  - 'DENT에 들어갈 모양': 미리 보기 줄을 고른 칸대로 바꿔 보인다. 서버와 같은 규칙으로 건너뛸 줄(빈 문장 등)을 표시한다.
  - 라벨 수(미리 보기에서 센 것, 허깅페이스 ClassLabel이면 이름 목록의 수)와, 메타데이터로 들어갈 나머지 칸.
  고른 칸은 v-model:mapping으로 화면에 돌려준다. [이전]은 back, [다음]은 next.
-->
<script setup lang="ts">
import { LoaderCircle, OctagonAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { clip, fmt, showValue } from '@/system/format'
import {
  sourceColumns,
  sourceLabelNames,
  sourceLines,
  sourceRows,
  type LoadedSource,
} from '@/system/sources/source'
import { Badge } from '@/system/ui/badge'
import { Button } from '@/system/ui/button'
import { NativeSelect } from '@/system/ui/native-select'

import { suggestMapping } from '@/modules/classification/api'
import type { MappingDraft, SuggestedMapping } from '@/modules/classification/types'

const props = defineProps<{
  source: LoadedSource
}>()

const emit = defineEmits<{
  back: []
  next: []
}>()

const mapping = defineModel<MappingDraft>('mapping', { required: true })

// 백엔드(modules/classification/models.py)와 같은 길이 제한. 넘으면 그 줄을 건너뛴다.
const TEXT_MAX_LENGTH = 10_000
const LABEL_NAME_MAX_LENGTH = 200

// 예시 값을 보일 최대 글자 수
const SAMPLE_LENGTH = 40

// 라벨 칩을 보일 최대 개수. 넘으면 '+n'
const LABEL_CHIPS_MAX = 40

// 건너뛰는 이유. 백엔드(modules/classification/importing.py)와 같은 낱말이다.
const SKIP_EMPTY_TEXT = '빈 문장'
const SKIP_TOO_LONG_TEXT = '너무 긴 문장'
const SKIP_NO_LABEL = '라벨 없음'
const SKIP_TOO_LONG_LABEL = '너무 긴 라벨'

// 미리 보기 한 줄이 DENT에 들어갈 모양
interface MappedRow {
  line: number
  text: string
  label: string
  // 건너뛰는 이유. 들어가면 null
  skip: string | null
}

const suggested = ref<SuggestedMapping | null>(null)
const isSuggesting = ref(false)
let suggestController: AbortController | null = null

const columns = computed(() => sourceColumns(props.source))
const rows = computed(() => sourceRows(props.source))

// 라벨 칸이 허깅페이스 ClassLabel이면 그 이름 목록. 아니면 null
const classLabelNames = computed(() => {
  const label = mapping.value.label
  return label ? (sourceLabelNames(props.source)[label] ?? null) : null
})

const isReady = computed(() => mapping.value.text !== null && mapping.value.label !== null)

// 칸마다의 문제. 없으면 null
const problems = computed(() => {
  const { text, label } = mapping.value
  return {
    text: text === null ? '필수' : null,
    label: label === null ? '필수' : label === text ? '문장 칸과 같음' : null,
  }
})
const missing = computed(() => {
  const names: string[] = []
  if (mapping.value.text === null) names.push('문장 칸')
  if (mapping.value.label === null) names.push('라벨 칸')
  return names
})
const canGoNext = computed(() => Object.values(problems.value).every((problem) => problem === null))

// 문장·라벨로 쓰지 않는 나머지 칸. 가져올 때 메타데이터(extra)로 들어간다.
const extraColumns = computed(() => {
  const used = new Set([mapping.value.text, mapping.value.label])
  return columns.value.filter((column) => !used.has(column))
})

const mappedRows = computed<MappedRow[]>(() => {
  const { text: textColumn, label: labelColumn } = mapping.value
  if (!textColumn || !labelColumn) return []
  const lines = sourceLines(props.source)
  return rows.value.map((row, index) => {
    const text = showValue(row[textColumn]).trim()
    const label = showValue(row[labelColumn]).trim()
    return {
      line: lines[index],
      text,
      label,
      skip: skipReason(text, label),
    }
  })
})

const skippedCount = computed(() => mappedRows.value.filter((row) => row.skip !== null).length)

// 미리 보기에서 센 라벨별 줄 수. 많은 것부터
const previewLabels = computed(() => {
  const counts = new Map<string, number>()
  for (const row of mappedRows.value) {
    if (row.skip === null) counts.set(row.label, (counts.get(row.label) ?? 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])
})

// 서버(importing.parse_row)와 같은 순서로 건너뛸 이유를 고른다.
function skipReason(text: string, label: string): string | null {
  if (!text) return SKIP_EMPTY_TEXT
  if (text.length > TEXT_MAX_LENGTH) return SKIP_TOO_LONG_TEXT
  if (!label) return SKIP_NO_LABEL
  if (label.length > LABEL_NAME_MAX_LENGTH) return SKIP_TOO_LONG_LABEL
  return null
}

/** 칸 하나를 바꾼다. */
function setColumn(key: keyof MappingDraft, value: string | null | undefined): void {
  mapping.value = { ...mapping.value, [key]: value ?? null }
}

/** 칸의 첫 값(비지 않은 것)을 짧게. 예시로 보인다. */
function sample(column: string | null): string {
  if (!column) return ''
  for (const row of rows.value) {
    const value = showValue(row[column]).trim()
    if (value) return clip(value, SAMPLE_LENGTH)
  }
  return ''
}

/** 칸 이름으로 짐작한 값을 받아, 아직 고르지 않은 칸만 채운다. */
async function suggest(): Promise<void> {
  const controller = new AbortController()
  suggestController = controller
  isSuggesting.value = true
  try {
    const result = await suggestMapping({ columns: columns.value })
    if (controller.signal.aborted) return
    suggested.value = result
    // 기다리는 동안 사용자가 고른 칸은 그대로 둔다.
    mapping.value = {
      text: mapping.value.text ?? result.text,
      label: mapping.value.label ?? result.label,
    }
  } catch {
    // 짐작은 돕는 일일 뿐이다. 실패하면 사용자가 직접 고른다.
  } finally {
    if (suggestController === controller) isSuggesting.value = false
  }
}

// 이 칸 값이 짐작한 값 그대로인지 ('추천' 표시)
function isSuggested(key: keyof MappingDraft): boolean {
  const value = mapping.value[key]
  return value !== null && suggested.value?.[key] === value
}

onMounted(() => {
  // 시트나 구성을 바꾸면 칸이 바뀔 수 있으므로 원본에 없는 칸은 지운다.
  const known = (column: string | null) =>
    column !== null && columns.value.includes(column) ? column : null
  mapping.value = {
    text: known(mapping.value.text),
    label: known(mapping.value.label),
  }
  const isUntouched = mapping.value.text === null && mapping.value.label === null
  if (isUntouched) void suggest()
})

onBeforeUnmount(() => suggestController?.abort())
</script>

<template>
  <section class="card" aria-label="칸 맞추기">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <h2 class="text-body font-semibold">칸 맞추기</h2>
      <span class="ml-auto flex items-center gap-2 text-ui text-muted-foreground">
        <LoaderCircle v-if="isSuggesting" class="size-4 animate-spin" aria-label="짐작하는 중" />
        원본 칸 {{ columns.length }}
      </span>
    </div>

    <div class="divide-y">
      <!-- 문장 칸 -->
      <div class="grid items-center gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[132px_minmax(0,260px)_minmax(0,1fr)]">
        <label for="map-text" class="flex items-center gap-2 text-ui font-[550]">
          문장 칸
          <span class="text-meta font-medium" :class="problems.text ? 'text-danger-ink' : 'text-subtle-foreground'">필수</span>
        </label>
        <NativeSelect
          id="map-text"
          class="font-mono"
          :model-value="mapping.text"
          :aria-invalid="problems.text ? true : undefined"
          @update:model-value="setColumn('text', $event)"
        >
          <option :value="null" disabled>고르기</option>
          <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
        </NativeSelect>
        <span class="flex min-w-0 items-center gap-2 text-ui text-muted-foreground">
          <Badge v-if="isSuggested('text')" class="h-5 px-1.5 text-meta text-muted-foreground">추천</Badge>
          <span v-if="sample(mapping.text)" class="truncate">예: {{ sample(mapping.text) }}</span>
        </span>
      </div>

      <!-- 라벨 칸 -->
      <div class="grid items-center gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[132px_minmax(0,260px)_minmax(0,1fr)]">
        <label for="map-label" class="flex items-center gap-2 text-ui font-[550]">
          라벨 칸
          <span class="text-meta font-medium" :class="problems.label ? 'text-danger-ink' : 'text-subtle-foreground'">필수</span>
        </label>
        <NativeSelect
          id="map-label"
          class="font-mono"
          :model-value="mapping.label"
          :aria-invalid="problems.label && mapping.label !== null ? true : undefined"
          @update:model-value="setColumn('label', $event)"
        >
          <option :value="null" disabled>고르기</option>
          <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
        </NativeSelect>
        <span class="flex min-w-0 items-center gap-2 text-ui text-muted-foreground">
          <span
            v-if="problems.label && mapping.label !== null"
            class="inline-flex items-center gap-1.5 font-semibold text-danger-ink"
          >
            <OctagonAlert class="size-4 text-danger" />{{ problems.label }}
          </span>
          <template v-else>
            <Badge v-if="isSuggested('label')" class="h-5 px-1.5 text-meta text-muted-foreground">추천</Badge>
            <Badge v-if="classLabelNames" class="h-5 px-1.5 text-meta text-muted-foreground">
              ClassLabel · {{ classLabelNames.length }}
            </Badge>
            <span v-else-if="sample(mapping.label)" class="truncate">예: {{ sample(mapping.label) }}</span>
          </template>
        </span>
      </div>

      <!-- 나머지 칸 -->
      <div class="grid items-start gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[132px_minmax(0,1fr)]">
        <span class="text-ui font-[550] sm:leading-6">나머지 칸 → 메타데이터</span>
        <span class="flex min-w-0 flex-wrap items-center gap-1.5">
          <Badge v-for="column in extraColumns" :key="column" class="font-mono">{{ column }}</Badge>
          <span v-if="extraColumns.length === 0" class="text-ui leading-6 text-subtle-foreground">없음</span>
        </span>
      </div>
    </div>
  </section>

  <section class="card overflow-hidden" aria-label="DENT에 들어갈 모양">
    <div class="flex h-11 items-center gap-2.5 border-b px-4">
      <h2 class="text-body font-semibold">DENT에 들어갈 모양</h2>
      <span class="text-ui text-muted-foreground">앞 {{ rows.length }}줄</span>
    </div>

    <template v-if="isReady">
      <dl class="grid grid-cols-2 divide-x border-b">
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">
            라벨 · {{ classLabelNames ? 'ClassLabel' : `미리 보기 ${rows.length}줄` }}
          </dt>
          <dd class="mt-0.5 text-title font-semibold">
            {{ fmt(classLabelNames ? classLabelNames.length : previewLabels.length) }}
          </dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">건너뜀 · 미리 보기</dt>
          <dd
            class="mt-0.5 text-title font-semibold"
            :class="skippedCount ? 'text-warning-ink' : 'text-subtle-foreground'"
          >
            {{ skippedCount }}
          </dd>
        </div>
      </dl>

      <div class="flex flex-wrap gap-1.5 border-b px-4 py-3">
        <template v-if="classLabelNames">
          <Badge v-for="name in classLabelNames.slice(0, LABEL_CHIPS_MAX)" :key="name">{{ name }}</Badge>
          <span v-if="classLabelNames.length > LABEL_CHIPS_MAX" class="self-center text-ui text-muted-foreground">
            +{{ classLabelNames.length - LABEL_CHIPS_MAX }}
          </span>
        </template>
        <template v-else>
          <Badge v-for="[name, count] in previewLabels.slice(0, LABEL_CHIPS_MAX)" :key="name" class="max-w-full">
            <span class="truncate">{{ name }}</span><span class="font-semibold">{{ count }}</span>
          </Badge>
          <span v-if="previewLabels.length > LABEL_CHIPS_MAX" class="self-center text-ui text-muted-foreground">
            +{{ previewLabels.length - LABEL_CHIPS_MAX }}
          </span>
          <span v-if="previewLabels.length === 0" class="text-ui text-subtle-foreground">라벨 없음</span>
        </template>
      </div>

      <div class="max-h-[440px] overflow-auto">
        <table class="w-full min-w-[560px] table-fixed border-separate border-spacing-0 text-ui">
          <colgroup>
            <col class="w-14" />
            <col />
            <col class="w-[180px]" />
          </colgroup>
          <thead>
            <tr>
              <th
                v-for="(head, index) in ['줄', '문장', '라벨']"
                :key="head"
                scope="col"
                class="sticky top-0 z-[1] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-meta font-semibold whitespace-nowrap text-muted-foreground"
                :class="index === 0 ? 'text-right' : 'text-left'"
              >
                {{ head }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in mappedRows" :key="row.line" class="group" :class="{ 'bg-muted/40': row.skip }">
              <td class="h-10 border-b px-2.5 text-right font-mono text-meta text-subtle-foreground group-last:border-b-0">
                {{ row.line }}
              </td>
              <td class="h-10 border-b px-2.5 group-last:border-b-0">
                <div v-if="row.text" class="truncate" :class="{ 'text-muted-foreground': row.skip }" :title="row.text">
                  {{ row.text }}
                </div>
                <span v-else class="text-subtle-foreground">—</span>
              </td>
              <td v-if="row.skip" class="h-10 border-b px-2.5 group-last:border-b-0">
                <span class="inline-flex items-center gap-1.5 font-semibold text-warning-ink">
                  건너뜀 <span class="font-normal text-muted-foreground">· {{ row.skip }}</span>
                </span>
              </td>
              <td v-else class="h-10 border-b px-2.5 group-last:border-b-0">
                <Badge class="max-w-full" :title="row.label"><span class="truncate">{{ row.label }}</span></Badge>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <div v-else class="flex h-24 items-center justify-center gap-2 text-ui text-subtle-foreground">
      {{ missing.join(' · ') }} 고르기
    </div>
  </section>

  <div class="flex items-center gap-3">
    <Button type="button" variant="outline" @click="emit('back')">이전</Button>
    <span v-if="missing.length" class="ml-auto text-ui text-muted-foreground">빠짐 · {{ missing.join(' · ') }}</span>
    <Button type="button" :class="{ 'ml-auto': !missing.length }" :disabled="!canGoNext" @click="emit('next')">
      다음
    </Button>
  </div>
</template>
