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

// 한 번에 읽는 끝난 작업 수와, 한 번 물을 때 넘겨 읽는 최대 쪽 수
const ANNOUNCE_LIMIT = 50
const MAX_PAGES = 10

// 이미 본 끝난 시각보다 이만큼 앞부터 다시 묻는다(밀리초). 작업 실행기 여럿이 함께 돌면, 먼저 끝난 시각을 적은 작업이
// 뒤에 커밋되어 늦게 보일 수 있다(끝난 시각은 처리 함수가 적고 커밋은 그 뒤에 한다). 본 작업은 번호로 거른다.
const LOOKBACK_MS = 30_000

// 확인 안 한 실패 수
export const unseenFailures = ref(0)

let timer: ReturnType<typeof setInterval> | undefined
// 앱을 켠 때. 이보다 먼저 끝난 작업은 알리지 않는다.
const startedAt = Date.now()
// 받은 작업의 가장 늦은 끝난 시각(밀리초). 다음에는 이보다 LOOKBACK_MS 앞부터 묻는다.
let checkedUntil = startedAt
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
    const since = new Date(Math.max(startedAt, checkedUntil - LOOKBACK_MS)).toISOString()
    // finished_after를 주면 끝난 순서(먼저 끝난 것부터)로 온다. 한 쪽이 가득 차면 다음 쪽까지 넘겨 읽는다.
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const finished = await listJobs({ finished_after: since, limit: ANNOUNCE_LIMIT, offset: page * ANNOUNCE_LIMIT })
      for (const job of finished.items) {
        // 서버 시각(마이크로초)과 브라우저 시각(밀리초)은 글자 길이가 달라 날짜 값으로 견준다.
        const finishedAt = job.finished_at ? Date.parse(job.finished_at) : NaN
        if (!Number.isNaN(finishedAt)) checkedUntil = Math.max(checkedUntil, finishedAt)
        if (announced.has(job.id)) continue
        announced.add(job.id)
        announce(job)
      }
      if (finished.items.length < ANNOUNCE_LIMIT) break
    }
    const failures = await listJobs({ status: ['failed'], finished_after: seenAt(), limit: 1 })
    unseenFailures.value = failures.total
  } catch {
    // 서버가 잠깐 안 되면 다음에 다시 묻는다. 서버 상태는 사이드바 아래 상태 줄이 알린다.
  }
}
