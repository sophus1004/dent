// 진단 검사 일곱 개: 글자 검사 넷(진단 API overview) + 뜻 검사 셋(뜻 분석 checks).
// 고정 낱말, 등급 이름, 값 · 기준 글자, 순서, 종합 상태를 만든다.
// 글자 검사의 기준은 overview.thresholds(백엔드가 등급을 매긴 경계)를, 뜻 검사의 기준은 checks.thresholds를 글자로 바꾼 것이다.
// 화면 글은 문장이 아니라 낱말 · 숫자+단위 · 키: 값이다. 검사마다 정의 · 영향 · 조치는 늘 같은 낱말을 쓴다.
import { ChartBar, Copy, EqualApproximately, Ruler, Scale, ScanSearch, Tags } from '@lucide/vue'
import type { LocationQueryRaw, RouteLocationRaw } from 'vue-router'

import { GRADES, type Check as BaseCheck, type CheckView, type CheckWords, type LadderStep } from '@/system/diagnosis/grades'
import { fmt } from '@/system/format'

import { DEFAULT_VIEW, NO_FILTERS, writeDataQuery, type DataFilters } from '@/modules/classification/data/filters'
import type { MapChecksRead, OverviewLabel, OverviewRead, Threshold } from '@/modules/classification/types'

// 글자 검사 (규칙으로 센다)
export type TextCheckKey = 'balance' | 'conflict' | 'short' | 'duplicate'
// 뜻 검사 (뜻 분석 한 번이 만든다)
export type MeaningCheckKey = 'suspect' | 'near_duplicate' | 'skew'
export type CheckKey = TextCheckKey | MeaningCheckKey

// 뜻 검사 순서 (분석 전 흐린 줄도 이 순서)
export const MEANING_CHECK_KEYS: MeaningCheckKey[] = ['suspect', 'near_duplicate', 'skew']

export const CHECK_WORDS: Record<CheckKey, CheckWords> = {
  balance: {
    name: '라벨 균형',
    icon: Scale,
    definition: '최다 라벨 건수 ÷ 최소 라벨 건수',
    impact: '소수 라벨 과소학습',
    fix: '소수 라벨 보강',
  },
  conflict: {
    name: '라벨 충돌',
    icon: Tags,
    definition: '같은 문장에 서로 다른 라벨',
    impact: '학습 신호 상충',
    fix: '라벨 하나로 통일',
  },
  short: {
    name: '짧은 문장',
    icon: Ruler,
    definition: '기준 글자 수 미만 문장 · 공백 포함',
    impact: '근거 부족',
    fix: '검토 후 제외',
  },
  duplicate: {
    name: '중복',
    icon: Copy,
    definition: '문장·라벨 모두 같은 여분',
    impact: '학습 낭비',
    fix: '여분 제외',
  },
  suspect: {
    name: '오라벨 의심',
    icon: ScanSearch,
    definition: '분류기가 다른 라벨로 봄 · Jev로 한 번 더 확인',
    impact: '학습 신호 흐림',
    fix: '라벨 바꾸기',
  },
  near_duplicate: {
    name: '근접 중복',
    icon: EqualApproximately,
    definition: '글자는 달라도 뜻이 거의 같은 두 문장',
    impact: '학습 낭비 · 신호 상충',
    fix: '검토 후 제외',
  },
  skew: {
    name: '의미 쏠림',
    icon: ChartBar,
    definition: '라벨 안 문장이 한 무리에 몰림',
    impact: '표현 다양성 부족',
    fix: '다른 표현 보강',
  },
}

// 등급 · 종합 상태 · 검사 줄의 바탕은 시스템(@/system/diagnosis/grades)의 것이다.
export { GRADE_ORDER, GRADES, gradeCounts, overallStatus } from '@/system/diagnosis/grades'
export type { CheckView, CheckWords, LadderStep, OverallStatus } from '@/system/diagnosis/grades'

// 같은 등급이면 DENT 안에서 바로 고칠 수 있는 것을 먼저 둔다. 라벨 균형은 데이터를 더 모아야 해서 뒤로.
const FIX_LATER: CheckKey[] = ['balance']

// 분류의 검사 한 줄: 시스템 검사 줄에 분류의 검사 이름을 붙인 것
export interface Check extends BaseCheck {
  key: CheckKey
}

/** 진단 결과로 글자 검사 네 줄을 만든다. 심각 → 주의 → 통과 순. */
export function buildChecks(overview: OverviewRead): Check[] {
  const { balance, conflicts, short, duplicates, thresholds } = overview
  const largest = largestLabel(overview)
  const smallest = smallestLabel(overview)
  const hasRatio = balance.ratio !== null
  const denominator: [string, string] = ['분모', `학습 포함 ${fmt(overview.included)}건`]
  const toData = (filters: Partial<DataFilters>, count: number): CheckView => ({
    to: dataRoute(overview.dataset_id, filters),
    count,
    label: `문장 ${fmt(count)}건 보기`,
  })
  const checks: Check[] = [
    {
      key: 'balance',
      grade: balance.grade,
      value: hasRatio ? (balance.ratio ?? 0).toFixed(1) : '—',
      unit: hasRatio ? '배' : '',
      sub: largest && smallest ? `${largest.name} ${fmt(largest.included)} / ${smallest.name} ${fmt(smallest.included)}` : '',
      ...thresholdTexts(thresholds.balance),
      definition: CHECK_WORDS.balance.definition,
      scope: ['범위', '학습 포함 문장'],
      view: smallest ? toData({ labelId: smallest.label_id }, smallest.included) : null,
    },
    {
      key: 'conflict',
      grade: conflicts.grade,
      value: fmt(conflicts.records),
      unit: '건',
      sub: `${fmt(conflicts.groups)}묶음`,
      ...thresholdTexts(thresholds.conflicts),
      definition: CHECK_WORDS.conflict.definition,
      scope: denominator,
      view: conflicts.records ? toData({ problem: 'conflict' }, conflicts.records) : null,
    },
    {
      key: 'short',
      grade: short.grade,
      value: fmt(short.records),
      unit: '건',
      sub: percentText(overview.included ? short.records / overview.included : 0),
      ...thresholdTexts(thresholds.short),
      definition: `${short.threshold}자 미만 문장 · 공백 포함`,
      scope: null,
      view: short.records ? toData({ problem: 'short' }, short.records) : null,
    },
    {
      // 중복은 두 수가 있다: 무리에 든 문장(원본 포함 = 여분 + 무리 수)과 여분. 둘을 같이 적는다.
      key: 'duplicate',
      grade: duplicates.grade,
      value: percentText(duplicates.rate).replace('%', ''),
      unit: '%',
      sub: `여분 ${fmt(duplicates.extra_records)}건`,
      ...thresholdTexts(thresholds.duplicates),
      definition: CHECK_WORDS.duplicate.definition,
      scope: denominator,
      view: duplicates.extra_records
        ? toData({ problem: 'duplicate' }, duplicates.extra_records + duplicates.groups)
        : null,
    },
  ]
  return sortChecks(checks)
}

/** 뜻 분석 요약으로 뜻 검사 세 줄을 만든다. 심각 → 주의 → 통과 순. */
export function buildMeaningChecks(checks: MapChecksRead, datasetId: number): Check[] {
  const { suspects, near_duplicates: near, skews, thresholds, grades, jev } = checks
  const skewed = skews.filter((skew) => skew.is_skewed)
  const widest = skews.reduce<(typeof skews)[number] | null>(
    (best, skew) => (best === null || skew.largest_share > best.largest_share ? skew : best),
    null,
  )
  const skewLimit = percentText(thresholds.skew_largest_share)
  const good = percentText(thresholds.suspect_good_rate)
  const warn = percentText(thresholds.suspect_warn_rate)
  const jevSub = jev.status === 'judged' && suspects.judged ? ` · Jev ${fmt(suspects.confirmed_open)}` : ''
  // 쏠린 라벨이 있으면 가장 쏠린 라벨을, 없으면 전체를 지도에서 본다(라벨로 칠해 무리를 눈으로 본다).
  const skewTarget = skewed.length ? skewed.reduce((a, b) => (a.largest_share >= b.largest_share ? a : b)) : null
  const meaningChecks: Check[] = [
    {
      key: 'suspect',
      grade: grades.suspect,
      value: fmt(suspects.open),
      unit: '건',
      sub: `${percentText(suspects.open_rate)}${jevSub}`,
      ladder: [
        { grade: 'good', text: `< ${good}` },
        { grade: 'warn', text: `< ${warn}` },
        { grade: 'bad', text: `≥ ${warn}` },
      ],
      definition: CHECK_WORDS.suspect.definition,
      scope: ['분모', '분석한 문장'],
      view: suspects.open
        ? {
            to: { name: 'classification-suggestions', params: { datasetId } },
            count: suspects.open,
            label: `제안 ${fmt(suspects.open)}건 보기`,
          }
        : null,
    },
    {
      key: 'near_duplicate',
      grade: grades.near_duplicate,
      value: fmt(near.pairs),
      unit: '쌍',
      sub: `라벨 다름 ${fmt(near.label_mismatch)}`,
      ladder: [
        { grade: 'good', text: '라벨 다름 0쌍' },
        { grade: 'warn', text: '라벨 다른 쌍' },
      ],
      definition: `${CHECK_WORDS.near_duplicate.definition} · 유사도 ≥ ${thresholds.near_duplicate_similarity}`,
      scope: null,
      view: near.pairs
        ? { to: dataRoute(datasetId, { problem: 'near_duplicate' }), count: near.texts, label: `문장 ${fmt(near.texts)}건 보기` }
        : null,
    },
    {
      key: 'skew',
      grade: grades.skew,
      value: fmt(skewed.length),
      unit: `/${fmt(skews.length)}`,
      sub: widest ? `최대 ${percentText(widest.largest_share)}` : '',
      ladder: [
        { grade: 'good', text: `모든 라벨 < ${skewLimit}` },
        { grade: 'warn', text: `한 라벨이라도 ≥ ${skewLimit}` },
      ],
      definition: `${CHECK_WORDS.skew.definition} · 가장 큰 무리 비율`,
      scope: checks.unmeasured_labels ? ['재지 못함', `라벨 ${fmt(checks.unmeasured_labels)} · 문장 적음`] : null,
      view: skews.length
        ? {
            to: {
              name: 'classification-data',
              params: { datasetId },
              query: { ...dataQuery(skewTarget ? { labelId: skewTarget.label_id } : {}), view: 'map' },
            },
            count: skewTarget?.text_count ?? 0,
            label: '의미 지도 보기',
          }
        : null,
    },
  ]
  return sortChecks(meaningChecks)
}

/** 등급 사다리 전체. 마지막 칸은 앞 칸의 경계를 넘는 값이다. 예: 통과 ≤ 1.5배 · 주의 ≤ 2.5배 · 심각 > 2.5배 */
export function ladder(threshold: Threshold): LadderStep[] {
  return threshold.steps.map((step, index) => {
    if (step.op !== null) return { grade: step.grade, text: stepText(step.op, step.value, threshold.unit) }
    const previous = threshold.steps[index - 1]
    return { grade: step.grade, text: previous ? beyondText(previous.op, previous.value, threshold.unit) : '전부' }
  })
}

/** 비율(0~1)을 소수 한 자리 백분율로. 조금이라도 있는데 0.1%가 안 되면 '< 0.1%' */
export function percentText(rate: number): string {
  const percent = rate * 100
  const isTinyButPresent = rate > 0 && percent < 0.1
  return isTinyButPresent ? '< 0.1%' : `${percent.toFixed(1)}%`
}

/** 가장 많은 라벨 (라벨 균형의 분자). 라벨이 둘보다 적으면 null */
export function largestLabel(overview: OverviewRead): OverviewLabel | null {
  return overview.labels.find((label) => label.name === overview.balance.max_label) ?? null
}

/** 가장 적은 라벨 (라벨 균형의 분모). 라벨이 둘보다 적으면 null */
export function smallestLabel(overview: OverviewRead): OverviewLabel | null {
  return overview.labels.find((label) => label.name === overview.balance.min_label) ?? null
}

/** 진단에서 데이터 탭으로 갈 때의 주소. 진단은 학습 포함 문장만 세므로 상태도 학습 포함으로 건다. */
export function dataRoute(datasetId: number, filters: Partial<DataFilters>): RouteLocationRaw {
  return { name: 'classification-data', params: { datasetId }, query: dataQuery(filters) }
}

/** 진단에서 데이터 탭으로 갈 때의 주소 뒤 ?. 진단은 학습 포함 문장만 세므로 상태도 학습 포함으로 건다. */
export function dataQuery(filters: Partial<DataFilters>): LocationQueryRaw {
  return writeDataQuery({
    ...NO_FILTERS,
    status: 'included',
    ...filters,
    page: 1,
    recordId: null,
    view: DEFAULT_VIEW,
  })
}

// 사다리 글자 (글자 검사)
function thresholdTexts(threshold: Threshold): { ladder: LadderStep[] } {
  return { ladder: ladder(threshold) }
}

// 검사 표 순서: 등급 → 바로 고칠 수 있는 것 먼저 → 처음 순서
function sortChecks(checks: Check[]): Check[] {
  const later = (check: Check) => (FIX_LATER.includes(check.key) ? 1 : 0)
  return checks
    .map((check, index) => ({ check, index }))
    .sort(
      (a, b) =>
        GRADES[a.check.grade].rank - GRADES[b.check.grade].rank ||
        later(a.check) - later(b.check) ||
        a.index - b.index,
    )
    .map(({ check }) => check)
}

// 경계 한 칸. 0 이하는 '0건'으로 쓴다(없어야 통과).
function stepText(op: 'le' | 'lt' | null, value: number | null, unit: Threshold['unit']): string {
  if (op === null || value === null) return '전부'
  if (op === 'le' && value === 0) return '0건'
  return `${op === 'le' ? '≤' : '<'} ${unitText(value, unit)}`
}

// 앞 칸의 경계를 넘는 값. 0 이하를 넘으면 '1건 이상'
function beyondText(op: 'le' | 'lt' | null, value: number | null, unit: Threshold['unit']): string {
  if (op === null || value === null) return '전부'
  if (op === 'le' && value === 0) return '1건 이상'
  return `${op === 'le' ? '>' : '≥'} ${unitText(value, unit)}`
}

function unitText(value: number, unit: Threshold['unit']): string {
  if (unit === 'ratio') return `${value}배`
  if (unit === 'rate') return `${Number((value * 100).toFixed(2))}%`
  return `${fmt(value)}건`
}
