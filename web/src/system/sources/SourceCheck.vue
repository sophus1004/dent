<!--
  가져오기 2단계 '형식 확인': 서버가 읽은 원본의 모양을 숫자로 보이고, 앞쪽 줄을 표로 보인다. 모든 모듈이 같이 쓴다.
  - 파일: 형식 · 인코딩 · 구분자 · 줄 수 · 칸. 엑셀이면 시트를 고르고, 고르면 그 시트로 다시 미리 본다.
  - 허깅페이스: 구성(고르면 다시 미리 봄) · 가져올 분할(체크, 처음에는 모두) · 줄 수 · 칸 · 라벨 이름(ClassLabel).
    미리 보던 분할을 빼면 남은 첫 분할로 다시 미리 본다.
  고친 원본은 v-model:source로 화면에 돌려준다. [이전]은 back, [다음]은 next.
-->
<script setup lang="ts">
import { Database, FileSpreadsheet, FileText, LoaderCircle, OctagonAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref } from 'vue'

import { previewHuggingFace, previewUpload } from '@/system/api'
import { fileSize, fmt } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import PreviewTable from '@/system/sources/PreviewTable.vue'
import {
  fileSource,
  huggingFaceSource,
  sourceLines,
  sourceRowCount,
  type LoadedSource,
} from '@/system/sources/source'
import { Badge } from '@/system/ui/badge'
import { Button } from '@/system/ui/button'
import { NativeSelect } from '@/system/ui/native-select'

const emit = defineEmits<{
  back: []
  next: []
}>()

const source = defineModel<LoadedSource>('source', { required: true })

// 화면에 보일 이름
const FORMAT_NAMES = { csv: 'CSV', xlsx: '엑셀' }
const ENCODING_NAMES = { 'utf-8': 'UTF-8', cp949: 'CP949' }
const DELIMITER_NAMES = { ',': '쉼표', '\t': '탭', ';': '세미콜론' }

// 라벨 이름을 칩으로 보일 최대 개수. 넘으면 '+n'
const LABEL_CHIPS_MAX = 40

const file = computed(() => (source.value.source === 'file' ? source.value : null))
const huggingFace = computed(() => (source.value.source === 'huggingface' ? source.value : null))

const isReloading = ref(false)
const reloadProblem = ref<string | null>(null)
let reloadController: AbortController | null = null

// 가져올 전체 줄 수 (허깅페이스는 고른 분할의 합)
const rowCount = computed(() => sourceRowCount(source.value))

// 표 머리줄에 붙일 표시: ClassLabel 열 → '라벨 7'
const marks = computed(() => {
  const labelNames = huggingFace.value?.preview.label_names ?? {}
  const entries = Object.entries(labelNames).map(([column, names]) => [column, `라벨 ${names.length}`])
  return Object.fromEntries(entries)
})

const hasNoSplit = computed(() => huggingFace.value !== null && huggingFace.value.splits.length === 0)
const hasColumns = computed(() => source.value.preview.columns.length > 0)
const canGoNext = computed(() => !isReloading.value && !hasNoSplit.value && hasColumns.value)

/** 미리 보기를 다시 받는다. 앞서 받던 것은 끊는다. */
async function reload(load: (signal: AbortSignal) => Promise<LoadedSource>): Promise<void> {
  reloadController?.abort()
  const controller = new AbortController()
  reloadController = controller
  isReloading.value = true
  reloadProblem.value = null
  try {
    source.value = await load(controller.signal)
  } catch (error) {
    if (!isAbortError(error)) reloadProblem.value = errorMessage(error)
  } finally {
    if (reloadController === controller) {
      isReloading.value = false
      reloadController = null
    }
  }
}

function changeSheet(sheet: string | null | undefined): void {
  const loaded = file.value
  if (!loaded || !sheet || sheet === loaded.preview.sheet) return
  void reload(async (signal) =>
    fileSource(loaded.fileName, await previewUpload(loaded.preview.upload_id, sheet, signal)),
  )
}

function changeConfig(config: string | null | undefined): void {
  const loaded = huggingFace.value
  if (!loaded || !config || config === loaded.preview.config) return
  const repo = loaded.preview.repo
  void reload(async (signal) => huggingFaceSource(await previewHuggingFace({ repo, config }, signal)))
}

function toggleSplit(name: string, event: Event): void {
  const loaded = huggingFace.value
  if (!loaded) return
  const checked = (event.target as HTMLInputElement).checked
  // 분할 순서는 허깅페이스가 준 순서 그대로 둔다.
  const splits = loaded.preview.splits
    .map((split) => split.name)
    .filter((splitName) => (splitName === name ? checked : loaded.splits.includes(splitName)))
  source.value = { ...loaded, splits }

  const isPreviewSplitDropped = !splits.includes(loaded.preview.split) && splits.length > 0
  if (!isPreviewSplitDropped) return
  const { repo, config } = loaded.preview
  void reload(async (signal) => {
    const preview = await previewHuggingFace({ repo, config, split: splits[0] }, signal)
    // 기다리는 동안 체크를 또 바꿨을 수 있으므로 지금 고른 분할을 그대로 둔다.
    const latestSplits = source.value.source === 'huggingface' ? source.value.splits : splits
    return { source: 'huggingface', preview, splits: latestSplits }
  })
}

onBeforeUnmount(() => reloadController?.abort())
</script>

<template>
  <section class="card" aria-label="형식">
    <!-- 파일 -->
    <template v-if="file">
      <div class="flex h-11 min-w-0 items-center gap-2 border-b px-4">
        <component
          :is="file.preview.format === 'xlsx' ? FileSpreadsheet : FileText"
          class="size-4 shrink-0 text-muted-foreground"
        />
        <h2 class="truncate text-body font-semibold">{{ file.fileName }}</h2>
        <span class="ml-auto shrink-0 text-ui text-muted-foreground">{{ fileSize(file.preview.size_bytes) }}</span>
      </div>
      <dl class="grid grid-cols-2 sm:grid-flow-col sm:grid-cols-none sm:auto-cols-fr sm:divide-x">
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">형식</dt>
          <dd class="mt-0.5 text-title font-semibold">{{ FORMAT_NAMES[file.preview.format] }}</dd>
        </div>
        <template v-if="file.preview.format === 'csv'">
          <div class="px-4 py-3">
            <dt class="text-meta text-muted-foreground">인코딩</dt>
            <dd class="mt-0.5 text-title font-semibold">
              {{ file.preview.encoding ? ENCODING_NAMES[file.preview.encoding] : '—' }}
            </dd>
          </div>
          <div class="px-4 py-3">
            <dt class="text-meta text-muted-foreground">구분자</dt>
            <dd class="mt-0.5 text-title font-semibold">
              {{ file.preview.delimiter ? DELIMITER_NAMES[file.preview.delimiter] : '—' }}
            </dd>
          </div>
        </template>
        <div v-else class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">
            <label for="source-sheet">시트 · {{ file.preview.sheets.length }}개</label>
          </dt>
          <dd class="mt-0.5">
            <NativeSelect
              id="source-sheet"
              :model-value="file.preview.sheet"
              class="max-w-[220px]"
              @update:model-value="changeSheet"
            >
              <option v-for="sheet in file.preview.sheets" :key="sheet" :value="sheet">{{ sheet }}</option>
            </NativeSelect>
          </dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">줄 수</dt>
          <dd class="mt-0.5 text-title font-semibold">{{ rowCount === null ? '—' : fmt(rowCount) }}</dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">칸</dt>
          <dd class="mt-0.5 text-title font-semibold">{{ file.preview.columns.length }}</dd>
        </div>
      </dl>
    </template>

    <!-- 허깅페이스 -->
    <template v-else-if="huggingFace">
      <div class="flex h-11 min-w-0 items-center gap-2 border-b px-4">
        <Database class="size-4 shrink-0 text-muted-foreground" />
        <h2 class="truncate font-mono text-body font-semibold">{{ huggingFace.preview.repo }}</h2>
        <Badge class="ml-1">허깅페이스</Badge>
      </div>
      <dl class="grid grid-cols-2 sm:grid-flow-col sm:grid-cols-none sm:auto-cols-fr sm:divide-x">
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">
            <label for="source-config">구성 · {{ huggingFace.preview.configs.length }}개</label>
          </dt>
          <dd class="mt-0.5">
            <NativeSelect
              id="source-config"
              :model-value="huggingFace.preview.config"
              class="max-w-[220px] font-mono"
              @update:model-value="changeConfig"
            >
              <option v-for="config in huggingFace.preview.configs" :key="config" :value="config">
                {{ config }}
              </option>
            </NativeSelect>
          </dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">분할</dt>
          <dd class="mt-0.5 text-title font-semibold">
            {{ huggingFace.splits.length }}
            <span class="text-ui font-normal text-muted-foreground">/ {{ huggingFace.preview.splits.length }}</span>
          </dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">줄 수</dt>
          <dd class="mt-0.5 text-title font-semibold">{{ rowCount === null ? '—' : fmt(rowCount) }}</dd>
        </div>
        <div class="px-4 py-3">
          <dt class="text-meta text-muted-foreground">칸</dt>
          <dd class="mt-0.5 text-title font-semibold">{{ huggingFace.preview.columns.length }}</dd>
        </div>
      </dl>

      <div class="space-y-3 border-t px-4 py-3.5">
        <fieldset class="flex flex-wrap items-center gap-2">
          <legend class="caps mb-2">가져올 분할</legend>
          <label
            v-for="split in huggingFace.preview.splits"
            :key="split.name"
            class="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md bg-card px-2.5 text-ui shadow-[inset_0_0_0_1px_var(--border-strong)] transition-colors hover:bg-muted has-checked:bg-accent/60 has-checked:shadow-[inset_0_0_0_1px_var(--primary)]"
          >
            <input
              type="checkbox"
              class="size-3.5 accent-primary"
              :checked="huggingFace.splits.includes(split.name)"
              @change="toggleSplit(split.name, $event)"
            />
            <span class="font-mono font-medium">{{ split.name }}</span>
            <span class="text-muted-foreground">{{ split.num_rows === null ? '—' : fmt(split.num_rows) }}</span>
          </label>
          <span v-if="hasNoSplit" class="inline-flex items-center gap-1.5 text-ui font-semibold text-danger-ink">
            <OctagonAlert class="size-4 text-danger" />분할 없음
          </span>
        </fieldset>

        <div v-for="(names, column) in huggingFace.preview.label_names" :key="column">
          <div class="caps mb-2">라벨 이름 · <span class="font-mono normal-case">{{ column }}</span> · {{ names.length }}</div>
          <div class="flex flex-wrap gap-1.5">
            <Badge v-for="name in names.slice(0, LABEL_CHIPS_MAX)" :key="name">{{ name }}</Badge>
            <span v-if="names.length > LABEL_CHIPS_MAX" class="self-center text-ui text-muted-foreground">
              +{{ names.length - LABEL_CHIPS_MAX }}
            </span>
          </div>
        </div>
      </div>
    </template>
  </section>

  <section class="card overflow-hidden" aria-label="미리 보기">
    <div class="flex h-11 items-center gap-2.5 border-b px-4">
      <h2 class="text-body font-semibold">미리 보기</h2>
      <span v-if="huggingFace" class="font-mono text-ui text-muted-foreground">{{ huggingFace.preview.split }}</span>
      <span v-else-if="file?.preview.sheet" class="text-ui text-muted-foreground">{{ file.preview.sheet }}</span>
      <span class="ml-auto flex items-center gap-2 text-ui text-muted-foreground">
        <LoaderCircle v-if="isReloading" class="size-4 animate-spin" aria-label="다시 읽는 중" />
        앞 {{ source.preview.rows.length }}줄
      </span>
    </div>
    <p v-if="reloadProblem" class="flex items-start gap-2 border-b px-4 py-2.5 text-ui" role="alert">
      <OctagonAlert class="mt-0.5 size-4 shrink-0 text-danger" />
      <span class="min-w-0">
        <span class="font-semibold text-danger-ink">다시 읽기 실패</span>
        <span class="block text-muted-foreground">{{ reloadProblem }}</span>
      </span>
    </p>
    <div
      v-if="source.preview.rows.length === 0"
      class="flex h-24 items-center justify-center text-ui text-subtle-foreground"
    >
      줄 없음
    </div>
    <PreviewTable
      v-else
      :class="{ 'opacity-50': isReloading }"
      :columns="source.preview.columns"
      :rows="source.preview.rows"
      :lines="sourceLines(source)"
      :marks="marks"
    />
  </section>

  <div class="flex items-center justify-between">
    <Button type="button" variant="outline" @click="emit('back')">이전</Button>
    <Button type="button" :disabled="!canGoNext" @click="emit('next')">다음</Button>
  </div>
</template>
