// 분류 모듈: 문장 한 건에 라벨 하나를 단 학습 데이터. system에 이 모양(DentModule)으로 내놓는다.
import { Plus } from '@lucide/vue'

import type { DentModule } from '@/system/module'

import { notifyAnalysisFinished } from '@/modules/classification/analysisEvents'
import { startMap } from '@/modules/classification/api'
import { loadDatasets } from '@/modules/classification/datasets'
import HomeSection from '@/modules/classification/HomeSection.vue'
import { PHASE_NAMES } from '@/modules/classification/map/useSemanticMap'
import RecentCard from '@/modules/classification/RecentCard.vue'
import { routes } from '@/modules/classification/routes'
import SidebarSection from '@/modules/classification/SidebarSection.vue'

const classification: DentModule = {
  id: 'classification',
  name: '분류',
  routes,
  SidebarSection,
  HomeSection,
  RecentCard,
  commands: [{ label: '새 데이터셋 가져오기', icon: Plus, to: '/classification/new' }],
  jobKinds: {
    // 가져오기는 끝나면(실패해도) 원본을 지우므로 다시 할 수 없다. 새로 가져온다.
    import: { name: '가져오기' },
    // 도우미 실행 · 허락 뒤 새 문장 만들기. 다시 하기는 도우미 창의 [다시 시작]으로 한다.
    helper: { name: '도우미' },
    helper_generate: { name: '도우미 새 문장' },
    // 내보내기는 창에서 다시 만든다.
    export: { name: '내보내기', phases: { writing: '쓰기' } },
    map: {
      name: '뜻 분석',
      phases: PHASE_NAMES,
      // 다시 하면 캐시에 남은 임베딩은 건너뛰고 이어서 계산한다.
      retry: async (job) => {
        if (job.dataset_id !== null) await startMap(job.dataset_id)
      },
    },
  },
  // 가져오기가 끝나면 새 데이터셋 · 건수가 사이드바와 홈에 보이게 목록을 다시 읽는다.
  // 뜻 분석이 끝나면 제안 탭 이름 옆 대기 수를 다시 읽게 알린다.
  onJobFinished: (job) => {
    if (job.kind === 'import') void loadDatasets()
    if (job.kind === 'map') notifyAnalysisFinished()
  },
}

export default classification
