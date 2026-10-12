// 생성 모듈의 화면 주소. 모두 /generation으로 시작한다(#/generation/...).
//   #/generation/new              새 데이터셋 가져오기. ?source=file 또는 ?source=huggingface로 가져올 곳을 미리 고른다.
//   #/generation/<id>             데이터셋 화면. 첫 탭인 진단으로 보낸다.
//   #/generation/<id>/add         데이터 추가: 가져오기 네 단계로 이 데이터셋에 더한다(ImportPage).
//   #/generation/<id>/diagnosis   진단 탭. ?stage=1|2 이면 그 단계 카드를 보인다.
//   #/generation/<id>/review      검토(진단 탭 아래): ?check=<검사>의 문제를 한 건씩 보고 결정한다.
//   #/generation/<id>/data        데이터 탭. 거르기 · 쪽 · 연 대화는 주소 뒤 ?에 둔다(data/filters.ts).
//   #/generation/<id>/trash       휴지통 탭
//   #/generation/<id>/imports     가져온 기록 탭
// 번호가 숫자가 아니면 '없는 화면'으로 간다.
import type { RouteRecordRaw } from 'vue-router'

import DataTab from '@/modules/generation/pages/DataTab.vue'
import DatasetLayout from '@/modules/generation/pages/DatasetLayout.vue'
import DiagnosisTab from '@/modules/generation/pages/DiagnosisTab.vue'
import ImportPage from '@/modules/generation/pages/ImportPage.vue'
import ImportsTab from '@/modules/generation/pages/ImportsTab.vue'
import ReviewPage from '@/modules/generation/pages/ReviewPage.vue'
import TrashTab from '@/modules/generation/pages/TrashTab.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/generation/new', name: 'generation-new', component: ImportPage },
  { path: '/generation/:datasetId(\\d+)/add', name: 'generation-add', component: ImportPage },
  {
    path: '/generation/:datasetId(\\d+)',
    component: DatasetLayout,
    children: [
      { path: '', name: 'generation-dataset', redirect: { name: 'generation-diagnosis' } },
      { path: 'diagnosis', name: 'generation-diagnosis', component: DiagnosisTab },
      { path: 'review', name: 'generation-review', component: ReviewPage },
      { path: 'data', name: 'generation-data', component: DataTab },
      { path: 'trash', name: 'generation-trash', component: TrashTab },
      { path: 'imports', name: 'generation-imports', component: ImportsTab },
    ],
  },
]
