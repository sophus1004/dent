// 생성 도우미를 시스템의 도우미 창(HelperPanel)에 잇는 것: 생성 API(시작 · 되돌리기 · 남은 것 · AI로 고치기)와
// 보류 카드의 [정하기] · 남은 것 줄의 → 주소, 처음 화면에 보일 한 번 실행의 단계(백엔드 generation/helper.initial_steps와 같은 순서).
// 실행 설정 카드는 없다(창은 [시작]만 보인다). 응답 다시 쓰기는 실행 안에서 하지 않고 남은 것에서 골라 AI로 고친다.
import type { HelperApi, PlanStep } from '@/system/helper/useHelper'

import { fixHelper, getHelper, getHelperLeft, startHelper, undoHelper } from '@/modules/generation/api'

export const generationHelperApi: HelperApi = {
  getHelper,
  startHelper,
  undoHelper,
  getLeft: getHelperLeft,
  startFix: (datasetId, keys) => fixHelper(datasetId, keys),
  holdRoute: (datasetId, text) => ({ name: 'generation-data', params: { datasetId }, query: { q: text } }),
  // 진단의 검사 줄 → 와 같은 곳: 검토 큐. 과제 쏠림은 의미 지도(과제로 칠함)
  leftRoute: (datasetId, item) => {
    if (item.key === 'task_skew') return { name: 'generation-data', params: { datasetId }, query: { view: 'map' } }
    return { name: 'generation-review', params: { datasetId }, query: { check: item.key } }
  },
}

export const GENERATION_PLAN_STEPS: PlanStep[] = [
  { title: '계획' },
  { title: '대화 구조' },
  { title: '빈 지시' },
  { title: '같은 지시' },
  { title: '개인정보' },
  { title: '근접 중복' },
  { title: '빈 응답' },
  { title: '반복 루프' },
  { title: '거절 · 상투구' },
  { title: '짧은 응답' },
  { title: '지시 불일치' },
  { title: '보고' },
]
