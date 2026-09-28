<!--
  작업 상태 태그: 실패(빨강) · 완료(초록) · 도는 중(보라) · 대기 · 멈춤(회색). 작업 기록 표와 패널이 쓴다.
-->
<script setup lang="ts">
import { computed } from 'vue'

import { JOB_STATUS_NAMES } from '@/system/jobKinds'
import type { JobStatus } from '@/system/types'

const props = defineProps<{
  status: JobStatus
}>()

// 태그 모양 (연결 설정 대화상자의 태그와 같은 크기)
const BASE_CLASS = 'inline-flex h-5 shrink-0 items-center rounded-md px-[7px] text-meta font-semibold whitespace-nowrap'

const TONE_CLASS: Record<JobStatus, string> = {
  failed: 'text-danger-ink bg-danger/8 shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--danger)_55%,transparent)]',
  done: 'text-success-ink bg-success/8 shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--success)_50%,transparent)]',
  running: 'text-accent-foreground bg-accent shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--primary)_45%,transparent)]',
  queued: 'text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]',
  canceled: 'text-muted-foreground shadow-[inset_0_0_0_1px_var(--border-strong)]',
}

const tagClass = computed(() => [BASE_CLASS, TONE_CLASS[props.status]])
</script>

<template>
  <span :class="tagClass" :data-status="status">{{ JOB_STATUS_NAMES[status] }}</span>
</template>
