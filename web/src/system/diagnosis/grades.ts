// 진단의 바탕 (모든 모듈이 같이 쓴다): 등급(심각 · 주의 · 통과)의 이름 · 아이콘 · 색, 검사 한 줄의 모양, 종합 상태.
// 검사마다의 낱말(이름 · 정의 · 영향 · 조치)과 값은 모듈이 만든다. 검사 표(CheckTable) · 검사 카드(CheckGroup)가 이 모양을 그린다.
import { CircleCheck, OctagonAlert, TriangleAlert, type LucideIcon } from '@lucide/vue'
import type { RouteLocationRaw } from 'vue-router'

// 진단 등급: 통과 · 주의 · 심각
export type Grade = 'good' | 'warn' | 'bad'

// 등급 하나의 이름 · 아이콘 · 색. rank가 작을수록 심각하다.
export interface GradeWords {
  name: string
  icon: LucideIcon
  rank: number
  // 글자 색
  textClass: string
  // 아이콘 색
  iconClass: string
}

export const GRADES: Record<Grade, GradeWords> = {
  bad: { name: '심각', icon: OctagonAlert, rank: 0, textClass: 'text-danger-ink', iconClass: 'text-danger' },
  warn: { name: '주의', icon: TriangleAlert, rank: 1, textClass: 'text-warning-ink', iconClass: 'text-warning' },
  good: { name: '통과', icon: CircleCheck, rank: 2, textClass: 'text-success-ink', iconClass: 'text-success' },
}

export const GRADE_ORDER: Grade[] = ['bad', 'warn', 'good']

// 검사 하나의 고정 낱말
export interface CheckWords {
  name: string
  icon: LucideIcon
  // 정의 (ⓘ 툴팁)
  definition: string
  // 영향 태그
  impact: string
  // 조치 태그
  fix: string
}

// 검사 줄의 → 버튼: 갈 곳과 올렸을 때의 글자
export interface CheckView {
  to: RouteLocationRaw
  // 종합 상태의 주 버튼 글자에 쓰는 건수
  count: number
  // 예: '문장 16건 보기', '질의 12건 보기'
  label: string
}

// 등급 사다리 한 칸의 글자. 예: 통과 '< 2%'
export interface LadderStep {
  grade: Grade
  text: string
}

// 검사 표의 한 줄. 낱말은 key로 모듈의 낱말 표(words)에서 찾는다.
export interface Check {
  key: string
  grade: Grade
  // 큰 숫자와 작은 단위. 예: '8' + '건', '1.0' + '배', '< 0.1' + '%'
  value: string
  unit: string
  // 값 아래 작은 사실. 예: '4묶음', '여분 64건'
  sub: string
  // ⓘ의 등급 사다리 전체
  ladder: LadderStep[]
  // ⓘ의 정의 (기준 값이 드는 것은 여기서 채운다)
  definition: string
  // ⓘ에 한 줄 더: 무엇을 세는지. 예: ['분모', '학습 포함 1,000건']
  scope: [string, string] | null
  // 볼 것이 없으면 null
  view: CheckView | null
}

// 종합 상태: 검사 등급만 센다(새 점수를 만들지 않는다).
export interface OverallStatus {
  grade: Grade
  label: string
  counts: Record<Grade, number>
}

/** 검사마다 등급을 센다 (카드 머리의 '심각 2 · 주의 1 · 통과 2'). */
export function gradeCounts(checks: Check[]): Record<Grade, number> {
  const counts: Record<Grade, number> = { bad: 0, warn: 0, good: 0 }
  for (const check of checks) counts[check.grade] += 1
  return counts
}

/** 하나라도 심각이면 준비 안 됨, 심각 없이 주의가 있으면 확인 필요, 모두 통과면 준비됨. */
export function overallStatus(checks: Check[]): OverallStatus {
  const counts = gradeCounts(checks)
  if (counts.bad) return { grade: 'bad', label: '준비 안 됨', counts }
  if (counts.warn) return { grade: 'warn', label: '확인 필요', counts }
  return { grade: 'good', label: '준비됨', counts }
}
