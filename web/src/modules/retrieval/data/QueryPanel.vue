<!--
  질의 패널 (데이터 탭 오른쪽): 표 · 지도에서 질의를 누르면 열린다.
    머리     질의 #번호 · 이전(K) · 다음(J) · 닫기(Esc)
    질의     글 전체 · [글 고치기](고치면 출처가 사람으로)
    사실     출처 · 상태 · 첫 정답 순위 · 표시 · 답 근거
    판정 · 순위  기준 검색 상위 10에 판정 · 유사도 · Jev를 겹친다(뜻 분석 전이면 판정 목록만).
             줄마다 [정답으로] · [오답으로] · [떼기]는 바로 반영하고 알림에 [되돌리기].
             제안이 걸린 줄은 빨강(거짓 오답) · 호박(빠진 정답 · 정답 의심) 표시.
    10위 밖  순위 밖의 판정(정답 · 오답)
    고치기   [글 고치기] · [학습에서 빼기 | 다시 넣기] · [휴지통으로 | 되살리기]
    메타데이터 · 가져온 곳
  GET /retrieval/queries/{id}로 스스로 읽는다. 바꾸면 changed를 알려 부모가 목록 · 머리 수를 다시 읽는다.
-->
<script setup lang="ts">
import {
  ArchiveRestore,
  Check,
  ChevronDown,
  ChevronUp,
  CircleMinus,
  CircleSlash,
  Eye,
  EyeOff,
  FileQuestion,
  LoaderCircle,
  Pencil,
  Trash2,
  TriangleAlert,
  X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getImport } from '@/system/api'
import { dateTimeText, fmt, showValue } from '@/system/format'
import { ApiError, errorMessage, isAbortError } from '@/system/http'
import { pushToast } from '@/system/toasts'
import type { ImportRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { deleteJudgment, getQuery, setJudgment, updateQuery } from '@/modules/retrieval/api'
import { EXCLUDE_REASON_NAMES, MARKS, SOURCE_NAMES } from '@/modules/retrieval/data/filters'
import Marks from '@/modules/retrieval/data/Marks.vue'
import GradeChip from '@/modules/retrieval/GradeChip.vue'
import type { QueryDetailRead, RankedDocumentRead } from '@/modules/retrieval/types'

const props = defineProps<{
  queryId: number
  canPrev: boolean
  canNext: boolean
}>()

const emit = defineEmits<{
  close: []
  step: [delta: number]
  changed: [queryId: number]
  openDocument: [documentId: number]
}>()

// 제안 표시의 이름 · 색 (진단 검사와 같다)
const FLAG_WORDS = {
  false_negative: MARKS.false_negative,
  missing_positive: MARKS.missing,
  suspect_positive: MARKS.suspect,
} as const

const importCache = new Map<number, ImportRead>()

const FIELD_LABEL_CLASS = 'mb-1.5 flex items-center gap-1.5 text-ui font-[550]'

const detail = ref<QueryDetailRead | null>(null)
const missing = ref(false)
const source = ref<ImportRead | null>(null)
// 바꾸는 중인 것(판정은 문서 번호, 그 밖은 이름)과 실패한 까닭
const busy = ref<string | number | null>(null)
const actionError = ref<string | null>(null)
// 글 고치기
const editing = ref(false)
const draft = ref('')
let controller: AbortController | null = null

const query = computed(() => detail.value?.query ?? null)
const statusText = computed(() => {
  const current = query.value
  if (!current) return ''
  if (current.is_trashed) return `휴지통 · ${dateTimeText(current.trashed_at)}`
  if (current.exclude_reason) return `학습 제외 · ${EXCLUDE_REASON_NAMES[current.exclude_reason]}`
  return '학습 포함'
})
const extraFields = computed(() => Object.entries(query.value?.extra ?? {}))

watch(() => props.queryId, load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  detail.value = null
  missing.value = false
  editing.value = false
  actionError.value = null
  try {
    detail.value = await getQuery(props.queryId, current.signal)
    void loadSource(detail.value.query.import_id)
  } catch (error) {
    if (isAbortError(error)) return
    missing.value = error instanceof ApiError && error.status === 404
    if (!missing.value) actionError.value = errorMessage(error)
  }
}

async function reload(): Promise<void> {
  try {
    detail.value = await getQuery(props.queryId)
  } catch {
    // 못 읽으면 앞의 값을 그대로 보인다.
  }
}

async function loadSource(importId: number | null): Promise<void> {
  source.value = importId === null ? null : (importCache.get(importId) ?? null)
  if (importId === null || source.value) return
  try {
    const found = await getImport(importId)
    importCache.set(importId, found)
    if (query.value?.import_id === importId) source.value = found
  } catch {
    // 가져온 곳을 못 읽어도 번호는 보인다.
  }
}

/** 바꾸기 하나를 보내고, 성공하면 다시 읽고 부모에 알린다. */
async function run(key: string | number, action: () => Promise<unknown>): Promise<boolean> {
  busy.value = key
  actionError.value = null
  try {
    await action()
    await reload()
    emit('changed', props.queryId)
    return true
  } catch (error) {
    actionError.value = errorMessage(error)
    return false
  } finally {
    busy.value = null
  }
}

/** 판정 바꾸기 (grade null = 떼기). 알림에 [되돌리기]를 단다. */
async function judge(item: RankedDocumentRead, grade: number | null): Promise<void> {
  const queryId = props.queryId
  const documentId = item.document.id
  const before = item.grade
  const ok = await run(documentId, () =>
    grade === null ? deleteJudgment(queryId, documentId) : setJudgment(queryId, documentId, grade),
  )
  if (!ok) return
  const word = grade === null ? '떼기' : grade >= 1 ? '정답으로' : '오답으로'
  pushToast({
    tone: 'done',
    title: `판정 · ${word}`,
    detail: item.document.title || item.document.snippet,
    actions: [
      {
        label: '되돌리기',
        run: async () => {
          await (before === null ? deleteJudgment(queryId, documentId) : setJudgment(queryId, documentId, before))
          if (props.queryId === queryId) await reload()
          emit('changed', queryId)
        },
      },
    ],
  })
}

function startEdit(): void {
  draft.value = query.value?.text ?? ''
  editing.value = true
}

async function saveText(): Promise<void> {
  const current = query.value
  const text = draft.value.trim()
  if (!current || !text || text === current.text) {
    editing.value = false
    return
  }
  const ok = await run('text', () => updateQuery(current.id, { text, row_version: current.row_version }))
  if (ok) editing.value = false
}

function toggleExcluded(): void {
  const current = query.value
  if (!current) return
  void run('exclude', () => updateQuery(current.id, { excluded: current.exclude_reason === null, row_version: current.row_version }))
}

function toggleTrash(): void {
  const current = query.value
  if (!current) return
  void run('trash', () => updateQuery(current.id, { trashed: !current.is_trashed, row_version: current.row_version }))
}

function similarityText(value: number | null): string {
  return value === null ? '—' : value.toFixed(3)
}
</script>

<template>
  <aside
    class="flex h-[calc(100vh-52px)] w-[440px] flex-col border-l bg-card shadow-(--shadow-panel) duration-200 animate-in fade-in-0 slide-in-from-right-4 max-xl:fixed max-xl:top-[52px] max-xl:right-0 max-xl:bottom-0 max-xl:z-30 max-xl:h-auto max-xl:w-[380px]"
    aria-label="질의 상세"
    data-panel="query"
  >
    <div class="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <span class="text-body font-semibold">질의</span>
      <span class="font-mono text-ui text-subtle-foreground">#{{ queryId }}</span>
      <div class="ml-auto flex items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canPrev" aria-label="이전 질의" @click="emit('step', -1)"><ChevronUp /></Button>
          </TooltipTrigger>
          <TooltipContent>이전 · K</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canNext" aria-label="다음 질의" @click="emit('step', 1)"><ChevronDown /></Button>
          </TooltipTrigger>
          <TooltipContent>다음 · J</TooltipContent>
        </Tooltip>
        <span class="mx-1 h-4 w-px bg-border" />
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" aria-label="닫기" @click="emit('close')"><X /></Button>
          </TooltipTrigger>
          <TooltipContent>닫기 · Esc</TooltipContent>
        </Tooltip>
      </div>
    </div>

    <div v-if="missing" class="flex flex-1 flex-col items-center justify-center gap-2 text-center">
      <span class="grid size-10 place-items-center rounded-lg bg-muted"><FileQuestion class="size-5 text-muted-foreground" /></span>
      <span class="text-body font-semibold">없는 질의</span>
      <span class="font-mono text-meta text-muted-foreground">#{{ queryId }}</span>
    </div>

    <div v-else-if="!detail || !query" class="flex-1 space-y-5 p-4" aria-busy="true">
      <Skeleton class="h-16 w-full rounded-lg" />
      <div class="space-y-2.5">
        <Skeleton class="h-4 w-40" />
        <Skeleton class="h-4 w-28" />
      </div>
      <Skeleton class="h-40 w-full rounded-lg" />
    </div>

    <div v-else class="flex-1 space-y-5 overflow-y-auto p-4">
      <section>
        <div :class="FIELD_LABEL_CLASS">
          질의<span class="ml-auto font-normal text-muted-foreground">{{ fmt(query.text.length) }}자</span>
        </div>
        <template v-if="editing">
          <textarea
            v-model="draft"
            rows="3"
            class="w-full rounded-lg border border-input bg-card px-3 py-2 text-body outline-none focus:border-ring focus:ring-3 focus:ring-ring/22"
            aria-label="질의 글"
            data-field="query-text"
            @keydown.esc.stop="editing = false"
          />
          <div class="mt-1.5 flex justify-end gap-1.5">
            <Button type="button" variant="outline" size="sm" @click="editing = false">취소</Button>
            <Button type="button" size="sm" :disabled="busy === 'text'" data-action="save-query-text" @click="saveText">
              <LoaderCircle v-if="busy === 'text'" class="animate-spin" />저장
            </Button>
          </div>
        </template>
        <p v-else class="rounded-lg border px-3 py-2.5 text-body whitespace-pre-wrap">{{ query.text }}</p>
      </section>

      <dl class="grid grid-cols-[84px_minmax(0,1fr)] items-center gap-x-2.5 gap-y-2 text-ui">
        <dt class="text-muted-foreground">출처</dt>
        <dd class="flex min-w-0 items-center gap-1.5">
          {{ SOURCE_NAMES[query.source] }}
          <button
            v-if="query.source_document_id"
            type="button"
            class="font-mono text-meta text-accent-foreground hover:underline"
            @click="emit('openDocument', query.source_document_id)"
          >
            문서 #{{ query.source_document_id }}
          </button>
        </dd>
        <dt class="text-muted-foreground">상태</dt>
        <dd>{{ statusText }}</dd>
        <dt class="text-muted-foreground">첫 정답</dt>
        <dd>
          <template v-if="!detail.has_ranking"><span class="text-subtle-foreground">뜻 분석 뒤</span></template>
          <template v-else-if="detail.first_positive_rank === null"><span class="font-semibold text-warning-ink">10위 밖 · 정답 없음</span></template>
          <b v-else class="font-semibold">{{ detail.first_positive_rank }}위</b>
        </dd>
        <dt class="text-muted-foreground">표시</dt>
        <dd class="flex flex-wrap items-center gap-1">
          <Marks v-if="query.marks.length" :marks="query.marks" />
          <span v-else class="text-subtle-foreground">—</span>
        </dd>
        <template v-if="query.answer">
          <dt class="text-muted-foreground">답 근거</dt>
          <dd class="min-w-0 break-words">{{ query.answer }}</dd>
        </template>
      </dl>

      <section data-ranking>
        <div :class="FIELD_LABEL_CLASS">
          {{ detail.has_ranking ? '판정 · 순위' : '판정' }}
          <span class="ml-auto font-normal text-muted-foreground">
            정답 {{ fmt(query.positive_count) }} · 오답 {{ fmt(query.negative_count) }}
          </span>
        </div>
        <ul v-if="detail.ranked.length" class="divide-y overflow-hidden rounded-lg border">
          <li
            v-for="item in detail.ranked"
            :key="item.document.id"
            class="group px-3 py-2"
            :class="item.flag ? (FLAG_WORDS[item.flag].tone === 'bad' ? 'bg-[color-mix(in_oklab,var(--danger)_6%,var(--card))]' : 'bg-[color-mix(in_oklab,var(--warning)_7%,var(--card))]') : ''"
            :data-ranked="item.document.id"
          >
            <div class="flex items-center gap-2 text-ui">
              <span class="w-7 shrink-0 text-right font-mono text-meta text-muted-foreground">{{ item.rank ?? '—' }}</span>
              <GradeChip :grade="item.grade" />
              <span
                v-if="item.flag"
                class="inline-flex items-center gap-1 text-meta font-semibold"
                :class="FLAG_WORDS[item.flag].tone === 'bad' ? 'text-danger-ink' : 'text-warning-ink'"
              >
                <component :is="FLAG_WORDS[item.flag].icon" class="size-3.5" />{{ FLAG_WORDS[item.flag].name }}
              </span>
              <span class="ml-auto flex items-center gap-2 font-mono text-meta text-muted-foreground">
                <span v-if="item.similarity !== null">유사도 <b class="font-semibold text-foreground">{{ similarityText(item.similarity) }}</b></span>
                <span v-if="item.jev_probability !== null">Jev <b class="font-semibold text-foreground">{{ item.jev_probability.toFixed(2) }}</b></span>
              </span>
            </div>
            <button
              type="button"
              class="mt-1 ml-9 line-clamp-2 text-left text-ui text-muted-foreground hover:text-foreground"
              :title="item.document.snippet"
              @click="emit('openDocument', item.document.id)"
            >
              <b v-if="item.document.title" class="font-semibold text-foreground">{{ item.document.title }} · </b>{{ item.document.snippet }}
            </button>
            <div class="mt-1.5 ml-9 flex items-center gap-1">
              <LoaderCircle v-if="busy === item.document.id" class="size-3.5 animate-spin text-muted-foreground" />
              <Button
                v-if="item.grade === null || item.grade < 1"
                type="button"
                variant="outline"
                size="xs"
                :disabled="busy !== null"
                data-action="judge-positive"
                @click="judge(item, 1)"
              >
                <Check />정답으로
              </Button>
              <Button
                v-if="item.grade !== 0"
                type="button"
                variant="outline"
                size="xs"
                :disabled="busy !== null"
                data-action="judge-negative"
                @click="judge(item, 0)"
              >
                <CircleSlash />오답으로
              </Button>
              <Button
                v-if="item.grade !== null"
                type="button"
                variant="quiet"
                size="xs"
                :disabled="busy !== null"
                data-action="judge-remove"
                @click="judge(item, null)"
              >
                <CircleMinus />떼기
              </Button>
            </div>
          </li>
        </ul>
        <span v-else class="text-ui text-subtle-foreground">없음</span>
      </section>

      <section v-if="detail.outside.length">
        <div :class="FIELD_LABEL_CLASS">10위 밖 판정<span class="count-pill">{{ fmt(detail.outside.length) }}</span></div>
        <ul class="divide-y overflow-hidden rounded-lg border">
          <li v-for="item in detail.outside" :key="item.document.id" class="flex items-center gap-2 px-3 py-2 text-ui">
            <span class="w-10 shrink-0 text-right font-mono text-meta text-muted-foreground">{{ item.rank ? `${item.rank}위` : '—' }}</span>
            <GradeChip :grade="item.grade" />
            <button
              type="button"
              class="min-w-0 flex-1 truncate text-left text-muted-foreground hover:text-foreground"
              @click="emit('openDocument', item.document.id)"
            >
              {{ item.document.title || item.document.snippet }}
            </button>
            <Button type="button" variant="quiet" size="xs" :disabled="busy !== null" @click="judge(item, null)">
              <CircleMinus />떼기
            </Button>
          </li>
        </ul>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">고치기</div>
        <div class="flex flex-wrap gap-1.5">
          <Button type="button" variant="outline" size="sm" :disabled="busy !== null || query.is_trashed" data-action="edit-query" @click="startEdit">
            <Pencil />글 고치기
          </Button>
          <Button type="button" variant="outline" size="sm" :disabled="busy !== null || query.is_trashed" data-action="query-exclude" @click="toggleExcluded">
            <template v-if="query.exclude_reason"><Eye />다시 넣기</template>
            <template v-else><EyeOff />학습에서 빼기</template>
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            :class="query.is_trashed ? '' : 'hover:text-danger-ink'"
            :disabled="busy !== null"
            data-action="query-trash"
            @click="toggleTrash"
          >
            <LoaderCircle v-if="busy === 'trash'" class="animate-spin" />
            <template v-else-if="query.is_trashed"><ArchiveRestore />되살리기</template>
            <template v-else><Trash2 />휴지통으로</template>
          </Button>
        </div>
        <div v-if="actionError" class="mt-2 flex items-start gap-1.5 text-ui" role="alert">
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-danger" />
          <span class="font-semibold text-danger-ink">실패</span>
          <span class="text-muted-foreground">{{ actionError }}</span>
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
            <span v-if="query.import_id === null" class="text-subtle-foreground">—</span>
            <span v-else class="flex min-w-0 items-center gap-1.5">
              <span v-if="source" class="truncate" :title="source.source_name">{{ source.source_name }}</span>
              <Skeleton v-else class="h-3.5 w-32" />
              <span class="shrink-0 font-mono text-meta text-subtle-foreground">#{{ query.import_id }}</span>
            </span>
          </dd>
          <dt class="text-muted-foreground">추가</dt>
          <dd>{{ dateTimeText(query.created_at) }}</dd>
          <dt class="text-muted-foreground">수정</dt>
          <dd>{{ dateTimeText(query.updated_at) }}</dd>
        </dl>
      </section>
    </div>

    <div class="flex h-11 shrink-0 items-center gap-3 overflow-hidden border-t bg-muted/30 px-4 text-ui whitespace-nowrap text-muted-foreground">
      <span class="inline-flex items-center gap-1"><span class="kbd">J</span><span class="kbd">K</span>이동</span>
      <span class="inline-flex items-center gap-1"><span class="kbd">Esc</span>닫기</span>
    </div>
  </aside>
</template>
