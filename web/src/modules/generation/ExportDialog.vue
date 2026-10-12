<!--
  생성 내보내기 창. 상단 바 [내보내기]로 연다(분류 · 검색 내보내기 창과 같은 틀).
    파일 모양    학습 · jsonl(messages · source, TRL SFTTrainer · 허깅페이스 datasets). 첫 판은 이것 하나다.
                 나누지 않는다: 휴지통 밖 대화를 모두 넣는다.
    넣지 않음    휴지통
    미리 보기    고른 대로 만든 첫 줄 · 파일마다 대화 수 · 잠김(남은 심각, [파일 만들기]가 꺼진다) · 남은 주의(그대로 넣음)
  [파일 만들기] → 고른 모양마다 작업("generation", "export") 하나. 만드는 동안 이 창에서 파일마다 대기 · 단계를 보고
  (system/exports/ExportFiles), 끝나면 [내려받기](7일 보관).
-->
<script setup lang="ts">
import { Check, Download, FileDown, LoaderCircle, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import ExportFiles from '@/system/exports/ExportFiles.vue'
import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import type { ExportRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'

import { createExport, previewExport } from '@/modules/generation/api'
import { checkWords } from '@/modules/generation/checks'
import type { DatasetRead, ExportCreate, ExportFormat, ExportPreviewRead } from '@/modules/generation/types'

const props = defineProps<{
  dataset: DatasetRead
}>()

const open = defineModel<boolean>('open', { required: true })

const FORMATS: { key: ExportFormat; name: string; columns: string[]; note: string }[] = [
  { key: 'sft_jsonl', name: '학습 · jsonl', columns: ['messages', 'source'], note: 'TRL SFTTrainer · datasets' },
]

// 미리 보기를 다시 묻기까지 기다리는 시간(밀리초). 여러 칸을 잇달아 누르는 동안 여러 번 묻지 않게.
const PREVIEW_DELAY_MS = 300

const choice = reactive<ExportCreate>({ formats: ['sft_jsonl'] })
const preview = ref<ExportPreviewRead | null>(null)
const previewError = ref<string | null>(null)
const creating = ref(false)
const createError = ref<string | null>(null)
// 만든(만드는 중인) 내보내기들. 비어 있으면 고르는 화면
const made = ref<ExportRead[]>([])
let previewTimer: ReturnType<typeof setTimeout> | undefined

const blocked = computed(() => preview.value?.checks.filter((check) => check.grade === 'bad') ?? [])
const warnings = computed(() => preview.value?.checks.filter((check) => check.grade === 'warn') ?? [])
const conversations = computed(() => preview.value?.files[0]?.conversations ?? 0)
const canCreate = computed(
  () =>
    choice.formats.length > 0 &&
    !creating.value &&
    preview.value !== null &&
    !preview.value.blocked &&
    conversations.value > 0,
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
  if (!choice.formats.length) return
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

const ROW = 'grid grid-cols-[104px_minmax(0,1fr)] items-start gap-x-4 gap-y-1 border-b px-[18px] py-3'
const LABEL = 'pt-1 text-ui font-semibold text-muted-foreground'
const COLUMN_CHIP = 'rounded-[5px] bg-muted px-1.5 font-mono text-[11px] text-muted-foreground'
</script>

<template>
  <Dialog v-model:open="open">
    <DialogContent
      class="max-h-[calc(100vh-48px)] w-[min(760px,calc(100vw-32px))] max-w-none gap-0 overflow-auto bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="generation-export"
    >
      <DialogHeader class="flex-row items-center gap-2 border-b px-[18px] py-3.5 pr-12 text-left">
        <Download class="size-4 text-muted-foreground" />
        <DialogTitle class="text-title font-semibold">내보내기</DialogTitle>
        <span class="truncate text-ui text-muted-foreground">{{ dataset.name }}</span>
        <DialogDescription class="sr-only">SFT 학습 파일 만들기</DialogDescription>
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
          <span :class="LABEL">넣지 않음</span>
          <span class="pt-1 text-ui text-muted-foreground" data-export-left-out>
            휴지통 <b class="font-semibold text-foreground">{{ fmt(dataset.trash_count) }}</b>
          </span>
        </div>

        <div :class="ROW">
          <span :class="LABEL">한 줄</span>
          <pre
            class="max-h-40 overflow-auto rounded-lg bg-[color-mix(in_oklab,var(--muted)_70%,var(--card))] px-3 py-2 font-mono text-[11.5px] leading-[1.55] break-all whitespace-pre-wrap"
            data-export-line
          >{{ preview?.line || '—' }}</pre>
        </div>

        <div :class="ROW">
          <span :class="LABEL">파일</span>
          <div class="grid gap-2 sm:grid-cols-2">
            <div v-for="file in preview?.files ?? []" :key="file.file_name" class="grid gap-0.5 rounded-[10px] border px-3 py-2">
              <b class="truncate text-ui font-semibold">{{ file.file_name }}</b>
              <span class="text-meta text-muted-foreground">대화 {{ fmt(file.conversations) }}</span>
            </div>
            <span v-if="!preview" class="pt-1 text-ui text-subtle-foreground">—</span>
          </div>
        </div>

        <div v-if="blocked.length" :class="ROW" data-export-blocked>
          <span :class="LABEL">잠김</span>
          <span class="flex flex-wrap items-center gap-1.5 pt-0.5 text-ui">
            <span
              v-for="check in blocked"
              :key="check.key"
              class="inline-flex h-6 items-center gap-1 rounded-md px-2 font-semibold text-danger-ink shadow-[inset_0_0_0_1px_var(--border-strong)]"
              :data-check="check.key"
            >
              <component :is="checkWords(check.key).icon" class="size-3.5" />{{ checkWords(check.key).name }} {{ fmt(check.count) }}
            </span>
            <span class="text-muted-foreground">심각 · 고친 뒤 내보냄</span>
          </span>
        </div>

        <div v-if="warnings.length" :class="ROW" data-export-checks>
          <span :class="LABEL">남은 주의</span>
          <span class="flex flex-wrap items-center gap-1.5 pt-0.5 text-ui">
            <span
              v-for="check in warnings"
              :key="check.key"
              class="inline-flex h-6 items-center gap-1 rounded-md px-2 font-semibold text-warning-ink shadow-[inset_0_0_0_1px_var(--border-strong)]"
              :data-check="check.key"
            >
              <component :is="checkWords(check.key).icon" class="size-3.5" />{{ checkWords(check.key).name }} {{ fmt(check.count) }}
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
