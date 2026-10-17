// 화면 오른쪽 아래 알림(layout/Toaster.vue가 그린다). 작업이 끝나거나 실패하면 jobFeed가 넣는다.
// 실패는 닫을 때까지 두고, 나머지는 몇 초 뒤 저절로 닫는다. 한꺼번에 너무 많으면 오래된 완료 알림부터 뺀다.
import { ref } from 'vue'

// 알림의 버튼 하나
export interface ToastAction {
  // 짧은 버튼 이름. 예: '보기'
  label: string
  run: () => void | Promise<void>
  // 주 버튼(보라)인지
  primary?: boolean
}

export interface Toast {
  id: number
  // fail: 빨강, 닫을 때까지 · done: 초록, 저절로 닫힘
  tone: 'fail' | 'done'
  // 예: '실패 · 의미 지도'
  title: string
  // 예: 'fancyzhx/ag_news · 임베딩 60,416 / 82,973'
  detail: string
  actions: ToastAction[]
}

// 저절로 닫히기까지(밀리초)
export const AUTO_CLOSE_MS = 5000

// 한꺼번에 보일 최대 알림 수
const MAX_TOASTS = 4

export const toasts = ref<Toast[]>([])
let nextId = 1

/**
 * 알림을 띄운다. 실패가 아니면 몇 초 뒤 닫는다. 너무 많으면 오래된 완료 알림부터 뺀다(실패는 닫을 때까지 남긴다:
 * 작업 여럿이 함께 끝나 완료 알림이 몰려도 실패가 밀려나지 않게).
 */
export function pushToast(toast: Omit<Toast, 'id'>): void {
  const id = nextId++
  const next = [...toasts.value, { ...toast, id }]
  while (next.length > MAX_TOASTS) {
    const oldestDone = next.findIndex((item) => item.tone !== 'fail')
    next.splice(oldestDone >= 0 ? oldestDone : 0, 1)
  }
  toasts.value = next
  if (toast.tone !== 'fail') setTimeout(() => closeToast(id), AUTO_CLOSE_MS)
}

export function closeToast(id: number): void {
  toasts.value = toasts.value.filter((toast) => toast.id !== id)
}
