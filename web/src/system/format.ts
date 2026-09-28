// 숫자·날짜·글자를 화면에 보이는 모양으로 바꾸는 도구.

const SECOND_MS = 1000
const MINUTE_MS = 60 * SECOND_MS
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS
// 이보다 오래된 시각은 "n일 전" 대신 날짜로 쓴다.
const RELATIVE_DAYS_LIMIT = 7

// 파일 크기 단위(바이트)
const KB = 1024
const MB = KB * 1024
const GB = MB * 1024

// 한글 음절의 처음과 끝(가~힣). 받침을 계산할 때 쓴다.
const HANGUL_FIRST = 0xac00
const HANGUL_LAST = 0xd7a3
// 한글 음절 하나에 들어가는 받침의 가짓수(없음 포함)
const JONG_COUNT = 28
// 받침 ㄹ의 번호. '으로/로'는 ㄹ 받침 뒤에 '로'를 쓴다.
const JONG_RIEUL = 8
// 한글이 아닌 끝 글자는 읽는 소리로 받침을 정한다.
//   받침이 있는 것: m·n(train → 트레인), 0 영·3 삼·6 육
//   ㄹ 받침인 것: l(label → 레이블), 1 일·7 칠·8 팔
//   그 밖(test → 테스트, valid → 밸리드, 2 이)은 받침이 없는 것으로 본다.
const BATCHIM_ENDINGS = new Set(['m', 'n', '0', '3', '6'])
const RIEUL_ENDINGS = new Set(['l', '1', '7', '8'])

// 낱말 끝소리: 받침 없음 · 받침 있음 · ㄹ 받침
type Ending = 'none' | 'batchim' | 'rieul'

/** 숫자에 쉼표를 넣는다. 1234.6 → "1,235" */
export function fmt(value: number): string {
  return Math.round(value).toLocaleString('ko-KR')
}

/** 비율을 백분율 정수로. pct(3, 10) → 30, 전체가 0이면 0 */
export function pct(part: number, whole: number): number {
  return whole ? Math.round((part / whole) * 100) : 0
}

/** 백분율 글자. 조금이라도 있는데 반올림해서 0%가 되면 "1% 미만"으로 쓴다. pctText(2, 54785) → "1% 미만" */
export function pctText(part: number, whole: number): string {
  const percent = pct(part, whole)
  const isTinyButPresent = part > 0 && percent === 0
  return isTinyButPresent ? '1% 미만' : `${percent}%`
}

/**
 * 받침에 따라 조사를 고른다. jo('배송', '이', '가') → '배송이', jo('환불', '으로', '로') → '환불로',
 * jo('train', '으로', '로') → 'train으로', jo('test', '으로', '로') → 'test로'
 */
export function jo(word: string, withBatchim: string, withoutBatchim: string): string {
  const ending = endingOf(word)
  // '으로'는 ㄹ 받침 뒤에 쓰지 않는다(서울로, 레이블로).
  if (withBatchim === '으로') return word + (ending === 'batchim' ? '으로' : '로')
  return word + (ending === 'none' ? withoutBatchim : withBatchim)
}

/** 낱말의 끝소리. 한글은 받침으로, 영어·숫자는 읽는 소리로 정한다. */
function endingOf(word: string): Ending {
  const last = word.slice(-1).toLowerCase()
  const code = last.charCodeAt(0)
  const isHangul = code >= HANGUL_FIRST && code <= HANGUL_LAST
  if (isHangul) {
    const jong = (code - HANGUL_FIRST) % JONG_COUNT
    if (jong === 0) return 'none'
    return jong === JONG_RIEUL ? 'rieul' : 'batchim'
  }
  if (RIEUL_ENDINGS.has(last)) return 'rieul'
  // 'ng'(-ing, -ong)는 ㅇ 받침으로 읽는다.
  const endsWithNg = word.toLowerCase().endsWith('ng')
  if (BATCHIM_ENDINGS.has(last) || endsWithNg) return 'batchim'
  return 'none'
}

/** 시각을 "방금", "3분 전", "2시간 전", "어제", "3일 전", 그보다 오래되면 "2026-09-12"로. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const time = new Date(iso).getTime()
  const elapsed = Date.now() - time
  if (elapsed < MINUTE_MS) return '방금'
  if (elapsed < HOUR_MS) return `${Math.floor(elapsed / MINUTE_MS)}분 전`
  if (elapsed < DAY_MS) return `${Math.floor(elapsed / HOUR_MS)}시간 전`
  const days = Math.floor(elapsed / DAY_MS)
  if (days === 1) return '어제'
  if (days < RELATIVE_DAYS_LIMIT) return `${days}일 전`
  return dateText(iso)
}

/** 날짜만. "2026-09-23" */
export function dateText(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 날짜와 시각. "2026-09-23 10:12" */
export function dateTimeText(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  return `${dateText(iso)} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 시각. 오늘이면 "14:05:09", 다른 날이면 "09-24 23:24". */
export function clockText(iso: string | null | undefined): string {
  if (!iso) return ''
  const date = new Date(iso)
  const isToday = date.toDateString() === new Date().toDateString()
  if (isToday) return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 걸린 시간. "0.4초", "12초", "3분 5초", "1시간 2분". 끝 시각이 없으면 빈 글자. */
export function durationText(startIso: string | null | undefined, endIso: string | null | undefined): string {
  if (!startIso || !endIso) return ''
  const elapsed = Math.max(0, new Date(endIso).getTime() - new Date(startIso).getTime())
  if (elapsed < SECOND_MS * 10) return `${(elapsed / SECOND_MS).toFixed(1)}초`
  if (elapsed < MINUTE_MS) return `${Math.round(elapsed / SECOND_MS)}초`
  if (elapsed < HOUR_MS) {
    const seconds = Math.round((elapsed % MINUTE_MS) / SECOND_MS)
    return `${Math.floor(elapsed / MINUTE_MS)}분 ${seconds}초`
  }
  return `${Math.floor(elapsed / HOUR_MS)}시간 ${Math.floor((elapsed % HOUR_MS) / MINUTE_MS)}분`
}

/** 파일 크기. 1234567 → "1.2 MB" */
export function fileSize(bytes: number): string {
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / KB))} KB`
}

/** 긴 글을 n자에서 자르고 …을 붙인다. */
export function clip(text: string, length: number): string {
  return text.length > length ? text.slice(0, length) + '…' : text
}

/** 표 칸에 넣을 값을 글자로. null·undefined는 빈 글자, 목록·객체는 JSON. */
export function showValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}
