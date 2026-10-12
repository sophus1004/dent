// 생성 모듈 API를 부르는 함수. 주소 하나에 함수 하나이고, 이름은 백엔드 라우터 함수와 같다.
// 모두 /api/v1/generation 아래에 있다. 실패하면 system/http.ts의 ApiError(한국어 문장)를 던진다.
// 파일 올리기 · 허깅페이스 미리 보기 · 가져오기 기록 · 작업 · 내려받기는 system/api.ts에 있다.
import { request } from '@/system/http'
import type { ExampleRead, ExportRead, HelperLeftRead, HelperRunRead, ImportRead } from '@/system/types'

import type {
  AnalysisStateRead,
  ChangedRead,
  ChangeRead,
  CheckKey,
  ConversationBulkUpdate,
  ConversationPageRead,
  ConversationQuery,
  ConversationRead,
  DatasetRead,
  DatasetSummaryRead,
  DecideAllCreate,
  DecisionCreate,
  DecisionRead,
  ExportCreate,
  ExportPreviewRead,
  FieldMapping,
  HelperStateRead,
  HelperUndoRead,
  ImportCreate,
  MapMatchesRead,
  MapPointsRead,
  MappingSuggestCreate,
  MapTextRead,
  NearPartnerRead,
  OverviewRead,
  PairDecisionCreate,
  ReviewQueueRead,
  SettingsRead,
  SettingsUpdate,
} from '@/modules/generation/types'

const BASE = '/api/v1/generation'

// ---------- 데이터셋 ----------

/** 생성 데이터셋 목록과 개수. 최근에 고친 것부터. */
export function listDatasets(signal?: AbortSignal): Promise<DatasetSummaryRead[]> {
  return request('GET', `${BASE}/datasets`, { signal })
}

/** 데이터셋 하나. 없으면 404. */
export function getDataset(datasetId: number, signal?: AbortSignal): Promise<DatasetRead> {
  return request('GET', `${BASE}/datasets/${datasetId}`, { signal })
}

/** 데이터셋 설정 고치기(만들 데이터 · 평가셋 · 학습할 모델 · 최대 길이). 모델 · 길이를 바꾸면 길이를 다시 잰다. */
export function updateSettings(datasetId: number, data: SettingsUpdate): Promise<SettingsRead> {
  return request('PATCH', `${BASE}/datasets/${datasetId}/settings`, { body: data })
}

/** 평가셋과 다시 대조하기. 평가셋이 없으면 아무 일도 하지 않는다. */
export function checkContamination(datasetId: number): Promise<void> {
  return request('POST', `${BASE}/datasets/${datasetId}/contamination`)
}

/** 데이터셋을 대화 · 결정 기록 · 가져오기 기록까지 지운다. 가져오는 중이면 409. */
export function deleteDataset(datasetId: number): Promise<void> {
  return request('DELETE', `${BASE}/datasets/${datasetId}`)
}

// ---------- 가져오기 ----------

/** 원본의 열 이름으로 모양과 칸을 짐작한다. */
export function suggestMapping(data: MappingSuggestCreate): Promise<FieldMapping> {
  return request('POST', `${BASE}/mapping/suggest`, { body: data })
}

/** 내장 예시 데이터 목록 (심은 문제 · 이미 넣었으면 그 데이터셋 번호). */
export function listExamples(signal?: AbortSignal): Promise<ExampleRead[]> {
  return request('GET', `${BASE}/examples`, { signal })
}

/** 내장 예시를 새 데이터셋으로 가져오기 시작. 이미 넣었으면 409. */
export function installExample(key: string): Promise<ImportRead> {
  return request('POST', `${BASE}/examples/${key}`)
}

/** 가져오기 시작. 진행률은 system의 작업 API로 본다. */
export function createImport(data: ImportCreate): Promise<ImportRead> {
  return request('POST', `${BASE}/imports`, { body: data })
}

// ---------- 진단 ----------

/** 데이터셋 진단: 검사마다 문제가 남은 대화 수 · 등급과 흐름의 지금 단계. */
export function getOverview(datasetId: number, signal?: AbortSignal): Promise<OverviewRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/overview`, { signal })
}

// ---------- 대화 · 검토 ----------

/** 대화 목록. 상태 · 문제 · 검색어로 거른다. */
export function listConversations(
  datasetId: number,
  query: ConversationQuery = {},
  signal?: AbortSignal,
): Promise<ConversationPageRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/conversations`, { query: { ...query }, signal })
}

/** 대화 한 건. */
export function getConversation(conversationId: number, signal?: AbortSignal): Promise<ConversationRead> {
  return request('GET', `${BASE}/conversations/${conversationId}`, { signal })
}

/** 선호 쌍 하나가 든 대화 (그 지시의 응답 · 쌍 전부). */
export function getPairConversation(pairId: number, signal?: AbortSignal): Promise<ConversationRead> {
  return request('GET', `${BASE}/pairs/${pairId}`, { signal })
}

/** 검토 큐: 한 검사의 문제가 남은 번호들(쌍 검사면 쌍 번호). kept면 그대로 둔 것들. */
export function getReviewQueue(
  datasetId: number,
  check: CheckKey,
  signal?: AbortSignal,
  kept = false,
): Promise<ReviewQueueRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/review`, { query: { check, kept }, signal })
}

/** 검토 결정 하나(빼기 · 그대로 두기 · 가리기 · 자르기 · 다시 쓰기 · 응답 만들기). */
export function decide(conversationId: number, data: DecisionCreate): Promise<DecisionRead> {
  return request('POST', `${BASE}/conversations/${conversationId}/decide`, { body: data })
}

/** 선호 쌍 결정 하나(쌍 빼기 · 뒤집기 · 그대로 두기). */
export function decidePair(pairId: number, data: PairDecisionCreate): Promise<DecisionRead> {
  return request('POST', `${BASE}/pairs/${pairId}/decide`, { body: data })
}

/** '그대로 두기'를 거둬 그 검사로 다시 묻는다. */
export function unkeepConversation(conversationId: number, check: CheckKey): Promise<ConversationRead> {
  return request('POST', `${BASE}/conversations/${conversationId}/unkeep`, { body: { check } })
}

/** 쌍의 '그대로 두기'를 거둬 그 검사로 다시 묻는다. */
export function unkeepPair(pairId: number, check: CheckKey): Promise<ConversationRead> {
  return request('POST', `${BASE}/pairs/${pairId}/unkeep`, { body: { check } })
}

/** 한 검사의 남은 대화 모두에 같은 결정. */
export function decideAll(datasetId: number, data: DecideAllCreate): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/decide-all`, { body: data })
}

/** 되돌리지 않은 결정 기록, 최근 것부터. */
export function listChanges(datasetId: number, signal?: AbortSignal): Promise<ChangeRead[]> {
  return request('GET', `${BASE}/datasets/${datasetId}/changes`, { signal })
}

/** 결정 하나 되돌리기. 이미 되돌렸거나 그 뒤에 대화가 바뀌었으면 409. */
export function undoChange(changeId: number): Promise<ConversationRead> {
  return request('POST', `${BASE}/changes/${changeId}/undo`)
}

/** 다 만든 뜻 분석에서 이 대화와 뜻이 가까운 짝들. 분석이 없으면 빈 목록. */
export function listNearPartners(conversationId: number, signal?: AbortSignal): Promise<NearPartnerRead[]> {
  return request('GET', `${BASE}/conversations/${conversationId}/near`, { signal })
}

/** 여러 대화를 휴지통에 넣거나 되살린다. */
export function bulkUpdateConversations(datasetId: number, data: ConversationBulkUpdate): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/conversations/bulk`, { body: data })
}

/** 휴지통 비우기: 휴지통의 대화를 영구히 지운다. */
export function emptyTrash(datasetId: number): Promise<ChangedRead> {
  return request('DELETE', `${BASE}/datasets/${datasetId}/trash`)
}

// ---------- 뜻 분석 ----------

/** 뜻 분석 상태: 다 만든 것 · 도는 것 · 분석 이후 바뀌었는지. */
export function getAnalysis(datasetId: number, signal?: AbortSignal): Promise<AnalysisStateRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map`, { signal })
}

/** 뜻 분석 만들기. 임베딩 미연결이면 422, 이미 만드는 중이면 409. */
export function startAnalysis(datasetId: number): Promise<AnalysisStateRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/map`)
}

/** 만드는 중인 뜻 분석 멈추기. */
export function cancelAnalysis(datasetId: number): Promise<AnalysisStateRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/map/cancel`)
}

/** 의미 지도의 점들. 분석이 없으면 404. */
export function getMapPoints(datasetId: number, signal?: AbortSignal): Promise<MapPointsRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/points`, { signal })
}

/** 검색어가 든 대화 번호들 (지도 강조). */
export function getMapMatches(datasetId: number, q: string, signal?: AbortSignal): Promise<MapMatchesRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/matches`, { query: { q }, signal })
}

/** 지도 점 하나의 글 (말풍선). */
export function getMapText(datasetId: number, conversationId: number, signal?: AbortSignal): Promise<MapTextRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/text`, { query: { conversation_id: conversationId }, signal })
}

// ---------- LLM 도우미 ----------

/** 데이터셋의 가장 최근 도우미 실행과 되돌린 카드 번호들. */
export function getHelper(datasetId: number, signal?: AbortSignal): Promise<HelperStateRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/helper`, { signal })
}

/** 도우미 시작. 뜻 분석이 없거나 오래됐으면 먼저 다시 만든다. LLM이 없으면 422, 이미 도는 중이면 409. */
export function startHelper(datasetId: number): Promise<HelperRunRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/helper`)
}

/** 허락에 답한다 (응답 만들기 · 비교 만들기). 기다리는 중이 아니면 409. */
export function answerHelperPermission(runId: number, approve: boolean): Promise<HelperRunRead> {
  return request('POST', `${BASE}/helper/${runId}/permission`, { body: { approve } })
}

/** 도우미가 끝난 뒤 남은 것 (지금 진단으로 센다) */
export function getHelperLeft(datasetId: number, signal?: AbortSignal): Promise<HelperLeftRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/helper/left`, { signal })
}

/** 고른 남은 것을 AI로 고치기 */
export function fixHelper(datasetId: number, keys: string[]): Promise<HelperRunRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/helper/fix`, { body: { keys } })
}

/** 도우미가 바꾼 것을 되돌린다(eventId가 있으면 그 카드만). 도는 중이면 409. */
export function undoHelper(runId: number, eventId?: number): Promise<HelperUndoRead> {
  return request('POST', `${BASE}/helper/${runId}/undo`, { body: { event_id: eventId ?? null } })
}

// ---------- 내보내기 ----------

/** 내보내기 미리 보기: 첫 줄 · 파일마다 대화 수 · 남은 문제 · 막힘. */
export function previewExport(datasetId: number, data: ExportCreate): Promise<ExportPreviewRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/exports/preview`, { body: data })
}

/** 내보내기 시작. 심각이 남으면 409. */
export function createExport(datasetId: number, data: ExportCreate): Promise<ExportRead[]> {
  return request('POST', `${BASE}/datasets/${datasetId}/exports`, { body: data })
}
