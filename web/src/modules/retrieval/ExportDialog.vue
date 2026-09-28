<!--
  내보내기 창 (R-09, 흐름의 끝). 상단 바 [내보내기]로 연다. 심각이 남았으면 서버가 409와 까닭을 준다.
    파일 모양    학습 · jsonl(bge-m3 FlagEmbedding: query · pos · neg, 오답 풀 전체) · 학습 · 표(sentence-transformers:
                 anchor · positive · negative_1…) · 평가 · BEIR(zip: corpus · queries · qrels) · 색인 · 코퍼스(zip: 학습 글 ·
                 rules.json, 운영 색인이 같은 글을 쓰게). 여럿 고를 수 있다.
                 나누지 않는다: 학습에 쓰는 질의(학습 제외 · 휴지통 뺌)를 모든 파일에 넣는다(BEIR은 qrels 하나).
    넣을 출처    원본 · 찾기 · 합성 · 도우미 · 사람 (판정 수)
    오답         표 칸 수 n · jsonl은 풀 전체 · 모자란 질의 · 평균
    교사 점수    기본 켬 · Jev 확률을 로짓으로(pos_scores · neg_scores) · Jev에 물을 수
    출처 칸      학습 표를 고르면 보인다. 기본 켬 · source 칸(original · synthetic · human …). jsonl · BEIR에는 늘 들어간다
    미리 보기    고른 대로 만든 첫 줄 · 파일마다 수 · 잠김(남은 심각, [파일 만들기]가 꺼진다. 코퍼스만이면 켜짐) · 남은 주의
  [파일 만들기] → 고른 모양마다 작업("retrieval", "export") 하나. 교사 점수를 채울 학습 파일은 서버가 빠른 파일 뒤에 넣는다.
  만드는 동안 이 창에서 파일마다 대기 · 단계 n / m · 남은 시간을 보고(system/exports/ExportFiles), 끝나면 [내려받기](7일 보관).
  교사 점수 줄의 어림 시간은 가장 최근에 잰 Jev 속도로 센 것이다(teacher_seconds).
-->
<script setup lang="ts">
import { Check, Download, FileDown, LoaderCircle, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import ExportFiles from '@/system/exports/ExportFiles.vue'
import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import { remainingText } from '@/system/jobs'
import type { ExportRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Input } from '@/system/ui/input'

import { createExport, previewExport } from '@/modules/retrieval/api'
import { checkWords } from '@/modules/retrieval/diagnosis/checks'
import type { DatasetRead, ExportCreate, ExportFormat, ExportPreviewRead, JudgmentSource } from '@/modules/retrieval/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

const open = defineModel<boolean>('open', { required: true })

const FORMATS: { key: ExportFormat; name: string; columns: string[]; note: string }[] = [
  { key: 'train_jsonl', name: '학습 · jsonl', columns: ['query', 'pos', 'neg'], note: 'bge-m3 · FlagEmbedding' },
  { key: 'train_table', name: '학습 · 표', columns: ['anchor', 'positive', 'negative'], note: 'sentence-transformers' },
  { key: 'beir', name: '평가 · BEIR', columns: ['corpus', 'queries', 'qrels'], note: 'zip · 질의 전부' },
  { key: 'corpus', name: '색인 · 코퍼스', columns: ['text', 'rules'], note: 'zip · 운영 색인' },
]

const SOURCES: { key: JudgmentSource; name: string }[] = [
  { key: 'original', name: '원본' },
  { key: 'mined', name: '찾기' },
  { key: 'synthetic', name: '합성' },
  { key: 'helper', name: '도우미' },
  { key: 'human', name: '사람' },
]

// 미리 보기를 다시 묻기까지 기다리는 시간(밀리초). 숫자를 치는 동안 여러 번 묻지 않게.
const PREVIEW_DELAY_MS = 300

const choice = reactive<ExportCreate>({
  formats: ['train_jsonl', 'beir'],
  sources: SOURCES.map((source) => source.key),
  negatives: 7,
  teacher_scores: true,
  source_column: true,
})
const preview = ref<ExportPreviewRead | null>(null)
const previewError = ref<string | null>(null)
const creating = ref(false)
const createError = ref<string | null>(null)
// 만든(만드는 중인) 내보내기들. 비어 있으면 고르는 화면
const made = ref<ExportRead[]>([])
let previewTimer: ReturnType<typeof setTimeout> | undefined

const blocked = computed(() => preview.value?.blocked ?? [])
// 코퍼스만 고르면 남은 심각과 상관없이 만든다(학습 · 평가 파일이 아니다).
const isCorpusOnly = computed(() => choice.formats.length > 0 && choice.formats.every((format) => format === 'corpus'))
const canCreate = computed(
  () =>
    choice.formats.length > 0 &&
    choice.sources.length > 0 &&
    !creating.value &&
    (blocked.value.length === 0 || isCorpusOnly.value),
)
const warnings = computed(() => Object.entries(preview.value?.warnings ?? {}).filter(([, count]) => count > 0))

watch(open, (isOpen) => {
  if (!isOpen) return
  made.value = []
  createError.value = null
  choice.negatives = props.dataset.settings?.negatives ?? 7
  schedulePreview()
})
watch(choice, schedulePreview, { deep: true })
onBeforeUnmount(() => clearTimeout(previewTimer))

function toggle<T>(list: T[], value: T): void {
  const index = list.indexOf(value)
  if (index >= 0) list.splice(index, 1)
  else list.push(value)
}

function schedulePreview(): void {
  if (!open.value) return
  clearTimeout(previewTimer)
  previewTimer = setTimeout(loadPreview, PREVIEW_DELAY_MS)
}

async function loadPreview(): Promise<void> {
  if (!choice.formats.length || !choice.sources.length) return
  try {
    preview.value = await previewExport(props.dataset.id, { ...choice })
    previewError.value = null
  } catch (error) {
    previewError.value = errorMessage(error)
  }
}

async function create(): Promise<void> {
  creating.value = true
  createError.value = null
  try {
    made.value = await createExport(props.dataset.id, { ...choice })
  } catch (error) {
    createError.value = errorMessage(error)
  } finally {
    creating.value = false
  }
}

function sourceCount(key: JudgmentSource): number {
  return preview.value?.source_counts[key] ?? 0
}

const ROW = 'grid grid-cols-[104px_minmax(0,1fr)] items-start gap-x-4 gap-y-1 border-b px-[18px] py-3'
const LABEL = 'pt-1 text-ui font-semibold text-muted-foreground'
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent
      class="max-h-[calc(100vh-48px)] w-[min(760px,calc(100vw-32px))] max-w-none gap-0 overflow-auto bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="retrieval-export"
    >
      <DialogHeader class="flex-row items-center gap-2 border-b px-[18px] py-3.5 pr-12 text-left">
        <Download class="size-4 text-muted-foreground" />
        <DialogTitle class="text-title font-semibold">내보내기</DialogTitle>
        <span class="truncate text-ui text-muted-foreground">{{ dataset.name }}</span>
        <DialogDescription class="sr-only">학습 · 평가 파일 만들기</DialogDescription>
      </DialogHeader>

      <template v-if="!made.length">
        <div :class="ROW">
          <span :class="LABEL">파일 모양</span>
          <div class="grid gap-2 sm:grid-cols-4">
            <button
              v-for="format in FORMATS"
              :key="format.key"
              type="button"
              class="grid gap-1.5 rounded-[10px] border p-2.5 text-left text-ui transition-shadow"
              :class="choice.formats.includes(format.key) ? 'border-primary shadow-[0_0_0_3px_color-mix(in_oklab,var(--primary)_16%,transparent)]' : 'hover:border-border-strong'"
              :aria-pressed="choice.formats.includes(format.key)"
              :data-format="format.key"
              @click="toggle(choice.formats, format.key)"
            >
              <span class="flex items-center gap-1.5 font-[650]">
                <span
                  class="grid size-[15px] place-items-center rounded-[4px]"
                  :class="choice.formats.includes(format.key) ? 'bg-primary text-primary-foreground' : 'shadow-[inset_0_0_0_1.5px_var(--border-strong)]'"
                >
                  <Check v-if="choice.formats.includes(format.key)" class="size-[11px]" :stroke-width="3" />
                </span>
                {{ format.name }}
              </span>
              <span class="flex flex-wrap gap-1">
                <span v-for="column in format.columns" :key="column" class="rounded-[5px] bg-muted px-1.5 font-mono text-[11px] text-muted-foreground">{{ column }}</span>
              </span>
              <span class="text-meta text-muted-foreground">{{ format.note }}</span>
            </button>
          </div>
        </div>

        <div :class="ROW">
          <span :class="LABEL">넣을 출처</span>
          <span class="flex flex-wrap items-center gap-1.5">
            <button
              v-for="source in SOURCES"
              :key="source.key"
              type="button"
              class="inline-flex h-7 items-center gap-1.5 rounded-lg border border-border-strong px-2 text-ui font-[550]"
              :aria-pressed="choice.sources.includes(source.key)"
              :data-source="source.key"
              @click="toggle(choice.sources, source.key)"
            >
              <span
                class="grid size-3.5 place-items-center rounded-[4px]"
                :class="choice.sources.includes(source.key) ? 'bg-primary text-primary-foreground' : 'shadow-[inset_0_0_0_1.5px_var(--border-strong)]'"
              >
                <Check v-if="choice.sources.includes(source.key)" class="size-2.5" :stroke-width="3" />
              </span>
              {{ source.name }} {{ fmt(sourceCount(source.key)) }}
            </button>
          </span>
        </div>

        <div :class="ROW">
          <span :class="LABEL">오답</span>
          <span class="flex flex-wrap items-center gap-2 text-ui">
            표 칸
            <Input v-model.number="choice.negatives" type="number" min="1" max="50" class="h-7 w-16" data-field="export-negatives" />
            <span class="text-muted-foreground">· jsonl 풀 전체</span>
            <span v-if="preview" class="text-muted-foreground">
              · 모자란 질의 {{ fmt(preview.lacking) }} · 평균 {{ preview.average_negatives.toFixed(1) }}
            </span>
          </span>
        </div>

        <div :class="ROW">
          <span :class="LABEL">교사 점수</span>
          <label class="flex cursor-pointer items-center gap-2 pt-1 text-ui">
            <input v-model="choice.teacher_scores" type="checkbox" class="size-4 accent-[var(--primary)]" data-field="teacher-scores" />
            Jev 점수 넣기 · 로짓
            <span class="rounded-[5px] bg-muted px-1.5 font-mono text-[11px] text-muted-foreground">pos_scores</span>
            <span class="rounded-[5px] bg-muted px-1.5 font-mono text-[11px] text-muted-foreground">neg_scores</span>
            <span v-if="choice.teacher_scores && preview" class="text-muted-foreground" data-export-teacher>
              · Jev에 물을 판정 {{ fmt(preview.teacher_missing) }}
              <template v-if="preview.teacher_missing && typeof preview.teacher_seconds === 'number'">
                · {{ remainingText(preview.teacher_seconds) }}
              </template>
            </span>
          </label>
        </div>

        <div v-if="choice.formats.includes('train_table')" :class="ROW">
          <span :class="LABEL">출처 칸</span>
          <label class="flex cursor-pointer items-center gap-2 pt-1 text-ui">
            <input v-model="choice.source_column" type="checkbox" class="size-4 accent-[var(--primary)]" data-field="source-column" />
            학습 표에 넣기
            <span class="rounded-[5px] bg-muted px-1.5 font-mono text-[11px] text-muted-foreground">source</span>
            <span class="text-muted-foreground">· 원본 · 합성 · 사람</span>
          </label>
        </div>

        <div :class="ROW">
          <span :class="LABEL">한 줄</span>
          <pre
            class="max-h-40 overflow-auto rounded-lg bg-[color-mix(in_oklab,var(--muted)_70%,var(--card))] px-3 py-2 font-mono text-[11.5px] leading-[1.55] break-all whitespace-pre-wrap"
            data-export-line
          >{{ preview?.line ?? '—' }}</pre>
        </div>

        <div :class="ROW">
          <span :class="LABEL">파일</span>
          <div class="grid gap-2 sm:grid-cols-2">
            <div v-for="file in preview?.files ?? []" :key="file.file_name" class="grid gap-0.5 rounded-[10px] border px-3 py-2">
              <b class="truncate text-ui font-semibold">{{ file.file_name }}</b>
              <span class="text-meta text-muted-foreground">
                <template v-if="file.format === 'corpus'">문서 {{ fmt(file.documents) }} · 규칙</template>
                <template v-else>
                  질의 {{ fmt(file.queries) }} · 정답 {{ fmt(file.positives) }}
                  <template v-if="file.format === 'beir'"> · 문서 {{ fmt(file.documents) }}</template>
                  <template v-else> · 오답 {{ fmt(file.negatives) }}</template>
                </template>
              </span>
            </div>
            <span v-if="!preview" class="pt-1 text-ui text-subtle-foreground">—</span>
          </div>
        </div>

        <div v-if="blocked.length" :class="ROW" data-export-blocked>
          <span :class="LABEL">잠김</span>
          <span class="flex flex-wrap items-center gap-1.5 pt-0.5 text-ui">
            <span
              v-for="key in blocked"
              :key="key"
              class="inline-flex h-6 items-center gap-1 rounded-md px-2 font-semibold text-danger-ink shadow-[inset_0_0_0_1px_var(--border-strong)]"
            >
              <component :is="checkWords(key).icon" class="size-3.5" />{{ checkWords(key).name }}
            </span>
            <span class="text-muted-foreground">심각 · 고친 뒤 내보냄</span>
          </span>
        </div>

        <div v-if="warnings.length" :class="ROW">
          <span :class="LABEL">남은 주의</span>
          <span class="flex flex-wrap items-center gap-1.5 pt-0.5 text-ui">
            <span
              v-for="[key, count] in warnings"
              :key="key"
              class="inline-flex h-6 items-center gap-1 rounded-md px-2 font-semibold text-warning-ink shadow-[inset_0_0_0_1px_var(--border-strong)]"
            >
              <component :is="checkWords(key).icon" class="size-3.5" />{{ checkWords(key).name }} {{ fmt(count) }}
            </span>
            <span class="text-muted-foreground">그대로 넣음</span>
          </span>
        </div>

        <div v-if="previewError || createError" class="flex items-start gap-2 border-b px-[18px] py-3 text-ui" role="alert">
          <TriangleAlert class="mt-0.5 size-4 shrink-0 text-danger" />
          <span class="font-semibold text-danger-ink">실패</span>
          <span class="text-muted-foreground">{{ createError ?? previewError }}</span>
        </div>

        <div class="flex items-center gap-2 px-[18px] py-3">
          <span class="text-meta text-muted-foreground">작업으로 만듦 · 끝나면 알림 · 7일 보관</span>
          <Button type="button" variant="outline" class="ml-auto" @click="open = false">취소</Button>
          <Button type="button" :disabled="!canCreate" data-action="create-export" @click="create">
            <LoaderCircle v-if="creating" class="animate-spin" /><FileDown v-else />파일 만들기
          </Button>
        </div>
      </template>

      <template v-else>
        <ExportFiles :items="made" />
        <div class="flex items-center gap-2 border-t px-[18px] py-3">
          <span class="text-meta text-muted-foreground">7일 보관</span>
          <Button type="button" variant="outline" class="ml-auto" @click="open = false">닫기</Button>
        </div>
      </template>
    </DialogContent>
  </Dialog>
</template>
