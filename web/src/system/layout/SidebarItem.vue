<!--
  사이드바 한 줄: 아이콘 + 이름 (+ 오른쪽 작은 글자). system과 모듈이 같이 쓴다.
  to가 있으면 그 주소로 가는 줄이고, 지금 그 화면이면 흰 바탕으로 켜진다. to가 없으면 누를 수 없는 줄이다.
  사이드바가 아이콘 줄(rail)로 접히면 이름을 숨기고 툴팁으로 보인다.
-->
<script setup lang="ts">
import type { LucideIcon } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { isRail } from '@/system/layout/rail'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/system/ui/tooltip'

const props = defineProps<{
  icon: LucideIcon
  label: string
  // 누르면 갈 주소
  to?: string
  // 이름 오른쪽의 작은 글자. 예: '예정'
  note?: string
  // 흐린 글자(아직 없는 기능)
  disabled?: boolean
  // 이름 오른쪽의 빨간 수. 0이면 숨긴다. 예: 확인 안 한 실패 수
  badge?: number
}>()

// 줄 모양. 접히면 아이콘만 가운데에 둔다.
const ROW_CLASS =
  'flex min-h-8 items-center gap-[9px] rounded-[7px] px-2.5 text-body font-medium transition-colors [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-muted-foreground rail:min-h-9 rail:justify-center rail:px-0'

// 누를 수 있는 줄
const LINK_CLASS = 'text-foreground/85 hover:bg-sidebar-accent hover:text-foreground'

// 지금 보고 있는 화면의 줄: 흰 바탕 + 보라 아이콘
const ACTIVE_CLASS =
  'bg-card text-foreground! shadow-[0_0_0_1px_var(--border),var(--shadow-xs)] [&>svg]:text-primary!'

const tooltip = computed(() => {
  if (props.badge) return `${props.label} · 실패 ${props.badge}`
  return props.note ? `${props.label} · ${props.note}` : props.label
})
</script>

<template>
  <Tooltip :disabled="!isRail">
    <TooltipTrigger as-child>
      <RouterLink v-if="to" :to="to" :class="[ROW_CLASS, LINK_CLASS, 'relative']" :exact-active-class="ACTIVE_CLASS">
        <component :is="icon" />
        <!-- 접힌 사이드바(rail)에서는 수 대신 아이콘 오른쪽 위에 빨간 점 -->
        <span v-if="badge" class="absolute top-1.5 left-[calc(50%+3px)] hidden size-2 rounded-full bg-danger ring-2 ring-sidebar rail:block" />
        <span class="min-w-0 flex-1 truncate rail:hidden">{{ label }}</span>
        <span v-if="note" class="text-meta rail:hidden">{{ note }}</span>
        <span
          v-if="badge"
          class="inline-flex h-[18px] min-w-5 items-center justify-center rounded-full bg-danger px-1.5 text-[11px] font-semibold text-white rail:hidden"
          :aria-label="`확인 안 한 실패 ${badge}`"
          data-badge
        >
          {{ badge }}
        </span>
      </RouterLink>
      <div
        v-else
        :class="[ROW_CLASS, disabled ? 'text-subtle-foreground' : 'text-foreground/85']"
        :aria-disabled="disabled || undefined"
      >
        <component :is="icon" />
        <span class="min-w-0 flex-1 truncate rail:hidden">{{ label }}</span>
        <span v-if="note" class="text-meta rail:hidden">{{ note }}</span>
      </div>
    </TooltipTrigger>
    <TooltipContent side="right">{{ tooltip }}</TooltipContent>
  </Tooltip>
</template>
