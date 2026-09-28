// 검색 모듈 API를 부르는 함수. 주소 하나에 함수 하나이고, 이름은 백엔드 라우터 함수와 같다.
// 모두 /api/v1/retrieval 아래에 있다. 실패하면 system/http.ts의 ApiError(한국어 문장)를 던진다.
// 파일 올리기·허깅페이스 미리 보기·가져오기 기록·작업·도우미 실행·내보낸 파일은 system/api.ts에 있다.
import { request } from '@/system/http'
import type { ExampleRead, ExportRead, HelperLeftRead, HelperRunRead, ImportRead } from '@/system/types'

import type {
  AnalysisStateRead,
  ChangedRead,
  DatasetRead,
  DatasetSummaryRead,
  DocumentBulkUpdate,
  DocumentDetailRead,
  DocumentListQuery,
  DocumentPageRead,
  DocumentUpdate,
  ExportCreate,
  ExportPreviewRead,
  FieldMapping,
  HelperEstimateRead,
  HelperStateRead,
  HelperUndoRead,
  ImportCreate,
  JudgmentRead,
  MappingSuggestCreate,
  MapPointsRead,
  OverviewRead,
  QueryBulkUpdate,
  QueryDetailRead,
  QueryListQuery,
  QueryPageRead,
  QueryUpdate,
  RepeatBulkUpdate,
  RepeatDecision,
  RepeatListRead,
  RepeatRead,
  RepeatSampleRead,
  SettingsRead,
  SettingsUpdate,
  SuggestionListQuery,
  SuggestionPageRead,
} from '@/modules/retrieval/types'

const BASE = '/api/v1/retrieval'

// ---------- 데이터셋 · 설정 ----------

/** 검색 데이터셋 목록과 수. 최근에 고친 것부터. */
export function listDatasets(signal?: AbortSignal): Promise<DatasetSummaryRead[]> {
  return request('GET', `${BASE}/datasets`, { signal })
}

/** 데이터셋 하나와 수 · 설정. 없으면 404. */
export function getDataset(datasetId: number, signal?: AbortSignal): Promise<DatasetRead> {
  return request('GET', `${BASE}/datasets/${datasetId}`, { signal })
}

/** 데이터셋을 질의 · 문서 · 판정 · 뜻 분석 · 가져오기 기록까지 지운다. 가져오거나 분석하는 중이면 409. */
export function deleteDataset(datasetId: number): Promise<void> {
  return request('DELETE', `${BASE}/datasets/${datasetId}`)
}

/** 설정 고치기(학습할 모델 · 길이 · 오답 수 · 오답 찾기 · 구획 제목 · 되찾기 · 쉬운 쌍 상한). */
export function updateSettings(datasetId: number, data: SettingsUpdate): Promise<SettingsRead> {
  return request('PATCH', `${BASE}/datasets/${datasetId}/settings`, { body: data })
}

/** 반복 구간 목록(메타 모양 먼저, 많이 든 것부터)과 결정별 수. */
export function listRepeats(datasetId: number, signal?: AbortSignal): Promise<RepeatListRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/repeats`, { signal })
}

/** 반복 구간 고르기(떼기 · 남김 · null이면 고르기 전). 든 문서의 학습 글이 다시 만들어진다. */
export function decideRepeat(repeatId: number, decision: RepeatDecision | null): Promise<RepeatRead> {
  return request('PATCH', `${BASE}/repeats/${repeatId}`, { body: { decision } })
}

/** 반복 구간 여럿을 한 번에 고르기(떼기 · 남김 · 고르기 전 · 제안대로). changed는 결정이 바뀐 수. */
export function decideRepeats(datasetId: number, data: RepeatBulkUpdate): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/repeats/bulk`, { body: data })
}

/** 반복 구간이 든 보기 문서 몇 개와 그 안의 구간(앞뒤 글과 함께). */
export function getRepeatSamples(repeatId: number, signal?: AbortSignal): Promise<RepeatSampleRead[]> {
  return request('GET', `${BASE}/repeats/${repeatId}/samples`, { signal })
}

/** 반복 구간 다시 살피기 작업을 넣는다. */
export function scanRepeats(datasetId: number): Promise<{ job_id: number }> {
  return request('POST', `${BASE}/datasets/${datasetId}/repeats/scan`)
}

/** 휴지통 비우기(queries 또는 documents). changed는 영구히 지운 수. */
export function emptyTrash(datasetId: number, kind: 'queries' | 'documents'): Promise<ChangedRead> {
  return request('DELETE', `${BASE}/datasets/${datasetId}/trash`, { query: { kind } })
}

// ---------- 가져오기 ----------

/** 원본의 열 이름으로 모양과 칸을 짐작한다. */
export function suggestMapping(data: MappingSuggestCreate): Promise<FieldMapping> {
  return request('POST', `${BASE}/mapping/suggest`, { body: data })
}

/** 가져오기를 시작한다. 새 데이터셋 이름이 이미 있으면 409. 진행은 job_id로 본다. */
export function createImport(data: ImportCreate): Promise<ImportRead> {
  return request('POST', `${BASE}/imports`, { body: data })
}

// ---------- 진단 ----------

/** 진단: 흐름 · 단계별 검사 · 기준 검색 점수 · 분포. */
export function getOverview(datasetId: number, signal?: AbortSignal): Promise<OverviewRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/overview`, { signal })
}

// ---------- 질의 ----------

/** 질의 한 쪽. 거르기는 query로. */
export function listQueries(
  datasetId: number,
  query: QueryListQuery = {},
  signal?: AbortSignal,
): Promise<QueryPageRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/queries`, { query: { ...query }, signal })
}

/** 질의 패널: 질의 · 판정 · 기준 검색 순위. */
export function getQuery(queryId: number, signal?: AbortSignal): Promise<QueryDetailRead> {
  return request('GET', `${BASE}/queries/${queryId}`, { signal })
}

/** 질의 고치기. row_version이 다르면 409. */
export function updateQuery(queryId: number, data: QueryUpdate): Promise<QueryDetailRead> {
  return request('PATCH', `${BASE}/queries/${queryId}`, { body: data })
}

/** 여러 질의에 한 번에(빼기 · 넣기 · 휴지통 · 되살리기). */
export function bulkUpdateQueries(datasetId: number, data: QueryBulkUpdate): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/queries/bulk`, { body: data })
}

// ---------- 문서 ----------

/** 문서 한 쪽. 거르기는 query로. */
export function listDocuments(
  datasetId: number,
  query: DocumentListQuery = {},
  signal?: AbortSignal,
): Promise<DocumentPageRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/documents`, { query: { ...query }, signal })
}

/** 문서 패널: 문서 · 쓰는 질의 · 가까운 질의 · 만든 질의 · 같은 원문의 청크. */
export function getDocument(documentId: number, signal?: AbortSignal): Promise<DocumentDetailRead> {
  return request('GET', `${BASE}/documents/${documentId}`, { signal })
}

/** 문서 고치기. row_version이 다르면 409. */
export function updateDocument(documentId: number, data: DocumentUpdate): Promise<DocumentDetailRead> {
  return request('PATCH', `${BASE}/documents/${documentId}`, { body: data })
}

/** 여러 문서에 한 번에(휴지통 · 되살리기 · 질의 안 만듦 · 만듦). */
export function bulkUpdateDocuments(datasetId: number, data: DocumentBulkUpdate): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/documents/bulk`, { body: data })
}

/** 문서 하나를 청크로 나눈다(원문은 가리고 청크를 더한다). changed는 만든 청크 수. 길지 않으면 422. */
export function splitDocument(documentId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/documents/${documentId}/split`)
}

/** 나누기 되돌리기: 청크를 지우고 원문을 되살린다. changed는 지운 청크 수. */
export function unsplitDocument(documentId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/documents/${documentId}/unsplit`)
}

// ---------- 판정 ----------

/** 판정 두기(등급 0 = 오답 · 1~3 = 정답). */
export function setJudgment(queryId: number, documentId: number, grade: number): Promise<JudgmentRead> {
  return request('PUT', `${BASE}/queries/${queryId}/judgments/${documentId}`, { body: { grade } })
}

/** 판정 떼기(모름으로). */
export function deleteJudgment(queryId: number, documentId: number): Promise<void> {
  return request('DELETE', `${BASE}/queries/${queryId}/judgments/${documentId}`)
}

// ---------- 뜻 분석 ----------

/** 뜻 분석 상태: 다 만든 것 · 도는 것 · 바뀜 · 모델. */
export function getAnalysis(datasetId: number, signal?: AbortSignal): Promise<AnalysisStateRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map`, { signal })
}

/** 뜻 분석 시작. 이미 도는 중이면 409, 임베딩 미연결이면 422. */
export function startAnalysis(datasetId: number): Promise<AnalysisStateRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/map`)
}

/** 도는 뜻 분석 멈추기. */
export function cancelAnalysis(datasetId: number): Promise<AnalysisStateRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/map/cancel`)
}

/** 지도의 점들. 뜻 분석이 없으면 404. */
export function getMapPoints(datasetId: number, signal?: AbortSignal): Promise<MapPointsRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/points`, { signal })
}

/** 검색어가 든 질의 · 문서 번호들. */
export function getMapMatches(
  datasetId: number,
  q: string,
  signal?: AbortSignal,
): Promise<{ query_ids: number[]; document_ids: number[] }> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/matches`, { query: { q }, signal })
}

/** 지도 점 하나의 글(말풍선). 질의면 질의 글, 문서면 제목 · 앞부분. */
export function getMapText(
  datasetId: number,
  kind: 'query' | 'document',
  itemId: number,
  signal?: AbortSignal,
): Promise<{ text: string; title: string }> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/text`, { query: { kind, item_id: itemId }, signal })
}

// ---------- 제안 ----------

/** 제안 목록과 종류별 대기 수. */
export function listSuggestions(
  datasetId: number,
  query: SuggestionListQuery = {},
  signal?: AbortSignal,
): Promise<SuggestionPageRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/suggestions`, { query: { ...query }, signal })
}

/** 제안 수락(판정을 바꾼다). */
export function acceptSuggestion(suggestionId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/suggestions/${suggestionId}/accept`)
}

/** 제안 유지(판정 그대로, 다음 분석에서도 묻지 않음). */
export function keepSuggestion(suggestionId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/suggestions/${suggestionId}/keep`)
}

/** Jev가 확인한 제안을 모두 수락. */
export function acceptConfirmedSuggestions(datasetId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/suggestions/accept-confirmed`)
}

// ---------- 도우미 ----------

/** 데이터셋의 가장 최근 도우미 실행과 되돌린 카드 번호들. */
export function getHelper(datasetId: number, signal?: AbortSignal): Promise<HelperStateRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/helper`, { signal })
}

/** 도우미 시작. LLM이 없으면 422, 이미 도는 중이면 409. */
export function startHelper(datasetId: number): Promise<HelperRunRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/helper`)
}

/** 도우미가 끝난 뒤 남은 것 (지금 진단으로 센다) */
export function getHelperLeft(datasetId: number, signal?: AbortSignal): Promise<HelperLeftRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/helper/left`, { signal })
}

/** 고른 남은 것을 AI로 고치기 (검색은 더할 수 칸이 없어 adds를 보내지 않는다) */
export function fixHelper(datasetId: number, keys: string[]): Promise<HelperRunRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/helper/fix`, { body: { keys } })
}

/** 실행 설정 카드의 어림: 고른 청크 크기 · 오버랩 · 청크마다 질의 수로 나누고 만들면 몇이 되나 */
export function estimateHelper(
  datasetId: number,
  query: { chunk_tokens: number; overlap: number; per_chunk: number },
  signal?: AbortSignal,
): Promise<HelperEstimateRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/helper/estimate`, { query: { ...query }, signal })
}

/** 도우미가 바꾼 것을 되돌린다(eventId가 있으면 그 카드만). */
export function undoHelper(runId: number, eventId?: number): Promise<HelperUndoRead> {
  return request('POST', `${BASE}/helper/${runId}/undo`, { body: { event_id: eventId ?? null } })
}

/** 허락에 답한다(문서 나누기 · 질의 만들기). action = chunk_first면 질의 만들기 허락 카드의 [먼저 나누기]. */
export function answerHelperPermission(runId: number, approve: boolean, action?: string): Promise<HelperRunRead> {
  return request('POST', `${BASE}/helper/${runId}/permission`, { body: { approve, action: action ?? null } })
}

// ---------- 내보내기 · 다시 찾기 ----------

/** 내보내기 미리 보기: 한 줄과 파일마다 수. */
export function previewExport(datasetId: number, data: ExportCreate): Promise<ExportPreviewRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/exports/preview`, { body: data })
}

/** 내보내기 시작(고른 파일 모양마다 하나). 파일은 /api/v1/exports/{id}/file로 받는다. 심각이 남았으면 409. */
export function createExport(datasetId: number, data: ExportCreate): Promise<ExportRead[]> {
  return request('POST', `${BASE}/datasets/${datasetId}/exports`, { body: data })
}

/** 오답 찾기: 오답 풀이 모자란 질의만 채우는 작업을 넣는다. 임베딩 미연결이면 422. */
export function mine(datasetId: number): Promise<{ job_id: number }> {
  return request('POST', `${BASE}/datasets/${datasetId}/mine`)
}

/** 오답 다시 찾기(찾은 오답을 떼고 지금 설정으로 다시). 작업 번호를 돌려준다. */
export function remine(datasetId: number): Promise<{ job_id: number }> {
  return request('POST', `${BASE}/datasets/${datasetId}/remine`)
}

/** 오답 훑기: 순위 묶음 × 문턱의 거짓 오답 비율을 재는 작업. 결과는 설정의 negative_scan. Jev 미연결이면 422. */
export function scanNegatives(datasetId: number): Promise<{ job_id: number }> {
  return request('POST', `${BASE}/datasets/${datasetId}/negative-scan`)
}

// ---------- 내장 예시 데이터 ----------

/** 내장 예시 데이터 목록 (심은 문제 · 이미 넣었으면 그 데이터셋 번호) */
export function listExamples(signal?: AbortSignal): Promise<ExampleRead[]> {
  return request('GET', `${BASE}/examples`, { signal })
}

/** 내장 예시를 새 데이터셋으로 가져오기 시작. 이미 넣었으면 409. */
export function installExample(key: string): Promise<ImportRead> {
  return request('POST', `${BASE}/examples/${key}`)
}
