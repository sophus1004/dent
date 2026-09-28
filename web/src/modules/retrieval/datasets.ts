// 검색 데이터셋 목록(GET /api/v1/retrieval/datasets). 사이드바와 홈이 이 목록 하나를 같이 본다.
import { ref } from 'vue'

import { listDatasets } from '@/modules/retrieval/api'
import type { DatasetSummaryRead } from '@/modules/retrieval/types'

// 읽은 목록. 최근에 고친 것부터. 아직 못 읽었으면 null
export const datasets = ref<DatasetSummaryRead[] | null>(null)

// 마지막으로 읽기에 실패했는지
export const loadFailed = ref(false)

/** 목록을 다시 읽는다. 실패하면 loadFailed를 켜고, 전에 읽은 목록은 그대로 둔다. */
export async function loadDatasets(): Promise<void> {
  try {
    datasets.value = await listDatasets()
    loadFailed.value = false
  } catch {
    loadFailed.value = true
  }
}
