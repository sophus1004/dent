<!--
  생성 가져오기 3단계 '칸 맞추기': 원본의 모양을 고르고 칸을 대화에 맞춘다. 원본이 train · test처럼 나뉘어 있어도 한 데이터로 가져온다.
    모양      messages(role · content) · ShareGPT(from · value) · Alpaca(지시 · 입력 · 응답) · 턴마다 칸(input1 · output1 …)
              · 선호 쌍(chosen · rejected) · 두 응답 + 점수(response1 · response2 · 선호 점수)
              처음 열면 칸 이름으로 짐작한 값(POST /generation/mapping/suggest)을 채운다.
    칸        모양마다 꼭 고를 칸이 다르다. 시스템 칸은 Alpaca · 턴마다 칸에서, 도구 칸은 messages · ShareGPT에서 고를 수 있다(선택).
              Alpaca의 응답 칸을 비우면 지시만 가져온다(입구 1: 응답 만들기로 채운다).
    나머지    대화로 쓰지 않는 칸은 메타데이터(extra)로 들어간다.
    미리 보기 앞쪽 줄을 서버(importing.parse_row)와 같은 규칙으로 대화로 풀어 턴 수 · 첫 사용자 말 · 마지막(고른) 응답, 건너뛸 줄과 이유를 보인다.
  고른 칸은 v-model:mapping으로 화면에 돌려준다. [이전]은 back, [다음]은 next.
-->
<script setup lang="ts">
import {
  CircleCheck,
  ClipboardList,
  Columns3,
  GitCompare,
  LoaderCircle,
  MessageSquareQuote,
  MessagesSquare,
  Minus,
  Plus,
  Scale,
  type LucideIcon,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { clip, fmt, showValue } from '@/system/format'
import { sourceColumns, sourceLines, sourceRows, type LoadedSource } from '@/system/sources/source'
import { Badge } from '@/system/ui/badge'
import { Button } from '@/system/ui/button'
import { NativeSelect } from '@/system/ui/native-select'

import { suggestMapping } from '@/modules/generation/api'
import type { FieldMapping, Shape, TurnColumns } from '@/modules/generation/types'

const props = defineProps<{
  source: LoadedSource
}>()

const emit = defineEmits<{
  back: []
  next: []
}>()

const mapping = defineModel<FieldMapping>('mapping', { required: true })

// 원본 모양: 이름 · 아이콘 · 대표 칸 · 쓰는 곳
const SHAPES: { key: Shape; name: string; icon: LucideIcon; columns: string[]; example: string }[] = [
  { key: 'messages', name: 'messages', icon: MessagesSquare, columns: ['role', 'content'], example: 'TRL · OpenAI' },
  { key: 'sharegpt', name: 'ShareGPT', icon: MessageSquareQuote, columns: ['from', 'value'], example: 'LLaMA-Factory · Axolotl' },
  { key: 'alpaca', name: 'Alpaca', icon: ClipboardList, columns: ['instruction', 'input', 'output'], example: '지시 한 번 · 응답 한 번' },
  { key: 'turns', name: '턴마다 칸', icon: Columns3, columns: ['input1', 'output1', '…'], example: '표 · 엑셀' },
  { key: 'preference', name: '선호 쌍', icon: GitCompare, columns: ['chosen', 'rejected'], example: 'TRL DPO · UltraFeedback' },
  { key: 'pairwise', name: '두 응답 + 점수', icon: Scale, columns: ['response1', 'response2', '점수'], example: 'HelpSteer3' },
]

// 칸 이름 (선택 칸의 열쇠 → 낱말 · 필수 · 설명)
type ColumnKey =
  | 'messages'
  | 'instruction'
  | 'input'
  | 'output'
  | 'system'
  | 'tools'
  | 'prompt'
  | 'chosen'
  | 'rejected'
  | 'chosen_score'
  | 'rejected_score'
  | 'response1'
  | 'response2'
  | 'preference'

interface FieldRow {
  key: ColumnKey
  name: string
  required: boolean
  // 선택 칸의 작은 글
  note?: string
}

// 모양마다 고를 칸 (턴마다 칸은 따로 그린다)
const FIELDS: Record<Shape, FieldRow[]> = {
  messages: [
    { key: 'messages', name: '대화 칸', required: true },
    { key: 'tools', name: '도구 칸', required: false, note: '선택 · 도구 정의' },
  ],
  sharegpt: [
    { key: 'messages', name: '대화 칸', required: true },
    { key: 'tools', name: '도구 칸', required: false, note: '선택 · 도구 정의' },
  ],
  alpaca: [
    { key: 'instruction', name: '지시 칸', required: true },
    { key: 'input', name: '입력 칸', required: false, note: '선택 · 지시 뒤에 붙음' },
    { key: 'output', name: '응답 칸', required: false, note: '선택 · 없으면 지시만' },
    { key: 'system', name: '시스템 칸', required: false, note: '선택' },
  ],
  turns: [{ key: 'system', name: '시스템 칸', required: false, note: '선택' }],
  preference: [
    { key: 'prompt', name: '지시 칸', required: false, note: '선택 · 없으면 고른 응답의 앞 메시지' },
    { key: 'chosen', name: '고른 응답 칸', required: true },
    { key: 'rejected', name: '버린 응답 칸', required: true },
    { key: 'chosen_score', name: '고른 점수 칸', required: false, note: '선택 · 같으면 동점' },
    { key: 'rejected_score', name: '버린 점수 칸', required: false, note: '선택' },
  ],
  pairwise: [
    { key: 'prompt', name: '지시 칸', required: true },
    { key: 'response1', name: '응답 1 칸', required: true },
    { key: 'response2', name: '응답 2 칸', required: true },
    { key: 'preference', name: '선호 점수 칸', required: true, note: '음수 → 1 · 양수 → 2 · 0 동점' },
  ],
}

// 모든 고를 칸 (원본에 없는 칸을 지울 때 · 메타데이터를 셀 때)
const COLUMN_KEYS: ColumnKey[] = [
  'messages',
  'instruction',
  'input',
  'output',
  'system',
  'tools',
  'prompt',
  'chosen',
  'rejected',
  'chosen_score',
  'rejected_score',
  'response1',
  'response2',
  'preference',
]

// 맞출 수 있는 최대 턴 수 (백엔드 schemas.MAX_TURN_COLUMNS와 같다)
const MAX_TURN_COLUMNS = 20

// 예시 · 미리 보기 글을 줄이는 글자 수
const SAMPLE_LENGTH = 40
const PREVIEW_LENGTH = 70

// 백엔드(importing.py)와 같은 역할 이름 · 건너뛰는 이유
const ROLE_ALIASES: Record<string, 'system' | 'user' | 'assistant'> = {
  system: 'system',
  user: 'user',
  human: 'user',
  prompter: 'user',
  assistant: 'assistant',
  gpt: 'assistant',
  bot: 'assistant',
  model: 'assistant',
  chatgpt: 'assistant',
}
// 도구 결과 · 도구 호출을 따로 된 메시지로 두는 역할 (서버가 tool · 응답의 tool_calls로 맞춘다)
const TOOL_ROLES = new Set(['tool', 'function', 'function_call', 'tool_call', 'observation', 'ipython'])
const SKIP = {
  empty: '빈 대화',
  notList: '대화 목록이 아님',
  unknownRole: '알 수 없는 역할',
  noPair: '고른 응답 · 버린 응답이 없음',
  badPreference: '선호 점수를 읽을 수 없음',
}

interface Message {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
}

interface MappedRow {
  line: number
  turns: number
  firstUser: string
  // 마지막 응답 (선호면 고른 응답). 지시만이면 빈 글
  lastAnswer: string
  skip: string | null
}

const suggested = ref<FieldMapping | null>(null)
const isSuggesting = ref(false)
let suggestController: AbortController | null = null

const columns = computed(() => sourceColumns(props.source))
const rows = computed(() => sourceRows(props.source))

// 모양마다 꼭 고를 칸이 빠졌는지
const missing = computed(() => {
  const value = mapping.value
  if (value.shape === 'turns') {
    const isTurnsFilled = value.turns.length > 0 && value.turns.every((turn) => turn.user && turn.assistant)
    return isTurnsFilled ? [] : ['턴 칸']
  }
  return FIELDS[value.shape].filter((field) => field.required && !value[field.key]).map((field) => field.name)
})

// 흐름의 입구: 선호 쌍 → 3, 응답 칸 없는 Alpaca → 1(지시만), 나머지 → 2
const entryNote = computed(() => {
  const value = mapping.value
  if (value.shape === 'preference' || value.shape === 'pairwise') return '입구 3단계 · 선호 쌍'
  if (value.shape === 'alpaca' && !value.output) return '입구 1단계 · 지시만'
  return '입구 2단계 · 지시만 · 도구 대화도'
})
const isReady = computed(() => missing.value.length === 0)
const isPairShape = computed(() => mapping.value.shape === 'preference' || mapping.value.shape === 'pairwise')

// 대화로 쓰지 않는 칸 → 메타데이터
const extraColumns = computed(() => {
  const value = mapping.value
  const used = new Set(
    [...COLUMN_KEYS.map((key) => value[key]), ...value.turns.flatMap((turn) => [turn.user, turn.assistant])].filter(
      (name): name is string => Boolean(name),
    ),
  )
  return columns.value.filter((column) => !used.has(column))
})

const mappedRows = computed<MappedRow[]>(() => {
  if (!isReady.value) return []
  const lines = sourceLines(props.source)
  return rows.value.map((row, index) => mapRow(row, lines[index]))
})
const skippedCount = computed(() => mappedRows.value.filter((row) => row.skip !== null).length)

function text(value: unknown): string {
  return showValue(value).trim()
}

/** 서버(importing.parse_row)와 같은 규칙으로 한 줄을 대화로 풀어 미리 보기 한 줄로. */
function mapRow(row: Record<string, unknown>, line: number): MappedRow {
  const skipped = (skip: string): MappedRow => ({ line, turns: 0, firstUser: '', lastAnswer: '', skip })
  const shape = mapping.value.shape
  if (shape === 'preference' || shape === 'pairwise') {
    const pair = pairOf(row)
    if (typeof pair === 'string') return skipped(pair)
    if (!pair.context.length) return skipped(SKIP.empty)
    return {
      line,
      turns: pair.context.filter((message) => message.role === 'assistant').length + 1,
      firstUser: pair.context.find((message) => message.role === 'user')?.content ?? '',
      lastAnswer: pair.chosen,
      skip: null,
    }
  }
  const messages = messagesOf(row)
  if (typeof messages === 'string') return skipped(messages)
  if (!messages.length) return skipped(SKIP.empty)
  // 마지막이 응답이 아니면 지시만이다(응답 만들기로 채운다).
  const last = messages[messages.length - 1]
  const hasAnswer = last.role === 'assistant'
  return {
    line,
    turns: messages.filter((message) => message.role === 'assistant').length,
    firstUser: messages.find((message) => message.role === 'user')?.content ?? '',
    lastAnswer: hasAnswer ? last.content : '',
    skip: null,
  }
}

function messagesOf(row: Record<string, unknown>): Message[] | string {
  const value = mapping.value
  if (value.shape === 'messages' || value.shape === 'sharegpt') return parseList(row[value.messages!])
  const system = value.system ? text(row[value.system]) : ''
  const head: Message[] = system ? [{ role: 'system', content: system }] : []
  if (value.shape === 'alpaca') {
    const instruction = text(row[value.instruction!])
    const input = value.input ? text(row[value.input]) : ''
    const user = instruction + (input ? `\n\n${input}` : '')
    const answer = value.output ? text(row[value.output]) : ''
    if (!user && !answer) return []
    return [...head, { role: 'user', content: user }, ...(value.output ? [{ role: 'assistant' as const, content: answer }] : [])]
  }
  const turns: Message[] = []
  for (const pair of value.turns) {
    const user = text(row[pair.user])
    const answer = text(row[pair.assistant])
    if (!user && !answer) break
    turns.push({ role: 'user', content: user }, { role: 'assistant', content: answer })
  }
  return turns.length ? [...head, ...turns] : []
}

/** 지시 칸: 대화 목록이면 그대로, 글이면 사용자 메시지 하나. */
function promptOf(cell: unknown): Message[] | string {
  const parsed = jsonOf(cell)
  if (Array.isArray(parsed)) return parseList(parsed)
  const content = text(cell)
  return content ? [{ role: 'user', content }] : []
}

/** 응답 칸: 대화 목록이면 (앞 메시지, 마지막 응답), 글이면 응답 하나. */
function answerOf(cell: unknown): { before: Message[]; answer: string | null } | string {
  const parsed = jsonOf(cell)
  if (Array.isArray(parsed)) {
    const messages = parseList(parsed)
    if (typeof messages === 'string') return messages
    const last = messages[messages.length - 1]
    return last?.role === 'assistant' ? { before: messages.slice(0, -1), answer: last.content } : { before: messages, answer: null }
  }
  const content = text(cell)
  return { before: [], answer: content || null }
}

/** 선호 · 두 응답 한 줄: (맥락, 고른 응답) 또는 건너뛰는 이유. */
function pairOf(row: Record<string, unknown>): { context: Message[]; chosen: string } | string {
  const value = mapping.value
  if (value.shape === 'preference') {
    const chosen = answerOf(row[value.chosen!])
    const rejected = answerOf(row[value.rejected!])
    if (typeof chosen === 'string') return chosen
    if (typeof rejected === 'string') return rejected
    const context = value.prompt ? promptOf(row[value.prompt]) : chosen.before
    if (typeof context === 'string') return context
    if (chosen.answer === null || rejected.answer === null) return SKIP.noPair
    return { context, chosen: chosen.answer }
  }
  const context = promptOf(row[value.prompt!])
  if (typeof context === 'string') return context
  const first = answerOf(row[value.response1!])
  const second = answerOf(row[value.response2!])
  if (typeof first === 'string') return first
  if (typeof second === 'string') return second
  const score = Number(text(row[value.preference!]))
  if (!text(row[value.preference!]) || Number.isNaN(score)) return SKIP.badPreference
  const chosen = score > 0 ? second.answer : first.answer
  const other = score > 0 ? first.answer : second.answer
  if (chosen === null || other === null) return SKIP.noPair
  return { context, chosen }
}

/** 칸 값이 JSON 글이면 읽는다. 아니면 그대로. */
function jsonOf(cell: unknown): unknown {
  if (typeof cell !== 'string') return cell
  const trimmed = cell.trim()
  if (!trimmed.startsWith('[') && !trimmed.startsWith('{')) return cell
  try {
    return JSON.parse(trimmed)
  } catch {
    return cell
  }
}

function parseList(cell: unknown): Message[] | string {
  if (cell === null || cell === undefined) return []
  let items: unknown = cell
  if (typeof cell === 'string') {
    try {
      items = JSON.parse(cell)
    } catch {
      return SKIP.notList
    }
  }
  if (!Array.isArray(items)) return SKIP.notList
  const messages: Message[] = []
  for (const item of items) {
    if (typeof item !== 'object' || item === null) return SKIP.notList
    const record = item as Record<string, unknown>
    const rawRole = String(record.role ?? record.from ?? '').trim().toLowerCase()
    // 도구 호출은 응답으로, 도구 결과는 tool로 본다(서버가 tool_calls · tool_call_id로 맞춘다).
    const role = TOOL_ROLES.has(rawRole) ? (rawRole === 'function_call' || rawRole === 'tool_call' ? 'assistant' : 'tool') : ROLE_ALIASES[rawRole]
    if (!role) return SKIP.unknownRole
    const content = record.content ?? record.value
    messages.push({ role, content: Array.isArray(content) ? content.map((part) => (part as { text?: string }).text ?? '').join('\n') : showValue(content) })
  }
  return messages
}

function setShape(shape: Shape): void {
  mapping.value = { ...mapping.value, shape }
}

function setColumn(key: ColumnKey, value: string | null | undefined): void {
  mapping.value = { ...mapping.value, [key]: value || null }
}

function setTurn(index: number, key: keyof TurnColumns, value: string | null | undefined): void {
  const turns = mapping.value.turns.map((turn, at) => (at === index ? { ...turn, [key]: value ?? '' } : turn))
  mapping.value = { ...mapping.value, turns }
}

function addTurn(): void {
  if (mapping.value.turns.length >= MAX_TURN_COLUMNS) return
  mapping.value = { ...mapping.value, turns: [...mapping.value.turns, { user: '', assistant: '' }] }
}

function removeTurn(index: number): void {
  mapping.value = { ...mapping.value, turns: mapping.value.turns.filter((_turn, at) => at !== index) }
}

/** 칸의 첫 값(비지 않은 것)을 짧게. 예시로 보인다. */
function sample(column: string | null): string {
  if (!column) return ''
  for (const row of rows.value) {
    const value = text(row[column])
    if (value) return clip(value, SAMPLE_LENGTH)
  }
  return ''
}

/** 칸 이름으로 짐작한 값을 받아 채운다(아직 아무것도 고르지 않았을 때만 부른다). */
async function suggest(): Promise<void> {
  const controller = new AbortController()
  suggestController = controller
  isSuggesting.value = true
  try {
    const result = await suggestMapping({ columns: columns.value })
    if (controller.signal.aborted) return
    suggested.value = result
    mapping.value = result
  } catch {
    // 짐작은 돕는 일일 뿐이다. 실패하면 사용자가 직접 고른다.
  } finally {
    if (suggestController === controller) isSuggesting.value = false
  }
}

onMounted(() => {
  // 시트나 구성을 바꾸면 칸이 바뀔 수 있으므로 원본에 없는 칸은 지운다.
  const known = (column: string | null) => (column !== null && columns.value.includes(column) ? column : null)
  const value = mapping.value
  const kept = Object.fromEntries(COLUMN_KEYS.map((key) => [key, known(value[key] ?? null)]))
  mapping.value = {
    ...value,
    ...kept,
    turns: value.turns.filter((turn) => columns.value.includes(turn.user) && columns.value.includes(turn.assistant)),
  } as FieldMapping
  const isUntouched = COLUMN_KEYS.every((key) => !mapping.value[key]) && !mapping.value.turns.length
  if (isUntouched) void suggest()
})

onBeforeUnmount(() => suggestController?.abort())

const ROW = 'grid items-center gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[132px_minmax(0,260px)_minmax(0,1fr)]'
const LABEL = 'flex items-center gap-2 text-ui font-[550]'
</script>

<template>
  <section class="card" aria-label="원본 모양">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <h2 class="text-body font-semibold">원본 모양</h2>
      <span class="ml-auto flex items-center gap-2 text-ui text-muted-foreground">
        <LoaderCircle v-if="isSuggesting" class="size-4 animate-spin" aria-label="짐작하는 중" />
        {{ entryNote }}
      </span>
    </div>
    <div class="grid grid-cols-2 gap-2 px-4 pt-3 pb-3.5 md:grid-cols-3 xl:grid-cols-6">
      <button
        v-for="shape in SHAPES"
        :key="shape.key"
        type="button"
        class="grid content-start gap-[7px] rounded-[10px] border bg-card p-2.5 text-left transition-shadow"
        :class="mapping.shape === shape.key ? 'border-primary shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_18%,transparent)]' : 'hover:border-border-strong'"
        :aria-pressed="mapping.shape === shape.key"
        :data-shape="shape.key"
        @click="setShape(shape.key)"
      >
        <span class="flex items-center gap-1.5 text-[13.5px] font-[650]">
          <component :is="shape.icon" class="size-[15px]" :class="mapping.shape === shape.key ? 'text-primary' : 'text-muted-foreground'" />
          {{ shape.name }}
          <CircleCheck v-if="mapping.shape === shape.key" class="ml-auto size-[15px] text-primary" />
          <Badge v-else-if="suggested?.shape === shape.key" class="ml-auto h-[18px] px-1 text-[10.5px] text-muted-foreground">추천</Badge>
        </span>
        <span class="flex flex-wrap gap-[3px]">
          <span v-for="column in shape.columns" :key="column" class="rounded-[5px] bg-muted px-[5px] font-mono text-[11px] text-muted-foreground">{{ column }}</span>
        </span>
        <span class="text-[11.5px] text-muted-foreground">{{ shape.example }}</span>
      </button>
    </div>
  </section>

  <section class="card" aria-label="칸 맞추기">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <h2 class="text-body font-semibold">칸 맞추기</h2>
      <span class="ml-auto text-ui text-muted-foreground">원본 칸 {{ columns.length }}</span>
    </div>

    <div class="divide-y">
      <!-- 모양마다 고를 칸 (턴마다 칸은 아래) -->
      <div v-for="field in FIELDS[mapping.shape]" :key="field.key" :class="ROW" :data-field="field.key">
        <label :for="`map-${field.key}`" :class="LABEL">
          {{ field.name }}
          <span class="text-meta font-medium" :class="field.required && !mapping[field.key] ? 'text-danger-ink' : 'text-subtle-foreground'">
            {{ field.required ? '필수' : field.note }}
          </span>
        </label>
        <NativeSelect :id="`map-${field.key}`" class="font-mono" :model-value="mapping[field.key] ?? null" @update:model-value="setColumn(field.key, $event)">
          <option :value="null">{{ field.required ? '고르기' : '없음' }}</option>
          <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
        </NativeSelect>
        <span class="truncate text-ui text-muted-foreground">{{ sample(mapping[field.key] ?? null) ? `예: ${sample(mapping[field.key] ?? null)}` : '' }}</span>
      </div>

      <!-- 턴마다 칸 -->
      <template v-if="mapping.shape === 'turns'">
        <div v-for="(turn, index) in mapping.turns" :key="index" class="grid items-center gap-x-3 gap-y-1.5 px-4 py-2.5 sm:grid-cols-[132px_minmax(0,1fr)_minmax(0,1fr)_32px]">
          <span :class="LABEL">{{ index + 1 }}번째 턴</span>
          <NativeSelect class="font-mono" :model-value="turn.user" aria-label="사용자 칸" @update:model-value="setTurn(index, 'user', $event)">
            <option value="" disabled>사용자 칸</option>
            <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
          </NativeSelect>
          <NativeSelect class="font-mono" :model-value="turn.assistant" aria-label="응답 칸" @update:model-value="setTurn(index, 'assistant', $event)">
            <option value="" disabled>응답 칸</option>
            <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
          </NativeSelect>
          <Button type="button" variant="quiet" size="icon-sm" aria-label="턴 빼기" @click="removeTurn(index)"><Minus /></Button>
        </div>
        <div class="px-4 py-2.5">
          <Button type="button" variant="outline" size="sm" :disabled="mapping.turns.length >= MAX_TURN_COLUMNS" @click="addTurn">
            <Plus />턴 더하기
          </Button>
        </div>
      </template>

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
      <span v-if="isReady" class="ml-auto text-ui" :class="skippedCount ? 'font-semibold text-warning-ink' : 'text-subtle-foreground'">
        건너뜀 {{ fmt(skippedCount) }}
      </span>
    </div>

    <div v-if="isReady" class="max-h-[440px] overflow-auto">
      <table class="w-full min-w-[640px] table-fixed border-separate border-spacing-0 text-ui">
        <colgroup>
          <col class="w-14" />
          <col class="w-12" />
          <col />
          <col />
        </colgroup>
        <thead>
          <tr>
            <th
              v-for="(head, index) in ['줄', '턴', '첫 사용자 말', isPairShape ? '고른 응답' : '마지막 응답']"
              :key="head"
              scope="col"
              class="sticky top-0 z-[1] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-meta font-semibold whitespace-nowrap text-muted-foreground"
              :class="index < 2 ? 'text-right' : 'text-left'"
            >
              {{ head }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in mappedRows" :key="row.line" class="group" :class="{ 'bg-muted/40': row.skip }">
            <td class="h-10 border-b px-2.5 text-right font-mono text-meta text-subtle-foreground group-last:border-b-0">{{ row.line }}</td>
            <td class="h-10 border-b px-2.5 text-right group-last:border-b-0">{{ row.skip ? '' : row.turns }}</td>
            <td v-if="row.skip" colspan="2" class="h-10 border-b px-2.5 group-last:border-b-0">
              <span class="inline-flex items-center gap-1.5 font-semibold text-warning-ink">
                건너뜀 <span class="font-normal text-muted-foreground">· {{ row.skip }}</span>
              </span>
            </td>
            <template v-else>
              <td class="h-10 border-b px-2.5 group-last:border-b-0">
                <div v-if="row.firstUser" class="truncate" :title="row.firstUser">{{ clip(row.firstUser, PREVIEW_LENGTH) }}</div>
                <span v-else class="text-subtle-foreground">(빈 지시)</span>
              </td>
              <td class="h-10 border-b px-2.5 group-last:border-b-0">
                <div v-if="row.lastAnswer" class="truncate text-muted-foreground" :title="row.lastAnswer">{{ clip(row.lastAnswer, PREVIEW_LENGTH) }}</div>
                <span v-else class="text-subtle-foreground">(응답 없음 · 지시만)</span>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="flex h-24 items-center justify-center gap-2 text-ui text-subtle-foreground">{{ missing.join(' · ') }} 고르기</div>
  </section>

  <div class="flex items-center gap-3">
    <Button type="button" variant="outline" @click="emit('back')">이전</Button>
    <span v-if="missing.length" class="ml-auto text-ui text-muted-foreground">빠짐 · {{ missing.join(' · ') }}</span>
    <Button type="button" :class="{ 'ml-auto': !missing.length }" :disabled="!isReady" @click="emit('next')">다음</Button>
  </div>
</template>
