<!--
  선호 쌍 보기 (검토 큐의 쌍 검사 · 대화 패널): 지시(맥락) 아래 고른 응답 · 버린 응답을 나란히 둔다.
    지시      ConversationView를 맥락만으로(주 응답을 빼고) 그린다
    판정 줄   Jev 따름 고른 n · 버린 n (응답마다 따로 물은 점수) · 길이 n : n자 · 출처(원본 · 합성) · 남은 쌍 문제 칩
    두 칸     고른 응답(초록 머리) · 버린 응답(빨강 머리). 주 응답(SFT에 쓰는 것)에 '주 응답' 태그. 원본 점수가 있으면 머리에.
  칸의 폭이 30rem보다 좁으면(도우미 창을 옆에 둘 때) 두 응답을 위아래로 쌓는다(@container).
  글을 HTML로 그리지 않는다. 코드 울타리(```) 안은 고정폭으로 그린다.
-->
<script setup lang="ts">
import { ThumbsDown, ThumbsUp } from '@lucide/vue'
import { computed } from 'vue'

import { GRADES } from '@/system/diagnosis/grades'
import { fmt } from '@/system/format'

import { SOURCE_WORDS, checkWords } from '@/modules/generation/checks'
import ConversationView from '@/modules/generation/ConversationView.vue'
import type { AnswerRead, CheckKey, ConversationRead, Grade, PairRead } from '@/modules/generation/types'

const props = defineProps<{
  conversation: ConversationRead
  // 볼 쌍 (대화 안의 쌍 번호)
  pairId: number
  // 검사마다 등급 (칠하는 색)
  grades: Partial<Record<CheckKey, Grade>>
}>()

// 코드 울타리 안의 글 모양
const CODE_CLASS = 'font-mono text-[12.5px]'

const pair = computed<PairRead | null>(() => props.conversation.pairs.find((item) => item.id === props.pairId) ?? null)
const answers = computed(() => new Map(props.conversation.answers.map((answer) => [answer.id, answer])))
const chosen = computed<AnswerRead | null>(() => (pair.value ? answers.value.get(pair.value.chosen_id) ?? null : null))
const rejected = computed<AnswerRead | null>(() => (pair.value ? answers.value.get(pair.value.rejected_id) ?? null : null))

interface Piece {
  text: string
  code: boolean
}

/** 코드 울타리(```)로 글을 나눈다(울타리 안은 고정폭). */
function pieces(content: string): Piece[] {
  const parts = content.split(/(```[\s\S]*?(?:```|$))/g)
  return parts.filter((part) => part.length > 0).map((part) => ({ text: part, code: part.startsWith('```') }))
}

function gradeOf(key: CheckKey): Grade {
  return props.grades[key] === 'bad' ? 'bad' : 'warn'
}

const columns = computed(() => [
  { key: 'chosen', title: '고른 응답', icon: ThumbsUp, answer: chosen.value },
  { key: 'rejected', title: '버린 응답', icon: ThumbsDown, answer: rejected.value },
])
const HEAD_TONE: Record<string, string> = {
  chosen: 'bg-[color-mix(in_oklab,var(--success)_10%,transparent)]',
  rejected: 'bg-[color-mix(in_oklab,var(--danger)_8%,transparent)]',
}
</script>

<template>
  <div class="@container grid gap-2" data-pair-view>
    <ConversationView :conversation="conversation" :grades="grades" context-only />
    <div v-if="pair" class="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground" data-pair-facts>
      <span v-if="pair.judge_chosen !== null && pair.judge_rejected !== null">
        Jev 따름 고른 <b class="font-semibold text-foreground">{{ pair.judge_chosen.toFixed(2) }}</b>
        · 버린 <b class="font-semibold text-foreground">{{ pair.judge_rejected.toFixed(2) }}</b>
      </span>
      <span v-else>Jev 판정 전</span>
      <span class="text-border-strong">·</span>
      <span>길이 {{ fmt(chosen?.content.length ?? 0) }} : {{ fmt(rejected?.content.length ?? 0) }}자</span>
      <span class="text-border-strong">·</span>
      <span>{{ SOURCE_WORDS[pair.source] ?? pair.source }}</span>
      <span v-if="pair.trashed" class="font-semibold text-subtle-foreground">· 뺀 쌍</span>
      <span
        v-for="key in pair.problems"
        :key="key"
        class="inline-flex h-[21px] items-center gap-1 rounded-md px-1.5 text-[11.5px] font-semibold shadow-[inset_0_0_0_1px_var(--border-strong)]"
        :class="GRADES[gradeOf(key)].textClass"
      >
        <component :is="checkWords(key).icon" class="size-3" />{{ checkWords(key).name }}
      </span>
    </div>
    <div class="grid gap-3 @min-[30rem]:grid-cols-2">
      <div
        v-for="column in columns"
        :key="column.key"
        class="min-w-0 overflow-hidden rounded-[10px] border"
        :data-pair-side="column.key"
      >
        <h3 class="flex items-center gap-2 border-b px-3 py-2 text-[13px] font-semibold" :class="HEAD_TONE[column.key]">
          <component :is="column.icon" class="size-3.5" />{{ column.title }}
          <span v-if="column.answer?.is_main" class="rounded bg-card px-1 text-[11px] font-semibold text-accent-foreground">주 응답</span>
          <span v-if="column.answer?.score !== null && column.answer?.score !== undefined" class="text-meta font-normal text-muted-foreground">
            점수 {{ column.answer.score }}
          </span>
          <span v-if="column.answer && column.answer.source !== 'original'" class="text-meta font-normal text-muted-foreground">
            {{ SOURCE_WORDS[column.answer.source] ?? column.answer.source }}
          </span>
        </h3>
        <div class="px-3 py-2.5 text-[13.5px] leading-[1.65] break-words whitespace-pre-wrap">
          <template v-if="column.answer">
            <template v-for="(piece, index) in pieces(column.answer.content)" :key="index">
              <span v-if="piece.code" :class="CODE_CLASS">{{ piece.text }}</span>
              <template v-else>{{ piece.text }}</template>
            </template>
            <span v-if="!column.answer.content" class="text-subtle-foreground">(비어 있음)</span>
          </template>
          <span v-else class="text-subtle-foreground">—</span>
        </div>
      </div>
    </div>
  </div>
</template>
