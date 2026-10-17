// 뜻 분석 작업이 끝났음을 데이터셋 화면에 알린다. 분석 결과를 쓰는 곳(제안 탭 이름 옆 대기 수)이 자기 데이터셋의 값을
// 보고 다시 읽는다(작업 실행기 여럿이 다른 데이터셋의 분석을 함께 돌리므로 데이터셋마다 센다).
// 작업이 끝났다는 소식은 system의 작업 살피기(jobFeed)가 모듈의 onJobFinished로 준다(index.ts).
import { reactive } from 'vue'

import type { JobRead } from '@/system/types'

// 데이터셋 번호 → 뜻 분석이 끝난 수 (끝날 때마다 1씩 는다)
const finished = reactive(new Map<number, number>())

/** 이 데이터셋의 뜻 분석이 끝난 수. watch(() => analysisFinished(id), …)로 본다. */
export function analysisFinished(datasetId: number): number {
  return finished.get(datasetId) ?? 0
}

/** 뜻 분석 작업 하나가 끝났다(다 만듦 · 실패 · 취소). */
export function notifyAnalysisFinished(job: Pick<JobRead, 'dataset_id'>): void {
  if (job.dataset_id !== null) finished.set(job.dataset_id, analysisFinished(job.dataset_id) + 1)
}
