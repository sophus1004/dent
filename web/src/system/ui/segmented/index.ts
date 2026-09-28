// 몇 가지 값 가운데 하나를 고르는 붙은 버튼 줄 (도우미 실행 설정의 청크 크기 · 오버랩 …).
export { default as Segmented } from "./Segmented.vue"

// 고를 값 하나: 값 · 보이는 이름
export interface SegmentedOption<T extends string | number> {
  value: T
  label: string
}
