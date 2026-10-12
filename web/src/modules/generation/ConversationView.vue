<!--
  대화 보기 (검토 큐 · 데이터 탭 패널): 말풍선이 아니라 역할마다 한 덩어리(왼쪽 역할 칸 + 글).
    역할 칸   시스템 · 사용자 · 응답 + 몇 번째
    글        학습에 들어가는 글 그대로(코드 울타리 ```도 그대로, 울타리 안은 고정폭 글꼴). 문제 자리(서버의 start · end)를 칠한다: 심각 빨강 · 주의 호박.
              보고 있는 검사(focus)의 자리는 더 진하게. 글이 길면 앞부분과 문제 둘레만 보이고 [전체 보기]로 편다.
    칩        그 메시지의 문제 이름(진단 검사 이름과 같다). 대화 전체에 걸친 문제(구조 · 같은 지시)는 맨 위 한 줄.
    도구      응답이 부른 도구는 글 아래 '→ 이름(인자)' 고정폭 줄, 도구 결과는 '도구 결과 · 호출 번호'.
    응답 없음 지시만인 대화는 끝에 점선 줄 '응답 없음'. contextOnly면(선호 쌍 보기) 주 응답을 빼고 맥락만 그린다.
  글을 HTML로 그리지 않는다. 칠한 자리는 글 조각으로 나눠 그린다.
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { GRADES } from '@/system/diagnosis/grades'
import { fmt } from '@/system/format'

import { checkWords } from '@/modules/generation/checks'
import type { CheckKey, ConversationRead, Grade, MessageRead, ProblemRead, ToolCall } from '@/modules/generation/types'

const props = defineProps<{
  conversation: ConversationRead
  // 검사마다 등급 (칠하는 색). 모르면 주의로 칠한다.
  grades: Partial<Record<CheckKey, Grade>>
  // 보고 있는 검사 (그 자리를 더 진하게)
  focus?: CheckKey | null
  // 맥락만 (주 응답을 빼고 그린다: 선호 쌍 보기)
  contextOnly?: boolean
}>()

// 이보다 긴 글은 접는다(글자 수)
const FOLD_LENGTH = 1_400
// 접을 때 앞에 남기는 글자 수 · 문제 자리 앞뒤로 남기는 글자 수
const FOLD_HEAD = 500
const FOLD_AROUND = 320

const ROLE_WORDS = { system: '시스템', user: '사용자', assistant: '응답', tool: '도구 결과' } as const

// 도구 호출 인자를 한 줄로 보일 때의 최대 글자 수
const CALL_ARGS_LENGTH = 160

// 편 메시지 순서들
const expanded = ref(new Set<number>())
watch(
  () => props.conversation.id,
  () => (expanded.value = new Set()),
)

// 대화 전체에 걸친 문제 (자리가 없는 것)
const wholeProblems = computed(() => props.conversation.problems.filter((problem) => problem.turn === null))

interface Segment {
  text: string
  // 칠하는 문제. 칠하지 않으면 null
  problem: ProblemRead | null
  // 코드 울타리(```) 안인지
  code?: boolean
  // 접어서 뺀 자리 표시(⋯)
  gap?: boolean
}

// 코드 울타리 안의 글 모양
const CODE_CLASS = 'font-mono text-[12.5px]'

interface TurnView {
  index: number
  role: MessageRead['role']
  // 역할마다 몇 번째
  order: number
  segments: Segment[]
  problems: ProblemRead[]
  folded: boolean
  length: number
  // 응답이 부른 도구 (이름(인자) 한 줄씩) · 도구 결과가 답하는 호출 번호
  calls: string[]
  callId: string | null
}

// 그릴 메시지: 맥락만이면 주 응답(마지막 메시지)을 뺀다
const shown = computed(() => {
  const messages = props.conversation.messages
  return props.contextOnly && props.conversation.has_answer ? messages.slice(0, -1) : messages
})

/** 도구 호출 한 줄: 이름(인자). 인자는 JSON 글이면 그대로, 아니면 JSON으로. */
function callText(call: ToolCall): string {
  const body = call.function ?? call
  const args = typeof body.arguments === 'string' ? body.arguments : JSON.stringify(body.arguments ?? {})
  const short = args.length > CALL_ARGS_LENGTH ? `${args.slice(0, CALL_ARGS_LENGTH)}…` : args
  return `${body.name ?? '?'}(${short})`
}

const turns = computed<TurnView[]>(() => {
  const counts = { system: 0, user: 0, assistant: 0, tool: 0 }
  return shown.value.map((message, index) => {
    counts[message.role] += 1
    const problems = props.conversation.problems.filter((problem) => problem.turn === index)
    const isLong = message.content.length > FOLD_LENGTH
    const folded = isLong && !expanded.value.has(index)
    return {
      index,
      role: message.role,
      order: counts[message.role],
      segments: folded
        ? foldedSegments(message.content, problems)
        : segmentsOf(message.content, problems, 0, codeRanges(message.content)),
      problems,
      folded,
      length: message.content.length,
      calls: (message.tool_calls ?? []).map(callText),
      callId: message.tool_call_id,
    }
  })
})

// 지시만인 대화: 끝에 '응답 없음' 줄 (맥락만 보일 때는 그리지 않는다)
const missingAnswer = computed(() => !props.conversation.has_answer && !props.contextOnly)

/** 코드 울타리(```) 안의 자리들 [시작, 끝). 닫는 울타리가 없으면 글 끝까지. */
function codeRanges(content: string): [number, number][] {
  const ranges: [number, number][] = []
  let open: number | null = null
  for (const match of content.matchAll(/```/g)) {
    if (open === null) open = match.index
    else {
      ranges.push([open, match.index + match[0].length])
      open = null
    }
  }
  if (open !== null) ranges.push([open, content.length])
  return ranges
}

/**
 * 글을 칠한 자리 · 코드 울타리 경계로 조각낸다. offset은 text가 원래 글에서 시작하는 자리,
 * code는 원래 글에서 잰 코드 자리들.
 */
function segmentsOf(text: string, problems: ProblemRead[], offset: number, code: [number, number][]): Segment[] {
  const spans = problems
    .filter((problem) => problem.start !== null && problem.end !== null && problem.end > problem.start)
    .map((problem) => ({ problem, start: problem.start! - offset, end: problem.end! - offset }))
    .filter((span) => span.end > 0 && span.start < text.length)
    .sort((a, b) => a.start - b.start)
  const ranges = code.map(([start, end]) => [start - offset, end - offset]).filter(([start, end]) => end > 0 && start < text.length)
  const clamp = (at: number) => Math.min(Math.max(at, 0), text.length)
  const cuts = new Set([0, text.length])
  for (const span of spans) [span.start, span.end].forEach((at) => cuts.add(clamp(at)))
  for (const [start, end] of ranges) [start, end].forEach((at) => cuts.add(clamp(at)))
  const points = [...cuts].sort((a, b) => a - b)
  const segments: Segment[] = []
  for (let index = 0; index < points.length - 1; index += 1) {
    const [from, to] = [points[index], points[index + 1]]
    if (to <= from) continue
    // 겹친 자리는 먼저 시작한 문제로 칠한다.
    const problem = spans.find((span) => span.start <= from && span.end >= to)?.problem ?? null
    const isCode = ranges.some(([start, end]) => start <= from && end >= to)
    const last = segments.at(-1)
    if (last && last.problem === problem && last.code === isCode) last.text += text.slice(from, to)
    else segments.push({ text: text.slice(from, to), problem, code: isCode })
  }
  return segments
}

/** 긴 글: 앞부분 + (문제가 뒤에 있으면) 첫 문제 둘레. */
function foldedSegments(text: string, problems: ProblemRead[]): Segment[] {
  const code = codeRanges(text)
  const first = problems.find((problem) => problem.start !== null)
  const head = segmentsOf(text.slice(0, FOLD_HEAD), problems, 0, code)
  if (!first || first.start! < FOLD_HEAD) return [...head, { text: '', problem: null, gap: true }]
  const from = Math.max(FOLD_HEAD, first.start! - FOLD_AROUND)
  const to = Math.min(text.length, first.end! + FOLD_AROUND)
  const around = segmentsOf(text.slice(from, to), problems, from, code)
  return [...head, { text: '', problem: null, gap: true }, ...around, ...(to < text.length ? [{ text: '', problem: null, gap: true }] : [])]
}

function toggle(index: number): void {
  const next = new Set(expanded.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expanded.value = next
}

function gradeOf(problem: ProblemRead): Grade {
  const grade = props.grades[problem.key]
  return grade === 'bad' ? 'bad' : 'warn'
}

// 칠하는 모양: 심각 빨강 · 주의 호박, 보고 있는 검사는 더 진하게
function markClass(problem: ProblemRead): string {
  const strong = problem.key === props.focus
  return gradeOf(problem) === 'bad'
    ? `rounded-[3px] ${strong ? 'bg-[color-mix(in_oklab,var(--danger)_28%,transparent)]' : 'bg-[color-mix(in_oklab,var(--danger)_14%,transparent)]'} shadow-[inset_0_-2px_0_var(--danger)]`
    : `rounded-[3px] ${strong ? 'bg-[color-mix(in_oklab,var(--warning)_30%,transparent)]' : 'bg-[color-mix(in_oklab,var(--warning)_16%,transparent)]'} shadow-[inset_0_-2px_0_var(--warning)]`
}

function turnTone(turn: TurnView): string {
  if (!turn.problems.length) return ''
  return turn.problems.some((problem) => gradeOf(problem) === 'bad')
    ? 'bg-[color-mix(in_oklab,var(--danger)_5%,transparent)]'
    : 'bg-[color-mix(in_oklab,var(--warning)_6%,transparent)]'
}
</script>

<template>
  <div class="grid" data-conversation>
    <div v-if="wholeProblems.length" class="flex flex-wrap items-center gap-1.5 border-b py-2">
      <span
        v-for="problem in wholeProblems"
        :key="problem.key"
        class="inline-flex h-[21px] items-center gap-1 rounded-md px-1.5 text-[11.5px] font-semibold shadow-[inset_0_0_0_1px_var(--border-strong)]"
        :class="GRADES[gradeOf(problem)].textClass"
      >
        <component :is="checkWords(problem.key).icon" class="size-3" />{{ checkWords(problem.key).name }}
      </span>
      <span class="truncate text-meta text-muted-foreground">{{ wholeProblems[0].snippet }}</span>
    </div>

    <div
      v-for="turn in turns"
      :key="turn.index"
      class="grid grid-cols-[72px_minmax(0,1fr)] gap-2.5 border-b py-2.5 last:border-b-0"
      :class="turnTone(turn)"
      :data-turn="turn.index"
    >
      <div
        class="flex flex-col items-start gap-0.5 pl-1 text-meta font-[650]"
        :class="turn.role === 'assistant' ? 'text-accent-foreground' : turn.role === 'system' || turn.role === 'tool' ? 'text-subtle-foreground' : 'text-muted-foreground'"
      >
        {{ ROLE_WORDS[turn.role] }}
        <span v-if="turn.role === 'tool' && turn.callId" class="font-mono text-[11px] font-medium text-subtle-foreground">{{ turn.callId }}</span>
        <span v-else-if="turn.role !== 'system'" class="text-[11px] font-medium text-subtle-foreground">{{ turn.order }}번째</span>
      </div>
      <div class="min-w-0">
        <div
          class="text-[13.5px] leading-[1.65] break-words whitespace-pre-wrap"
          :class="turn.role === 'system' ? 'text-muted-foreground' : ''"
        >
          <template v-for="(segment, index) in turn.segments" :key="index">
            <span v-if="segment.gap" class="mx-1 text-subtle-foreground">⋯</span>
            <mark v-else-if="segment.problem" :class="[markClass(segment.problem), segment.code ? CODE_CLASS : '']" class="text-inherit">{{
              segment.text
            }}</mark>
            <span v-else-if="segment.code" :class="CODE_CLASS">{{ segment.text }}</span>
            <template v-else>{{ segment.text }}</template>
          </template>
          <span v-if="!turn.length && !turn.calls.length" class="text-subtle-foreground">(비어 있음)</span>
        </div>
        <div v-if="turn.calls.length" class="mt-1 grid gap-1" data-tool-calls>
          <div
            v-for="(call, callIndex) in turn.calls"
            :key="callIndex"
            class="w-fit max-w-full truncate rounded-md bg-muted px-1.5 py-0.5 font-mono text-[12px] text-foreground"
          >
            → {{ call }}
          </div>
        </div>
        <div v-if="turn.problems.length || turn.length > FOLD_LENGTH" class="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span
            v-for="problem in turn.problems"
            :key="problem.key"
            class="inline-flex h-[21px] items-center gap-1 rounded-md px-1.5 text-[11.5px] font-semibold shadow-[inset_0_0_0_1px_var(--border-strong)]"
            :class="GRADES[gradeOf(problem)].textClass"
          >
            <component :is="checkWords(problem.key).icon" class="size-3" />{{ checkWords(problem.key).name }}
          </span>
          <button
            v-if="turn.length > FOLD_LENGTH"
            type="button"
            class="ml-auto text-meta font-semibold text-muted-foreground hover:text-foreground"
            @click="toggle(turn.index)"
          >
            {{ turn.folded ? `전체 보기 · ${fmt(turn.length)}자` : '접기' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="missingAnswer"
      class="grid grid-cols-[72px_minmax(0,1fr)] gap-2.5 border-t border-dashed py-2.5"
      data-missing-answer
    >
      <div class="pl-1 text-meta font-[650] text-subtle-foreground">응답</div>
      <div class="text-[13.5px] text-subtle-foreground">응답 없음 · 응답 만들기</div>
    </div>
  </div>
</template>
