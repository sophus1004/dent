<!--
  문서 패널 (데이터 탭 오른쪽): 문서 표 · 지도 · 질의 패널에서 문서를 누르면 열린다.
    머리     문서 #번호 · 이전(K) · 다음(J) · 닫기(Esc)
    본문     제목 · 본문(최대 토큰에서 자르는 곳 표시, 뒤는 흐리게) · [본문 고치기]
    사실     청크(원문의 몇째) · 머리말 · 묶음 · 질의 만들기 · 쓰임 · 상태 · 표시
    쓰는 질의       이 문서를 정답 · 오답으로 쓰는 질의 (누르면 그 질의를 연다)
    가까운 질의     판정 없이 기준 검색 상위에 이 문서가 뜨는 질의 · [정답으로]
    만든 질의       이 문서로 만든 합성 질의
    같은 원문 청크  나누기로 생긴 형제 청크
    고치기   [나누기](긴 문서 · 확인 한 번, 나눈 원문이면 [나누기 되돌리기]) · [질의 안 만듦 | 만들기에 넣기]
             · [휴지통으로 | 되살리기]
  GET /retrieval/documents/{id}로 스스로 읽는다. 바꾸면 changed를 알린다.
-->
<script setup lang="ts">
import {
  ArchiveRestore,
  Check,
  ChevronDown,
  ChevronUp,
  FileQuestion,
  ListFilter,
  ListPlus,
  LoaderCircle,
  Pencil,
  Scissors,
  Trash2,
  TriangleAlert,
  Undo2,
  X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { dateTimeText, fmt } from '@/system/format'
import { ApiError, errorMessage, isAbortError } from '@/system/http'
import { pushToast } from '@/system/toasts'
import { Button } from '@/system/ui/button'
import { Skeleton } from '@/system/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

import { getDocument, setJudgment, splitDocument, unsplitDocument, updateDocument } from '@/modules/retrieval/api'
import Marks from '@/modules/retrieval/data/Marks.vue'
import GradeChip from '@/modules/retrieval/GradeChip.vue'
import type { DocumentDetailRead, JudgedQueryRead } from '@/modules/retrieval/types'

const props = defineProps<{
  documentId: number
  canPrev: boolean
  canNext: boolean
  // 문서 최대 토큰 (설정)
  maxTokens: number
}>()

const emit = defineEmits<{
  close: []
  step: [delta: number]
  changed: [documentId: number]
  openQuery: [queryId: number]
  openDocument: [documentId: number]
}>()

// 토큰 어림. 백엔드(text_rules.py)와 같다.
const HANGUL_CHARS_PER_TOKEN = 1.4
const OTHER_CHARS_PER_TOKEN = 3.6

const FIELD_LABEL_CLASS = 'mb-1.5 flex items-center gap-1.5 text-ui font-[550]'

const detail = ref<DocumentDetailRead | null>(null)
const missing = ref(false)
const busy = ref<string | number | null>(null)
const actionError = ref<string | null>(null)
const editing = ref(false)
const draft = ref('')
const confirmSplit = ref(false)
let controller: AbortController | null = null

const document = computed(() => detail.value?.document ?? null)
const isLong = computed(() => (document.value?.token_count ?? 0) > props.maxTokens)
const expectedPieces = computed(() => Math.ceil((document.value?.token_count ?? 0) / props.maxTokens))

// 최대 토큰에서 자르는 자리 (글자 번호). 넘지 않으면 null
const cutAt = computed(() => {
  const text = document.value?.text ?? ''
  if (!isLong.value) return null
  let tokens = 0
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]
    if (char >= '가' && char <= '힣') tokens += 1 / HANGUL_CHARS_PER_TOKEN
    else if (!/\s/.test(char)) tokens += 1 / OTHER_CHARS_PER_TOKEN
    if (tokens > props.maxTokens) return index
  }
  return null
})

watch(() => props.documentId, load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  detail.value = null
  missing.value = false
  editing.value = false
  confirmSplit.value = false
  actionError.value = null
  try {
    detail.value = await getDocument(props.documentId, current.signal)
  } catch (error) {
    if (isAbortError(error)) return
    missing.value = error instanceof ApiError && error.status === 404
    if (!missing.value) actionError.value = errorMessage(error)
  }
}

async function reload(): Promise<void> {
  try {
    detail.value = await getDocument(props.documentId)
  } catch {
    // 못 읽으면 앞의 값을 그대로 보인다.
  }
}

async function run(key: string | number, action: () => Promise<unknown>): Promise<boolean> {
  busy.value = key
  actionError.value = null
  try {
    await action()
    await reload()
    emit('changed', props.documentId)
    return true
  } catch (error) {
    actionError.value = errorMessage(error)
    return false
  } finally {
    busy.value = null
  }
}

function startEdit(): void {
  draft.value = document.value?.text ?? ''
  editing.value = true
}

async function saveText(): Promise<void> {
  const current = document.value
  const text = draft.value.trim()
  if (!current || !text || text === current.text) {
    editing.value = false
    return
  }
  const ok = await run('text', () => updateDocument(current.id, { text, row_version: current.row_version }))
  if (ok) editing.value = false
}

function toggleGeneration(): void {
  const current = document.value
  if (!current) return
  void run('skip', () => updateDocument(current.id, { skip_generation: !current.skip_generation, row_version: current.row_version }))
}

function toggleTrash(): void {
  const current = document.value
  if (!current) return
  void run('trash', () => updateDocument(current.id, { trashed: !current.is_trashed, row_version: current.row_version }))
}

async function split(): Promise<void> {
  const current = document.value
  if (!current) return
  busy.value = 'split'
  actionError.value = null
  try {
    const result = await splitDocument(current.id)
    confirmSplit.value = false
    emit('changed', current.id)
    pushToast({ tone: 'done', title: '나누기', detail: `청크 ${fmt(result.changed)}`, actions: [] })
    await reload()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busy.value = null
  }
}

async function unsplit(): Promise<void> {
  const current = document.value
  if (!current) return
  busy.value = 'unsplit'
  actionError.value = null
  try {
    const result = await unsplitDocument(current.id)
    emit('changed', current.id)
    pushToast({ tone: 'done', title: '나누기 되돌림', detail: `청크 ${fmt(result.changed)} 지움`, actions: [] })
    await reload()
  } catch (error) {
    actionError.value = errorMessage(error)
  } finally {
    busy.value = null
  }
}

function markPositive(item: JudgedQueryRead): void {
  void run(item.query_id, () => setJudgment(item.query_id, props.documentId, 1))
}
</script>

<template>
  <aside
    class="flex h-[calc(100vh-52px)] w-[440px] flex-col border-l bg-card shadow-(--shadow-panel) duration-200 animate-in fade-in-0 slide-in-from-right-4 max-xl:fixed max-xl:top-[52px] max-xl:right-0 max-xl:bottom-0 max-xl:z-30 max-xl:h-auto max-xl:w-[380px]"
    aria-label="문서 상세"
    data-panel="document"
  >
    <div class="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <span class="text-body font-semibold">문서</span>
      <span class="font-mono text-ui text-subtle-foreground">#{{ documentId }}</span>
      <div class="ml-auto flex items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canPrev" aria-label="이전 문서" @click="emit('step', -1)"><ChevronUp /></Button>
          </TooltipTrigger>
          <TooltipContent>이전 · K</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="quiet" size="icon-sm" :disabled="!canNext" aria-label="다음 문서" @click="emit('step', 1)"><ChevronDown /></Button>
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
      <span class="text-body font-semibold">없는 문서</span>
      <span class="font-mono text-meta text-muted-foreground">#{{ documentId }}</span>
    </div>

    <div v-else-if="!detail || !document" class="flex-1 space-y-5 p-4" aria-busy="true">
      <Skeleton class="h-40 w-full rounded-lg" />
      <div class="space-y-2.5">
        <Skeleton class="h-4 w-40" />
        <Skeleton class="h-4 w-28" />
      </div>
    </div>

    <div v-else class="flex-1 space-y-5 overflow-y-auto p-4">
      <section>
        <div :class="FIELD_LABEL_CLASS">
          <span class="min-w-0 truncate">{{ document.title || '본문' }}</span>
          <span class="ml-auto shrink-0 font-normal" :class="isLong ? 'font-semibold text-warning-ink' : 'text-muted-foreground'">
            {{ fmt(document.token_count) }}토큰
          </span>
        </div>
        <template v-if="editing">
          <textarea
            v-model="draft"
            rows="10"
            class="w-full rounded-lg border border-input bg-card px-3 py-2 text-ui leading-[1.7] outline-none focus:border-ring focus:ring-3 focus:ring-ring/22"
            aria-label="문서 본문"
            data-field="document-text"
            @keydown.esc.stop="editing = false"
          />
          <div class="mt-1.5 flex justify-end gap-1.5">
            <Button type="button" variant="outline" size="sm" @click="editing = false">취소</Button>
            <Button type="button" size="sm" :disabled="busy === 'text'" data-action="save-document-text" @click="saveText">
              <LoaderCircle v-if="busy === 'text'" class="animate-spin" />저장
            </Button>
          </div>
        </template>
        <p v-else class="max-h-[340px] overflow-y-auto rounded-lg border px-3 py-2.5 text-ui leading-[1.7] whitespace-pre-wrap">
          <template v-if="cutAt === null">{{ document.text }}</template>
          <template v-else>{{ document.text.slice(0, cutAt) }}<span
              class="mx-1 inline-flex items-center gap-1 text-[11px] font-[650] text-warning-ink"
            ><Scissors class="size-3" />{{ maxTokens }}</span><span class="text-subtle-foreground">{{ document.text.slice(cutAt) }}</span></template>
        </p>
      </section>

      <!-- 학습 글이 본문과 다를 때만 (머리말 · 떼기로 고른 반복 구간). 임베딩 · 내보내기가 이 글을 쓴다. -->
      <section v-if="document.training_text !== document.text" data-training-text>
        <div :class="FIELD_LABEL_CLASS">학습 글</div>
        <p class="max-h-[220px] overflow-y-auto rounded-lg border bg-muted/40 px-3 py-2.5 text-ui leading-[1.7] whitespace-pre-wrap">{{ document.training_text }}</p>
      </section>

      <dl class="grid grid-cols-[84px_minmax(0,1fr)] items-center gap-x-2.5 gap-y-2 text-ui">
        <dt class="text-muted-foreground">청크</dt>
        <dd>
          <template v-if="document.source_document_id">
            <button
              type="button"
              class="font-mono text-meta text-accent-foreground hover:underline"
              data-action="open-original"
              @click="emit('openDocument', document.source_document_id)"
            >
              원문 #{{ document.source_document_id }}
            </button>
            · {{ (document.chunk_index ?? 0) + 1 }}째
          </template>
          <span v-else class="text-subtle-foreground">원문</span>
        </dd>
        <template v-if="document.header || document.section">
          <dt class="text-muted-foreground">머리말</dt>
          <dd class="min-w-0 truncate" :title="[document.header, document.section].filter(Boolean).join(' › ')">
            {{ [document.header, document.section].filter(Boolean).join(' › ') }}
          </dd>
        </template>
        <template v-if="document.group_key">
          <dt class="text-muted-foreground">묶음</dt>
          <dd class="min-w-0 truncate font-mono text-meta">{{ document.group_key }}</dd>
        </template>
        <dt class="text-muted-foreground">질의 만들기</dt>
        <dd>{{ document.skip_generation ? '안 만듦' : '만듦' }}</dd>
        <dt class="text-muted-foreground">쓰임</dt>
        <dd>
          정답으로 <b class="font-semibold">{{ fmt(document.positive_count) }}</b>
          · 오답으로 <b class="font-semibold">{{ fmt(document.negative_count) }}</b>
          · 만든 질의 <b class="font-semibold">{{ fmt(document.synthetic_count) }}</b>
        </dd>
        <dt class="text-muted-foreground">상태</dt>
        <dd>{{ document.is_trashed ? '휴지통' : document.is_replaced ? '나눔 · 청크로 대신' : '코퍼스' }}</dd>
        <dt class="text-muted-foreground">표시</dt>
        <dd class="flex flex-wrap items-center gap-1">
          <Marks v-if="document.marks.length" :marks="document.marks" />
          <span v-else class="text-subtle-foreground">—</span>
        </dd>
        <template v-if="document.doc_key">
          <dt class="text-muted-foreground">원래 번호</dt>
          <dd class="font-mono text-meta">{{ document.doc_key }}</dd>
        </template>
      </dl>

      <section>
        <div :class="FIELD_LABEL_CLASS">쓰는 질의<span v-if="detail.uses.length" class="count-pill">{{ fmt(detail.uses.length) }}</span></div>
        <ul v-if="detail.uses.length" class="divide-y overflow-hidden rounded-lg border">
          <li v-for="item in detail.uses" :key="item.query_id">
            <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-ui hover:bg-muted/60" @click="emit('openQuery', item.query_id)">
              <GradeChip :grade="item.grade" />
              <span class="min-w-0 flex-1 truncate">{{ item.text }}</span>
              <span v-if="item.rank" class="shrink-0 font-mono text-meta text-muted-foreground">{{ item.rank }}위</span>
            </button>
          </li>
        </ul>
        <span v-else class="text-ui text-subtle-foreground">없음</span>
      </section>

      <section v-if="detail.nearby.length">
        <div :class="FIELD_LABEL_CLASS">가까운 질의 · 판정 없음<span class="count-pill">{{ fmt(detail.nearby.length) }}</span></div>
        <ul class="divide-y overflow-hidden rounded-lg border">
          <li v-for="item in detail.nearby" :key="item.query_id" class="flex items-center gap-2 px-3 py-2 text-ui">
            <span class="w-8 shrink-0 text-right font-mono text-meta text-muted-foreground">{{ item.rank }}위</span>
            <button type="button" class="min-w-0 flex-1 truncate text-left hover:underline" @click="emit('openQuery', item.query_id)">
              {{ item.text }}
            </button>
            <span v-if="item.jev_probability !== null" class="shrink-0 font-mono text-meta text-muted-foreground">
              Jev <b class="font-semibold text-foreground">{{ item.jev_probability.toFixed(2) }}</b>
            </span>
            <Button type="button" variant="outline" size="xs" :disabled="busy !== null" @click="markPositive(item)">
              <LoaderCircle v-if="busy === item.query_id" class="animate-spin" /><Check v-else />정답으로
            </Button>
          </li>
        </ul>
      </section>

      <section v-if="detail.synthetic.length">
        <div :class="FIELD_LABEL_CLASS">이 문서로 만든 질의<span class="count-pill">{{ fmt(detail.synthetic.length) }}</span></div>
        <ul class="divide-y overflow-hidden rounded-lg border">
          <li v-for="item in detail.synthetic" :key="item.query_id">
            <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-ui hover:bg-muted/60" @click="emit('openQuery', item.query_id)">
              <span class="min-w-0 flex-1 truncate">{{ item.text }}</span>
            </button>
          </li>
        </ul>
      </section>

      <section v-if="detail.siblings.length">
        <div :class="FIELD_LABEL_CLASS">같은 원문의 청크<span class="count-pill">{{ fmt(detail.siblings.length) }}</span></div>
        <ul class="divide-y overflow-hidden rounded-lg border">
          <li v-for="item in detail.siblings" :key="item.id">
            <button type="button" class="flex w-full items-center gap-2 px-3 py-2 text-left text-ui hover:bg-muted/60" @click="emit('openDocument', item.id)">
              <span class="min-w-0 flex-1 truncate text-muted-foreground">{{ item.snippet }}</span>
              <span class="shrink-0 font-mono text-meta text-subtle-foreground">{{ fmt(item.token_count) }}토큰</span>
            </button>
          </li>
        </ul>
      </section>

      <section>
        <div :class="FIELD_LABEL_CLASS">고치기</div>
        <div v-if="confirmSplit" class="mb-2 rounded-lg border border-primary/40 bg-accent/50 px-3 py-2.5 text-ui" data-confirm="split">
          <div class="font-semibold">나누기 · 청크 약 {{ fmt(expectedPieces) }}</div>
          <div class="mt-0.5 text-muted-foreground">
            문단 → 문장 경계 · 최대 {{ maxTokens }}토큰 · 판정은 답이 든 청크로 · 되돌리기는 원문에서
          </div>
          <div class="mt-2 flex justify-end gap-1.5">
            <Button type="button" variant="outline" size="sm" @click="confirmSplit = false">취소</Button>
            <Button type="button" size="sm" :disabled="busy === 'split'" data-action="split-document" @click="split">
              <LoaderCircle v-if="busy === 'split'" class="animate-spin" /><Scissors v-else />나누기
            </Button>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-1.5 [&>button]:justify-start">
          <Button type="button" variant="outline" size="sm" :disabled="busy !== null || document.is_trashed" data-action="edit-document" @click="startEdit">
            <Pencil />본문 고치기
          </Button>
          <Button
            v-if="document.is_replaced"
            type="button"
            variant="outline"
            size="sm"
            :disabled="busy !== null"
            data-action="unsplit-document"
            @click="unsplit"
          >
            <LoaderCircle v-if="busy === 'unsplit'" class="animate-spin" /><Undo2 v-else />나누기 되돌리기
          </Button>
          <Button
            v-else
            type="button"
            variant="outline"
            size="sm"
            :disabled="busy !== null || !isLong || document.is_trashed"
            data-action="open-split"
            @click="confirmSplit = true"
          >
            <Scissors />나누기
          </Button>
          <Button type="button" variant="outline" size="sm" :disabled="busy !== null || document.is_trashed" data-action="toggle-generation" @click="toggleGeneration">
            <template v-if="document.skip_generation"><ListPlus />만들기에 넣기</template>
            <template v-else><ListFilter />질의 안 만듦</template>
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            :class="document.is_trashed ? '' : 'hover:text-danger-ink'"
            :disabled="busy !== null"
            data-action="document-trash"
            @click="toggleTrash"
          >
            <LoaderCircle v-if="busy === 'trash'" class="animate-spin" />
            <template v-else-if="document.is_trashed"><ArchiveRestore />되살리기</template>
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
        <div :class="FIELD_LABEL_CLASS">가져온 곳</div>
        <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-ui">
          <dt class="text-muted-foreground">가져오기</dt>
          <dd class="font-mono text-meta">{{ document.import_id ? `#${document.import_id}` : '—' }}</dd>
          <dt class="text-muted-foreground">추가</dt>
          <dd>{{ dateTimeText(document.created_at) }}</dd>
          <dt class="text-muted-foreground">수정</dt>
          <dd>{{ dateTimeText(document.updated_at) }}</dd>
        </dl>
      </section>
    </div>

    <div class="flex h-11 shrink-0 items-center gap-3 overflow-hidden border-t bg-muted/30 px-4 text-ui whitespace-nowrap text-muted-foreground">
      <span class="inline-flex items-center gap-1"><span class="kbd">J</span><span class="kbd">K</span>이동</span>
      <span class="inline-flex items-center gap-1"><span class="kbd">Esc</span>닫기</span>
    </div>
  </aside>
</template>
