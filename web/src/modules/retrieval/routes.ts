// 검색 모듈의 화면 주소. 모두 /retrieval으로 시작한다(#/retrieval/...).
//   #/retrieval/new               새 데이터셋 가져오기. ?source=file 또는 ?source=huggingface로 가져올 곳을 미리 고른다.
//   #/retrieval/<id>              데이터셋 화면. 첫 탭인 진단으로 보낸다.
//   #/retrieval/<id>/add          데이터 추가: 가져오기 네 단계로 이 데이터셋에 더한다(ImportPage).
//   #/retrieval/<id>/diagnosis    진단 탭. ?stage=1|2|3 이면 그 단계 카드를 보인다.
//   #/retrieval/<id>/data         데이터 탭. 질의 · 문서 · 지도 보기와 거르기 · 쪽 · 연 줄은 주소 뒤 ?에 둔다(data/filters.ts).
//   #/retrieval/<id>/suggestions  제안 탭 (거짓 오답 · 빠진 정답 · 정답 의심)
//   #/retrieval/<id>/trash        휴지통 탭
//   #/retrieval/<id>/imports      가져온 기록 탭
// 번호가 숫자가 아니면 '없는 화면'으로 간다.
import type { RouteRecordRaw } from 'vue-router'

import DataTab from '@/modules/retrieval/pages/DataTab.vue'
import DatasetLayout from '@/modules/retrieval/pages/DatasetLayout.vue'
import DiagnosisTab from '@/modules/retrieval/pages/DiagnosisTab.vue'
import ImportPage from '@/modules/retrieval/pages/ImportPage.vue'
import ImportsTab from '@/modules/retrieval/pages/ImportsTab.vue'
import SuggestionsTab from '@/modules/retrieval/pages/SuggestionsTab.vue'
import TrashTab from '@/modules/retrieval/pages/TrashTab.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/retrieval/new', name: 'retrieval-new', component: ImportPage },
  { path: '/retrieval/:datasetId(\\d+)/add', name: 'retrieval-add', component: ImportPage },
  {
    path: '/retrieval/:datasetId(\\d+)',
    component: DatasetLayout,
    children: [
      { path: '', name: 'retrieval-dataset', redirect: { name: 'retrieval-diagnosis' } },
      { path: 'diagnosis', name: 'retrieval-diagnosis', component: DiagnosisTab },
      { path: 'data', name: 'retrieval-data', component: DataTab },
      { path: 'suggestions', name: 'retrieval-suggestions', component: SuggestionsTab },
      { path: 'trash', name: 'retrieval-trash', component: TrashTab },
      { path: 'imports', name: 'retrieval-imports', component: ImportsTab },
    ],
  },
]
