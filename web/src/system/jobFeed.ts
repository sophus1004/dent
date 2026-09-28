// 작업 결과 살피기: 5초마다 새로 끝난 작업을 물어 알린다(announce). 사이드바 '작업 기록'에 확인 안 한 실패 수를 센다.
// 앱을 켜기 전에 끝난 작업은 알리지 않는다. 확인한 시각은 이 브라우저에만 적는다(localStorage).
// 처음 켜면 최근 24시간의 실패를 확인 안 한 것으로 센다. 작업 기록 화면을 열면 markJobsSeen으로 비운다.
import { ref } from 'vue'

import { listJobs } from '@/system/api'
import type { JobRead } from '@/system/types'

// 다시 묻는 간격(밀리초)
const POLL_INTERVAL_MS = 5000

// 확인한 시각을 적는 열쇠
const SEEN_KEY = 'dent-jobs-seen-at'

// 확인한 적이 없으면 이만큼 앞부터의 실패를 센다(밀리초, 24시간)
const FIRST_SEEN_WINDOW_MS = 24 * 60 * 60 * 1000

// 한 번에 알릴 수 있는 끝난 작업 수
const ANNOUNCE_LIMIT = 50

// 확인 안 한 실패 수
export const unseenFailures = ref(0)

let timer: ReturnType<typeof setInterval> | undefined
// 이 시각 뒤에 끝난 작업만 알린다. 처음은 앱을 켠 때, 그 뒤로는 받은 작업의 가장 늦은 끝난 시각
let checkedUntil = new Date().toISOString()
const announced = new Set<number>()

/** 살피기를 시작한다. 앱이 켜질 때 한 번 부른다. */
export function startJobFeed(announce: (job: JobRead) => void): void {
  if (timer) return
  void poll(announce)
  timer = setInterval(() => void poll(announce), POLL_INTERVAL_MS)
}

/** 작업 기록을 봤다: 지금까지의 실패를 확인한 것으로 둔다. */
export function markJobsSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, new Date().toISOString())
  } catch {
    // 저장소를 못 쓰면 이 화면을 여는 동안만 비운다.
  }
  unseenFailures.value = 0
}

function seenAt(): string {
  try {
    const saved = localStorage.getItem(SEEN_KEY)
    if (saved) return saved
  } catch {
    // 저장소를 못 쓰면 처음과 같이 센다.
  }
  return new Date(Date.now() - FIRST_SEEN_WINDOW_MS).toISOString()
}

async function poll(announce: (job: JobRead) => void): Promise<void> {
  try {
    const finished = await listJobs({ finished_after: checkedUntil, limit: ANNOUNCE_LIMIT })
    // 최근 것부터 오므로 뒤집어 끝난 순서대로 알린다.
    for (const job of [...finished.items].reverse()) {
      if (announced.has(job.id)) continue
      announced.add(job.id)
      announce(job)
      // 서버 시각(마이크로초)과 브라우저 시각(밀리초)은 글자 길이가 달라 날짜 값으로 견준다.
      const isLater = job.finished_at !== null && Date.parse(job.finished_at) > Date.parse(checkedUntil)
      if (isLater && job.finished_at) checkedUntil = job.finished_at
    }
    const failures = await listJobs({ status: ['failed'], finished_after: seenAt(), limit: 1 })
    unseenFailures.value = failures.total
  } catch {
    // 서버가 잠깐 안 되면 다음에 다시 묻는다. 서버 상태는 사이드바 아래 상태 줄이 알린다.
  }
}
