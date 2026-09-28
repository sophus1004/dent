// 검색 작업(뜻 분석 · 질의 만들기 · 오답 찾기 · 내보내기)이 끝났음을 데이터셋 화면에 알린다.
// 결과를 쓰는 곳(진단 · 제안 탭 이름 옆 대기 수 · 데이터 탭)이 이 값을 보고 다시 읽는다.
// 작업이 끝났다는 소식은 system의 작업 살피기(jobFeed)가 모듈의 onJobFinished로 준다(index.ts).
import { ref } from 'vue'

// 끝날 때마다 1씩 는다
export const jobFinished = ref(0)

/** 검색 작업 하나가 끝났다(완료 · 실패 · 멈춤). */
export function notifyJobFinished(): void {
  jobFinished.value += 1
}

/** 작업 밖에서 데이터가 바뀌었다(반복 구간 고르기). 작업이 끝났을 때와 같은 곳이 다시 읽는다. */
export function notifyDataChanged(): void {
  jobFinished.value += 1
}
