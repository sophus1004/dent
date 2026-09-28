// 가져오기 1·2단계에서 불러온 원본(올린 파일 · 허깅페이스)의 모양과, 그것을 읽는 작은 도구들.
// 모듈의 3단계(필드 맞추기)와 가져오기 입력이 이 모양 하나만 읽는다. 원본을 줄로 읽는 일은 서버가 한다.
import type {
  FilePreviewRead,
  HuggingFacePreviewRead,
  PreviewRow,
  SourceCreate,
} from '@/system/types'

// 올린 파일
export interface FileSource {
  source: 'file'
  // 사용자가 고른 파일의 원래 이름. 서버가 저장한 이름(preview.file_name)은 공백 등이 _로 바뀐다.
  fileName: string
  preview: FilePreviewRead
}

// 허깅페이스 데이터셋
export interface HuggingFaceSource {
  source: 'huggingface'
  preview: HuggingFacePreviewRead
  // 가져올 분할 이름들. 처음에는 모두 고른다.
  splits: string[]
}

// 불러온 원본
export type LoadedSource = FileSource | HuggingFaceSource

// 4단계(ImportRun)의 상태.
// ready: 누르기 전 · starting: 시작 요청 중 · running: 작업 도는 중 · done: 끝남 · failed: 실패
export type ImportPhase = 'ready' | 'starting' | 'running' | 'done' | 'failed'

// 파일로 받는 확장자. 서버(system/tables.py의 FORMAT_BY_SUFFIX)와 같다.
export const FILE_SUFFIXES = ['.csv', '.tsv', '.xlsx']

/** 받는 확장자인지. 'a.CSV' → true */
export function isSupportedFile(fileName: string): boolean {
  const lower = fileName.toLowerCase()
  return FILE_SUFFIXES.some((suffix) => lower.endsWith(suffix))
}

/** 올린 파일을 원본으로. */
export function fileSource(fileName: string, preview: FilePreviewRead): FileSource {
  return { source: 'file', fileName, preview }
}

/** 허깅페이스 미리 보기를 원본으로. 분할은 모두 고른다. */
export function huggingFaceSource(preview: HuggingFacePreviewRead): HuggingFaceSource {
  return { source: 'huggingface', preview, splits: preview.splits.map((split) => split.name) }
}

/** 원본의 열 이름들. */
export function sourceColumns(loaded: LoadedSource): string[] {
  return loaded.preview.columns
}

/** 미리 보기 줄들 (앞 20줄). */
export function sourceRows(loaded: LoadedSource): PreviewRow[] {
  return loaded.preview.rows
}

/** 미리 보기 줄마다의 줄 번호. 파일은 원본의 줄 번호, 허깅페이스는 1부터 센 순서. */
export function sourceLines(loaded: LoadedSource): number[] {
  if (loaded.source === 'file') return loaded.preview.lines
  return loaded.preview.rows.map((_row, index) => index + 1)
}

/** 화면에 보일 원본 이름. 파일 이름, 또는 'klue/klue · ynat' */
export function sourceName(loaded: LoadedSource): string {
  if (loaded.source === 'file') return loaded.fileName
  return `${loaded.preview.repo} · ${loaded.preview.config}`
}

/** 새 데이터셋 이름의 기본값. 파일은 확장자를 뺀 이름, 허깅페이스는 'repo · config' */
export function defaultDatasetName(loaded: LoadedSource): string {
  if (loaded.source === 'huggingface') return sourceName(loaded)
  const dot = loaded.fileName.lastIndexOf('.')
  return dot > 0 ? loaded.fileName.slice(0, dot) : loaded.fileName
}

/**
 * 원본이 같은지 가르는 열쇠. 열쇠가 바뀌면 열 이름도 바뀔 수 있어 필드 맞춤을 새로 짐작한다.
 * 파일은 올린 파일 + 시트, 허깅페이스는 저장소 + 구성.
 */
export function sourceKey(loaded: LoadedSource): string {
  if (loaded.source === 'file') return `file:${loaded.preview.upload_id}:${loaded.preview.sheet ?? ''}`
  return `huggingface:${loaded.preview.repo}:${loaded.preview.config}`
}

/** 가져올 전체 줄 수. 허깅페이스는 고른 분할의 합. 모르면 null. */
export function sourceRowCount(loaded: LoadedSource): number | null {
  if (loaded.source === 'file') return loaded.preview.row_count
  let total = 0
  for (const split of loaded.preview.splits) {
    if (!loaded.splits.includes(split.name)) continue
    if (split.num_rows === null) return null
    total += split.num_rows
  }
  return total
}

/** 미리 보기 줄이 나온 허깅페이스 분할 이름. 파일이면 null. */
export function previewSplitName(loaded: LoadedSource): string | null {
  return loaded.source === 'huggingface' ? loaded.preview.split : null
}

/** 번호로 저장된 라벨 열(ClassLabel)의 이름 목록. 파일이면 {}. */
export function sourceLabelNames(loaded: LoadedSource): Record<string, string[]> {
  return loaded.source === 'huggingface' ? loaded.preview.label_names : {}
}

/** 가져오기 입력의 '가져올 곳' 부분. 모듈의 가져오기 입력(SourceCreate를 이어 받음)에 펼쳐 넣는다. */
export function toSourceCreate(loaded: LoadedSource): SourceCreate {
  if (loaded.source === 'file') {
    return { source: 'file', upload_id: loaded.preview.upload_id, sheet: loaded.preview.sheet }
  }
  return {
    source: 'huggingface',
    repo: loaded.preview.repo,
    config: loaded.preview.config,
    splits: loaded.splits,
  }
}
