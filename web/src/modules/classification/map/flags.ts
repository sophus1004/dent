// 분류 의미 지도의 점 표시 비트와 문제로 칠하는 순서. 색은 시스템(@/system/map/palette)의 범주 색을 쓴다.

// 점의 표시 비트. 백엔드(semantic_map.py의 FLAG_*)와 같은 값이다(4는 비움).
export const FLAG = {
  duplicate: 1,
  conflict: 2,
  short: 8,
  excluded: 16,
  near_duplicate: 32,
  suspect: 64,
} as const

// 문제로 칠할 때 한 점에 문제가 여럿이면 앞의 것으로 칠한다(고치기 어려운 것부터).
export const PROBLEM_PRIORITY = ['conflict', 'suspect', 'near_duplicate', 'duplicate', 'short'] as const

// 문제마다 범주 색 자리. 데이터 탭 문제 거르기의 순서
// (중복 · 라벨 충돌 · 짧은 문장 · 근접 중복 · 오라벨 의심)와 같다.
export const PROBLEM_SLOTS: Record<(typeof PROBLEM_PRIORITY)[number], number> = {
  duplicate: 0,
  conflict: 1,
  short: 2,
  near_duplicate: 3,
  suspect: 4,
}
