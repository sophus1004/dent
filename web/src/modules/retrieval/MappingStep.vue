<!--
  검색 가져오기 3단계 '필드 맞추기': 원본 모양을 고르고, 원본의 칸을 검색의 칸에 맞춘다.
    원본 모양   6장: 쌍 · 세 쌍 · 독해 MRC · 점수 · 문서만 · BEIR(예정). 카드마다 칸 · 예 · 입구 단계 · 채울 것.
                처음 열면 칸 이름으로 짐작한 모양과 칸(POST /retrieval/mapping/suggest)을 채운다.
    칸 맞추기   모양마다 칸 줄이 다르다. 질의 · 정답 · 오답(여럿) · 문서 · 점수 · 답 · 제목 · 문서 번호 ·
                머리말 칸(여럿, 학습 글 머리말에 붙음) · 묶음 칸(같은 값이면 한 묶음) · 나머지 칸.
    들어갈 모양 미리 보기 줄을 고른 칸대로 바꿔 보인다. 서버와 같은 규칙으로 건너뛸 줄(빈 질의 등)을 표시한다.
  원본에 분할(분할 열 · 허깅페이스 분할)이 있어도 나누지 않고 한 덩어리로 가져온다(같은 질의는 하나로).
  고른 칸은 v-model:mapping으로 화면에 돌려준다. [이전]은 back, [다음]은 next.
-->
<script setup lang="ts">
import { CircleCheck, LoaderCircle, OctagonAlert, X } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { clip, fmt, showValue } from '@/system/format'
import { sourceColumns, sourceLines, sourceRows, type LoadedSource } from '@/system/sources/source'
import { Badge } from '@/system/ui/badge'
import { Button } from '@/system/ui/button'
import { NativeSelect } from '@/system/ui/native-select'

import { suggestMapping } from '@/modules/retrieval/api'
import { SHAPE_ORDER, SHAPES, SOON_SHAPE } from '@/modules/retrieval/shapes'
import StageNo from '@/modules/retrieval/StageNo.vue'
import type { FieldMapping, Shape } from '@/modules/retrieval/types'

const props = defineProps<{
  source: LoadedSource
}>()

const emit = defineEmits<{
  back: []
  next: []
}>()

const mapping = defineModel<FieldMapping>('mapping', { required: true })

// 백엔드(modules/retrieval/models.py)와 같은 길이 제한. 넘으면 그 줄을 건너뛴다.
const QUERY_MAX_LENGTH = 2_000
const DOCUMENT_MAX_LENGTH = 200_000

// 토큰 어림. 백엔드(text_rules.py)의 HANGUL_CHARS_PER_TOKEN · OTHER_CHARS_PER_TOKEN과 같다.
const HANGUL_CHARS_PER_TOKEN = 1.4
const OTHER_CHARS_PER_TOKEN = 3.6

// 예시 값 · 표 칸을 보일 최대 글자 수
const SAMPLE_LENGTH = 40
const CELL_LENGTH = 80

// 건너뛰는 이유. 백엔드(modules/retrieval/importing.py)와 같은 낱말이다.
const SKIP_EMPTY_QUERY = '빈 질의'
const SKIP_TOO_LONG_QUERY = '너무 긴 질의'
const SKIP_EMPTY_DOCUMENT = '빈 문서'
const SKIP_TOO_LONG_DOCUMENT = '너무 긴 문서'
const SKIP_BAD_SCORE = '알 수 없는 점수'

// 한 칸 줄: 맞춤의 이름 · 보일 이름 · 필수 · 설명
type ColumnKey =
  | 'text'
  | 'query'
  | 'positive'
  | 'document'
  | 'score'
  | 'answer'
  | 'title'
  | 'doc_key'
  | 'group_column'
interface RoleRow {
  key: ColumnKey
  name: string
  required: boolean
  note: string
}

const ROLES: Record<Shape, RoleRow[]> = {
  documents: [
    { key: 'text', name: '본문 칸', required: true, note: '' },
    { key: 'title', name: '문서 제목 칸', required: false, note: '' },
    { key: 'doc_key', name: '문서 번호 칸', required: false, note: '원래 번호 · 내보낼 때 씀' },
    { key: 'group_column', name: '묶음 칸', required: false, note: '같은 값 → 한 묶음' },
  ],
  pair: [
    { key: 'query', name: '질의 칸', required: true, note: '' },
    { key: 'positive', name: '정답 문서 칸', required: true, note: '' },
    { key: 'title', name: '문서 제목 칸', required: false, note: '' },
    { key: 'group_column', name: '묶음 칸', required: false, note: '같은 값 → 한 묶음' },
  ],
  mrc: [
    { key: 'query', name: '질의 칸', required: true, note: '' },
    { key: 'positive', name: '정답 문서 칸', required: true, note: '지문 → 정답 문서 · 같은 지문은 하나로' },
    { key: 'answer', name: '답 칸', required: false, note: '답 근거' },
    { key: 'title', name: '문서 제목 칸', required: false, note: '' },
    { key: 'group_column', name: '묶음 칸', required: false, note: '같은 값 → 한 묶음' },
  ],
  triplet: [
    { key: 'query', name: '질의 칸', required: true, note: '' },
    { key: 'positive', name: '정답 문서 칸', required: true, note: '' },
    { key: 'title', name: '문서 제목 칸', required: false, note: '' },
    { key: 'group_column', name: '묶음 칸', required: false, note: '같은 값 → 한 묶음' },
  ],
  scored: [
    { key: 'query', name: '질의 칸', required: true, note: '' },
    { key: 'document', name: '문서 칸', required: true, note: '' },
    { key: 'score', name: '점수 칸', required: true, note: '0 → 오답 · 1 → 정답 · 0~3은 등급 그대로' },
    { key: 'group_column', name: '묶음 칸', required: false, note: '같은 값 → 한 묶음' },
  ],
}

// 미리 보기 한 줄이 DENT에 들어갈 모양
interface MappedRow {
  line: number
  // 표 칸 넷 (모양마다 뜻이 다르다)
  cells: string[]
  skip: string | null
}

const suggested = ref<FieldMapping | null>(null)
const isSuggesting = ref(false)
let suggestController: AbortController | null = null

const columns = computed(() => sourceColumns(props.source))
const rows = computed(() => sourceRows(props.source))

const shape = computed(() => mapping.value.shape)
const roles = computed(() => ROLES[shape.value])
const hasQueries = computed(() => shape.value !== 'documents')

const missing = computed(() =>
  ROLES[shape.value].filter((role) => role.required && !mapping.value[role.key]).map((role) => role.name),
)
const used = computed(() => {
  const names = ROLES[shape.value].map((role) => mapping.value[role.key]).filter((name): name is string => !!name)
  if (shape.value === 'triplet') names.push(...mapping.value.negatives)
  names.push(...mapping.value.header_columns)
  return names
})
const clashing = computed(() => {
  const seen = new Set<string>()
  const clash = new Set<string>()
  for (const name of used.value) {
    if (seen.has(name)) clash.add(name)
    seen.add(name)
  }
  return clash
})
const canGoNext = computed(() => missing.value.length === 0 && clashing.value.size === 0)
const extraColumns = computed(() => columns.value.filter((column) => !used.value.includes(column)))
const negativeChoices = computed(() =>
  columns.value.filter((column) => !mapping.value.negatives.includes(column) && !used.value.includes(column)),
)
const headerChoices = computed(() => columns.value.filter((column) => !used.value.includes(column)))

const tableHead = computed(() => {
  if (shape.value === 'documents') return ['문서 제목', '본문', '토큰']
  if (shape.value === 'scored') return ['질의', '문서', '점수 → 판정']
  if (shape.value === 'mrc') return ['질의', '정답 문서', '답']
  return ['질의', '정답 문서', '오답']
})

const mappedRows = computed<MappedRow[]>(() => {
  if (missing.value.length) return []
  const lines = sourceLines(props.source)
  const value = (row: Record<string, unknown>, column: string | null) => (column ? showValue(row[column]).trim() : '')
  return rows.value.map((row, index) => {
    const m = mapping.value
    if (shape.value === 'documents') {
      const text = value(row, m.text)
      const skip = !text ? SKIP_EMPTY_DOCUMENT : text.length > DOCUMENT_MAX_LENGTH ? SKIP_TOO_LONG_DOCUMENT : null
      return { line: lines[index], cells: [value(row, m.title), text, fmt(estimateTokens(text))], skip }
    }
    const query = value(row, m.query)
    const documentColumn = shape.value === 'scored' ? m.document : m.positive
    const document = value(row, documentColumn)
    let third = ''
    let skip: string | null = null
    if (!query) skip = SKIP_EMPTY_QUERY
    else if (query.length > QUERY_MAX_LENGTH) skip = SKIP_TOO_LONG_QUERY
    else if (!document) skip = SKIP_EMPTY_DOCUMENT
    else if (document.length > DOCUMENT_MAX_LENGTH) skip = SKIP_TOO_LONG_DOCUMENT
    if (shape.value === 'scored') {
      const score = value(row, m.score)
      const grade = gradeOf(score)
      if (!skip && grade === null) skip = SKIP_BAD_SCORE
      third = grade === null ? score : `${score} → ${grade >= 1 ? `정답${grade > 1 ? ` ${grade}` : ''}` : '오답'}`
    } else if (shape.value === 'mrc') {
      third = answerText(value(row, m.answer))
    } else if (shape.value === 'triplet') {
      third = m.negatives.map((column) => value(row, column)).filter(Boolean).map((text) => clip(text, 24)).join(' · ') || '—'
    } else {
      third = '—'
    }
    return { line: lines[index], cells: [query, document, third], skip }
  })
})

const skippedCount = computed(() => mappedRows.value.filter((row) => row.skip !== null).length)
const kept = computed(() => mappedRows.value.filter((row) => row.skip === null))
const distinctDocuments = computed(() => new Set(kept.value.map((row) => (shape.value === 'documents' ? row.cells[1] : row.cells[1]))).size)

/** 서버(importing._grade)와 같은 규칙: 0~3 정수는 그대로, 0~1 실수는 0.5 이상이면 1. 모르면 null. */
function gradeOf(score: string): number | null {
  const number = Number(score)
  if (score === '' || Number.isNaN(number)) return null
  if (Number.isInteger(number) && number >= 0 && number <= 3) return number
  if (number >= 0 && number <= 1) return number >= 0.5 ? 1 : 0
  return null
}

/** 허깅페이스 MRC의 {"text": [...]} 모양이면 첫 답. */
function answerText(raw: string): string {
  if (!raw.startsWith('{')) return raw
  try {
    const parsed = JSON.parse(raw) as { text?: unknown }
    return Array.isArray(parsed.text) && parsed.text.length ? String(parsed.text[0]) : raw
  } catch {
    return raw
  }
}

/** 백엔드 text_rules.estimate_tokens와 같은 어림: 한글 1.4글자 · 그 밖 3.6글자(공백 빼고)가 1토큰. */
function estimateTokens(text: string): number {
  let hangul = 0
  let other = 0
  for (const char of text) {
    if (char >= '가' && char <= '힣') hangul += 1
    else if (!/\s/.test(char)) other += 1
  }
  return Math.ceil(hangul / HANGUL_CHARS_PER_TOKEN + other / OTHER_CHARS_PER_TOKEN)
}

function setShape(next: Shape): void {
  if (next === shape.value) return
  const from = suggested.value
  // 모양을 바꾸면 그 모양에 맞는 짐작 칸을 다시 채운다(질의 · 제목은 이어 간다).
  mapping.value = {
    shape: next,
    text: next === 'documents' ? (mapping.value.text ?? from?.text ?? pick(['text', 'content', 'body', '본문'])) : null,
    query: next === 'documents' ? null : (mapping.value.query ?? from?.query ?? null),
    positive: ['pair', 'mrc', 'triplet'].includes(next) ? (mapping.value.positive ?? from?.positive ?? null) : null,
    negatives: next === 'triplet' ? (mapping.value.negatives.length ? mapping.value.negatives : (from?.negatives ?? [])) : [],
    document: next === 'scored' ? (mapping.value.document ?? from?.document ?? mapping.value.positive ?? from?.positive ?? null) : null,
    score: next === 'scored' ? (mapping.value.score ?? from?.score ?? null) : null,
    answer: next === 'mrc' ? (mapping.value.answer ?? from?.answer ?? null) : null,
    title: mapping.value.title ?? from?.title ?? null,
    doc_key: next === 'documents' ? (mapping.value.doc_key ?? from?.doc_key ?? null) : null,
    header_columns: mapping.value.header_columns,
    group_column: mapping.value.group_column,
  }
}

function pick(names: string[]): string | null {
  const lower = new Map(columns.value.map((column) => [column.trim().toLowerCase(), column]))
  for (const name of names) if (lower.has(name)) return lower.get(name)!
  return null
}

function setColumn(key: ColumnKey, value: string | null | undefined): void {
  mapping.value = { ...mapping.value, [key]: value ?? null }
}

function addNegative(column: string | null | undefined): void {
  if (!column) return
  mapping.value = { ...mapping.value, negatives: [...mapping.value.negatives, column] }
}

function removeNegative(column: string): void {
  mapping.value = { ...mapping.value, negatives: mapping.value.negatives.filter((name) => name !== column) }
}

function addHeader(column: string | null | undefined): void {
  if (!column) return
  mapping.value = { ...mapping.value, header_columns: [...mapping.value.header_columns, column] }
}

function removeHeader(column: string): void {
  mapping.value = {
    ...mapping.value,
    header_columns: mapping.value.header_columns.filter((name) => name !== column),
  }
}

function sample(column: string | null): string {
  if (!column) return ''
  for (const row of rows.value) {
    const value = showValue(row[column]).trim()
    if (value) return clip(value, SAMPLE_LENGTH)
  }
  return ''
}

function isSuggested(key: ColumnKey): boolean {
  const value = mapping.value[key]
  return value !== null && suggested.value?.[key] === value
}

async function suggest(): Promise<void> {
  const controller = new AbortController()
  suggestController = controller
  isSuggesting.value = true
  try {
    const result = await suggestMapping({ columns: columns.value })
    if (controller.signal.aborted) return
    suggested.value = result
    mapping.value = { ...result }
  } catch {
    // 짐작은 돕는 일일 뿐이다. 실패하면 사용자가 직접 고른다.
  } finally {
    if (suggestController === controller) isSuggesting.value = false
  }
}

onMounted(() => {
  // 시트나 구성을 바꾸면 칸이 바뀔 수 있으므로 원본에 없는 칸은 지운다.
  const known = (column: string | null) => (column !== null && columns.value.includes(column) ? column : null)
  const current = mapping.value
  mapping.value = {
    ...current,
    text: known(current.text),
    query: known(current.query),
    positive: known(current.positive),
    negatives: current.negatives.filter((column) => columns.value.includes(column)),
    document: known(current.document),
    score: known(current.score),
    answer: known(current.answer),
    title: known(current.title),
    doc_key: known(current.doc_key),
    header_columns: current.header_columns.filter((column) => columns.value.includes(column)),
    group_column: known(current.group_column),
  }
  const isUntouched = used.value.length === 0
  if (isUntouched) void suggest()
})

onBeforeUnmount(() => suggestController?.abort())
</script>

<template>
  <section class="card" aria-label="원본 모양">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <h2 class="text-body font-semibold">원본 모양</h2>
      <span class="ml-auto flex items-center gap-2 text-ui text-muted-foreground">
        <LoaderCircle v-if="isSuggesting" class="size-4 animate-spin" aria-label="짐작하는 중" />
        입구 = 흐름의 몇 단계로 들어오나
      </span>
    </div>
    <div class="grid grid-cols-2 gap-2 px-4 pt-3 pb-3.5 md:grid-cols-3 xl:grid-cols-6">
      <button
        v-for="key in SHAPE_ORDER"
        :key="key"
        type="button"
        class="grid content-start gap-[7px] rounded-[10px] border bg-card p-2.5 text-left transition-shadow"
        :class="key === shape ? 'border-primary shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_18%,transparent)]' : 'hover:border-border-strong'"
        :aria-pressed="key === shape"
        :data-shape="key"
        @click="setShape(key)"
      >
        <span class="flex items-center gap-1.5 text-[13.5px] font-[650]">
          <component :is="SHAPES[key].icon" class="size-[15px]" :class="key === shape ? 'text-primary' : 'text-muted-foreground'" />
          {{ SHAPES[key].name }}
          <CircleCheck v-if="key === shape" class="ml-auto size-[15px] text-primary" />
          <Badge v-else-if="suggested?.shape === key" class="ml-auto h-[18px] px-1 text-[10.5px] text-muted-foreground">추천</Badge>
        </span>
        <span class="flex flex-wrap gap-[3px]">
          <span v-for="column in SHAPES[key].columns" :key="column" class="rounded-[5px] bg-muted px-[5px] font-mono text-[11px] text-muted-foreground">{{ column }}</span>
        </span>
        <span class="text-[11.5px] text-muted-foreground">{{ SHAPES[key].example }}</span>
        <span class="flex flex-wrap gap-[3px]">
          <span class="inline-flex h-[18px] items-center gap-1 rounded-[5px] px-[5px] text-[10.5px] font-[650] shadow-[inset_0_0_0_1px_var(--border-strong)]">
            <StageNo :no="SHAPES[key].entry" size="xs" />입구 {{ SHAPES[key].entry }}단계
          </span>
          <span
            v-for="[fill, permission] in SHAPES[key].fills"
            :key="fill"
            class="inline-flex h-[18px] items-center rounded-[5px] bg-accent px-[5px] text-[10.5px] font-[650] whitespace-nowrap text-accent-foreground"
          >{{ fill }}{{ permission ? ' · 허락' : '' }}</span>
        </span>
      </button>
      <span class="grid cursor-default content-start gap-[7px] rounded-[10px] border bg-card p-2.5 opacity-55" aria-disabled="true">
        <span class="flex items-center gap-1.5 text-[13.5px] font-[650]">
          <component :is="SOON_SHAPE.icon" class="size-[15px] text-muted-foreground" />{{ SOON_SHAPE.name }}
          <span class="ml-auto text-[11px] text-muted-foreground">예정</span>
        </span>
        <span class="flex flex-wrap gap-[3px]">
          <span v-for="column in SOON_SHAPE.columns" :key="column" class="rounded-[5px] bg-muted px-[5px] font-mono text-[11px] text-muted-foreground">{{ column }}</span>
        </span>
        <span class="text-[11.5px] text-muted-foreground">{{ SOON_SHAPE.example }}</span>
      </span>
    </div>
  </section>

  <section class="card" aria-label="칸 맞추기">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <h2 class="text-body font-semibold">칸 맞추기</h2>
      <span class="ml-auto text-ui text-muted-foreground">원본 칸 {{ columns.length }}</span>
    </div>
    <div class="divide-y">
      <div
        v-for="role in roles"
        :key="role.key"
        class="grid items-center gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[140px_minmax(0,260px)_minmax(0,1fr)]"
      >
        <label :for="`map-${role.key}`" class="flex items-center gap-2 text-ui font-[550]">
          {{ role.name }}
          <span v-if="role.required" class="text-meta font-medium" :class="!mapping[role.key] ? 'text-danger-ink' : 'text-subtle-foreground'">필수</span>
        </label>
        <NativeSelect
          :id="`map-${role.key}`"
          class="font-mono"
          :model-value="mapping[role.key] as string | null"
          :aria-invalid="(role.required && !mapping[role.key]) || clashing.has(mapping[role.key] as string) ? true : undefined"
          @update:model-value="setColumn(role.key, $event as string | null)"
        >
          <option :value="null" :disabled="role.required">{{ role.required ? '고르기' : '없음' }}</option>
          <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
        </NativeSelect>
        <span class="flex min-w-0 flex-wrap items-center gap-2 text-ui text-muted-foreground">
          <span v-if="clashing.has(mapping[role.key] as string)" class="inline-flex items-center gap-1.5 font-semibold text-danger-ink">
            <OctagonAlert class="size-4 text-danger" />겹침
          </span>
          <Badge v-else-if="isSuggested(role.key)" class="h-5 px-1.5 text-meta text-muted-foreground">추천</Badge>
          <span v-if="role.note" class="truncate">{{ role.note }}</span>
          <span v-else-if="sample(mapping[role.key] as string | null)" class="truncate">예: {{ sample(mapping[role.key] as string | null) }}</span>
        </span>
      </div>

      <!-- 세 쌍: 오답 칸 여럿 -->
      <div v-if="shape === 'triplet'" class="grid items-center gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[140px_minmax(0,1fr)]">
        <span class="text-ui font-[550]">오답 문서 칸</span>
        <span class="flex min-w-0 flex-wrap items-center gap-1.5">
          <span
            v-for="column in mapping.negatives"
            :key="column"
            class="inline-flex h-6 items-center gap-1 rounded-md bg-accent px-1.5 font-mono text-meta text-accent-foreground"
          >
            {{ column }}
            <button type="button" class="grid place-items-center" :aria-label="`${column} 빼기`" @click="removeNegative(column)">
              <X class="size-3" />
            </button>
          </span>
          <NativeSelect
            v-if="negativeChoices.length"
            class="w-40 font-mono"
            :model-value="null"
            aria-label="오답 칸 더하기"
            @update:model-value="addNegative($event as string | null)"
          >
            <option :value="null">+ 더하기</option>
            <option v-for="column in negativeChoices" :key="column" :value="column">{{ column }}</option>
          </NativeSelect>
          <span class="text-ui text-muted-foreground">여럿 · 빈 칸은 건너뜀</span>
        </span>
      </div>

      <!-- 머리말 칸 여럿: 학습 글 = 제목 › 머리말 칸 › 구획 + 본문 -->
      <div class="grid items-center gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[140px_minmax(0,1fr)]" data-map="header-columns">
        <span class="text-ui font-[550]">머리말 칸</span>
        <span class="flex min-w-0 flex-wrap items-center gap-1.5">
          <span
            v-for="column in mapping.header_columns"
            :key="column"
            class="inline-flex h-6 items-center gap-1 rounded-md bg-accent px-1.5 font-mono text-meta text-accent-foreground"
          >
            {{ column }}
            <button type="button" class="grid place-items-center" :aria-label="`${column} 빼기`" @click="removeHeader(column)">
              <X class="size-3" />
            </button>
          </span>
          <NativeSelect
            v-if="headerChoices.length"
            class="w-40 font-mono"
            :model-value="null"
            aria-label="머리말 칸 더하기"
            @update:model-value="addHeader($event as string | null)"
          >
            <option :value="null">+ 더하기</option>
            <option v-for="column in headerChoices" :key="column" :value="column">{{ column }}</option>
          </NativeSelect>
          <span class="text-ui text-muted-foreground">학습 글 머리말 · 제목 다음</span>
        </span>
      </div>

      <div class="grid items-start gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[140px_minmax(0,1fr)]">
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

    <template v-if="!missing.length">
      <dl class="grid grid-cols-3 divide-x border-b">
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">{{ hasQueries ? '질의' : '문서' }} · 미리 보기</dt>
          <dd class="mt-0.5 text-title font-semibold">{{ fmt(kept.length) }}</dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">문서 · 같은 글 합침</dt>
          <dd class="mt-0.5 text-title font-semibold">{{ fmt(distinctDocuments) }}</dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">건너뜀 · 미리 보기</dt>
          <dd class="mt-0.5 text-title font-semibold" :class="skippedCount ? 'text-warning-ink' : 'text-subtle-foreground'">
            {{ skippedCount }}
          </dd>
        </div>
      </dl>

      <div class="max-h-[440px] overflow-auto">
        <table class="w-full min-w-[640px] table-fixed border-separate border-spacing-0 text-ui">
          <colgroup>
            <col class="w-14" />
            <col :class="shape === 'documents' ? 'w-[200px]' : 'w-[30%]'" />
            <col />
            <col :class="shape === 'documents' ? 'w-[72px]' : 'w-[22%]'" />
          </colgroup>
          <thead>
            <tr>
              <th
                v-for="(head, index) in ['줄', ...tableHead]"
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
              <td class="h-10 border-b px-2.5 text-right font-mono text-meta text-subtle-foreground group-last:border-b-0">{{ row.line }}</td>
              <td v-for="(cell, index) in row.cells.slice(0, 2)" :key="index" class="h-10 border-b px-2.5 group-last:border-b-0">
                <div v-if="cell" class="truncate" :class="{ 'text-muted-foreground': row.skip }" :title="cell">{{ clip(cell, CELL_LENGTH) }}</div>
                <span v-else class="text-subtle-foreground">—</span>
              </td>
              <td v-if="row.skip" class="h-10 border-b px-2.5 group-last:border-b-0">
                <span class="inline-flex items-center gap-1.5 font-semibold text-warning-ink">
                  건너뜀 <span class="font-normal text-muted-foreground">· {{ row.skip }}</span>
                </span>
              </td>
              <template v-else>
                <td class="h-10 border-b px-2.5 text-muted-foreground group-last:border-b-0">
                  <div class="truncate" :title="row.cells[2]">{{ row.cells[2] || '—' }}</div>
                </td>
              </template>
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
    <Button type="button" :class="{ 'ml-auto': !missing.length }" :disabled="!canGoNext" @click="emit('next')">다음</Button>
  </div>
</template>
