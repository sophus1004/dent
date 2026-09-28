// 데이터 탭의 보기 · 거르기 · 쪽 · 연 줄. 모두 주소 뒤 ?에 둔다
// (?view=&q=&problem=&source=&use=&status=&page=&open=). 새로고침 · 뒤로 가기에도 같은 화면이 나온다.
// 기본값(질의 보기, 첫 쪽, 상태 전체)은 주소에 쓰지 않는다.
import {
  CircleSlash,
  Copy,
  Equal,
  EqualApproximately,
  Eraser,
  Feather,
  GitCompare,
  Layers,
  ListFilter,
  MessageCircleQuestion,
  Ruler,
  ScanSearch,
  ShieldAlert,
  Stamp,
  CirclePlus,
  WrapText,
  type LucideIcon,
} from '@lucide/vue'
import type { LocationQuery, LocationQueryRaw } from 'vue-router'

import type {
  DocumentListQuery,
  DocumentProblem,
  DocumentUse,
  ExcludeReason,
  QueryListQuery,
  QueryProblem,
  QuerySource,
  StatusFilter,
} from '@/modules/retrieval/types'

// 한 쪽에 보이는 줄 수
export const PAGE_SIZE = 50

// 데이터 탭의 보기: 질의 표 · 문서 표 · 지도. 질의가 기본이다.
export type DataView = 'queries' | 'documents' | 'map'
export const DEFAULT_VIEW: DataView = 'queries'

export const STATUSES: StatusFilter[] = ['active', 'included', 'excluded', 'trash']
export const DEFAULT_STATUS: StatusFilter = 'active'
export const STATUS_NAMES: Record<StatusFilter, string> = {
  active: '전체',
  included: '학습 포함',
  excluded: '학습 제외',
  trash: '휴지통',
}

// 문제 표시(질의 · 문서 표의 표시 칸과 거르기): 이름 · 아이콘 · 등급 색. 진단 검사 이름과 같다.
export interface MarkWords {
  name: string
  icon: LucideIcon
  tone: 'bad' | 'warn'
}

export const QUERY_PROBLEMS: QueryProblem[] = [
  'no_positive',
  'false_negative',
  'conflict',
  'suspect',
  'missing',
  'duplicate',
  'short_long',
  'context',
  'easy_pair',
  'no_negative',
  'same_negative',
  'easy_negative',
]

export const DOCUMENT_PROBLEMS: DocumentProblem[] = ['broken', 'repeat', 'long', 'duplicate', 'near', 'pick']

// 지도 보기는 질의 · 문서 점을 함께 그리므로 두 쪽의 문제를 모두 받는다(범례의 근접 중복 · 긴 문서).
const MAP_PROBLEMS: string[] = [...QUERY_PROBLEMS, ...DOCUMENT_PROBLEMS]

export const MARKS: Record<string, MarkWords> = {
  no_positive: { name: '정답 없음', icon: CircleSlash, tone: 'bad' },
  false_negative: { name: '거짓 오답', icon: ShieldAlert, tone: 'bad' },
  conflict: { name: '판정 충돌', icon: GitCompare, tone: 'warn' },
  suspect: { name: '정답 의심', icon: ScanSearch, tone: 'warn' },
  missing: { name: '빠진 정답', icon: CirclePlus, tone: 'warn' },
  near: { name: '근접 중복', icon: EqualApproximately, tone: 'warn' },
  duplicate: { name: '중복', icon: Copy, tone: 'warn' },
  short_long: { name: '짧은 · 긴 질의', icon: Ruler, tone: 'warn' },
  context: { name: '문맥 의존', icon: MessageCircleQuestion, tone: 'warn' },
  easy_pair: { name: '쉬운 쌍', icon: Equal, tone: 'warn' },
  no_negative: { name: '오답 부족', icon: Layers, tone: 'warn' },
  same_negative: { name: '정답과 같은 오답', icon: Equal, tone: 'warn' },
  easy_negative: { name: '쉬운 오답', icon: Feather, tone: 'warn' },
  broken: { name: '깨진 글자', icon: Eraser, tone: 'warn' },
  repeat: { name: '반복 구간', icon: Stamp, tone: 'warn' },
  long: { name: '긴 문서', icon: WrapText, tone: 'warn' },
  pick: { name: '질의 안 만들 청크', icon: ListFilter, tone: 'warn' },
}

export const SOURCES: QuerySource[] = ['original', 'synthetic', 'human']
export const SOURCE_NAMES: Record<string, string> = {
  original: '원본',
  synthetic: '합성',
  human: '사람',
  mined: '찾기',
  helper: '도우미',
}

export const USES: DocumentUse[] = ['positive', 'negative', 'unused']
export const USE_NAMES: Record<DocumentUse, string> = {
  positive: '정답으로',
  negative: '오답으로',
  unused: '안 쓰임',
}

export const EXCLUDE_REASON_NAMES: Record<ExcludeReason, string> = {
  manual: '직접 뺌',
  duplicate: '중복 정리',
  helper: '도우미',
}

// 주소에서 읽은 거르기
export interface DataFilters {
  q: string
  // 질의 보기면 QueryProblem, 문서 보기면 DocumentProblem
  problem: string | null
  source: QuerySource | null
  use: DocumentUse | null
  status: StatusFilter
}

export interface DataQuery extends DataFilters {
  view: DataView
  page: number
  // 패널에 연 줄: 질의 번호(질의 · 지도 보기) 또는 문서 번호(문서 보기). 지도의 문서 점은 'd' 앞머리로 가른다.
  open: OpenItem | null
}

// 패널에 연 것
export interface OpenItem {
  kind: 'query' | 'document'
  id: number
}

export const NO_FILTERS: DataFilters = { q: '', problem: null, source: null, use: null, status: DEFAULT_STATUS }

function one(value: LocationQuery[string]): string | null {
  const first = Array.isArray(value) ? value[0] : value
  return typeof first === 'string' && first !== '' ? first : null
}

/** 주소 → 데이터 탭 값. 모르는 값은 기본값으로. */
export function readDataQuery(query: LocationQuery): DataQuery {
  const view = one(query.view)
  const status = one(query.status) as StatusFilter | null
  const source = one(query.source) as QuerySource | null
  const use = one(query.use) as DocumentUse | null
  const page = Number(one(query.page) ?? 1)
  const resolvedView: DataView = view === 'documents' || view === 'map' ? view : DEFAULT_VIEW
  const problems: string[] =
    resolvedView === 'documents' ? DOCUMENT_PROBLEMS : resolvedView === 'map' ? MAP_PROBLEMS : QUERY_PROBLEMS
  const problem = one(query.problem)
  return {
    view: resolvedView,
    q: one(query.q) ?? '',
    problem: problem && problems.includes(problem) ? problem : null,
    source: source && SOURCES.includes(source) ? source : null,
    use: use && USES.includes(use) ? use : null,
    status: status && STATUSES.includes(status) ? status : DEFAULT_STATUS,
    page: Number.isInteger(page) && page > 0 ? page : 1,
    open: readOpen(one(query.open), resolvedView),
  }
}

function readOpen(value: string | null, view: DataView): OpenItem | null {
  if (!value) return null
  const isDocument = value.startsWith('d')
  const id = Number(isDocument ? value.slice(1) : value)
  if (!Number.isInteger(id) || id <= 0) return null
  return { kind: isDocument || view === 'documents' ? 'document' : 'query', id }
}

/** 데이터 탭 값 → 주소. 기본값은 빼고 쓴다. */
export function writeDataQuery(data: DataQuery): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  if (data.view !== DEFAULT_VIEW) query.view = data.view
  if (data.q) query.q = data.q
  if (data.problem) query.problem = data.problem
  if (data.source && data.view !== 'documents') query.source = data.source
  if (data.use && data.view === 'documents') query.use = data.use
  if (data.status !== DEFAULT_STATUS) query.status = data.status
  if (data.page > 1) query.page = String(data.page)
  if (data.open) {
    const isPlain = data.open.kind === 'query' || data.view === 'documents'
    query.open = isPlain ? String(data.open.id) : `d${data.open.id}`
  }
  return query
}

/** 거르기가 걸려 있는지 (보기 · 쪽 · 연 줄은 빼고). */
export function hasFilters(data: DataFilters): boolean {
  return Boolean(data.q || data.problem || data.source || data.use || data.status !== DEFAULT_STATUS)
}

/** 질의 목록 API의 거르기. */
export function toQueryListQuery(data: DataQuery): QueryListQuery {
  return {
    q: data.q || null,
    problem: (data.problem as QueryProblem | null) ?? null,
    source: data.source,
    status: data.status,
    limit: PAGE_SIZE,
    offset: (data.page - 1) * PAGE_SIZE,
  }
}

/** 문서 목록 API의 거르기. */
export function toDocumentListQuery(data: DataQuery): DocumentListQuery {
  return {
    q: data.q || null,
    problem: (data.problem as DocumentProblem | null) ?? null,
    use: data.use,
    status: data.status,
    limit: PAGE_SIZE,
    offset: (data.page - 1) * PAGE_SIZE,
  }
}
