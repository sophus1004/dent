<!--
  작업 기록 화면의 오른쪽 패널: 연 작업 하나의 자세한 것.
    머리       작업 #번호 · 상태 · 이전(K) · 다음(J) · 닫기(Esc)
    사실       종류 · 데이터셋(누르면 그 데이터셋) · 시작 → 끝 · 걸린 시간 · 시작 횟수 · 멈추라고 함
    단계       꺼냄 → 단계마다(시각 · 멈춘 단계는 진행 막대) → 아직 안 간 단계(흐리게). 단계 이름은 모듈이 알려 준다.
    까닭       실패 문장(서버가 준 그대로)
    다시 보내기 외부 서버에 다시 보낸 시각 · 몇 번째 · 까닭
    쓴 연결    그때 쓴 서버(주소 · 모델)와 지금 상태(/readyz)
    버튼       [다시 하기](모듈이 알려 준 종류 · 실패 · 멈춤 · 데이터셋 있음) · [데이터셋] · [기록 복사]
-->
<script setup lang="ts">
import {
  ChevronDown,
  ChevronUp,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  CircleX,
  Copy,
  LoaderCircle,
  RotateCcw,
  SquareArrowOutUpRight,
  X,
} from '@lucide/vue'
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { clockText, durationText, fmt } from '@/system/format'
import { canRetry, currentPhase, eventsOf, phaseName, ROLE_NAMES } from '@/system/jobKinds'
import type { ModuleJobKind } from '@/system/module'
import JobStatusTag from '@/system/pages/JobStatusTag.vue'
import { readiness } from '@/system/readiness'
import type { JobRead } from '@/system/types'
import { Button } from '@/system/ui/button'

const props = defineProps<{
  job: JobRead
  // 모듈이 알려 준 종류 정보. 모르는 종류면 null
  kind: ModuleJobKind | null
  kindName: string
  canPrev: boolean
  canNext: boolean
}>()

const emit = defineEmits<{
  close: []
  step: [delta: number]
  retry: []
}>()

// 칸 이름 (문장 패널과 같은 모양)
const FIELD_LABEL_CLASS = 'mb-2 flex items-center gap-1.5 text-ui font-[550]'

// 단계 한 줄의 상태
type StepState = 'done' | 'running' | 'failed' | 'canceled' | 'waiting'

interface TimelineStep {
  key: string
  name: string
  state: StepState
  // 시작 시각 · 끝 시각(없으면 빈 글자)
  from: string
  to: string
  // 진행 막대(0~1). 멈춘 · 도는 단계에서만
  ratio: number | null
  // 막대 옆 수. 예: '60,416 / 82,973'
  amount: string
}

const copied = ref(false)

const lastPhase = computed(() => currentPhase(props.job))
const retryable = computed(() => canRetry(props.kind, props.job))
const datasetLink = computed(() =>
  props.job.dataset_id !== null ? `/${props.job.module}/${props.job.dataset_id}` : null,
)
const isDatasetDeleted = computed(() => props.job.dataset_id === null && props.job.dataset_name !== null)
// 다시 보내기: 동시에 보낸 요청들이 같은 초에 같은 까닭으로 실패하면 한 줄로 묶고 수를 붙인다.
const retryCount = computed(() => eventsOf(props.job, 'retry').length)
const retries = computed(() => {
  const groups: { key: string; at: string; role: string; attempt: number; reason: string; count: number }[] = []
  for (const event of eventsOf(props.job, 'retry')) {
    const key = [clockText(event.at), event.role, event.attempt, event.reason].join('|')
    const last = groups.at(-1)
    if (last?.key === key) last.count += 1
    else groups.push({ key, at: event.at, role: event.role, attempt: event.attempt, reason: event.reason, count: 1 })
  }
  return groups
})
const connection = computed(() => eventsOf(props.job, 'connection').at(-1) ?? null)
const connectionNow = computed(() => {
  const role = connection.value?.role
  const check = role ? readiness.value?.info[role] : undefined
  return check ?? null
})

// 끝난(또는 지금) 단계의 모양: 작업 상태를 따른다.
const LAST_STATE: Record<JobRead['status'], StepState> = {
  queued: 'waiting',
  running: 'running',
  done: 'done',
  failed: 'failed',
  canceled: 'canceled',
}

const timeline = computed<TimelineStep[]>(() => {
  const job = props.job
  const phases = eventsOf(job, 'phase')
  const amount = job.progress_total ? `${fmt(job.progress_done)} / ${fmt(job.progress_total)}` : ''
  const ratio = job.progress_total ? Math.min(1, job.progress_done / job.progress_total) : null
  const steps: TimelineStep[] = eventsOf(job, 'start').map((event, index) => ({
    key: `start-${index}`,
    name: event.attempt > 1 ? `다시 꺼냄 · ${event.attempt}번째` : '꺼냄',
    state: 'done',
    from: clockText(event.at),
    to: '',
    ratio: null,
    amount: '',
  }))
  if (phases.length === 0) {
    // 단계 없이 도는 작업(가져오기 등): 처리 한 줄로 보인다.
    steps.push({
      key: 'work',
      name: '처리',
      state: LAST_STATE[job.status],
      from: clockText(job.started_at),
      to: clockText(job.finished_at),
      ratio: job.status === 'done' ? null : ratio,
      amount,
    })
    return steps
  }
  phases.forEach((event, index) => {
    const isLast = index === phases.length - 1
    steps.push({
      key: `phase-${index}`,
      name: phaseName(props.kind, event.phase),
      state: isLast ? LAST_STATE[job.status] : 'done',
      from: clockText(event.at),
      to: clockText(isLast ? job.finished_at : phases[index + 1].at),
      ratio: isLast && job.status !== 'done' ? ratio : null,
      amount: isLast ? amount : '',
    })
  })
  // 모듈이 알려 준 단계 가운데 아직 안 간 것
  const known = Object.keys(props.kind?.phases ?? {}).filter((phase) => phase !== 'queued')
  const reached = known.indexOf(lastPhase.value ?? '')
  if (reached >= 0 && job.status !== 'done') {
    for (const phase of known.slice(reached + 1)) {
      steps.push({ key: `later-${phase}`, name: phaseName(props.kind, phase), state: 'waiting', from: '', to: '', ratio: null, amount: '' })
    }
  }
  return steps
})

async function copyRecord(): Promise<void> {
  try {
    await navigator.clipboard.writeText(JSON.stringify(props.job, null, 2))
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    // 클립보드를 못 쓰면(권한 없음) 아무 일도 하지 않는다.
  }
}
</script>

<template>
  <aside
    class="sticky top-[52px] flex h-[calc(100vh-52px)] w-[420px] shrink-0 flex-col border-l bg-card shadow-(--shadow-panel) max-lg:w-[360px]"
    :aria-label="`작업 #${job.id}`"
    data-panel="job"
  >
    <div class="flex h-12 shrink-0 items-center gap-2 border-b pr-2 pl-4">
      <b class="text-body font-semibold">작업</b>
      <span class="font-mono text-meta text-subtle-foreground">#{{ job.id }}</span>
      <JobStatusTag :status="job.status" />
      <div class="ml-auto flex items-center gap-0.5">
        <Button variant="quiet" size="icon-sm" :disabled="!canPrev" aria-label="이전 작업" @click="emit('step', -1)">
          <ChevronUp />
        </Button>
        <Button variant="quiet" size="icon-sm" :disabled="!canNext" aria-label="다음 작업" @click="emit('step', 1)">
          <ChevronDown />
        </Button>
        <Button variant="quiet" size="icon-sm" aria-label="닫기" @click="emit('close')"><X /></Button>
      </div>
    </div>

    <div class="flex-1 space-y-5 overflow-y-auto p-4 pb-16">
      <dl class="grid grid-cols-[84px_minmax(0,1fr)] items-center gap-x-2.5 gap-y-2 text-ui">
        <dt class="text-muted-foreground">종류</dt>
        <dd class="min-w-0 truncate">
          <b class="font-semibold">{{ kindName }}</b>
          <span class="ml-1.5 font-mono text-meta text-subtle-foreground">{{ job.module }} · {{ job.kind }}</span>
        </dd>
        <dt class="text-muted-foreground">데이터셋</dt>
        <dd class="min-w-0 truncate">
          <RouterLink v-if="datasetLink" :to="datasetLink" class="font-[550] text-primary hover:underline">
            {{ job.dataset_name ?? `#${job.dataset_id}` }}
          </RouterLink>
          <template v-else-if="isDatasetDeleted">
            {{ job.dataset_name }}
            <span class="ml-1 rounded-md px-1.5 text-meta font-semibold text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]">지움</span>
          </template>
          <span v-else class="text-subtle-foreground">—</span>
        </dd>
        <dt class="text-muted-foreground">시작 · 끝</dt>
        <dd class="tabular-nums">{{ clockText(job.started_at ?? job.created_at) }} → {{ clockText(job.finished_at) || '—' }}</dd>
        <dt class="text-muted-foreground">걸린 시간</dt>
        <dd class="tabular-nums">{{ durationText(job.started_at, job.finished_at) || '—' }}</dd>
        <template v-if="job.attempts > 0">
          <dt class="text-muted-foreground">시작 횟수</dt>
          <dd>{{ job.attempts + 1 }} · 끊김 뒤 다시 꺼냄</dd>
        </template>
        <template v-if="job.cancel_requested && job.status !== 'canceled'">
          <dt class="text-muted-foreground">멈추기</dt>
          <dd>눌림 · 이번 묶음 뒤 멈춤</dd>
        </template>
      </dl>

      <section>
        <div :class="FIELD_LABEL_CLASS">단계</div>
        <ol class="grid">
          <li
            v-for="(step, index) in timeline"
            :key="step.key"
            class="relative grid grid-cols-[18px_minmax(0,1fr)_auto] items-start gap-2.5 pb-3 text-ui"
            :data-step-state="step.state"
          >
            <span
              v-if="index < timeline.length - 1"
              class="absolute top-[18px] bottom-0 left-[8.5px] w-px bg-border-strong"
              aria-hidden="true"
            />
            <span class="relative grid size-[18px] place-items-center rounded-full bg-card">
              <CircleCheck v-if="step.state === 'done'" class="size-4 text-success" />
              <CircleX v-else-if="step.state === 'failed'" class="size-4 text-danger" />
              <CircleSlash v-else-if="step.state === 'canceled'" class="size-4 text-muted-foreground" />
              <LoaderCircle v-else-if="step.state === 'running'" class="size-4 animate-spin text-primary" />
              <CircleDashed v-else class="size-4 text-subtle-foreground" />
            </span>
            <div class="min-w-0 space-y-1.5">
              <div :class="step.state === 'waiting' ? 'text-subtle-foreground' : ''">
                <b v-if="step.state !== 'done' && step.state !== 'waiting'" class="font-semibold">{{ step.name }}</b>
                <template v-else>{{ step.name }}</template>
                <span v-if="step.amount" class="ml-1.5 text-muted-foreground tabular-nums">{{ step.amount }}</span>
              </div>
              <div v-if="step.ratio !== null" class="h-1.5 overflow-hidden rounded-full bg-muted">
                <i
                  class="block h-full rounded-full"
                  :class="step.state === 'failed' ? 'bg-danger' : step.state === 'canceled' ? 'bg-subtle-foreground' : 'bg-primary'"
                  :style="{ width: `${Math.round(step.ratio * 100)}%` }"
                />
              </div>
            </div>
            <span class="text-right text-meta leading-tight text-muted-foreground tabular-nums">
              {{ step.from || '—' }}<template v-if="step.to"><br />→ {{ step.to }}</template>
            </span>
          </li>
        </ol>
      </section>

      <section v-if="job.error">
        <div :class="FIELD_LABEL_CLASS">까닭</div>
        <p class="rounded-lg border border-l-[3px] border-l-danger bg-muted px-3 py-2 text-ui break-words" data-job-error>
          {{ job.error }}
        </p>
      </section>

      <section v-if="retries.length">
        <div :class="FIELD_LABEL_CLASS">다시 보내기<span class="count-pill">{{ retryCount }}</span></div>
        <ul class="grid grid-cols-[64px_minmax(0,1fr)] gap-x-2.5 gap-y-1 text-ui" data-retries>
          <template v-for="retry in retries" :key="retry.key">
            <li class="text-muted-foreground tabular-nums">{{ clockText(retry.at) }}</li>
            <li>
              {{ ROLE_NAMES[retry.role] ?? retry.role }} · {{ retry.attempt }}번째 실패 · {{ retry.reason }}
              <span v-if="retry.count > 1" class="ml-1 text-muted-foreground tabular-nums">×{{ retry.count }}</span>
            </li>
          </template>
        </ul>
      </section>

      <section v-if="connection">
        <div :class="FIELD_LABEL_CLASS">쓴 연결</div>
        <dl class="grid grid-cols-[84px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-ui">
          <dt class="text-muted-foreground">{{ ROLE_NAMES[connection.role] ?? connection.role }}</dt>
          <dd class="min-w-0 break-all">
            <span class="font-mono text-meta">{{ connection.base_url }}</span>
            <span v-if="connection.model" class="text-muted-foreground"> · {{ connection.model }}</span>
          </dd>
          <dt class="text-muted-foreground">지금</dt>
          <dd class="flex items-center gap-1.5">
            <span
              class="size-1.5 shrink-0 rounded-full"
              :class="connectionNow?.ok ? 'bg-success' : connectionNow ? 'bg-danger' : 'ring-[1.5px] ring-subtle-foreground ring-inset'"
            />
            {{ connectionNow?.detail ?? '모름' }}
          </dd>
        </dl>
      </section>

      <div class="flex flex-wrap gap-1.5">
        <Button v-if="retryable" type="button" size="sm" data-action="job-retry" @click="emit('retry')">
          <RotateCcw />다시 하기
        </Button>
        <Button v-if="datasetLink" variant="outline" size="sm" as-child>
          <RouterLink :to="datasetLink"><SquareArrowOutUpRight />데이터셋</RouterLink>
        </Button>
        <Button type="button" variant="quiet" size="sm" @click="copyRecord">
          <Copy />{{ copied ? '복사함' : '기록 복사' }}
        </Button>
      </div>
    </div>
  </aside>
</template>
