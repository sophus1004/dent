// 생성 도우미를 시스템의 도우미 창(HelperPanel)에 잇는 것: 생성 API(시작 · 되돌리기 · 허락 · 남은 것 · AI로 고치기)와
// 보류 카드의 [정하기] · 남은 것 줄의 → 주소, 실행 설정 카드(HelperSettings), 처음 화면에 보일 한 번 실행의 단계
// (백엔드 generation/helper.initial_steps와 같은 순서. 3 선호는 만들 데이터가 선호일 때만 서버의 단계 줄에 있다).
// 응답 다시 쓰기는 실행 안에서 하지 않고 남은 것에서 골라 AI로 고친다.
import type { HelperApi, PlanStep } from '@/system/helper/useHelper'

import {
  answerHelperPermission,
  fixHelper,
  getHelper,
  getHelperLeft,
  startHelper,
  undoHelper,
} from '@/modules/generation/api'
import HelperSettings from '@/modules/generation/HelperSettings.vue'

// 데이터셋의 모양이라 볼 대화가 따로 없는 검사 → 의미 지도 · 진단
const SHAPE_ROUTES: Record<string, string> = { task_skew: 'map', length_bias: 'diagnosis' }

export const generationHelperApi: HelperApi = {
  getHelper,
  startHelper,
  undoHelper,
  answerPermission: (runId, approve) => answerHelperPermission(runId, approve),
  getLeft: getHelperLeft,
  startFix: (datasetId, keys) => fixHelper(datasetId, keys),
  settingsCard: HelperSettings,
  holdRoute: (datasetId, text) => ({ name: 'generation-data', params: { datasetId }, query: { q: text } }),
  // 진단의 검사 줄 → 와 같은 곳: 검토 큐. 과제 쏠림은 의미 지도(과제로 칠함), 쌍 없음 · 길이 편향은 진단의 3 선호
  leftRoute: (datasetId, item) => {
    if (SHAPE_ROUTES[item.key] === 'map') return { name: 'generation-data', params: { datasetId }, query: { view: 'map' } }
    if (item.key === 'pairs' || SHAPE_ROUTES[item.key] === 'diagnosis') {
      return { name: 'generation-diagnosis', params: { datasetId }, query: { stage: '3' } }
    }
    return { name: 'generation-review', params: { datasetId }, query: { check: item.key } }
  },
}

// 한 번 실행의 단계 (선호면 3 선호 단계가 붙는다)
const SFT_STEPS: PlanStep[] = [
  { title: '계획' },
  { title: '대화 구조' },
  { title: '빈 지시' },
  { title: '같은 지시' },
  { title: '개인정보' },
  { title: '평가셋 오염' },
  { title: '근접 중복' },
  { title: '응답 만들기 · 허락', permission: true, key: 'no_answer' },
  { title: '도구 호출 형식' },
  { title: '빈 응답' },
  { title: '반복 루프' },
  { title: '거절 · 상투구' },
  { title: '짧은 응답' },
  { title: '지시 불일치' },
  { title: '안전' },
]
const PREFERENCE_STEPS: PlanStep[] = [
  { title: '비교 만들기 · 허락', permission: true, key: 'pairs' },
  { title: '같은 쌍 · 동점' },
  { title: '판정 반대' },
]

/** 처음 화면에 보일 한 번 실행의 단계. withPairs면(만들 데이터가 선호) 3 선호 단계를 붙인다. */
export function generationPlanSteps(withPairs: boolean): PlanStep[] {
  return [...SFT_STEPS, ...(withPairs ? PREFERENCE_STEPS : []), { title: '보고' }]
}
