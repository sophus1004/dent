// 시스템 API를 부르는 함수: 올린 파일, 허깅페이스 미리 보기, 데이터셋 목록, 가져오기 기록, 작업, 내보낸 파일, 외부 모델 연결.
// 주소 하나에 함수 하나이고, 이름은 백엔드 라우터 함수와 같다. 모두 /api/v1 아래에 있다.
// 실패하면 http.ts의 ApiError(한국어 문장)를 던진다. 모듈만의 API는 각 모듈의 api.ts에 있다.
import { request, upload, type UploadOptions } from '@/system/http'
import type {
  ConnectionCheckRead,
  ConnectionRead,
  ConnectionRole,
  ConnectionUpdate,
  DatasetRead,
  DatasetUpdate,
  ExportRead,
  FilePreviewRead,
  HelperEventRead,
  HelperRunRead,
  HuggingFacePreviewRead,
  HuggingFacePreviewRequest,
  ImportRead,
  JobPageRead,
  JobQuery,
  JobRead,
} from '@/system/types'

const BASE = '/api/v1'

// ---------- 올린 파일 ----------

/** 파일(.csv·.tsv·.xlsx)을 올리고 처음 20줄을 미리 본다. 진행률은 options.onProgress로 받는다. */
export function uploadFile(file: File, options: UploadOptions = {}): Promise<FilePreviewRead> {
  const form = new FormData()
  form.append('file', file)
  return upload(`${BASE}/uploads`, form, options)
}

/** 올린 파일의 다른 시트를 미리 본다. 올린 파일이 없으면 404. */
export function previewUpload(
  uploadId: string,
  sheet: string | null,
  signal?: AbortSignal,
): Promise<FilePreviewRead> {
  return request('GET', `${BASE}/uploads/${uploadId}/preview`, { query: { sheet }, signal })
}

// ---------- 허깅페이스 ----------

/** 허깅페이스 데이터셋을 미리 본다. 없으면 404, 인터넷이 안 되면 502. */
export function previewHuggingFace(
  data: HuggingFacePreviewRequest,
  signal?: AbortSignal,
): Promise<HuggingFacePreviewRead> {
  return request('POST', `${BASE}/huggingface/preview`, { body: data, signal })
}

// ---------- 데이터셋 목록 ----------

/** 모든 모듈의 데이터셋 목록. 최근에 고친 것부터. */
export function listDatasets(signal?: AbortSignal): Promise<DatasetRead[]> {
  return request('GET', `${BASE}/datasets`, { signal })
}

/** 데이터셋 이름·설명을 바꾼다. 같은 모듈에 같은 이름이 있으면 409. */
export function updateDataset(datasetId: number, data: DatasetUpdate): Promise<DatasetRead> {
  return request('PATCH', `${BASE}/datasets/${datasetId}`, { body: data })
}

/** 데이터셋의 가져오기 기록. 최근 것부터. */
export function listImports(datasetId: number, signal?: AbortSignal): Promise<ImportRead[]> {
  return request('GET', `${BASE}/datasets/${datasetId}/imports`, { signal })
}

// ---------- 가져오기 기록 · 작업 ----------

/** 가져오기 하나의 결과. */
export function getImport(importId: number, signal?: AbortSignal): Promise<ImportRead> {
  return request('GET', `${BASE}/imports/${importId}`, { signal })
}

/** 작업 하나를 읽는다. 없으면 ApiError(404). */
export function getJob(jobId: number, signal?: AbortSignal): Promise<JobRead> {
  return request('GET', `${BASE}/jobs/${jobId}`, { signal })
}

/** 작업 목록 한 쪽(최근에 넣은 것부터)과 상태별 수. status는 여러 개를 함께 거를 수 있다. */
export function listJobs(query: JobQuery, signal?: AbortSignal): Promise<JobPageRead> {
  // 같은 이름(status)을 여러 번 보내야 해서 주소 뒤를 직접 만든다.
  const params = new URLSearchParams()
  for (const status of query.status ?? []) params.append('status', status)
  for (const [key, value] of Object.entries(query)) {
    const isSimple = key !== 'status' && value !== undefined && value !== null && value !== ''
    if (isSimple) params.set(key, String(value))
  }
  const search = params.toString()
  return request('GET', `${BASE}/jobs${search ? `?${search}` : ''}`, { signal })
}

// ---------- 내보낸 파일 ----------

/** 데이터셋의 내보내기 기록(최근 것부터). */
export function listExports(datasetId: number, signal?: AbortSignal): Promise<ExportRead[]> {
  return request('GET', `${BASE}/exports`, { query: { dataset_id: datasetId }, signal })
}

/** 내보내기 한 번. 없으면 404. */
export function getExport(exportId: number, signal?: AbortSignal): Promise<ExportRead> {
  return request('GET', `${BASE}/exports/${exportId}`, { signal })
}

/** 내보낸 파일을 내려받는 주소. <a href download>로 쓴다. */
export function exportFileUrl(exportId: number): string {
  return `${BASE}/exports/${exportId}/file`
}

// ---------- 외부 모델 연결 ----------

/** 세 역할(임베딩 · Jev · LLM)의 연결과 마지막 확인 결과. */
export function listConnections(signal?: AbortSignal): Promise<ConnectionRead[]> {
  return request('GET', `${BASE}/connections`, { signal })
}

/** 저장하지 않고 연결만 확인한다. 주소 모양이 틀리면 422. */
export function checkConnection(role: ConnectionRole, data: ConnectionUpdate): Promise<ConnectionCheckRead> {
  return request('POST', `${BASE}/connections/${role}/check`, { body: data })
}

/** 연결을 저장하고 바로 확인한 결과를 받는다. 주소 모양이 틀리면 422. */
export function saveConnection(role: ConnectionRole, data: ConnectionUpdate): Promise<ConnectionRead> {
  return request('PUT', `${BASE}/connections/${role}`, { body: data })
}

/** 연결을 끊는다(저장한 연결을 지운다). 없으면 404. */
export function deleteConnection(role: ConnectionRole): Promise<void> {
  return request('DELETE', `${BASE}/connections/${role}`)
}

// ---------- LLM 도우미 ----------

/** 도우미 실행 한 번의 상태 · 단계 줄 · 사용량. 없으면 404. */
export function getHelperRun(runId: number, signal?: AbortSignal): Promise<HelperRunRead> {
  return request('GET', `${BASE}/helper/runs/${runId}`, { signal })
}

/** after 번호 다음 사건들(번호 순서). 도우미 창이 1초마다 이어 읽는다. */
export function listHelperEvents(runId: number, after: number, signal?: AbortSignal): Promise<HelperEventRead[]> {
  return request('GET', `${BASE}/helper/runs/${runId}/events`, { query: { after }, signal })
}

/** [멈추기]. 도우미는 다음 도구를 부르기 전에 멈춘다. 도는 중이 아니면 409. */
export function stopHelperRun(runId: number): Promise<HelperRunRead> {
  return request('POST', `${BASE}/helper/runs/${runId}/stop`)
}
