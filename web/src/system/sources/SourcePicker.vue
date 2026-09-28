<!--
  가져오기 1단계 '가져올 곳' 칸 두 개: 파일 올리기 · 허깅페이스 저장소. 모든 모듈의 가져오기 화면이 같이 쓴다.
  - 파일: 끌어 놓거나 '파일 고르기'로 고르면 바로 올린다(진행 막대). 형식이 틀리거나 서버가 거절하면 칸 아래에 알린다.
  - 허깅페이스: 저장소 이름을 넣고 [불러오기](또는 Enter)를 누르면 미리 보기를 받는다. 404 찾지 못함 · 502 연결 실패.
  불러오기가 끝나면 loaded로 원본을 알린다. 화면은 그것을 받아 2단계로 넘어간다.
  이미 불러온 원본(loaded)이 있으면 그 칸에 체크 줄과 [다음]을 보인다. 다른 파일을 올리면 바꾼다.
  picked가 file이나 huggingface면(주소의 ?source=) 그 칸을 보라 테두리로 미리 골라 둔다.
-->
<script setup lang="ts">
import { CircleCheck, Database, FileText, FileUp, LoaderCircle, OctagonAlert, Upload } from '@lucide/vue'
import { computed, onBeforeUnmount, ref } from 'vue'

import { previewHuggingFace, uploadFile } from '@/system/api'
import { fileSize, fmt } from '@/system/format'
import { ApiError, errorMessage, isAbortError } from '@/system/http'
import {
  FILE_SUFFIXES,
  fileSource,
  huggingFaceSource,
  isSupportedFile,
  sourceName,
  type LoadedSource,
} from '@/system/sources/source'
import type { ImportSource } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Input } from '@/system/ui/input'
import { Progress } from '@/system/ui/progress'

const props = defineProps<{
  // 주소로 미리 고른 가져올 곳. 없으면 null
  picked: ImportSource | null
  // 이미 불러온 원본. 없으면 null
  loaded: LoadedSource | null
}>()

const emit = defineEmits<{
  // 원본을 새로 불러왔다
  loaded: [source: LoadedSource]
  // [다음]을 눌렀다
  next: []
}>()

// 허깅페이스 저장소 이름. 예: 'klue/klue'
const repo = defineModel<string>('repo', { required: true })

// 미리 고른 칸의 테두리
const PICKED_CLASS = 'border-primary ring-3 ring-primary/15'

// 칸 아래에 보일 문제: 짧은 낱말 + 서버가 준 한 줄
interface Problem {
  label: string
  detail: string
}

// 올리는 중인 파일
interface Uploading {
  name: string
  sizeBytes: number
  // 올린 비율 0~1
  ratio: number
}

const fileInput = ref<HTMLInputElement | null>(null)
const dropZone = ref<HTMLElement | null>(null)
const isDragging = ref(false)
const uploading = ref<Uploading | null>(null)
const fileProblem = ref<Problem | null>(null)
const isLoadingHuggingFace = ref(false)
const huggingFaceProblem = ref<Problem | null>(null)

// 진행 중인 요청을 끊는 신호. 화면을 떠나면 끊는다.
let uploadController: AbortController | null = null
let huggingFaceController: AbortController | null = null

// 다 올리고 서버가 표를 읽는 중인지 (큰 파일은 줄 수를 세느라 몇 초 걸린다)
const isReading = computed(() => uploading.value !== null && uploading.value.ratio >= 1)

// 칸 테두리: 불러온 원본의 칸, 없으면 주소로 미리 고른 칸
const highlighted = computed(() => props.loaded?.source ?? props.picked)

const loadedFile = computed(() => (props.loaded?.source === 'file' ? props.loaded : null))
const loadedHuggingFace = computed(() => (props.loaded?.source === 'huggingface' ? props.loaded : null))
const canLoadHuggingFace = computed(() => repo.value.trim() !== '' && !isLoadingHuggingFace.value)

// ---------- 파일 ----------

function pickFile(): void {
  fileInput.value?.click()
}

function onFileChosen(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // 같은 파일을 다시 골라도 change가 오게 비운다.
  input.value = ''
  if (file) void startUpload(file)
}

function onDragOver(event: DragEvent): void {
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  isDragging.value = true
}

function onDragLeave(event: DragEvent): void {
  // 칸 안의 글자·아이콘으로 옮겨 갈 때도 dragleave가 오므로, 칸 밖으로 나갔을 때만 끈다.
  const stillInside = dropZone.value?.contains(event.relatedTarget as Node | null) ?? false
  if (!stillInside) isDragging.value = false
}

function onDrop(event: DragEvent): void {
  event.preventDefault()
  isDragging.value = false
  const file = event.dataTransfer?.files[0]
  if (file) void startUpload(file)
}

async function startUpload(file: File): Promise<void> {
  // 올리는 중에 또 누르거나 끌어 놓아도 하나만 올린다.
  if (uploading.value) return
  fileProblem.value = null
  if (!isSupportedFile(file.name)) {
    fileProblem.value = { label: '형식 오류', detail: `${file.name} · 받는 형식 ${FILE_SUFFIXES.join(' ')}` }
    return
  }

  uploading.value = { name: file.name, sizeBytes: file.size, ratio: 0 }
  const controller = new AbortController()
  uploadController = controller
  try {
    const preview = await uploadFile(file, {
      signal: controller.signal,
      onProgress: (loadedBytes, totalBytes) => {
        if (uploading.value && totalBytes > 0) uploading.value.ratio = loadedBytes / totalBytes
      },
    })
    emit('loaded', fileSource(file.name, preview))
  } catch (error) {
    if (!isAbortError(error)) {
      fileProblem.value = { label: uploadErrorLabel(error), detail: errorMessage(error) }
    }
  } finally {
    uploading.value = null
    uploadController = null
  }
}

function cancelUpload(): void {
  uploadController?.abort()
}

// 올리기 실패의 짧은 낱말
function uploadErrorLabel(error: unknown): string {
  const status = error instanceof ApiError ? error.status : -1
  if (status === 422) return '읽기 오류'
  if (status === 413) return '크기 초과'
  if (status === 0) return '연결 실패'
  return '올리기 실패'
}

// ---------- 허깅페이스 ----------

async function loadHuggingFace(): Promise<void> {
  // 불러오는 중에 또 누르면 무시한다.
  if (!canLoadHuggingFace.value) return
  huggingFaceProblem.value = null
  isLoadingHuggingFace.value = true
  const controller = new AbortController()
  huggingFaceController = controller
  try {
    const preview = await previewHuggingFace({ repo: repo.value.trim() }, controller.signal)
    emit('loaded', huggingFaceSource(preview))
  } catch (error) {
    if (!isAbortError(error)) {
      huggingFaceProblem.value = { label: huggingFaceErrorLabel(error), detail: errorMessage(error) }
    }
  } finally {
    isLoadingHuggingFace.value = false
    huggingFaceController = null
  }
}

// 허깅페이스 불러오기 실패의 짧은 낱말
function huggingFaceErrorLabel(error: unknown): string {
  const status = error instanceof ApiError ? error.status : -1
  if (status === 404) return '찾지 못함'
  if (status === 502 || status === 0) return '연결 실패'
  if (status === 422) return '입력 오류'
  return '불러오기 실패'
}

onBeforeUnmount(() => {
  uploadController?.abort()
  huggingFaceController?.abort()
})
</script>

<template>
  <section aria-label="가져올 곳">
    <div class="caps mb-2">가져올 곳</div>
    <div class="grid gap-4 md:grid-cols-2">
      <article class="card flex flex-col" :class="{ [PICKED_CLASS]: highlighted === 'file' }">
        <div class="flex h-11 items-center gap-2 border-b px-4">
          <FileUp class="size-4 text-muted-foreground" />
          <h2 class="text-body font-semibold">파일 올리기</h2>
        </div>
        <div class="flex flex-1 flex-col gap-3 p-4">
          <input
            ref="fileInput"
            type="file"
            class="hidden"
            :accept="FILE_SUFFIXES.join(',')"
            aria-label="파일 고르기"
            @change="onFileChosen"
          />

          <div
            v-if="uploading"
            class="flex min-h-[148px] flex-1 flex-col justify-center gap-2.5 rounded-lg border bg-muted/40 px-4 py-5"
            aria-live="polite"
          >
            <div class="flex min-w-0 items-center gap-2 text-ui">
              <FileText class="size-4 shrink-0 text-muted-foreground" />
              <span class="truncate font-medium">{{ uploading.name }}</span>
              <span class="ml-auto shrink-0 text-muted-foreground">{{ fileSize(uploading.sizeBytes) }}</span>
            </div>
            <Progress :value="isReading ? null : uploading.ratio" label="올리기" />
            <div class="flex h-6 items-center gap-2 text-meta text-muted-foreground">
              <template v-if="isReading">
                <LoaderCircle class="size-3.5 animate-spin" />읽는 중
              </template>
              <template v-else>
                올리는 중 <span class="font-semibold text-foreground">{{ Math.round(uploading.ratio * 100) }}%</span>
              </template>
              <Button type="button" variant="quiet" size="xs" class="ml-auto" @click="cancelUpload">취소</Button>
            </div>
          </div>

          <div
            v-else
            ref="dropZone"
            class="flex min-h-[148px] flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-6 text-center transition-colors"
            :class="isDragging ? 'border-primary bg-accent/60' : 'border-border-strong bg-muted/40'"
            @dragover="onDragOver"
            @dragleave="onDragLeave"
            @drop="onDrop"
          >
            <span class="grid size-9 place-items-center rounded-lg bg-card shadow-[0_0_0_1px_var(--border)]">
              <Upload class="size-4" :class="isDragging ? 'text-primary' : 'text-muted-foreground'" />
            </span>
            <span class="text-ui font-semibold">CSV · TSV · 엑셀(.xlsx)</span>
            <span class="flex items-center gap-1 text-ui text-muted-foreground">
              {{ isDragging ? '놓기' : '끌어 놓기' }} ·
              <button
                type="button"
                class="rounded-sm font-semibold text-primary underline-offset-4 hover:underline"
                @click="pickFile"
              >
                파일 고르기
              </button>
            </span>
          </div>

          <p v-if="fileProblem" class="flex items-start gap-2 text-ui" role="alert">
            <OctagonAlert class="mt-0.5 size-4 shrink-0 text-danger" />
            <span class="min-w-0">
              <span class="font-semibold text-danger-ink">{{ fileProblem.label }}</span>
              <span class="block text-muted-foreground">{{ fileProblem.detail }}</span>
            </span>
          </p>
          <p v-else-if="loadedFile && !uploading" class="flex min-w-0 items-center gap-2 text-ui">
            <CircleCheck class="size-4 shrink-0 text-success" />
            <span class="truncate font-medium">{{ loadedFile.fileName }}</span>
            <span class="ml-auto shrink-0 text-muted-foreground">
              {{ fileSize(loadedFile.preview.size_bytes) }}
              <template v-if="loadedFile.preview.row_count !== null">
                · {{ fmt(loadedFile.preview.row_count) }}줄
              </template>
            </span>
          </p>
        </div>
      </article>

      <article class="card flex flex-col" :class="{ [PICKED_CLASS]: highlighted === 'huggingface' }">
        <div class="flex h-11 items-center gap-2 border-b px-4">
          <Database class="size-4 text-muted-foreground" />
          <h2 class="text-body font-semibold">허깅페이스</h2>
        </div>
        <div class="flex flex-1 flex-col gap-3 p-4">
          <div>
            <label for="hf-repo" class="mb-1.5 block text-ui font-[550]">저장소</label>
            <div class="flex gap-2">
              <Input
                id="hf-repo"
                v-model="repo"
                placeholder="예: klue/klue"
                class="font-mono"
                autocomplete="off"
                spellcheck="false"
                :aria-invalid="huggingFaceProblem ? true : undefined"
                @keydown.enter.prevent="loadHuggingFace"
              />
              <Button
                type="button"
                variant="outline"
                class="w-[104px] shrink-0"
                :disabled="!canLoadHuggingFace"
                :aria-busy="isLoadingHuggingFace || undefined"
                @click="loadHuggingFace"
              >
                <LoaderCircle v-if="isLoadingHuggingFace" class="animate-spin" />
                불러오기
              </Button>
            </div>
          </div>

          <p v-if="huggingFaceProblem" class="flex items-start gap-2 text-ui" role="alert">
            <OctagonAlert class="mt-0.5 size-4 shrink-0 text-danger" />
            <span class="min-w-0">
              <span class="font-semibold text-danger-ink">{{ huggingFaceProblem.label }}</span>
              <span class="block break-all text-muted-foreground">{{ huggingFaceProblem.detail }}</span>
            </span>
          </p>
          <p v-else-if="loadedHuggingFace" class="flex min-w-0 items-center gap-2 text-ui">
            <CircleCheck class="size-4 shrink-0 text-success" />
            <span class="truncate font-medium">{{ sourceName(loadedHuggingFace) }}</span>
            <span class="ml-auto shrink-0 text-muted-foreground">
              구성 {{ loadedHuggingFace.preview.configs.length }} · 분할 {{ loadedHuggingFace.preview.splits.length }}
            </span>
          </p>
        </div>
      </article>
    </div>

    <div v-if="loaded" class="mt-4 flex justify-end">
      <Button type="button" :disabled="uploading !== null" @click="emit('next')">다음</Button>
    </div>
  </section>
</template>
