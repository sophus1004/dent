// 검색 모듈: 질의 · 문서 · 판정(정답 · 하드 네거티브)으로 된 임베딩 · 리랭커 학습 데이터. system에 이 모양(DentModule)으로 내놓는다.
import { Plus } from '@lucide/vue'

import type { DentModule } from '@/system/module'

import { mine, scanNegatives, scanRepeats, startAnalysis } from '@/modules/retrieval/api'
import { loadDatasets } from '@/modules/retrieval/datasets'
import HomeSection from '@/modules/retrieval/HomeSection.vue'
import { notifyJobFinished } from '@/modules/retrieval/jobEvents'
import { ANALYSIS_PHASE_NAMES } from '@/modules/retrieval/map/analysis'
import RecentCard from '@/modules/retrieval/RecentCard.vue'
import { routes } from '@/modules/retrieval/routes'
import SidebarSection from '@/modules/retrieval/SidebarSection.vue'

// 반복 구간 살피기의 단계 (repeats.py의 PHASE_*)
const SCAN_PHASE_NAMES = { sample: '표본', count: '반복 세기', refresh: '학습 글' }

const retrieval: DentModule = {
  id: 'retrieval',
  name: '검색',
  routes,
  SidebarSection,
  HomeSection,
  RecentCard,
  commands: [{ label: '검색 데이터셋 가져오기', icon: Plus, to: '/retrieval/new' }],
  jobKinds: {
    // 가져오기는 끝나면(실패해도) 원본을 지우므로 다시 할 수 없다. 새로 가져온다.
    // 줄을 다 넣으면 반복 구간을 살피고 학습 글을 만든다(scan과 같은 단계).
    import: { name: '가져오기', phases: SCAN_PHASE_NAMES },
    scan: {
      name: '반복 구간 살피기',
      phases: SCAN_PHASE_NAMES,
      retry: async (job) => {
        if (job.dataset_id !== null) await scanRepeats(job.dataset_id)
      },
    },
    analysis: {
      name: '뜻 분석',
      phases: ANALYSIS_PHASE_NAMES,
      // 다시 하면 캐시에 남은 임베딩은 건너뛰고 이어서 계산한다.
      retry: async (job) => {
        if (job.dataset_id !== null) await startAnalysis(job.dataset_id)
      },
    },
    // 도우미 실행(허락 뒤 이어 돌기 포함). 다시 하기는 도우미 창의 [다시 시작]으로 한다.
    helper: { name: '도우미' },
    mine: {
      name: '오답 찾기',
      phases: { embedding: '임베딩', ranking: '순위', judging: 'Jev 확인', saving: '저장' },
      retry: async (job) => {
        if (job.dataset_id !== null) await mine(job.dataset_id)
      },
    },
    negative_scan: {
      name: '오답 훑기',
      phases: { embedding: '임베딩', judging: 'Jev 확인' },
      retry: async (job) => {
        if (job.dataset_id !== null) await scanNegatives(job.dataset_id)
      },
    },
    export: { name: '내보내기', phases: { teacher: '교사 점수', writing: '쓰기' } },
  },
  // 가져오기가 끝나면 새 데이터셋 · 수가 사이드바와 홈에 보이게 목록을 다시 읽는다.
  // 그 밖의 작업이 끝나면 데이터셋 화면(진단 · 제안 대기 수 · 데이터)이 다시 읽게 알린다.
  onJobFinished: (job) => {
    if (job.kind === 'import') void loadDatasets()
    notifyJobFinished()
  },
}

export default retrieval
