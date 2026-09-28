// 분류 모듈 API를 부르는 함수. 주소 하나에 함수 하나이고, 이름은 백엔드 라우터 함수와 같다.
// 모두 /api/v1/classification 아래에 있다. 실패하면 system/http.ts의 ApiError(한국어 문장)를 던진다.
// 파일 올리기·허깅페이스 미리 보기·데이터셋 이름 고치기·가져오기 기록·작업은 system/api.ts에 있다.
import { request } from '@/system/http'
import type { FixAdds } from '@/system/helper/useHelper'
import type { ExampleRead, ExportRead, HelperLeftRead, HelperRunRead, ImportRead } from '@/system/types'

import type {
  BulkUpdate,
  ChangedRead,
  DatasetRead,
  DatasetSummaryRead,
  ExportCreate,
  ExportPreviewRead,
  HelperStateRead,
  HelperUndoRead,
  ImportCreate,
  LabelCreate,
  LabelRead,
  LabelUpdate,
  MapMatchesRead,
  MappingSuggestCreate,
  MapPointsRead,
  MapStateRead,
  NearDuplicateRead,
  OverviewRead,
  RecordPageRead,
  RecordQuery,
  RecordRead,
  RecordUpdate,
  SettingsRead,
  SettingsUpdate,
  SuggestedMapping,
  SuspectAccept,
  SuspectPageRead,
  SuspectQuery,
} from '@/modules/classification/types'

const BASE = '/api/v1/classification'

// ---------- 데이터셋 ----------

/** 분류 데이터셋 목록과 건수. 최근에 고친 것부터. */
export function listDatasets(signal?: AbortSignal): Promise<DatasetSummaryRead[]> {
  return request('GET', `${BASE}/datasets`, { signal })
}

/** 데이터셋 하나와 라벨. 없으면 404. */
export function getDataset(datasetId: number, signal?: AbortSignal): Promise<DatasetRead> {
  return request('GET', `${BASE}/datasets/${datasetId}`, { signal })
}

/** 도우미 실행 설정 고치기 (새 문장 목표 배율). */
export function updateSettings(datasetId: number, data: SettingsUpdate): Promise<SettingsRead> {
  return request('PATCH', `${BASE}/datasets/${datasetId}/settings`, { body: data })
}

/** 데이터셋을 문장 · 라벨 · 의미 지도 · 가져오기 기록까지 지운다. 가져오거나 지도를 만드는 중이면 409. */
export function deleteDataset(datasetId: number): Promise<void> {
  return request('DELETE', `${BASE}/datasets/${datasetId}`)
}

/** 휴지통 비우기: 휴지통의 문장을 영구히 지운다. changed는 지운 수. */
export function emptyTrash(datasetId: number): Promise<ChangedRead> {
  return request('DELETE', `${BASE}/datasets/${datasetId}/trash`)
}

// ---------- 라벨 ----------

/** 라벨을 더한다. 같은 이름이 있으면 409. */
export function createLabel(datasetId: number, data: LabelCreate): Promise<LabelRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/labels`, { body: data })
}

/** 라벨 이름·설명을 바꾼다. */
export function updateLabel(labelId: number, data: LabelUpdate): Promise<LabelRead> {
  return request('PATCH', `${BASE}/labels/${labelId}`, { body: data })
}

// ---------- 가져오기 ----------

/** 원본의 열 이름으로 문장·라벨 열을 짐작한다. 못 찾으면 null. */
export function suggestMapping(data: MappingSuggestCreate): Promise<SuggestedMapping> {
  return request('POST', `${BASE}/mapping/suggest`, { body: data })
}

/** 가져오기를 시작한다. 새 데이터셋 이름이 이미 있으면 409. 진행은 job_id로 본다. */
export function createImport(data: ImportCreate): Promise<ImportRead> {
  return request('POST', `${BASE}/imports`, { body: data })
}

// ---------- 레코드 ----------

/** 레코드 한 쪽. 거르기는 query로. 중복·충돌·same_as로 거르면 같은 문장끼리 붙어 나온다. */
export function listRecords(
  datasetId: number,
  query: RecordQuery = {},
  signal?: AbortSignal,
): Promise<RecordPageRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/records`, { query: { ...query }, signal })
}

/** 레코드 하나. 없으면 404. 의미 지도에서 점에 올렸을 때 문장을 보인다. */
export function getRecord(recordId: number, signal?: AbortSignal): Promise<RecordRead> {
  return request('GET', `${BASE}/records/${recordId}`, { signal })
}

/** 이 문장과 뜻이 가까운(근접 중복) 다른 문장들. 유사도가 높은 것부터. 뜻 분석이 없으면 빈 목록. */
export function listNearDuplicates(recordId: number, signal?: AbortSignal): Promise<NearDuplicateRead[]> {
  return request('GET', `${BASE}/records/${recordId}/near-duplicates`, { signal })
}

/** 레코드 하나를 고친다. row_version이 다르면 409(다른 곳에서 먼저 고침). */
export function updateRecord(recordId: number, data: RecordUpdate): Promise<RecordRead> {
  return request('PATCH', `${BASE}/records/${recordId}`, { body: data })
}

/** 여러 건을 한 번에 빼기·넣기·휴지통·되살리기·라벨 바꾸기. */
export function bulkUpdateRecords(datasetId: number, data: BulkUpdate): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/records/bulk`, { body: data })
}

/** 문장·라벨이 모두 같은 묶음마다 한 건만 남기고 나머지를 학습에서 뺀다. */
export function cleanupDuplicates(datasetId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/cleanup/duplicates`)
}

/** 중복 정리로 뺀 것을 모두 다시 넣는다. */
export function undoDuplicateCleanup(datasetId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/cleanup/duplicates/undo`)
}

// ---------- 진단 ----------

/** 진단 화면의 숫자 전부: 건수, 라벨별 모습, 균형·중복·충돌·짧은 문장, 손볼 곳. */
export function getOverview(datasetId: number, signal?: AbortSignal): Promise<OverviewRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/overview`, { signal })
}

// ---------- 의미 지도 · 뜻 분석 ----------

/** 뜻 분석 상태: 다 만든 지도와 요약(checks) · 그 뒤의 시도(만드는 중 · 실패 · 취소) · 지도 이후 바뀜. */
export function getMap(datasetId: number, signal?: AbortSignal): Promise<MapStateRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map`, { signal })
}

/** 뜻 분석(의미 지도 · 근접 중복 · 오라벨 의심 · 의미 쏠림) 시작. 진행률은 run.job_id로 본다. 이미 만드는 중이면 409, 임베딩 미연결이면 422. */
export function startMap(datasetId: number): Promise<MapStateRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/map`)
}

/** 만드는 중인 뜻 분석을 멈춘다. 만드는 중인 것이 없으면 409. */
export function cancelMap(datasetId: number): Promise<MapStateRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/map/cancel`)
}

/** 의미 지도의 점들(칸마다 목록). 지도가 없으면 404. */
export function getMapPoints(datasetId: number, signal?: AbortSignal): Promise<MapPointsRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/points`, { signal })
}

/** 검색어가 든 문장 번호들(휴지통 밖). 지도가 검색어에 맞는 점을 강조할 때 쓴다. */
export function getMapMatches(datasetId: number, q: string, signal?: AbortSignal): Promise<MapMatchesRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/map/matches`, { query: { q }, signal })
}

// ---------- 제안: 오라벨 의심 ----------

/** 오라벨 의심 목록. Jev가 확인한 것 · 지금 라벨 확률이 낮은 것부터. 뜻 분석이 없으면 404. */
export function listSuspects(
  datasetId: number,
  query: SuspectQuery = {},
  signal?: AbortSignal,
): Promise<SuspectPageRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/suspects`, { query: { ...query }, signal })
}

/** 수락: 추천(또는 준) 라벨로 바꾼다. changed는 바뀐 문장 수. 이미 판단했으면 409. */
export function acceptSuspect(
  datasetId: number,
  textHash: string,
  data: SuspectAccept = {},
): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/suspects/${textHash}/accept`, { body: data })
}

/** 유지: 라벨을 그대로 두고 다음 뜻 분석에서도 다시 묻지 않는다. 이미 판단했으면 409. */
export function keepSuspect(datasetId: number, textHash: string): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/suspects/${textHash}/keep`)
}

/** Jev도 확인한 의심을 모두 추천 라벨로 수락한다. changed는 수락한 의심 수. */
export function acceptConfirmedSuspects(datasetId: number): Promise<ChangedRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/suspects/accept-confirmed`)
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

/** 도우미가 끝난 뒤 남은 것 (지금 진단으로 센다) */
export function getHelperLeft(datasetId: number, signal?: AbortSignal): Promise<HelperLeftRead> {
  return request('GET', `${BASE}/datasets/${datasetId}/helper/left`, { signal })
}

/** 고른 남은 것을 AI로 고치기 */
export function fixHelper(datasetId: number, keys: string[], adds: FixAdds = {}): Promise<HelperRunRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/helper/fix`, { body: { keys, adds } })
}

/** 도우미가 바꾼 것을 되돌린다(eventId가 있으면 그 카드만). 도는 중이면 409. */
export function undoHelper(runId: number, eventId?: number): Promise<HelperUndoRead> {
  return request('POST', `${BASE}/helper/${runId}/undo`, { body: { event_id: eventId ?? null } })
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

// ---------- 내보내기 ----------

/** 내보내기 미리 보기: 첫 줄 · 파일마다 수 · 출처별 문장 수 · 남은 심각 · 주의. */
export function previewExport(datasetId: number, data: ExportCreate): Promise<ExportPreviewRead> {
  return request('POST', `${BASE}/datasets/${datasetId}/exports/preview`, { body: data })
}

/** 내보내기 시작(고른 파일 모양마다 하나). 파일은 /api/v1/exports/{id}/file로 받는다. 넣을 문장이 없으면 422. */
export function createExport(datasetId: number, data: ExportCreate): Promise<ExportRead[]> {
  return request('POST', `${BASE}/datasets/${datasetId}/exports`, { body: data })
}
