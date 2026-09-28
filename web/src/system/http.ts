// API를 부르는 곳. 모든 요청은 여기를 거친다.
// 실패하면 화면에 그대로 보여 줄 한국어 문장을 담은 ApiError를 던진다.

// 요청 방식
export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'

// 주소 뒤에 붙일 값. null·undefined·빈 문자열은 붙이지 않는다.
export type QueryValue = string | number | boolean | null | undefined

export interface RequestOptions {
  // 주소 뒤 ?a=1&b=2 로 붙일 값
  query?: Record<string, QueryValue>
  // 보낼 내용. FormData면 그대로, 아니면 JSON으로 보낸다.
  body?: unknown
  // 화면을 떠날 때 요청을 끊는 신호
  signal?: AbortSignal
}

// 서버에 붙지 못했을 때(종료됨, 네트워크 끊김)의 문장
const UNREACHABLE_MESSAGE = 'API 서버에 붙지 못했습니다. DENT가 실행 중인지 확인하세요.'

// 입력값 검사(FastAPI 422)가 목록으로 올 때의 문장
const INVALID_INPUT_MESSAGE = '입력값을 확인하세요.'

// 한글이 한 글자라도 있는지. 도메인 예외의 detail은 한국어 문장이고,
// FastAPI가 스스로 붙이는 detail("Not Found" 등)은 영어라서 이것으로 가른다.
const HANGUL_PATTERN = /[가-힣]/

// 서버가 이유를 알려 주지 않았을 때 상태 코드별 문장
const MESSAGE_BY_STATUS: Record<number, string> = {
  404: '찾을 수 없습니다. 주소가 바뀌었거나 지워졌을 수 있습니다.',
  413: '파일이 너무 큽니다.',
  500: '서버에서 문제가 생겼습니다. 런처 창의 로그를 확인하세요.',
  // 바깥 서비스 문제(ExternalServiceError)는 늘 한국어 detail을 준다. detail 없는 502는
  // 개발 서버(pnpm dev)가 종료된 API로 넘기지 못한 것이다.
  502: UNREACHABLE_MESSAGE,
  503: 'DENT가 아직 준비되지 않았습니다. 상태 화면을 확인하세요.',
}

/** API가 실패했다는 것. message는 사용자에게 그대로 보여 줄 한국어 문장이다. */
export class ApiError extends Error {
  // HTTP 상태 코드. 서버에 붙지 못했으면 0
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 요청을 보내고 JSON 답을 돌려준다. 실패하면 ApiError, 끊으면 AbortError를 던진다. */
export async function request<T>(
  method: HttpMethod,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const isJson = options.body !== undefined && !(options.body instanceof FormData)
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (isJson) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body: encodeBody(options.body),
      signal: options.signal,
    })
  } catch (error) {
    if (isAbortError(error)) throw error
    throw new ApiError(UNREACHABLE_MESSAGE, 0)
  }
  return readResponse<T>(response.status, response.ok, await response.text())
}

export interface UploadOptions {
  // 올린 바이트 수를 알려 준다. 큰 파일의 진행률을 보여 줄 때 쓴다.
  onProgress?: (loadedBytes: number, totalBytes: number) => void
  // 올리기를 끊는 신호
  signal?: AbortSignal
}

/** 파일을 올리고 JSON 답을 돌려준다. fetch는 올리는 진행률을 모르므로 XMLHttpRequest를 쓴다. */
export function upload<T>(path: string, form: FormData, options: UploadOptions = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (event) => options.onProgress?.(event.loaded, event.total)
    xhr.onload = () => {
      try {
        const isOk = xhr.status >= 200 && xhr.status < 300
        resolve(readResponse<T>(xhr.status, isOk, xhr.responseText))
      } catch (error) {
        reject(error)
      }
    }
    xhr.onerror = () => reject(new ApiError(UNREACHABLE_MESSAGE, 0))
    xhr.onabort = () => reject(new DOMException('올리기를 멈췄습니다.', 'AbortError'))
    options.signal?.addEventListener('abort', () => xhr.abort())
    xhr.send(form)
  })
}

/** 끊은 요청에서 난 에러인지. 끊은 요청은 화면에 알리지 않는다. */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/** 어떤 에러든 화면에 보여 줄 한국어 문장으로 바꾼다. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return '알 수 없는 문제가 생겼습니다. 새로고침해 보세요.'
}

// FormData는 브라우저가 경계 표시를 붙여 보내도록 그대로 넘기고, 나머지는 JSON 글자로 바꾼다.
function encodeBody(body: unknown): BodyInit | undefined {
  if (body === undefined) return undefined
  if (body instanceof FormData) return body
  return JSON.stringify(body)
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    const isEmpty = value === null || value === undefined || value === ''
    if (!isEmpty) params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `${path}?${text}` : path
}

// 답의 본문을 읽는다. 실패면 서버가 준 detail을 문장으로 쓴다.
function readResponse<T>(status: number, ok: boolean, text: string): T {
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (ok) return body as T
  throw new ApiError(messageFromBody(status, body), status)
}

function messageFromBody(status: number, body: unknown): string {
  const isObject = body !== null && typeof body === 'object'
  const detail = isObject ? (body as { detail?: unknown }).detail : undefined
  const isKoreanSentence = typeof detail === 'string' && HANGUL_PATTERN.test(detail)
  if (isKoreanSentence) return detail as string
  if (Array.isArray(detail)) return INVALID_INPUT_MESSAGE
  return MESSAGE_BY_STATUS[status] ?? `요청이 실패했습니다 (${status}).`
}
