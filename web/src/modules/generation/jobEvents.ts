// 생성 작업(가져오기 · 내보내기 · 뜻 분석 · 길이 · 오염 대조)이 끝났음을 데이터셋 화면에 알린다. 결과를 쓰는 곳이 자기
// 데이터셋의 값을 보고 다시 읽는다(작업 실행기 여럿이 다른 데이터셋의 작업을 함께 돌리므로 데이터셋마다 센다).
// 작업이 끝났다는 소식은 system의 작업 살피기(jobFeed)가 모듈의 onJobFinished로 준다(index.ts).
import { reactive } from 'vue'

import type { JobRead } from '@/system/types'

// 데이터셋 번호 → 작업이 끝난 수 (끝날 때마다 1씩 는다)
const finished = reactive(new Map<number, number>())

/** 이 데이터셋의 작업이 끝난 수. watch(() => jobFinished(id), …)로 본다. */
export function jobFinished(datasetId: number): number {
  return finished.get(datasetId) ?? 0
}

/** 생성 작업 하나가 끝났다(완료 · 실패 · 멈춤). */
export function notifyJobFinished(job: Pick<JobRead, 'dataset_id'>): void {
  if (job.dataset_id !== null) finished.set(job.dataset_id, jobFinished(job.dataset_id) + 1)
}
