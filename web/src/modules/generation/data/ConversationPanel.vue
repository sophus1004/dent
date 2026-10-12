<!--
  대화 상세 패널: 표에서 대화를 누르면 오른쪽에 열린다(분류 문장 패널과 같은 틀). 넓은 화면에서는 표 옆에, 좁으면 표 위에 뜬다.
    머리     대화 #번호 · 이전(K) · 다음(J) · 닫기(Esc)
    몸       상태 · 출처 · 문제 · Jev(뜻 분석이 물었으면) · 그대로 둔 검사 · 고치기 · 대화(ConversationView, 문제 자리 칠함) · 메타데이터 · 가져온 곳
    바닥     단축키
  고치기는 [휴지통으로] · [되살리기](POST conversations/bulk)다. 바꾸면 changed를 알려 부모가 목록 · 머리 건수 · 이 대화를 다시 읽는다.
  문제마다 고치는 결정(빼기 · 가리기 · 자르기 · 그대로 두기)은 검토 화면에서 한다([검토]가 그 검사의 큐를 연다).
  가져온 곳은 가져오기 기록(GET /imports/{id})으로 따로 읽는다.
-->
<script setup lang="ts">
import { ArchiveRestore, ChevronDown, ChevronUp, FileQuestion, ListChecks, LoaderCircle, Trash2, TriangleAlert, X } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { getImport } from '@/system/api'
import { GRADES } from '@/system/diagnosis/grades'
import { dateTimeText, fmt, showValue } from '@/system/format'
import { errorMessage } from '@/system/http'
import type { ImportRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { bulkUpdateConversations } from '@/modules/generation/api'
import { SOURCE_WORDS, checkWords } from '@/modules/generation/checks'
import ConversationView from '@/modules/generation/ConversationView.vue'
import type { CheckKey, ConversationRead, Grade } from '@/modules/generation/types'

const props = defineProps<{
  datasetId: number
  conversationId: number
  // 연 대화. 찾는 중이면 null
  conversation: ConversationRead | null
  // 찾아봤는데 없는 대화인지
  missing: boolean
  canPrev: boolean
  canNext: boolean
  // 검사마다 등급 (칠하는 색)
  grades: Partial<Record<CheckKey, Grade>>
}>()

const emit = defineEmits<{
  close: []
  // -1 이전 줄, +1 다음 줄
  step: [delta: number]
  // 이 대화를 휴지통으로 보냈거나 되살렸다
  changed: [conversationId: number]
}>()

// 가져오기 기록은 바뀌지 않으므로 한 번 읽은 것은 다시 묻지 않는다. 번호 → 기록
const importCache = new Map<number, ImportRead>()

// 칸 이름
const FIELD_LABEL_CLASS = 'mb-1.5 flex items-center gap-1.5 text-ui font-[550]'

const source = ref<ImportRead | null>(null)
const moving = ref(false)
const moveError = ref<string | null>(null)

// 문제 검사들 (같은 검사가 여러 자리에 있어도 한 번)
const problemKeys = computed(() => [...new Set(props.conversation?.problems.map((problem) => problem.key) ?? [])])
const extraFields = computed(() => Object.entries(props.conversation?.extra ?? {}))
const characters = computed(() => props.conversation?.messages.reduce((sum, message) => sum + message.content.length, 0) ?? 0)

watch(
  () => props.conversation?.id,
  () => {
    moveError.value = null
    void loadSource(props.conversation?.import_id ?? null)
  },
  { immediate: true },
)

/** 이 대화를 휴지통으로 보낸다. 휴지통의 대화면 되살린다. */
async function moveConversation(): Promise<void> {
  const conversation = props.conversation
  if (!conversation || moving.value) return
  moving.value = true
  moveError.value = null
  try {
    const action = conversation.trashed ? 'restore' : 'trash'
    await bulkUpdateConversations(props.datasetId, { ids: [conversation.id], action })
    emit('changed', conversation.id)
  } catch (error) {
    moveError.value = errorMessage(error)
  } finally {
    moving.value = false
  }
}

async function loadSource(importId: number | null): Promise<void> {
  source.value = importId === null ? null : (importCache.get(importId) ?? null)
  if (importId === null || source.value) return
  try {
    const found = await getImport(importId)
    importCache.set(importId, found)
    // 읽는 사이 다른 대화로 옮겼으면 버린다.
    if (props.conversation?.import_id === importId) source.value = found
  } catch {
    // 가져온 곳을 못 읽어도 번호는 보인다.
  }
}

function tone(key: CheckKey): string {
  return GRADES[props.grades[key] === 'bad' ? 'bad' : 'warn'].textClass
}
</script>

<template>
  <aside
    class="flex h-[calc(100vh-52px)] w-[30rem] flex-col border-l bg-card shadow-(--shadow-panel) duration-200 animate-in fade-in-0 slide-in-from-right-4 max-xl:fixed max-xl:top-[52px] max-xl:right-0 max-xl:bottom-0 max-xl:z-30 max-xl:h-auto max-xl:w-[26rem]"
    aria-label="대화 상세"
  >
    <div class="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <span class="text-body font-semibold">대화</span>
      <span class="font-mono text-ui text-subtle-foreground">#{{ conversationId }}</span>
      <div class="ml-auto flex items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canPrev" aria-label="이전 대화" @click="emit('step', -1)">
              <ChevronUp />
            </Button>
          </TooltipTrigger>
          <TooltipContent>이전 · K</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canNext" aria-label="다음 대화" @click="emit('step', 1)">
              <ChevronDown />
            </Button>
          </TooltipTrigger>
          <TooltipContent>다음 · J</TooltipContent>
        </Tooltip>
        <span class="mx-1 h-4 w-px bg-border" />
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" aria-label="닫기" @click="emit('close')">
              <X />
            </Button>
          </TooltipTrigger>
          <TooltipContent>닫기 · Esc</TooltipContent>
        </Tooltip>
      </div>
    </div>

    <div v-if="missing" class="flex flex-1 flex-col items-center justify-center gap-2 text-center">
      <span class="grid size-10 place-items-center rounded-lg bg-muted">
        <FileQuestion class="size-5 text-muted-foreground" />
      </span>
      <span class="text-body font-semibold">없는 대화</span>
      <span class="font-mono text-meta text-muted-foreground">#{{ conversationId }}</span>
    </div>

    <div v-else-if="!conversation" class="flex-1 space-y-5 p-4" aria-busy="true">
      <Skeleton class="h-32 w-full rounded-lg" />
      <div class="space-y-2.5">
        <Skeleton class="h-4 w-40" />
        <Skeleton class="h-4 w-28" />
      </div>
    </div>

    <div v-else class="flex-1 space-y-5 overflow-y-auto p-4">
      <dl class="grid grid-cols-[84px_minmax(0,1fr)] items-center gap-x-2.5 gap-y-2 text-ui">
        <dt class="text-muted-foreground">상태</dt>
        <dd>{{ conversation.trashed ? '휴지통' : '학습 포함' }}</dd>
        <dt class="text-muted-foreground">출처</dt>
        <dd>{{ SOURCE_WORDS[conversation.source] ?? conversation.source }}</dd>
        <dt class="text-muted-foreground">문제</dt>
        <dd class="flex flex-wrap items-center gap-1">
          <template v-if="problemKeys.length">
            <RouterLink
              v-for="key in problemKeys"
              :key="key"
              :to="{ name: 'generation-review', params: { datasetId }, query: { check: key } }"
              class="inline-flex h-[21px] items-center gap-1 rounded-md px-1.5 text-[11.5px] font-semibold shadow-[inset_0_0_0_1px_var(--border-strong)] hover:bg-muted"
              :class="tone(key)"
              :title="`${checkWords(key).name} 검토`"
            >
              <component :is="checkWords(key).icon" class="size-3" />{{ checkWords(key).name }}
            </RouterLink>
          </template>
          <span v-else class="text-subtle-foreground">—</span>
        </dd>
        <template v-if="conversation.jev">
          <dt class="text-muted-foreground">Jev</dt>
          <dd class="flex flex-wrap items-center gap-x-2 text-muted-foreground">
            <span v-if="conversation.jev.follows !== null">
              지시 따름 <b class="font-semibold text-foreground">{{ conversation.jev.follows.toFixed(2) }}</b>
            </span>
            <span v-if="conversation.jev.refusal_ok !== null">
              거절 마땅 <b class="font-semibold text-foreground">{{ conversation.jev.refusal_ok.toFixed(2) }}</b>
            </span>
          </dd>
        </template>
        <template v-if="conversation.kept.length">
          <dt class="text-muted-foreground">그대로 둠</dt>
          <dd class="flex flex-wrap items-center gap-1">
            <span
              v-for="key in conversation.kept"
              :key="key"
              class="inline-flex h-[21px] items-center gap-1 rounded-md bg-muted px-1.5 text-[11.5px] font-semibold text-muted-foreground"
            >
              <component :is="checkWords(key).icon" class="size-3" />{{ checkWords(key).name }}
            </span>
          </dd>
        </template>
      </dl>

      <section>
        <div :class="FIELD_LABEL_CLASS">고치기</div>
        <div class="grid grid-cols-2 gap-1.5 [&>*]:justify-start">
          <Button v-if="problemKeys.length && !conversation.trashed" variant="outline" size="sm" as-child>
            <RouterLink :to="{ name: 'generation-review', params: { datasetId }, query: { check: problemKeys[0] } }">
              <ListChecks />검토
            </RouterLink>
          </Button>
          <Button
            v-if="conversation.trashed"
            type="button"
            variant="outline"
            size="sm"
            :disabled="moving"
            data-action="conversation-restore"
            @click="moveConversation"
          >
            <LoaderCircle v-if="moving" class="animate-spin" /><ArchiveRestore v-else />되살리기
          </Button>
          <Button
            v-else
            type="button"
            variant="outline"
            size="sm"
            class="hover:text-danger-ink"
            :disabled="moving"
            data-action="conversation-trash"
            @click="moveConversation"
          >
            <LoaderCircle v-if="moving" class="animate-spin" /><Trash2 v-else />휴지통으로
          </Button>
        </div>
        <div v-if="moveError" class="mt-2 flex items-start gap-1.5 text-ui" role="alert">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-danger" />
          <span class="font-semibold text-danger-ink">실패</span>
          <span class="text-muted-foreground">{{ moveError }}</span>
        </div>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">
          대화<span class="ml-auto font-normal text-muted-foreground">메시지 {{ conversation.messages.length }} · {{ fmt(characters) }}자</span>
        </div>
        <div class="rounded-lg border px-3">
          <ConversationView :conversation="conversation" :grades="grades" />
        </div>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">메타데이터</div>
        <dl v-if="extraFields.length" class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-ui">
          <template v-for="[key, value] in extraFields" :key="key">
            <dt class="truncate text-muted-foreground" :title="key">{{ key }}</dt>
            <dd class="min-w-0 break-words">{{ showValue(value) || '—' }}</dd>
          </template>
        </dl>
        <span v-else class="text-ui text-subtle-foreground">없음</span>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">가져온 곳</div>
        <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-ui">
          <dt class="text-muted-foreground">원본</dt>
          <dd class="min-w-0">
            <span v-if="conversation.import_id === null" class="text-subtle-foreground">—</span>
            <span v-else class="flex min-w-0 items-center gap-1.5">
              <span v-if="source" class="truncate" :title="source.source_name">{{ source.source_name }}</span>
              <Skeleton v-else class="h-3.5 w-32" />
              <span class="shrink-0 font-mono text-meta text-subtle-foreground">#{{ conversation.import_id }}</span>
            </span>
          </dd>
          <dt class="text-muted-foreground">추가</dt>
          <dd>{{ dateTimeText(conversation.created_at) }}</dd>
          <dt class="text-muted-foreground">수정</dt>
          <dd>{{ dateTimeText(conversation.updated_at) }}</dd>
        </dl>
      </section>
    </div>

    <div
      class="flex h-11 shrink-0 items-center gap-3 overflow-hidden border-t bg-muted/30 px-4 text-ui whitespace-nowrap text-muted-foreground"
    >
      <span class="inline-flex items-center gap-1"><span class="kbd">J</span><span class="kbd">K</span>이동</span>
      <span class="inline-flex items-center gap-1"><span class="kbd">Esc</span>닫기</span>
    </div>
  </aside>
</template>
