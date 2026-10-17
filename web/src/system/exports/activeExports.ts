// 데이터셋에서 아직 만드는 중(대기 · 도는 중)인 내보내기. 내보내기 창을 다시 열면 고르는 화면 대신 이 파일들의 진행을 보인다
// (작업 실행기 여럿이 함께 돌아 다른 데이터셋을 보다 돌아와도, 창을 닫았다 열어도 그때의 진행이 보이게).
import { listExports } from '@/system/api'
import type { ExportRead } from '@/system/types'

/** 만드는 중인 내보내기들(만든 순서). 읽지 못하면 빈 목록(고르는 화면을 그대로 보인다). */
export async function activeExports(datasetId: number): Promise<ExportRead[]> {
  try {
    const items = await listExports(datasetId)
    return items.filter((item) => item.status === 'queued' || item.status === 'running').sort((a, b) => a.id - b.id)
  } catch {
    return []
  }
}
