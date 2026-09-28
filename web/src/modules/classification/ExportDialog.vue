<!--
  분류 내보내기 창. 상단 바 [내보내기]로 연다(검색 내보내기 창과 같은 틀).
    파일 모양    학습 · jsonl(text · label · label_text · source, SetFit · 허깅페이스 datasets) · 학습 · 표(csv) ·
                 라벨 목록(json: id2label · label2id, transformers 설정). 여럿 고를 수 있고, 라벨 번호는 모든 파일이 같다.
                 나누지 않는다: 학습에 쓰는 문장(학습 제외 · 휴지통 · 라벨 없음 뺌)을 모두 넣는다.
    넣을 출처    원본 · 새 문장(도우미가 만든 것, 파일에는 synthetic) (문장 수)
    출처 칸      학습 표를 고르면 보인다. 기본 켬 · source 칸. jsonl에는 늘 들어간다
    넣지 않음    학습 제외 n · 라벨 없음 n · 휴지통
    미리 보기    고른 대로 만든 첫 줄 · 파일마다 수 · 남은 심각 · 주의(막지 않는다, 그대로 넣음)
  [파일 만들기] → 고른 모양마다 작업("classification", "export") 하나. 만드는 동안 이 창에서 파일마다 대기 · 단계를 보고
  (system/exports/ExportFiles), 끝나면 [내려받기](7일 보관).
-->
<script setup lang="ts">
import { Check, Download, FileDown, LoaderCircle, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { GRADES } from '@/system/diagnosis/grades'
import ExportFiles from '@/system/exports/ExportFiles.vue'
import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import type { ExportRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'

import { createExport, previewExport } from '@/modules/classification/api'
import type { DatasetRead, ExportCreate, ExportFormat, ExportPreviewRead, RecordSource } from '@/modules/classification/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

const open = defineModel<boolean>('open', { required: true })

const FORMATS: { key: ExportFormat; name: string; columns: string[]; note: string }[] = [
  { key: 'train_jsonl', name: '학습 · jsonl', columns: ['text', 'label', 'label_text', 'source'], note: 'SetFit · datasets' },
  { key: 'train_table', name: '학습 · 표', columns: ['text', 'label', 'label_text'], note: 'csv · pandas' },
  { key: 'labels', name: '라벨 목록', columns: ['id2label', 'label2id'], note: 'json · transformers' },
]

const SOURCES: { key: RecordSource; name: string }[] = [
  { key: 'original', name: '원본' },
  { key: 'synthetic', name: '새 문장' },
]

// 미리 보기를 다시 묻기까지 기다리는 시간(밀리초). 여러 칸을 잇달아 누르는 동안 여러 번 묻지 않게.
const PREVIEW_DELAY_MS = 300

const choice = reactive<ExportCreate>({
  formats: ['train_jsonl', 'labels'],
  sources: SOURCES.map((source) => source.key),
  source_column: true,
})
const preview = ref<ExportPreviewRead | null>(null)
const previewError = ref<string | null>(null)
const creating = ref(false)
const createError = ref<string | null>(null)
// 만든(만드는 중인) 내보내기들. 비어 있으면 고르는 화면
const made = ref<ExportRead[]>([])
let previewTimer: ReturnType<typeof setTimeout> | undefined

const chosenRecords = computed(() =>
  choice.sources.reduce((sum, source) => sum + (preview.value?.source_counts[source] ?? 0), 0),
)
const canCreate = computed(
  () => choice.formats.length > 0 && choice.sources.length > 0 && !creating.value && chosenRecords.value > 0,
)

watch(open, (isOpen) => {
  if (!isOpen) return
  made.value = []
  createError.value = null
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

function sourceCount(key: RecordSource): number {
  return preview.value?.source_counts[key] ?? 0
}

const ROW = 'grid grid-cols-[104px_minmax(0,1fr)] items-start gap-x-4 gap-y-1 border-b px-[18px] py-3'
const LABEL = 'pt-1 text-ui font-semibold text-muted-foreground'
const COLUMN_CHIP = 'rounded-[5px] bg-muted px-1.5 font-mono text-[11px] text-muted-foreground'
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent
      class="max-h-[calc(100vh-48px)] w-[min(760px,calc(100vw-32px))] max-w-none gap-0 overflow-auto bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="classification-export"
    >
      <DialogHeader class="flex-row items-center gap-2 border-b px-[18px] py-3.5 pr-12 text-left">
        <Download class="size-4 text-muted-foreground" />
        <DialogTitle class="text-title font-semibold">내보내기</DialogTitle>
        <span class="truncate text-ui text-muted-foreground">{{ dataset.name }}</span>
        <DialogDescription class="sr-only">학습 파일 · 라벨 목록 만들기</DialogDescription>
      </DialogHeader>

      <template v-if="!made.length">
        <div :class="ROW">
          <span :class="LABEL">파일 모양</span>
          <div class="grid gap-2 sm:grid-cols-3">
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
                <span v-for="column in format.columns" :key="column" :class="COLUMN_CHIP">{{ column }}</span>
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

        <div v-if="choice.formats.includes('train_table')" :class="ROW">
          <span :class="LABEL">출처 칸</span>
          <label class="flex cursor-pointer items-center gap-2 pt-1 text-ui">
            <input v-model="choice.source_column" type="checkbox" class="size-4 accent-[var(--primary)]" data-field="source-column" />
            학습 표에 넣기
            <span :class="COLUMN_CHIP">source</span>
            <span class="text-muted-foreground">· original · synthetic</span>
          </label>
        </div>

        <div :class="ROW">
          <span :class="LABEL">넣지 않음</span>
          <span class="pt-1 text-ui text-muted-foreground" data-export-left-out>
            학습 제외 <b class="font-semibold text-foreground">{{ fmt(preview?.excluded ?? 0) }}</b>
            · 라벨 없음 <b class="font-semibold text-foreground">{{ fmt(preview?.unlabeled ?? 0) }}</b>
            · 휴지통
          </span>
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
                <template v-if="file.format === 'labels'">라벨 {{ fmt(file.labels) }}</template>
                <template v-else>문장 {{ fmt(file.records) }} · 라벨 {{ fmt(file.labels) }}</template>
              </span>
            </div>
            <span v-if="!preview" class="pt-1 text-ui text-subtle-foreground">—</span>
          </div>
        </div>

        <div v-if="preview?.checks.length" :class="ROW" data-export-checks>
          <span :class="LABEL">남은 문제</span>
          <span class="flex flex-wrap items-center gap-1.5 pt-0.5 text-ui">
            <span
              v-for="check in preview.checks"
              :key="check.key"
              class="inline-flex h-6 items-center gap-1 rounded-md px-2 font-semibold shadow-[inset_0_0_0_1px_var(--border-strong)]"
              :class="GRADES[check.grade].textClass"
              :data-check="check.key"
            >
              <component :is="GRADES[check.grade].icon" class="size-3.5" />{{ check.name }} {{ check.value }}
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
