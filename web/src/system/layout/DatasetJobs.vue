<!--
  데이터셋 머리의 작업 줄: 이 데이터셋에서 도는 작업과 기다리는 수. 세 모듈의 데이터셋 화면이 같이 쓴다.
    [⟳ 뜻 분석 · 임베딩 42% · 약 3분]  [대기 2]
  진행은 서버에서 읽으므로(useDatasetJobs) 다른 데이터셋을 보다 돌아와도 그때의 진행이 보인다.
  없으면 아무것도 보이지 않는다. 누르면 작업 기록(이 데이터셋의 도는 중 · 대기)을 연다.
  도던 작업이 끝나면 finished를 알린다(데이터셋 머리가 수를 다시 읽게).
-->
<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue'
import { computed, watch } from 'vue'

import { useDatasetJobs } from '@/system/datasetJobs'
import { fmt } from '@/system/format'
import { currentPhase, findJobKind, jobKindName, phaseName } from '@/system/jobKinds'
import { jobProgress, jobRemainingSeconds, remainingText } from '@/system/jobs'
import { useModules } from '@/system/module'

const props = defineProps<{
  datasetId: number
}>()

const emit = defineEmits<{
  // 이 데이터셋에서 도던 작업이 끝났다
  finished: []
}>()

// 100% (진행)
const PERCENT = 100

// 칩 모양 (데이터셋 머리의 다른 태그와 같다)
const CHIP_CLASS =
  'inline-flex h-5 max-w-full items-center gap-1 rounded-md bg-muted px-1.5 text-meta font-semibold text-muted-foreground hover:text-foreground'

// 기다리는 까닭 (작업의 waiting)
const WAITING_TITLES = { dataset: '같은 데이터셋의 앞 작업을 기다림', worker: '빈 작업 실행기를 기다림' }

const modules = useModules()
const { running, queued } = useDatasetJobs(() => props.datasetId)

const runningText = computed(() => {
  const job = running.value
  if (!job) return ''
  const kind = findJobKind(modules, job)
  const phase = currentPhase(job)
  const fraction = jobProgress(job)
  const seconds = jobRemainingSeconds(job)
  return [
    jobKindName(modules, job),
    phase ? phaseName(kind, phase) : null,
    fraction === null ? null : `${Math.floor(fraction * PERCENT)}%`,
    seconds === null ? null : remainingText(seconds),
  ]
    .filter(Boolean)
    .join(' · ')
})

const waitingTitle = computed(() => {
  const first = queued.value[0]
  const names = queued.value.map((job) => jobKindName(modules, job)).join(' · ')
  return first?.waiting ? `${WAITING_TITLES[first.waiting]} · ${names}` : names
})

// 도던 작업이 없어지거나 다른 작업으로 바뀌면 앞의 것이 끝난 것이다.
watch(
  () => running.value?.id ?? null,
  (_id, previous) => {
    if (previous !== null && previous !== undefined) emit('finished')
  },
)

const jobsLink = computed(() => ({ name: 'jobs', query: { dataset: String(props.datasetId), show: 'active' } }))
</script>

<template>
  <span v-if="running || queued.length" class="inline-flex min-w-0 items-center gap-1.5" data-part="dataset-jobs">
    <RouterLink v-if="running" :to="jobsLink" :class="CHIP_CLASS" :title="runningText" data-state="running">
      <LoaderCircle class="size-3 shrink-0 animate-spin text-primary motion-reduce:animate-none" />
      <span class="truncate">{{ runningText }}</span>
    </RouterLink>
    <RouterLink v-if="queued.length" :to="jobsLink" :class="CHIP_CLASS" :title="waitingTitle" data-state="queued">
      대기 {{ fmt(queued.length) }}
    </RouterLink>
  </span>
</template>
