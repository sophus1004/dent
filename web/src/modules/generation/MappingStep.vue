<!--
  생성 가져오기 3단계 '칸 맞추기': 원본의 모양을 고르고 칸을 대화에 맞춘다. 원본이 train · test처럼 나뉘어 있어도 한 데이터로 가져온다.
    모양      messages(role · content) · ShareGPT(from · value) · Alpaca(지시 · 입력 · 응답) · 턴마다 칸(input1 · output1 …)
              처음 열면 칸 이름으로 짐작한 값(POST /generation/mapping/suggest)을 채운다.
    칸        모양마다 꼭 고를 칸이 다르다. 시스템 칸은 Alpaca · 턴마다 칸에서 고를 수 있다(선택).
    나머지    대화로 쓰지 않는 칸은 메타데이터(extra)로 들어간다.
    미리 보기 앞쪽 줄을 서버(importing.parse_row)와 같은 규칙으로 대화로 풀어 턴 수 · 첫 사용자 말 · 마지막 응답, 건너뛸 줄과 이유를 보인다.
  고른 칸은 v-model:mapping으로 화면에 돌려준다. [이전]은 back, [다음]은 next.
-->
<script setup lang="ts">
import {
  CircleCheck,
  ClipboardList,
  Columns3,
  LoaderCircle,
  MessageSquareQuote,
  MessagesSquare,
  Minus,
  Plus,
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
const TOOL_ROLES = new Set(['tool', 'function', 'function_call', 'observation', 'ipython'])
const SKIP = {
  empty: '빈 대화',
  notList: '대화 목록이 아님',
  unknownRole: '알 수 없는 역할',
  tool: '도구 호출 대화(다음 판)',
  noAnswer: '응답 없음(지시만은 다음 판)',
  lastNotAnswer: '마지막이 응답이 아님',
}

interface Message {
  role: 'system' | 'user' | 'assistant'
  content: string
}

interface MappedRow {
  line: number
  turns: number
  firstUser: string
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
  if (value.shape === 'messages' || value.shape === 'sharegpt') return value.messages ? [] : ['대화 칸']
  if (value.shape === 'alpaca') return [...(value.instruction ? [] : ['지시 칸']), ...(value.output ? [] : ['응답 칸'])]
  const isTurnsFilled = value.turns.length > 0 && value.turns.every((turn) => turn.user && turn.assistant)
  return isTurnsFilled ? [] : ['턴 칸']
})
const isReady = computed(() => missing.value.length === 0)

// 대화로 쓰지 않는 칸 → 메타데이터
const extraColumns = computed(() => {
  const value = mapping.value
  const used = new Set(
    [value.messages, value.instruction, value.input, value.output, value.system, ...value.turns.flatMap((turn) => [turn.user, turn.assistant])].filter(
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
  const messages = messagesOf(row)
  const skipped = (skip: string): MappedRow => ({ line, turns: 0, firstUser: '', lastAnswer: '', skip })
  if (typeof messages === 'string') return skipped(messages)
  if (!messages.length) return skipped(SKIP.empty)
  const lastAnswer = messages.map((message) => message.role).lastIndexOf('assistant')
  if (lastAnswer < 0) return skipped(SKIP.noAnswer)
  if (lastAnswer !== messages.length - 1) return skipped(SKIP.lastNotAnswer)
  return {
    line,
    turns: messages.filter((message) => message.role === 'assistant').length,
    firstUser: messages.find((message) => message.role === 'user')?.content ?? '',
    lastAnswer: messages[lastAnswer].content,
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
    const answer = text(row[value.output!])
    if (!user && !answer) return []
    return [...head, { role: 'user', content: user }, { role: 'assistant', content: answer }]
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
    if (TOOL_ROLES.has(rawRole) || record.tool_calls || record.function_call) return SKIP.tool
    const role = ROLE_ALIASES[rawRole]
    if (!role) return SKIP.unknownRole
    const content = record.content ?? record.value
    messages.push({ role, content: Array.isArray(content) ? content.map((part) => (part as { text?: string }).text ?? '').join('\n') : showValue(content) })
  }
  return messages
}

function setShape(shape: Shape): void {
  mapping.value = { ...mapping.value, shape }
}

function setColumn(key: 'messages' | 'instruction' | 'input' | 'output' | 'system', value: string | null | undefined): void {
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
  mapping.value = {
    ...value,
    messages: known(value.messages),
    instruction: known(value.instruction),
    input: known(value.input),
    output: known(value.output),
    system: known(value.system),
    turns: value.turns.filter((turn) => columns.value.includes(turn.user) && columns.value.includes(turn.assistant)),
  }
  const isUntouched = !mapping.value.messages && !mapping.value.instruction && !mapping.value.turns.length
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
        입구 2단계 · 응답 있는 대화만
      </span>
    </div>
    <div class="grid grid-cols-2 gap-2 px-4 pt-3 pb-3.5 md:grid-cols-4">
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
      <!-- messages · ShareGPT: 대화 칸 -->
      <div v-if="mapping.shape === 'messages' || mapping.shape === 'sharegpt'" :class="ROW">
        <label for="map-messages" :class="LABEL">
          대화 칸
          <span class="text-meta font-medium" :class="mapping.messages ? 'text-subtle-foreground' : 'text-danger-ink'">필수</span>
        </label>
        <NativeSelect id="map-messages" class="font-mono" :model-value="mapping.messages" @update:model-value="setColumn('messages', $event)">
          <option :value="null" disabled>고르기</option>
          <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
        </NativeSelect>
        <span class="truncate text-ui text-muted-foreground">{{ sample(mapping.messages) ? `예: ${sample(mapping.messages)}` : '' }}</span>
      </div>

      <!-- Alpaca: 지시 · 입력 · 응답 -->
      <template v-if="mapping.shape === 'alpaca'">
        <div
          v-for="field in [
            { key: 'instruction', name: '지시 칸', required: true },
            { key: 'input', name: '입력 칸', required: false },
            { key: 'output', name: '응답 칸', required: true },
          ] as const"
          :key="field.key"
          :class="ROW"
        >
          <label :for="`map-${field.key}`" :class="LABEL">
            {{ field.name }}
            <span class="text-meta font-medium" :class="field.required && !mapping[field.key] ? 'text-danger-ink' : 'text-subtle-foreground'">
              {{ field.required ? '필수' : '선택 · 지시 뒤에 붙음' }}
            </span>
          </label>
          <NativeSelect :id="`map-${field.key}`" class="font-mono" :model-value="mapping[field.key]" @update:model-value="setColumn(field.key, $event)">
            <option :value="null">{{ field.required ? '고르기' : '없음' }}</option>
            <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
          </NativeSelect>
          <span class="truncate text-ui text-muted-foreground">{{ sample(mapping[field.key]) ? `예: ${sample(mapping[field.key])}` : '' }}</span>
        </div>
      </template>

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

      <!-- 시스템 칸 (Alpaca · 턴마다 칸) -->
      <div v-if="mapping.shape === 'alpaca' || mapping.shape === 'turns'" :class="ROW">
        <label for="map-system" :class="LABEL">시스템 칸 <span class="text-meta font-medium text-subtle-foreground">선택</span></label>
        <NativeSelect id="map-system" class="font-mono" :model-value="mapping.system" @update:model-value="setColumn('system', $event)">
          <option :value="null">없음</option>
          <option v-for="column in columns" :key="column" :value="column">{{ column }}</option>
        </NativeSelect>
        <span class="truncate text-ui text-muted-foreground">{{ sample(mapping.system) ? `예: ${sample(mapping.system)}` : '' }}</span>
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
              v-for="(head, index) in ['줄', '턴', '첫 사용자 말', '마지막 응답']"
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
                <span v-else class="text-subtle-foreground">(빈 응답)</span>
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
