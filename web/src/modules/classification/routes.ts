// 분류 모듈의 화면 주소. 모두 /classification으로 시작한다(#/classification/...).
//   #/classification/new               새 데이터셋 가져오기. ?source=file 또는 ?source=huggingface로 가져올 곳을 미리 고른다.
//   #/classification/<id>              데이터셋 화면. 첫 탭인 진단으로 보낸다.
//   #/classification/<id>/add          데이터 추가: 가져오기 네 단계로 이 데이터셋에 더한다(ImportPage).
//   #/classification/<id>/diagnosis    진단 탭
//   #/classification/<id>/data         데이터 탭. 거르기 · 쪽 · 연 문장은 주소 뒤 ?에 둔다(data/filters.ts).
//   #/classification/<id>/suggestions  제안 탭
//   #/classification/<id>/labels       라벨 탭
//   #/classification/<id>/trash        휴지통 탭. 쪽은 ?page=
//   #/classification/<id>/imports      가져온 기록 탭
// 번호가 숫자가 아니면 '없는 화면'으로 간다.
import type { RouteRecordRaw } from 'vue-router'

import DataTab from '@/modules/classification/pages/DataTab.vue'
import DatasetLayout from '@/modules/classification/pages/DatasetLayout.vue'
import DiagnosisTab from '@/modules/classification/pages/DiagnosisTab.vue'
import ImportPage from '@/modules/classification/pages/ImportPage.vue'
import ImportsTab from '@/modules/classification/pages/ImportsTab.vue'
import LabelsTab from '@/modules/classification/pages/LabelsTab.vue'
import SuggestionsTab from '@/modules/classification/pages/SuggestionsTab.vue'
import TrashTab from '@/modules/classification/pages/TrashTab.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/classification/new', name: 'classification-new', component: ImportPage },
  { path: '/classification/:datasetId(\\d+)/add', name: 'classification-add', component: ImportPage },
  {
    path: '/classification/:datasetId(\\d+)',
    component: DatasetLayout,
    children: [
      { path: '', name: 'classification-dataset', redirect: { name: 'classification-diagnosis' } },
      { path: 'diagnosis', name: 'classification-diagnosis', component: DiagnosisTab },
      { path: 'data', name: 'classification-data', component: DataTab },
      { path: 'suggestions', name: 'classification-suggestions', component: SuggestionsTab },
      { path: 'labels', name: 'classification-labels', component: LabelsTab },
      { path: 'trash', name: 'classification-trash', component: TrashTab },
      { path: 'imports', name: 'classification-imports', component: ImportsTab },
    ],
  },
]
