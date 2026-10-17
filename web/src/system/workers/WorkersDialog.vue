<!--
  작업 실행기 대화상자 (작게): 함께 돌 실행기 수를 고르고, 번호마다 상태와 도는 작업을 본다.
    실행기 수   [1 2 3 4 5 6 7 8]   실행 중 4 · 도는 중 2 · 대기 3
    번호 · 상태 · 작업(종류 · 데이터셋 · 진행)
  고르면 바로 저장하고 관리 프로세스가 몇 초 안에 늘리거나 줄인다(줄이면 큰 번호부터 하던 작업을 끝내고 종료).
  데이터셋끼리는 함께 돌고, 같은 데이터셋의 작업은 넣은 순서대로 하나씩 돈다.
  열려 있는 동안 2초마다 다시 묻는다. App.vue에 하나만 두고 openWorkerSettings()로 연다.
-->
<script setup lang="ts">
import { Cpu, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getWorkers, updateWorkers } from '@/system/api'
import { fmt } from '@/system/format'
import { errorMessage } from '@/system/http'
import { findJobKind, jobKindName, progressText } from '@/system/jobKinds'
import { useModules } from '@/system/module'
import { refreshReadiness } from '@/system/readiness'
import type { WorkerSlotRead, WorkersRead } from '@/system/types'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/system/ui/dialog'
import { Segmented, type SegmentedOption } from '@/system/ui/segmented'
import { SLOT_STATES, isWorkerSettingsOpen } from '@/system/workers/workers'

// 다시 묻는 간격(밀리초)
const POLL_INTERVAL_MS = 2000

// 태그 모양 (연결 설정의 태그와 같다)
const TAG_CLASS =
  'inline-flex h-5 shrink-0 items-center rounded-md px-[7px] text-meta font-semibold text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]'

const modules = useModules()
const workers = ref<WorkersRead | null>(null)
const loadError = ref<string | null>(null)
const isSaving = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

const options = computed<SegmentedOption<number>[]>(() => {
  const range = workers.value ?? { min_count: 1, max_count: 8 }
  return Array.from({ length: range.max_count - range.min_count + 1 }, (_, index) => {
    const value = range.min_count + index
    return { value, label: String(value) }
  })
})

const count = computed({
  get: () => workers.value?.count ?? 0,
  set: (value: number) => void save(value),
})

watch(isWorkerSettingsOpen, (isOpen) => {
  clearTimeout(timer)
  if (isOpen) {
    workers.value = null
    void load()
  }
})
onBeforeUnmount(() => clearTimeout(timer))

async function load(): Promise<void> {
  clearTimeout(timer)
  try {
    workers.value = await getWorkers()
    loadError.value = null
  } catch (error) {
    loadError.value = errorMessage(error)
  }
  if (isWorkerSettingsOpen.value) timer = setTimeout(() => void load(), POLL_INTERVAL_MS)
}

async function save(value: number): Promise<void> {
  if (isSaving.value || value === workers.value?.count) return
  isSaving.value = true
  try {
    workers.value = await updateWorkers(value)
    loadError.value = null
    void refreshReadiness()
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    isSaving.value = false
  }
}

/** 도는 작업 한 줄: '뜻 분석 · 뉴스 · 임베딩 1,024 / 8,000'. 없으면 '—'. */
function jobText(slot: WorkerSlotRead): string {
  const job = slot.job
  if (!job) return '—'
  const kind = findJobKind(modules, job)
  return [jobKindName(modules, job), job.dataset_name, progressText(kind, job)].filter(Boolean).join(' · ')
}
</script>

<template>
  <Dialog v-model:open="isWorkerSettingsOpen">
    <DialogContent
      class="w-[min(560px,calc(100vw-32px))] max-w-none gap-0 overflow-hidden bg-card p-0 shadow-(--shadow-pop) sm:max-w-none"
      data-dialog="worker-settings"
    >
      <DialogHeader class="flex-row items-center gap-2 border-b px-5 py-3.5 pr-12 text-left">
        <Cpu class="size-4 text-muted-foreground" />
        <DialogTitle class="text-title font-semibold">작업 실행기</DialogTitle>
        <span :class="TAG_CLASS">저장 즉시 적용</span>
        <DialogDescription class="sr-only">함께 돌 작업 실행기 수 · 번호마다 상태와 도는 작업</DialogDescription>
      </DialogHeader>

      <div v-if="loadError" class="flex items-center gap-2 border-b px-5 py-3 text-ui">
        <TriangleAlert class="size-4 text-danger" />
        <span class="font-semibold text-danger-ink">오류</span>
        <span class="min-w-0 flex-1 truncate text-muted-foreground">{{ loadError }}</span>
      </div>

      <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-5 py-3.5 text-ui">
        <span class="w-[76px] shrink-0 font-medium">실행기 수</span>
        <Segmented
          v-model="count"
          :options="options"
          label="작업 실행기 수"
          :disabled="!workers || isSaving"
          data-field="worker-count"
        />
        <span v-if="workers" class="text-muted-foreground tabular-nums">
          실행 중 {{ fmt(workers.running) }} · 도는 중 {{ fmt(workers.busy) }} · 대기 {{ fmt(workers.queued) }}
        </span>
      </div>

      <div
        v-if="workers && !workers.managed"
        class="flex items-center gap-2 border-b px-5 py-2.5 text-ui"
        data-state="unmanaged"
      >
        <TriangleAlert class="size-4 text-warning" />
        <span class="font-semibold text-warning-ink">관리 프로세스 없음</span>
        <span class="text-muted-foreground">저장만 · 다음 실행부터</span>
      </div>

      <table class="w-full text-ui">
        <thead>
          <tr class="border-b text-meta text-muted-foreground">
            <th class="w-20 px-5 py-2 text-left font-medium whitespace-nowrap">번호</th>
            <th class="w-32 py-2 text-left font-medium whitespace-nowrap">상태</th>
            <th class="py-2 pr-5 text-left font-medium">작업</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="slot in workers?.slots ?? []" :key="slot.slot" class="border-b last:border-b-0" :data-slot="slot.slot">
            <td class="px-5 py-2 font-medium tabular-nums">{{ slot.slot }}</td>
            <td class="py-2 font-semibold" :class="SLOT_STATES[slot.state].class">{{ SLOT_STATES[slot.state].word }}</td>
            <td class="max-w-0 truncate py-2 pr-5 text-muted-foreground" :title="jobText(slot)">{{ jobText(slot) }}</td>
          </tr>
          <tr v-if="!workers">
            <td colspan="3" class="px-5 py-3 text-muted-foreground">불러오는 중</td>
          </tr>
        </tbody>
      </table>
    </DialogContent>
  </Dialog>
</template>
