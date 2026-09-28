// 검색 도우미를 시스템의 도우미 창(HelperPanel)에 잇는 것: 검색 API(시작 · 되돌리기 · 허락 · 남은 것 · AI로 고치기)와
// 보류 카드의 [정하기] · 남은 것 줄의 → 주소, 실행 설정 카드(HelperSettings), 처음 화면에 보일 한 번 실행의 단계
// (백엔드 retrieval/helper.initial_steps와 같은 순서. 허락 단계에는 설정 카드가 알려 주는 값을 옆에 보인다).
import type { HelperApi, PlanStep } from '@/system/helper/useHelper'

import {
  answerHelperPermission,
  fixHelper,
  getHelper,
  getHelperLeft,
  startHelper,
  undoHelper,
} from '@/modules/retrieval/api'
import HelperSettings from '@/modules/retrieval/HelperSettings.vue'

export const retrievalHelperApi: HelperApi = {
  getHelper,
  startHelper,
  undoHelper,
  answerPermission: answerHelperPermission,
  getLeft: getHelperLeft,
  startFix: (datasetId, keys) => fixHelper(datasetId, keys),
  settingsCard: HelperSettings,
  holdRoute: (datasetId, text) => ({ name: 'retrieval-data', params: { datasetId }, query: { q: text } }),
  // 진단의 검사 줄 → 와 같은 곳: 반복 구간은 고르기 창, 제안은 제안 탭, 나머지는 데이터 탭의 문제 거르기
  leftRoute: (datasetId, item) => {
    if (item.key === 'repeat') return { name: 'retrieval-diagnosis', params: { datasetId }, query: { repeats: '1' } }
    if (item.view_target === 'suggestions') {
      return { name: 'retrieval-suggestions', params: { datasetId }, query: { kind: item.view_problem || undefined } }
    }
    if (item.view_target) {
      return {
        name: 'retrieval-data',
        params: { datasetId },
        query: {
          view: item.view_target,
          problem: item.view_problem || undefined,
          status: item.view_target === 'queries' ? 'included' : undefined,
        },
      }
    }
    return { name: 'retrieval-diagnosis', params: { datasetId } }
  },
}

export const RETRIEVAL_PLAN_STEPS: PlanStep[] = [
  { title: '계획' },
  { title: '문서 정리' },
  { title: '문서 나누기 · 허락', permission: true, key: 'chunk' },
  { title: '청크 고르기' },
  { title: '질의 만들기 · 허락', permission: true, key: 'generate' },
  { title: '질의 정리' },
  { title: '정답 확인' },
  { title: '오답 찾기' },
  { title: '거짓 오답 확인' },
  { title: '보고' },
]
