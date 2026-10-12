<!--
  대화 표: 지시(첫 사용자 말) · 응답(마지막 응답) · 턴 · 문제 · 번호. 머리줄은 상단 바 밑에 붙어 따라온다(분류 문장 표와 같은 틀).
  글은 두 줄까지 보이고, 줄을 누르면 오른쪽 패널에 연다(open). 연 줄은 보라 바탕. 휴지통의 대화는 흐리게.
  처음 불러오는 동안은 회색 막대 줄, 결과가 없으면 '결과 없음', 실패하면 오류 칸. 다시 불러오는 동안은 줄을 흐리게 둔다.
  narrow(패널을 열었거나 칸이 좁을 때)면 응답 · 번호 칸을 뺀다(패널에 있다).
  selectable이면 맨 앞에 고르기 칸을 둔다(휴지통의 되살리기용). 고른 번호는 v-model:selected로 주고받는다.
-->
<script setup lang="ts">
import { SearchX, TriangleAlert } from '@lucide/vue'
import { computed } from 'vue'

import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'

import ProblemMarks from '@/modules/generation/data/ProblemMarks.vue'
import type { CheckKey, ConversationRead, Grade } from '@/modules/generation/types'

const props = defineProps<{
  // 이 쪽의 대화. 아직 못 읽었으면 null
  rows: ConversationRead[] | null
  loading: boolean
  // 불러오기에 실패했을 때의 문장
  error: string | null
  // 오른쪽 패널에 연 대화 번호
  openId: number | null
  // 패널이 열려 표가 좁은지
  narrow: boolean
  // 거르기가 걸려 있어 [조건 지우기]를 보일지
  canClear: boolean
  // 검사마다 등급 (문제 아이콘 색)
  grades: Partial<Record<CheckKey, Grade>>
  // 맨 앞에 고르기 칸을 둘지
  selectable?: boolean
}>()

const emit = defineEmits<{
  open: [conversation: ConversationRead]
  clear: []
  retry: []
}>()

// 고른 대화 번호 (selectable일 때)
const selected = defineModel<number[]>('selected', { default: () => [] })

// 불러오는 동안 보일 회색 줄 수와, 글 막대의 폭(줄마다 조금씩 다르게)
const SKELETON_ROWS = 12
const SKELETON_WIDTHS = ['92%', '78%', '85%', '64%', '88%', '72%']

// 머리 칸: 흐린 회색 바탕, 상단 바(52px) 밑에 붙는다.
const TH_CLASS =
  'sticky top-[52px] z-[5] h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-2.5 text-left text-meta font-semibold whitespace-nowrap text-muted-foreground'

// 몸 칸
const TD_CLASS = 'h-[54px] px-2.5 py-1.5 align-middle'

const isWide = computed(() => !props.narrow)
const columnCount = computed(() => (isWide.value ? 5 : 3) + (props.selectable ? 1 : 0))

// 이 쪽의 대화 번호와, 그 가운데 고른 수
const pageIds = computed(() => (props.rows ?? []).map((conversation) => conversation.id))
const selectedOnPage = computed(() => pageIds.value.filter((id) => selected.value.includes(id)).length)
const isAllSelected = computed(() => pageIds.value.length > 0 && selectedOnPage.value === pageIds.value.length)
const isPartlySelected = computed(() => selectedOnPage.value > 0 && !isAllSelected.value)

/** 이 쪽을 모두 고른다. 이미 모두 골랐으면 모두 푼다. */
function toggleAll(): void {
  selected.value = isAllSelected.value ? [] : [...pageIds.value]
}

function toggle(conversationId: number): void {
  const isSelected = selected.value.includes(conversationId)
  selected.value = isSelected ? selected.value.filter((id) => id !== conversationId) : [...selected.value, conversationId]
}

function firstUser(conversation: ConversationRead): string {
  return conversation.messages.find((message) => message.role === 'user')?.content ?? ''
}

function lastAnswer(conversation: ConversationRead): string {
  // 지시만이면 응답이 없다(마지막 메시지는 사용자 말이다).
  if (!conversation.has_answer) return ''
  const last = conversation.messages.at(-1)
  const calls = last?.tool_calls?.map((call) => (call.function ?? call).name ?? '?') ?? []
  return last?.content || (calls.length ? `→ ${calls.join(' · ')}` : '')
}

function turnCount(conversation: ConversationRead): number {
  return conversation.messages.filter((message) => message.role === 'assistant').length
}

function rowClass(conversation: ConversationRead): string {
  return conversation.id === props.openId
    ? '[&>td]:bg-accent [&>td:first-child]:shadow-[inset_2px_0_0_var(--primary)]'
    : 'hover:[&>td]:bg-muted/65'
}
</script>

<template>
  <table class="w-full table-fixed border-separate border-spacing-0 text-ui">
    <colgroup>
      <col v-if="selectable" class="w-10" />
      <col />
      <col v-if="isWide" />
      <col class="w-[52px]" />
      <col :class="narrow ? 'w-[112px]' : 'w-[132px]'" />
      <col v-if="isWide" class="w-[80px]" />
    </colgroup>
    <thead>
      <tr>
        <th v-if="selectable" :class="[TH_CLASS, 'pr-0 pl-4']">
          <input
            type="checkbox"
            class="checkbox"
            aria-label="모두 고르기"
            :checked="isAllSelected"
            :indeterminate="isPartlySelected"
            :disabled="!pageIds.length"
            data-select="all"
            @change="toggleAll"
          />
        </th>
        <th :class="[TH_CLASS, selectable ? '' : 'pl-4']">지시</th>
        <th v-if="isWide" :class="TH_CLASS">응답</th>
        <th :class="[TH_CLASS, 'text-right']">턴</th>
        <th :class="TH_CLASS">문제</th>
        <th v-if="isWide" :class="[TH_CLASS, 'pr-4 text-right']"><span class="sr-only">번호</span></th>
      </tr>
    </thead>

    <tbody v-if="error">
      <tr>
        <td :colspan="columnCount" class="py-14 text-center">
          <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
            <TriangleAlert class="size-5 text-danger" />
          </span>
          <div class="mt-3 text-body font-semibold">불러오기 실패</div>
          <div class="mt-1 text-ui text-muted-foreground">{{ error }}</div>
          <Button variant="outline" size="sm" class="mt-4" @click="emit('retry')">다시 불러오기</Button>
        </td>
      </tr>
    </tbody>

    <tbody v-else-if="!rows" aria-busy="true">
      <tr v-for="index in SKELETON_ROWS" :key="index" class="[&>td]:border-b">
        <td v-if="selectable" :class="[TD_CLASS, 'pr-0 pl-4']" />
        <td :class="[TD_CLASS, selectable ? '' : 'pl-4']">
          <Skeleton class="h-3.5" :style="{ width: SKELETON_WIDTHS[index % SKELETON_WIDTHS.length] }" />
        </td>
        <td v-if="isWide" :class="TD_CLASS">
          <Skeleton class="h-3.5" :style="{ width: SKELETON_WIDTHS[(index + 2) % SKELETON_WIDTHS.length] }" />
        </td>
        <td :class="TD_CLASS" />
        <td :class="TD_CLASS" />
        <td v-if="isWide" :class="[TD_CLASS, 'pr-4']"><Skeleton class="ml-auto h-3 w-10" /></td>
      </tr>
    </tbody>

    <tbody v-else-if="rows.length === 0">
      <tr>
        <td :colspan="columnCount" class="py-14 text-center">
          <span class="mx-auto grid size-10 place-items-center rounded-lg bg-muted">
            <SearchX class="size-5 text-muted-foreground" />
          </span>
          <div class="mt-3 text-body font-semibold">결과 없음</div>
          <Button v-if="canClear" variant="outline" size="sm" class="mt-4" @click="emit('clear')">
            조건 지우기
          </Button>
        </td>
      </tr>
    </tbody>

    <tbody v-else class="transition-opacity" :class="{ 'opacity-60': loading }" :aria-busy="loading">
      <tr
        v-for="conversation in rows"
        :key="conversation.id"
        :data-conversation-id="conversation.id"
        class="cursor-pointer scroll-mt-[96px] scroll-mb-4 [&>td]:border-b [&>td]:transition-colors"
        :class="rowClass(conversation)"
        :aria-current="conversation.id === openId ? 'true' : undefined"
        @click="emit('open', conversation)"
      >
        <td v-if="selectable" :class="[TD_CLASS, 'pr-0 pl-4']" @click.stop>
          <input
            type="checkbox"
            class="checkbox"
            :aria-label="`#${conversation.id} 고르기`"
            :checked="selected.includes(conversation.id)"
            :data-select="conversation.id"
            @change="toggle(conversation.id)"
          />
        </td>
        <td :class="[TD_CLASS, selectable ? '' : 'pl-4']">
          <div class="line-clamp-2 break-all" :class="conversation.trashed ? 'text-muted-foreground' : 'text-foreground'">
            {{ firstUser(conversation) || '(빈 지시)' }}
          </div>
        </td>
        <td v-if="isWide" :class="TD_CLASS">
          <div class="line-clamp-2 break-all text-muted-foreground">{{ lastAnswer(conversation) || (conversation.has_answer ? '(빈 응답)' : '(응답 없음)') }}</div>
        </td>
        <td :class="[TD_CLASS, 'text-right tabular-nums']">{{ turnCount(conversation) }}</td>
        <td :class="TD_CLASS"><ProblemMarks :conversation="conversation" :grades="grades" /></td>
        <td v-if="isWide" :class="[TD_CLASS, 'pr-4 text-right font-mono text-meta text-subtle-foreground']">
          #{{ conversation.id }}
        </td>
      </tr>
    </tbody>
  </table>
</template>
