// 검색 작업(뜻 분석 · 질의 만들기 · 오답 찾기 · 내보내기)이 끝났음을 데이터셋 화면에 알린다.
// 결과를 쓰는 곳(진단 · 제안 탭 이름 옆 대기 수 · 데이터 탭)이 자기 데이터셋의 값을 보고 다시 읽는다.
// 작업 실행기 여럿이 다른 데이터셋의 작업을 함께 돌리므로, 다른 데이터셋의 작업이 끝났다고 다시 읽지 않게 데이터셋마다 센다.
// 작업이 끝났다는 소식은 system의 작업 살피기(jobFeed)가 모듈의 onJobFinished로 준다(index.ts).
import { reactive } from 'vue'

import type { JobRead } from '@/system/types'

// 데이터셋 번호 → 작업이 끝나거나 데이터가 바뀐 수 (바뀔 때마다 1씩 는다)
const changes = reactive(new Map<number, number>())

/** 이 데이터셋의 작업이 끝나거나 데이터가 바뀐 수. watch(() => jobFinished(id), …)로 본다. */
export function jobFinished(datasetId: number): number {
  return changes.get(datasetId) ?? 0
}

/** 검색 작업 하나가 끝났다(완료 · 실패 · 멈춤). */
export function notifyJobFinished(job: Pick<JobRead, 'dataset_id'>): void {
  if (job.dataset_id !== null) bump(job.dataset_id)
}

/** 작업 밖에서 데이터가 바뀌었다(반복 구간 고르기). 작업이 끝났을 때와 같은 곳이 다시 읽는다. */
export function notifyDataChanged(datasetId: number): void {
  bump(datasetId)
}

function bump(datasetId: number): void {
  changes.set(datasetId, jobFinished(datasetId) + 1)
}
