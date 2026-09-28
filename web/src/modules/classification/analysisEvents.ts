// 뜻 분석 작업이 끝났음을 데이터셋 화면에 알린다. 분석 결과를 쓰는 곳(제안 탭 이름 옆 대기 수)이 이 값을 보고 다시 읽는다.
// 작업이 끝났다는 소식은 system의 작업 살피기(jobFeed)가 모듈의 onJobFinished로 준다(index.ts).
import { ref } from 'vue'

// 끝날 때마다 1씩 는다
export const analysisFinished = ref(0)

/** 뜻 분석 작업 하나가 끝났다(다 만듦 · 실패 · 취소). */
export function notifyAnalysisFinished(): void {
  analysisFinished.value += 1
}
