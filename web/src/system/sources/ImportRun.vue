<!--
  가져오기 4단계 '가져오기': [가져오기] → 작업 진행률 → 결과(또는 실패). 모든 모듈이 같이 쓴다.
  - 가져오기를 시작하는 일(모듈 API 부르기)은 화면이 start로 넘긴다. system은 모듈을 모른다.
  - 시작하면 작업(/api/v1/jobs/{id})을 1초마다 물어 '처리 n / 전체 m'을 보이고, 끝나면 멈춘다(system/jobs.ts).
    작업이 대기에 머물고 /readyz가 작업 실행기 종료됨이라고 하면 '작업 실행기 종료됨 · 대기 중'을 보인다.
  - 끝나면 가져오기 기록(/api/v1/imports/{id})으로 결과를 보인다: 추가 · 건너뜀 · 소요, 건너뛴 줄 표.
    모듈만의 결과(예: 분류의 새 라벨)는 화면이 result 칸(slot)에 그린다.
  - 실패하면 이유와 [다시 시도]. 올린 파일은 가져오기가 끝나면(성공·실패) 서버가 지우므로,
    파일 가져오기가 실패하면 [다시 시도] 대신 [다시 올리기](1단계로)를 보인다.
  기본 칸(slot)에는 화면이 가져올 내용 요약을 넣는다. phase를 받아 시작한 뒤에는 입력을 잠근다.
  끝난 뒤 주 버튼은 [첫 화면으로]이고, 있던 데이터셋에 더할 때는 화면이 doneTo로 그 데이터셋을 준다.
  화면을 떠나면 묻기를 멈춘다.
-->
<script setup lang="ts">
import { CircleCheck, LoaderCircle, OctagonAlert, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { getImport } from '@/system/api'
import { durationText, fmt, pct } from '@/system/format'
import { errorMessage, isAbortError } from '@/system/http'
import { jobProgress, useJob } from '@/system/jobs'
import { readiness, refreshReadiness } from '@/system/readiness'
import type { ImportPhase } from '@/system/sources/source'
import type { ImportRead, ImportSource } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Progress } from '@/system/ui/progress'

const props = defineProps<{
  // 가져오기를 시작하는 함수. 모듈의 가져오기 API를 부르고 가져오기 기록을 돌려준다.
  start: () => Promise<ImportRead>
  // 시작할 수 있는지 (예: 데이터셋 이름을 넣었는지)
  canStart: boolean
  // 가져올 곳: file · huggingface. 파일이면 실패 뒤에 같은 파일로 다시 시도할 수 없다.
  source: ImportSource
  // 끝난 뒤 주 버튼이 갈 곳 (예: 더한 데이터셋). 없으면 첫 화면
  doneTo?: { label: string; to: string } | null
}>()

const emit = defineEmits<{
  // [이전]
  back: []
  // [다른 파일 가져오기]
  restart: []
  // [다시 올리기]: 실패한 파일 가져오기의 원본을 서버가 지웠으므로 1단계에서 다시 올린다
  reupload: []
  // 가져오기 기록이 생겼다 (데이터셋도 이때 생긴다)
  started: [importRead: ImportRead]
  // 끝났다 (done 또는 failed)
  finished: [importRead: ImportRead]
}>()

defineSlots<{
  // 가져올 내용 요약
  default(props: { phase: ImportPhase }): unknown
  // 모듈만의 결과
  result(props: { read: ImportRead }): unknown
}>()

// 작업이 대기 중일 때 작업 실행기가 실행 중인지 다시 묻는 간격(밀리초)
const WORKER_CHECK_INTERVAL_MS = 5000

// 건너뛴 줄 표에 보일 최대 줄 수. 서버는 앞쪽 100줄까지만 남긴다.
const SKIPPED_LINES_KEPT = 100

const phase = ref<ImportPhase>('ready')
const importId = ref<number | null>(null)
const jobId = ref<number | null>(null)
const result = ref<ImportRead | null>(null)
const failure = ref<string | null>(null)

const { job, error: pollError, isFinished } = useJob(jobId)

let resultController: AbortController | null = null
let workerTimer: ReturnType<typeof setInterval> | undefined

const isBusy = computed(() => phase.value === 'starting' || phase.value === 'running')
const isQueued = computed(() => phase.value === 'running' && job.value?.status === 'queued')
const isWorkerOff = computed(() => readiness.value?.info.worker?.ok === false)

// 파일 가져오기가 기록을 만든 뒤 실패했는지. 그러면 서버가 올린 원본을 이미 지웠다.
const needsReupload = computed(
  () => props.source === 'file' && phase.value === 'failed' && importId.value !== null,
)

// 진행률 0~1. 전체 양을 아직 모르면 null
const ratio = computed(() => (job.value ? jobProgress(job.value) : null))

// 진행 줄의 상태 낱말
const statusWord = computed(() => {
  if (phase.value === 'starting' || !job.value) return '시작 중'
  if (job.value.status === 'queued') return '대기'
  if (job.value.progress_total === null) return '원본 읽는 중'
  return '가져오는 중'
})

async function begin(): Promise<void> {
  // 두 번 눌러도 한 번만 시작한다.
  if (isBusy.value || phase.value === 'done' || !props.canStart) return
  phase.value = 'starting'
  failure.value = null
  result.value = null
  try {
    const created = await props.start()
    emit('started', created)
    importId.value = created.id
    phase.value = 'running'
    if (created.job_id === null) void loadResult(created.id)
    else jobId.value = created.job_id
  } catch (error) {
    failure.value = errorMessage(error)
    phase.value = 'failed'
  }
}

// 작업이 끝나면 가져오기 기록으로 결과를 읽는다.
async function loadResult(id: number): Promise<void> {
  resultController?.abort()
  const controller = new AbortController()
  resultController = controller
  try {
    const read = await getImport(id, controller.signal)
    if (read.status === 'done') {
      result.value = read
      phase.value = 'done'
    } else {
      failure.value = read.error ?? job.value?.error ?? '실패'
      phase.value = 'failed'
    }
    emit('finished', read)
    // 대기 작업 수가 바뀌었으므로 사이드바·홈의 상태 줄을 바로 맞춘다.
    void refreshReadiness()
  } catch (error) {
    if (isAbortError(error)) return
    failure.value = errorMessage(error)
    phase.value = 'failed'
  }
}

watch(isFinished, (finished) => {
  if (finished && importId.value !== null) void loadResult(importId.value)
})

// 작업이 대기에 머물면 작업 실행기가 종료됐는지 /readyz에 다시 묻는다(평소에는 30초마다라 느리다).
watch(isQueued, (queued) => {
  clearInterval(workerTimer)
  if (!queued) return
  void refreshReadiness()
  workerTimer = setInterval(() => void refreshReadiness(), WORKER_CHECK_INTERVAL_MS)
})

onBeforeUnmount(() => {
  clearInterval(workerTimer)
  resultController?.abort()
})
</script>

<template>
  <section class="card" aria-label="가져오기">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <h2 class="text-body font-semibold">가져오기</h2>
    </div>
    <div class="px-4 py-4">
      <slot :phase="phase" />
    </div>

    <div v-if="isBusy" class="space-y-2.5 border-t px-4 py-4" aria-live="polite">
      <div class="flex items-center gap-2 text-ui">
        <LoaderCircle class="size-4 animate-spin text-muted-foreground" />
        <span class="font-semibold">{{ statusWord }}</span>
        <template v-if="job && job.status !== 'queued'">
          <span class="text-border-strong">·</span>
          <span class="text-muted-foreground">
            처리 <b class="font-semibold text-foreground">{{ fmt(job.progress_done) }}</b>
            <template v-if="job.progress_total !== null">
              / 전체 <b class="font-semibold text-foreground">{{ fmt(job.progress_total) }}</b>
            </template>
          </span>
        </template>
        <span v-if="job?.progress_total" class="ml-auto font-semibold">
          {{ pct(job.progress_done, job.progress_total) }}%
        </span>
      </div>
      <Progress :value="job?.status === 'queued' ? 0 : ratio" label="가져오기 진행률" />
      <p v-if="isQueued && isWorkerOff" class="flex items-center gap-2 text-ui" role="status">
        <TriangleAlert class="size-4 shrink-0 text-warning" />
        <span class="font-semibold text-warning-ink">작업 실행기 종료됨 · 대기 중</span>
        <Button variant="quiet" size="xs" class="ml-auto" as-child>
          <RouterLink to="/status">상태 보기</RouterLink>
        </Button>
      </p>
      <p v-if="pollError" class="flex items-center gap-2 text-ui text-muted-foreground" role="status">
        <TriangleAlert class="size-4 shrink-0 text-warning" />
        <span class="font-semibold text-warning-ink">연결 끊김</span>
        <span class="truncate">{{ pollError }}</span>
      </p>
    </div>
  </section>

  <section v-if="phase === 'done' && result" class="card" aria-label="결과">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <CircleCheck class="size-4 text-success" />
      <h2 class="text-body font-semibold text-success-ink">완료</h2>
    </div>
    <dl class="grid grid-cols-2 sm:grid-cols-3 sm:divide-x">
      <div class="px-4 py-3">
        <dt class="text-meta text-muted-foreground">추가</dt>
        <dd class="mt-0.5 text-kpi font-semibold tracking-[-0.02em]">{{ fmt(result.rows_added) }}</dd>
      </div>
      <div class="px-4 py-3">
        <dt class="text-meta text-muted-foreground">건너뜀</dt>
        <dd
          class="mt-0.5 text-kpi font-semibold tracking-[-0.02em]"
          :class="result.rows_skipped ? 'text-warning-ink' : 'text-subtle-foreground'"
        >
          {{ fmt(result.rows_skipped) }}
        </dd>
      </div>
      <div class="px-4 py-3">
        <dt class="text-meta text-muted-foreground">소요</dt>
        <dd class="mt-0.5 text-kpi font-semibold tracking-[-0.02em]">
          {{ durationText(result.created_at, result.finished_at) || '—' }}
        </dd>
      </div>
    </dl>

    <slot name="result" :read="result" />

    <div v-if="result.skipped_lines.length" class="border-t">
      <div class="flex h-10 items-center gap-2 px-4">
        <span class="caps">건너뛴 줄</span>
        <span v-if="result.rows_skipped > result.skipped_lines.length" class="text-meta text-muted-foreground">
          앞 {{ SKIPPED_LINES_KEPT }}줄
        </span>
      </div>
      <div class="max-h-[320px] overflow-auto border-t">
        <table class="w-full border-separate border-spacing-0 text-ui">
          <thead>
            <tr>
              <th
                scope="col"
                class="sticky top-0 h-9 w-24 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-4 text-right text-meta font-semibold text-muted-foreground"
              >
                줄
              </th>
              <th
                scope="col"
                class="sticky top-0 h-9 border-b bg-[color-mix(in_oklab,var(--muted)_72%,var(--card))] px-4 text-left text-meta font-semibold text-muted-foreground"
              >
                이유
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="skipped in result.skipped_lines" :key="skipped.line" class="group">
              <td class="h-9 border-b px-4 text-right font-mono text-meta text-muted-foreground group-last:border-b-0">
                {{ skipped.line }}
              </td>
              <td class="h-9 border-b px-4 group-last:border-b-0">{{ skipped.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>

  <section v-if="phase === 'failed'" class="card" aria-label="실패">
    <div class="flex h-11 items-center gap-2 border-b px-4">
      <OctagonAlert class="size-4 text-danger" />
      <h2 class="text-body font-semibold text-danger-ink">실패</h2>
      <span v-if="needsReupload" class="ml-auto text-meta text-muted-foreground">원본 지움</span>
    </div>
    <p class="px-4 py-3.5 text-ui">{{ failure }}</p>
  </section>

  <div class="flex items-center gap-2">
    <template v-if="phase === 'done'">
      <Button type="button" variant="outline" class="ml-auto" @click="emit('restart')">다른 파일 가져오기</Button>
      <Button v-if="doneTo" as-child><RouterLink :to="doneTo.to">{{ doneTo.label }}</RouterLink></Button>
      <Button v-else as-child><RouterLink to="/">첫 화면으로</RouterLink></Button>
    </template>
    <Button v-else-if="needsReupload" type="button" class="ml-auto" @click="emit('reupload')">다시 올리기</Button>
    <template v-else>
      <Button type="button" variant="outline" :disabled="isBusy" @click="emit('back')">이전</Button>
      <Button type="button" class="ml-auto" :disabled="isBusy || !canStart" @click="begin">
        <LoaderCircle v-if="phase === 'starting'" class="animate-spin" />
        {{ phase === 'failed' ? '다시 시도' : '가져오기' }}
      </Button>
    </template>
  </div>
</template>
