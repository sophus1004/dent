// 생성 모듈: 지시 · 응답 대화로 된 LLM 학습 데이터(SFT · 선호 쌍 · 지시만 · 도구 호출). system에 이 모양(DentModule)으로 내놓는다.
import { Plus } from '@lucide/vue'

import type { DentModule } from '@/system/module'

import { startAnalysis } from '@/modules/generation/api'
import { loadDatasets } from '@/modules/generation/datasets'
import HomeSection from '@/modules/generation/HomeSection.vue'
import { notifyJobFinished } from '@/modules/generation/jobEvents'
import { ANALYSIS_PHASE_NAMES } from '@/modules/generation/map/analysis'
import RecentCard from '@/modules/generation/RecentCard.vue'
import { routes } from '@/modules/generation/routes'
import SidebarSection from '@/modules/generation/SidebarSection.vue'

const generation: DentModule = {
  id: 'generation',
  name: '생성',
  routes,
  SidebarSection,
  HomeSection,
  RecentCard,
  commands: [{ label: '생성 데이터셋 가져오기', icon: Plus, to: '/generation/new' }],
  jobKinds: {
    // 가져오기는 끝나면(실패해도) 원본을 지우므로 다시 할 수 없다. 새로 가져온다.
    import: { name: '가져오기' },
    analysis: {
      name: '뜻 분석',
      phases: ANALYSIS_PHASE_NAMES,
      // 다시 하면 캐시에 남은 임베딩은 건너뛰고 이어서 계산한다.
      retry: async (job) => {
        if (job.dataset_id !== null) await startAnalysis(job.dataset_id)
      },
    },
    // 도우미 실행(AI로 고치기 포함). 다시 하기는 도우미 창의 [다시 시작]으로 한다.
    helper: { name: '도우미' },
    export: { name: '내보내기', phases: { writing: '쓰기' } },
    // 학습할 모델 · 최대 길이를 바꾸면 토크나이저를 받아 모든 대화의 길이를 다시 잰다.
    length: { name: '길이 재기' },
    // 평가셋이 바뀌면 지시를 평가셋과 다시 대조한다(8-gram → 임베딩 → LLM 판정).
    contamination: { name: '오염 대조', phases: { ngram: '8-gram', embedding: '임베딩', judging: 'LLM 판정' } },
  },
  // 가져오기가 끝나면 새 데이터셋 · 수가 사이드바와 홈에 보이게 목록을 다시 읽는다.
  // 그 밖의 작업이 끝나면 데이터셋 화면(머리 · 진단)이 다시 읽게 알린다.
  onJobFinished: (job) => {
    if (job.kind === 'import') void loadDatasets()
    notifyJobFinished(job)
  },
}

export default generation
