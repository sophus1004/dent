// 작업 실행기 설정 대화상자를 여닫는 곳. 대화상자는 App.vue에 하나만 있고, 어디서든 openWorkerSettings()로 연다
// (홈의 시스템 칸 · 상태 화면의 작업 실행기 줄). 번호마다 상태 낱말과 색도 여기서 정한다.
import { ref } from 'vue'

import type { WorkerSlotState } from '@/system/types'

// 대화상자가 열려 있는지
export const isWorkerSettingsOpen = ref(false)

/** 작업 실행기 설정 대화상자를 연다. */
export function openWorkerSettings(): void {
  isWorkerSettingsOpen.value = true
}

// 번호마다 상태: 낱말과 글자색
export const SLOT_STATES: Record<WorkerSlotState, { word: string; class: string }> = {
  running: { word: '실행 중', class: 'text-success-ink' },
  starting: { word: '띄우는 중', class: 'text-muted-foreground' },
  draining: { word: '끝나면 종료', class: 'text-warning-ink' },
  restarting: { word: '다시 띄우는 중', class: 'text-warning-ink' },
  failed: { word: '멈춤', class: 'text-danger-ink' },
  stopped: { word: '종료됨', class: 'text-muted-foreground' },
}
