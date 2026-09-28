// 모듈이 system에 내놓는 모양. system은 모듈을 import하지 않고 이 모양만 안다.
// 모듈 목록은 src/modules.ts에 있고, src/main.ts가 그것을 앱에 넣어 준다(provideModules).
import type { LucideIcon } from '@lucide/vue'
import { inject, type App, type Component, type InjectionKey } from 'vue'
import type { RouteRecordRaw } from 'vue-router'

import type { JobRead } from '@/system/types'

// 모듈의 바로가기 하나. ⌘K 팔레트와 홈 상단 바에 보인다. 주소 이동만 한다.
export interface ModuleCommand {
  // 예: '새 데이터셋 가져오기'
  label: string
  icon: LucideIcon
  // 예: '/classification/new'
  to: string
}

// 모듈이 넣는 작업 종류 하나. 작업 기록 화면과 알림이 이름 · 단계 이름 · 다시 하기를 여기서 찾는다.
export interface ModuleJobKind {
  // 예: '의미 지도'
  name: string
  // 단계 이름. 예: { embedding: '임베딩', projecting: '좌표 계산', saving: '저장' }
  phases?: Record<string, string>
  // 실패하거나 멈춘 작업을 다시 넣는다. 다시 할 수 없는 종류(원본이 지워진 가져오기 등)면 두지 않는다.
  retry?: (job: JobRead) => Promise<void>
}

export interface DentModule {
  // 모듈 이름. 주소의 첫 칸이고 백엔드 API·테이블 앞머리와 같다. 예: 'classification' → #/classification/new
  id: string
  // 화면에 보일 이름. 예: '분류'
  name: string
  // 이 모듈의 화면 주소들. 모두 '/{id}'로 시작한다.
  routes: RouteRecordRaw[]
  // 사이드바 '모듈' 아래에 보일 이 모듈의 칸
  SidebarSection: Component
  // 홈 위쪽: 현황 숫자와 데이터셋(없으면 빈 칸 안내)
  HomeSection: Component
  // 홈 아래 왼쪽: 최근 가져오기. 오른쪽에는 system의 시스템 칸이 붙는다.
  RecentCard: Component
  commands: ModuleCommand[]
  // 이 모듈이 넣는 작업 종류. 키는 백엔드 작업의 kind (예: 'map')
  jobKinds: Record<string, ModuleJobKind>
  // 이 모듈이 넣은 작업이 끝났을 때(완료 · 실패 · 멈춤) 부른다. 예: 가져오기가 끝나면 사이드바 목록을 다시 읽는다.
  onJobFinished?: (job: JobRead) => void
}

const MODULES_KEY: InjectionKey<DentModule[]> = Symbol('dent-modules')

/** 앱에 모듈 목록을 넣는다. src/main.ts가 한 번 부른다. */
export function provideModules(app: App, modules: DentModule[]): void {
  app.provide(MODULES_KEY, modules)
}

/** 앱에 들어 있는 모듈 목록. */
export function useModules(): DentModule[] {
  return inject(MODULES_KEY, [])
}
