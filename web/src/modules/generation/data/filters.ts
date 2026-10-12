// 데이터 탭의 거르기 · 쪽 · 연 대화 · 보기. 모두 주소 뒤 ?에 둔다(?q=&problem=&status=&page=&conversation=&view=).
// 그래서 새로고침해도, 뒤로·앞으로 가도 같은 화면이 나온다. 기본값(첫 쪽, 휴지통 밖 등)은 주소에 쓰지 않는다.
import type { LocationQuery, LocationQueryRaw } from 'vue-router'

import { CHECK_WORDS, SHAPE_KEYS } from '@/modules/generation/checks'
import type { CheckKey, ConversationQuery } from '@/modules/generation/types'

// 한 쪽에 보이는 대화 수
export const PAGE_SIZE = 50

// 상태 거르기: 휴지통 밖(기본) · 휴지통
export type ConversationStatus = 'active' | 'trash'
export const STATUSES: ConversationStatus[] = ['active', 'trash']
export const DEFAULT_STATUS: ConversationStatus = 'active'
export const STATUS_NAMES: Record<ConversationStatus, string> = {
  active: '전체',
  trash: '휴지통',
}

// 문제 거르기: 검사 이름 하나 · 문제 있음(any) · 문제 없음(none). 한 번에 하나만 고른다(API가 하나만 받는다).
export type ProblemFilter = CheckKey | 'any' | 'none'
// 검사 이름 순서는 진단과 같다(검사 정의 순). 데이터셋의 모양인 검사(과제 쏠림)는 대화를 거르지 않는다.
export const PROBLEM_KEYS = (Object.keys(CHECK_WORDS) as CheckKey[]).filter((key) => !SHAPE_KEYS.includes(key))
export const PROBLEM_GROUP_NAMES: Record<'any' | 'none', string> = {
  any: '문제 있음',
  none: '문제 없음',
}

// 주소에서 읽은 거르기
export interface DataFilters {
  // 대화 속 글자 찾기. 없으면 ''
  q: string
  problem: ProblemFilter | null
  status: ConversationStatus
}

// 데이터 탭의 보기: 표 · 지도(의미 지도). 표가 기본이다.
export type DataView = 'table' | 'map'
export const DEFAULT_VIEW: DataView = 'table'

// 주소에서 읽은 데이터 탭의 모든 값
export interface DataQuery extends DataFilters {
  // 1부터
  page: number
  // 오른쪽 패널에 연 대화 번호. 닫혀 있으면 null
  conversationId: number | null
  // 표 · 지도
  view: DataView
}

// 아무것도 거르지 않은 상태
export const NO_FILTERS: DataFilters = {
  q: '',
  problem: null,
  status: DEFAULT_STATUS,
}

/** 주소 뒤 ?를 읽는다. 모르는 값은 없는 것으로 본다. */
export function readDataQuery(query: LocationQuery): DataQuery {
  const status = text(query.status) as ConversationStatus
  const problem = text(query.problem) as ProblemFilter
  const isProblem = problem === 'any' || problem === 'none' || PROBLEM_KEYS.includes(problem as CheckKey)
  return {
    q: text(query.q).trim(),
    problem: isProblem ? problem : null,
    status: STATUSES.includes(status) ? status : DEFAULT_STATUS,
    page: positiveInt(query.page) ?? 1,
    conversationId: positiveInt(query.conversation),
    view: text(query.view) === 'map' ? 'map' : DEFAULT_VIEW,
  }
}

/** 주소 뒤 ?로 쓴다. 기본값은 빼서 주소를 짧게 둔다. */
export function writeDataQuery(value: DataQuery): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  if (value.q) query.q = value.q
  if (value.problem) query.problem = value.problem
  if (value.status !== DEFAULT_STATUS) query.status = value.status
  if (value.page > 1) query.page = String(value.page)
  if (value.conversationId !== null) query.conversation = String(value.conversationId)
  if (value.view !== DEFAULT_VIEW) query.view = value.view
  return query
}

/** 대화 목록 API에 보낼 거르기와 쪽. */
export function toConversationQuery(value: DataQuery): ConversationQuery {
  return {
    status: value.status,
    problem: value.problem,
    q: value.q || null,
    limit: PAGE_SIZE,
    offset: (value.page - 1) * PAGE_SIZE,
  }
}

/** 거르기가 하나라도 걸려 있는지. */
export function hasFilters(value: DataFilters): boolean {
  return Boolean(value.q) || value.problem !== null || value.status !== DEFAULT_STATUS
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
