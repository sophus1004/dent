<!--
  내보낸 파일 목록: 모듈의 내보내기 창이 [파일 만들기] 뒤에 보인다(모든 모듈이 같이 쓴다).
    파일마다  이름 · 대기(작업 실행기가 앞 작업을 하는 중) 또는 단계 n / m · 남은 시간 · 진행 막대
              → 끝나면 크기 · [내려받기], 실패면 까닭.
  단계 이름은 모듈이 jobKinds로 준다. 남은 시간은 그 단계가 시작한 뒤 처리한 속도로 어림한다(system/jobs.ts).
  1초마다 내보내기 기록과 그 작업(/api/v1/jobs/{id})을 다시 읽고, 모두 끝나면 멈춘다. 창을 닫으면(이 부품이 사라지면) 묻기를 멈춘다.
-->
<script setup lang="ts">
import { Download, LoaderCircle } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { exportFileUrl, getExport, getJob } from '@/system/api'
import { fileSize } from '@/system/format'
import { findJobKind, progressText } from '@/system/jobKinds'
import { jobProgress, jobRemainingSeconds, remainingText } from '@/system/jobs'
import { useModules } from '@/system/module'
import type { ExportRead, JobRead } from '@/system/types'
import { Button } from '@/system/ui/button'
import { Progress } from '@/system/ui/progress'

const props = defineProps<{
  // [파일 만들기]가 돌려준 내보내기들 (만든 순서 = 작업 실행기가 처리할 순서)
  items: ExportRead[]
}>()

// 다시 읽는 간격(밀리초)
const POLL_INTERVAL_MS = 1000

// 파일 하나의 진행
interface FileProgress {
  // 작업 실행기가 아직 꺼내지 않았는지
  waiting: boolean
  // 단계 n / m (모르면 '만드는 중')
  text: string
  // 0~1. 모르면 null
  ratio: number | null
  // 남은 시간 글자. 어림할 수 없으면 null
  remaining: string | null
}

const modules = useModules()
const files = ref<ExportRead[]>([...props.items])
const jobs = ref<Record<number, JobRead>>({})
let timer: ReturnType<typeof setTimeout> | undefined

const allFinished = computed(() => files.value.every(isFinished))

onMounted(() => void poll())
onBeforeUnmount(() => clearTimeout(timer))

function isFinished(item: ExportRead): boolean {
  return item.status === 'done' || item.status === 'failed'
}

async function poll(): Promise<void> {
  try {
    files.value = await Promise.all(files.value.map((item) => (isFinished(item) ? item : getExport(item.id))))
    const running = files.value.filter((item) => !isFinished(item) && item.job_id !== null)
    const read = await Promise.all(running.map((item) => getJob(item.job_id as number)))
    jobs.value = { ...jobs.value, ...Object.fromEntries(read.map((job) => [job.id, job])) }
  } catch {
    // 잠깐 끊겼으면 다음에 다시 묻는다.
  }
  if (!allFinished.value) timer = setTimeout(() => void poll(), POLL_INTERVAL_MS)
}

function progressOf(item: ExportRead): FileProgress {
  const job = item.job_id !== null ? jobs.value[item.job_id] : undefined
  const isWaiting = !job || job.status === 'queued'
  if (isWaiting) return { waiting: true, text: '대기', ratio: null, remaining: null }
  const seconds = jobRemainingSeconds(job)
  return {
    waiting: false,
    text: progressText(findJobKind(modules, job), job) || '만드는 중',
    ratio: jobProgress(job),
    remaining: seconds === null ? null : remainingText(seconds),
  }
}
</script>

<template>
  <div class="divide-y">
    <div v-for="item in files" :key="item.id" class="grid gap-2 px-[18px] py-3 text-ui" :data-export="item.id">
      <div class="flex items-center gap-3">
        <b class="min-w-0 flex-1 truncate font-semibold">{{ item.file_name }}</b>
        <template v-if="item.status === 'done'">
          <span class="text-muted-foreground">{{ item.size_bytes !== null ? fileSize(item.size_bytes) : '' }}</span>
          <Button size="sm" as-child>
            <a :href="exportFileUrl(item.id)" download data-action="download-export"><Download />내려받기</a>
          </Button>
        </template>
        <span v-else-if="item.status === 'failed'" class="font-semibold text-danger-ink" :title="item.error ?? ''">
          실패 · {{ item.error }}
        </span>
        <span v-else-if="progressOf(item).waiting" class="text-muted-foreground" data-export-state="waiting">대기</span>
        <span v-else class="inline-flex items-center gap-1.5 text-muted-foreground" data-export-state="running">
          <LoaderCircle class="size-3.5 animate-spin" />{{ progressOf(item).text }}
          <template v-if="progressOf(item).remaining">· {{ progressOf(item).remaining }}</template>
        </span>
      </div>
      <Progress
        v-if="!isFinished(item) && !progressOf(item).waiting"
        :value="progressOf(item).ratio"
        :label="`${item.file_name} 진행률`"
      />
    </div>
  </div>
</template>
