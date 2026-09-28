<!--
  상단 바: 왼쪽에 지금 위치, 오른쪽에 이 화면의 버튼. 화면마다 맨 위에 하나씩 둔다.
  예: <TopBar :crumbs="[{ label: '홈', icon: House }]"><Button>…</Button></TopBar>
  마지막 칸이 지금 화면이다. to가 있는 칸은 누르면 그 주소로 간다.
-->
<script setup lang="ts">
import { ChevronRight, type LucideIcon } from '@lucide/vue'
import { RouterLink } from 'vue-router'

defineProps<{
  crumbs: { label: string; icon?: LucideIcon; to?: string }[]
}>()
</script>

<template>
  <header class="sticky top-0 z-30 flex h-[52px] shrink-0 items-center gap-3 border-b bg-background px-6">
    <nav class="flex min-w-0 items-center gap-1.5 text-ui text-muted-foreground" aria-label="현재 위치">
      <template v-for="(crumb, index) in crumbs" :key="index">
        <ChevronRight v-if="index > 0" class="size-3.5 shrink-0 text-subtle-foreground" />
        <RouterLink
          v-if="crumb.to"
          :to="crumb.to"
          class="flex items-center gap-1.5 whitespace-nowrap hover:text-foreground"
        >
          <component :is="crumb.icon" v-if="crumb.icon" class="size-3.5" />{{ crumb.label }}
        </RouterLink>
        <span
          v-else
          class="flex items-center gap-1.5 whitespace-nowrap"
          :class="{ 'font-semibold text-foreground': index === crumbs.length - 1 }"
        >
          <component :is="crumb.icon" v-if="crumb.icon" class="size-3.5" />{{ crumb.label }}
        </span>
      </template>
    </nav>
    <div class="ml-auto flex items-center gap-1.5">
      <slot />
    </div>
  </header>
</template>
