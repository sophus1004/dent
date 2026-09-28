// 데이터셋 화면(DatasetLayout)이 탭에 내주는 함수: 머리 · 탭의 수(질의 · 문서 · 휴지통)와 사이드바 목록을 다시 읽는다.
// 탭에서 질의 · 문서 · 판정을 바꾼 뒤 부른다. 탭은 DatasetLayout을 모르고 이 파일만 안다.
import { inject, provide, type InjectionKey } from 'vue'

const REFRESH_DATASET: InjectionKey<() => Promise<void>> = Symbol('refreshRetrievalDataset')

/** DatasetLayout이 머리를 다시 읽는 함수를 내준다. */
export function provideDatasetRefresh(refresh: () => Promise<void>): void {
  provide(REFRESH_DATASET, refresh)
}

/** 탭이 머리를 다시 읽는 함수를 받는다. 데이터셋 화면 밖이면 아무 일도 하지 않는 함수. */
export function useDatasetRefresh(): () => Promise<void> {
  return inject(REFRESH_DATASET, () => Promise.resolve())
}
