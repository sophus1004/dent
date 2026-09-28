// 분류 도우미를 시스템의 도우미 창(HelperPanel)에 잇는 것: 분류 API(시작 · 되돌리기 · 남은 것 · AI로 고치기)와
// 보류 카드의 [정하기] · 남은 것 줄의 → 주소, 처음 화면에 보일 한 번 실행의 단계(백엔드 classification/helper.initial_steps와 같은 순서).
// 새 문장은 실행 안의 허락 단계가 아니라 남은 것의 '라벨 균형' 줄이다(골라서 AI로 고친다). 목표 배율은 실행 설정 카드
// (HelperSettings), 라벨마다 더할 수는 그 줄의 표에서 고쳐 AI로 고치기에 함께 보낸다(adds).
import type { HelperApi, PlanStep } from '@/system/helper/useHelper'

import { fixHelper, getHelper, getHelperLeft, startHelper, undoHelper } from '@/modules/classification/api'
import HelperSettings from '@/modules/classification/HelperSettings.vue'
import type { RecordProblem } from '@/modules/classification/types'
import { dataQuery, dataRoute } from '@/modules/classification/diagnosis/checks'

export const classificationHelperApi: HelperApi = {
  getHelper,
  startHelper,
  undoHelper,
  getLeft: getHelperLeft,
  startFix: fixHelper,
  settingsCard: HelperSettings,
  holdRoute: (datasetId, text) => ({ name: 'classification-data', params: { datasetId }, query: { q: text } }),
  // 진단의 검사 줄 → 와 같은 곳: 오라벨 의심은 제안 탭, 라벨 균형은 라벨 탭, 의미 쏠림은 지도, 나머지는 데이터 탭의 문제 거르기
  leftRoute: (datasetId, item) => {
    if (item.key === 'suspect') return { name: 'classification-suggestions', params: { datasetId } }
    if (item.key === 'balance') return { name: 'classification-labels', params: { datasetId } }
    if (item.key === 'skew') {
      return { name: 'classification-data', params: { datasetId }, query: { ...dataQuery({}), view: 'map' } }
    }
    return dataRoute(datasetId, { problem: item.key as RecordProblem })
  },
}

export const CLASSIFICATION_PLAN_STEPS: PlanStep[] = [
  { title: '계획' },
  { title: '라벨 충돌' },
  { title: '오라벨 의심' },
  { title: '짧은 문장' },
  { title: '중복 여분' },
  { title: '근접 중복' },
  { title: '보고' },
]
