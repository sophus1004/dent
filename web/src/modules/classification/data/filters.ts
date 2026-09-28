// 데이터 탭의 거르기 · 쪽 · 연 문장 · 보기. 모두 주소 뒤 ?에 둔다(?q=&label=&status=&problem=&page=&record=&view=).
// 그래서 새로고침해도, 뒤로·앞으로 가도 같은 화면이 나온다. 기본값(첫 쪽, 상태 전체 등)은 주소에 쓰지 않는다.
import type { LocationQuery, LocationQueryRaw } from 'vue-router'

import type {
  ExcludeReason,
  RecordProblem,
  RecordQuery,
  RecordStatus,
} from '@/modules/classification/types'

// 한 쪽에 보이는 문장 수
export const PAGE_SIZE = 50

// 상태 거르기. active(휴지통 밖 전부)가 기본이다.
export const STATUSES: RecordStatus[] = ['active', 'included', 'excluded', 'trash']
export const DEFAULT_STATUS: RecordStatus = 'active'
export const STATUS_NAMES: Record<RecordStatus, string> = {
  active: '전체',
  included: '학습 포함',
  excluded: '학습 제외',
  trash: '휴지통',
}

// 문제 거르기. 한 번에 하나만 고른다(API가 하나만 받는다).
// 근접 중복 · 오라벨 의심은 다 만든 뜻 분석의 결과다(없으면 0건).
export const PROBLEMS: RecordProblem[] = ['duplicate', 'conflict', 'short', 'near_duplicate', 'suspect']
export const PROBLEM_NAMES: Record<RecordProblem, string> = {
  duplicate: '중복',
  conflict: '라벨 충돌',
  short: '짧은 문장',
  near_duplicate: '근접 중복',
  suspect: '오라벨 의심',
}

// 같은 문장끼리 붙어 나오는 문제. 이때 표에 묶음 사이 줄을 긋는다.
export const GROUPED_PROBLEMS: RecordProblem[] = ['duplicate', 'conflict']

// 학습에서 뺀 이유
export const EXCLUDE_REASON_NAMES: Record<ExcludeReason, string> = {
  manual: '직접 뺌',
  duplicate: '중복 정리',
  helper: '도우미',
}

// 데이터 탭의 보기: 표 · 지도(의미 지도). 표가 기본이다.
export type DataView = 'table' | 'map'
export const DEFAULT_VIEW: DataView = 'table'

// 주소에서 읽은 거르기
export interface DataFilters {
  // 문장 속 글자 찾기. 없으면 ''
  q: string
  labelId: number | null
  status: RecordStatus
  problem: RecordProblem | null
}

// 주소에서 읽은 데이터 탭의 모든 값
export interface DataQuery extends DataFilters {
  // 1부터
  page: number
  // 오른쪽 패널에 연 문장 번호. 닫혀 있으면 null
  recordId: number | null
  // 표 · 지도
  view: DataView
}

// 아무것도 거르지 않은 상태
export const NO_FILTERS: DataFilters = {
  q: '',
  labelId: null,
  status: DEFAULT_STATUS,
  problem: null,
}

/** 주소 뒤 ?를 읽는다. 모르는 값은 없는 것으로 본다. */
export function readDataQuery(query: LocationQuery): DataQuery {
  const status = text(query.status) as RecordStatus
  const problem = text(query.problem) as RecordProblem
  return {
    q: text(query.q).trim(),
    labelId: positiveInt(query.label),
    status: STATUSES.includes(status) ? status : DEFAULT_STATUS,
    problem: PROBLEMS.includes(problem) ? problem : null,
    page: positiveInt(query.page) ?? 1,
    recordId: positiveInt(query.record),
    view: text(query.view) === 'map' ? 'map' : DEFAULT_VIEW,
  }
}

/** 주소 뒤 ?로 쓴다. 기본값은 빼서 주소를 짧게 둔다. */
export function writeDataQuery(value: DataQuery): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  if (value.q) query.q = value.q
  if (value.labelId !== null) query.label = String(value.labelId)
  if (value.status !== DEFAULT_STATUS) query.status = value.status
  if (value.problem) query.problem = value.problem
  if (value.page > 1) query.page = String(value.page)
  if (value.recordId !== null) query.record = String(value.recordId)
  if (value.view !== DEFAULT_VIEW) query.view = value.view
  return query
}

/** 문장 목록 API에 보낼 거르기와 쪽. */
export function toRecordQuery(value: DataQuery): RecordQuery {
  return {
    status: value.status,
    label_id: value.labelId,
    problem: value.problem,
    q: value.q || null,
    limit: PAGE_SIZE,
    offset: (value.page - 1) * PAGE_SIZE,
  }
}

/** 거르기가 하나라도 걸려 있는지. */
export function hasFilters(value: DataFilters): boolean {
  const isDefault =
    !value.q &&
    value.labelId === null &&
    value.status === DEFAULT_STATUS &&
    value.problem === null
  return !isDefault
}

// ?a=1&a=2처럼 여러 번 오면 첫 값만 쓴다.
function text(value: LocationQuery[string]): string {
  const first = Array.isArray(value) ? value[0] : value
  return first ?? ''
}

function positiveInt(value: LocationQuery[string]): number | null {
  const raw = text(value)
  const isNumber = /^\d+$/.test(raw)
  const number = isNumber ? Number(raw) : 0
  return number >= 1 ? number : null
}
