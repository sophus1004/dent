<!--
  왼쪽 사이드바: 로고 · 작업 공간 · 검색 · 홈 · 작업 기록(확인 안 한 실패 수) · 모듈 · 아래쪽 상태 줄과 테마 버튼.
  모듈 칸은 각 모듈이 내놓은 SidebarSection을 그린다. system은 모듈의 속을 모른다.
  창이 73.75rem(기본 글꼴에서 1180px) 이하면 아이콘 줄(rail)로 접힌다(system/layout/rail.ts). 폭은 15.5rem · 접으면 3.75rem이고,
  0.2초 동안 부드럽게 바뀐다(동작 줄이기 설정이면 바로).
-->
<script setup lang="ts">
import {
  History,
  House,
  MessageSquareText,
  Monitor,
  Moon,
  Plug,
  Search,
  Sun,
  type LucideIcon,
} from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { unseenFailures } from '@/system/jobFeed'
import DentLogo from '@/system/layout/DentLogo.vue'
import { isRail } from '@/system/layout/rail'
import SidebarItem from '@/system/layout/SidebarItem.vue'
import { useModules } from '@/system/module'
import { checkedOnce, overallGrade, readiness } from '@/system/readiness'
import { systemRows } from '@/system/status'
import { cycleTheme, theme, THEME_NAMES, type Theme } from '@/system/theme'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

const emit = defineEmits<{
  // 검색 · 이동(⌘K 팔레트)을 연다
  openPalette: []
}>()

const modules = useModules()

// 아직 없는 모듈. 자리만 보인다.
const PLANNED_MODULES: { icon: LucideIcon; label: string }[] = [{ icon: MessageSquareText, label: '생성 (LLM)' }]

// 테마 버튼의 아이콘
const THEME_ICONS: Record<Theme, LucideIcon> = { system: Monitor, light: Sun, dark: Moon }

// 상태 점의 색
const DOT_CLASS = {
  good: 'bg-success',
  warn: 'bg-warning',
  bad: 'bg-danger',
  unknown: 'bg-subtle-foreground',
}

// 줄 이름 → 상태 낱말. 예: { db: '정상', worker: '실행 중', embedding: '연결됨' }
const words = computed<Record<string, string>>(() =>
  readiness.value ? Object.fromEntries(systemRows(readiness.value).map((row) => [row.key, row.word])) : {},
)

// 상태 줄 글자. 예: 'DB 정상 · 작업 실행기 실행 중'
const statusText = computed(() => {
  if (!checkedOnce.value) return '확인 중'
  if (!readiness.value) return 'API 서버 응답 없음'
  return `DB ${words.value.db} · 작업 실행기 ${words.value.worker}`
})

// 상태 줄 툴팁. 좁아서 잘리는 글자와 외부 모델 연결까지. 예: '… · 임베딩 연결됨 · Jev 연결됨'
const statusTooltip = computed(() => {
  if (!readiness.value) return statusText.value
  return `${statusText.value} · 임베딩 ${words.value.embedding} · Jev ${words.value.jev}`
})
</script>

<template>
  <aside
    class="sticky top-0 z-40 flex h-screen w-62 shrink-0 flex-col overflow-hidden border-r bg-sidebar transition-[width] duration-200 ease-out motion-reduce:transition-none rail:w-15"
    aria-label="주 메뉴"
  >
    <div class="flex h-[52px] shrink-0 items-center px-4 rail:justify-center rail:px-0">
      <RouterLink to="/" class="flex items-center gap-2.5 rounded-md" aria-label="DENT 홈">
        <DentLogo class="size-[22px]" />
        <span class="text-title font-bold tracking-[-0.03em] rail:hidden">DENT</span>
      </RouterLink>
    </div>

    <div class="space-y-1.5 px-3 rail:px-2.5">
      <div class="flex h-9 items-center gap-2.5 px-2 rail:hidden">
        <span
          class="grid size-6 place-items-center rounded-md bg-linear-135 from-[#6f58ea] to-[#17a596] text-meta font-bold text-white"
          aria-hidden="true"
        >
          로
        </span>
        <span class="min-w-0 flex-1 truncate text-body font-semibold">로컬 작업 공간</span>
      </div>

      <Tooltip :disabled="!isRail">
        <TooltipTrigger as-child>
          <button
            type="button"
            class="flex h-8 w-full items-center gap-2 rounded-lg bg-card px-2.5 text-ui text-muted-foreground shadow-[0_0_0_1px_var(--border)] transition-colors hover:text-foreground rail:justify-center rail:px-0"
            aria-label="검색 · 이동"
            @click="emit('openPalette')"
          >
            <Search class="size-[15px] shrink-0" />
            <span class="flex-1 text-left rail:hidden">검색 · 이동</span>
            <span class="kbd rail:hidden">⌘K</span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">검색 · 이동 ⌘K</TooltipContent>
      </Tooltip>
    </div>

    <nav class="mt-3 flex-1 space-y-0.5 overflow-y-auto px-3 rail:px-2.5">
      <SidebarItem :icon="House" label="홈" to="/" />
      <SidebarItem :icon="History" label="작업 기록" to="/jobs" :badge="unseenFailures" />

      <div class="caps px-2.5 pt-5 pb-1.5 rail:hidden">모듈</div>
      <div class="mx-2 my-3 hidden h-px bg-border rail:block" />
      <component :is="module.SidebarSection" v-for="module in modules" :key="module.id" />

      <div class="pt-1">
        <SidebarItem
          v-for="planned in PLANNED_MODULES"
          :key="planned.label"
          :icon="planned.icon"
          :label="planned.label"
          note="예정"
          disabled
        />
      </div>
    </nav>

    <div class="flex items-center gap-1 border-t px-3 pt-2 pb-3 rail:flex-col rail:px-2.5">
      <Tooltip>
        <TooltipTrigger as-child>
          <RouterLink
            to="/status"
            class="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-ui text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground rail:w-9 rail:flex-none rail:justify-center rail:px-0"
            exact-active-class="bg-card text-foreground! shadow-[0_0_0_1px_var(--border)]"
            aria-label="상태"
          >
            <span class="relative grid size-4 shrink-0 place-items-center">
              <Plug class="size-4" />
              <span
                class="absolute -top-0.5 -right-0.5 size-1.5 rounded-full ring-2 ring-sidebar"
                :class="DOT_CLASS[overallGrade]"
              />
            </span>
            <span class="truncate rail:hidden">{{ statusText }}</span>
          </RouterLink>
        </TooltipTrigger>
        <TooltipContent side="right">{{ statusTooltip }}</TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger as-child>
          <button
            type="button"
            class="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
            aria-label="테마 바꾸기"
            @click="cycleTheme"
          >
            <component :is="THEME_ICONS[theme]" class="size-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent :side="isRail ? 'right' : 'top'">테마 · {{ THEME_NAMES[theme] }}</TooltipContent>
      </Tooltip>
    </div>
  </aside>
</template>
