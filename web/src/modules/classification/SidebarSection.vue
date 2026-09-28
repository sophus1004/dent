<!--
  사이드바의 분류 칸: '분류' 줄, 그 아래 데이터셋 목록, '+ 새 데이터셋'.
  데이터셋을 누르면 그 데이터셋 화면(#/classification/<id>)으로 가고, 지금 보고 있는 데이터셋은 흰 바탕으로 켜진다.
  목록이 비면 흐린 '데이터셋 없음'. 사이드바가 아이콘 줄로 접히면 목록은 숨기고 + 아이콘만 남긴다.
-->
<script setup lang="ts">
import { Plus, Tags } from '@lucide/vue'
import { computed, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import SidebarItem from '@/system/layout/SidebarItem.vue'
import { Skeleton } from '@/system/ui/skeleton'

import { datasets, loadDatasets, loadFailed } from '@/modules/classification/datasets'

// 줄 모양. 지금 보고 있는 데이터셋은 SidebarItem의 켜진 줄처럼 흰 바탕
const ROW_CLASS = 'flex w-full items-start rounded-[7px] px-2.5 py-[7px] text-left text-ui transition-colors'
const IDLE_CLASS = 'text-foreground/85 hover:bg-sidebar-accent hover:text-foreground'
const CURRENT_CLASS = 'bg-card text-foreground shadow-[0_0_0_1px_var(--border),var(--shadow-xs)]'

const route = useRoute()

// 지금 보고 있는 데이터셋 번호. 데이터셋 화면이 아니면 null
const currentId = computed(() => {
  const param = route.params.datasetId
  return typeof param === 'string' ? Number(param) : null
})

onMounted(loadDatasets)
</script>

<template>
  <SidebarItem :icon="Tags" label="분류" />

  <div class="ml-[17px] space-y-0.5 border-l pl-2 rail:hidden">
    <div v-if="loadFailed && !datasets" class="px-2.5 py-1.5 text-ui text-subtle-foreground">목록 오류</div>
    <div v-else-if="!datasets" class="px-2.5 py-2"><Skeleton class="h-3.5 w-24" /></div>
    <div v-else-if="datasets.length === 0" class="px-2.5 py-1.5 text-ui text-subtle-foreground">데이터셋 없음</div>
    <RouterLink
      v-for="dataset in datasets"
      :key="dataset.id"
      :to="`/classification/${dataset.id}`"
      :class="[ROW_CLASS, dataset.id === currentId ? CURRENT_CLASS : IDLE_CLASS]"
      :aria-current="dataset.id === currentId ? 'page' : undefined"
    >
      <span class="line-clamp-2 min-w-0">{{ dataset.name }}</span>
    </RouterLink>
  </div>

  <RouterLink
    to="/classification/new"
    class="ml-[17px] flex min-h-8 items-center gap-2 rounded-[7px] pl-[18px] text-ui text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground rail:hidden"
    exact-active-class="bg-card text-foreground! shadow-[0_0_0_1px_var(--border),var(--shadow-xs)]"
  >
    <Plus class="size-3.5" />새 데이터셋
  </RouterLink>
  <div class="hidden rail:block">
    <SidebarItem :icon="Plus" label="새 데이터셋" to="/classification/new" />
  </div>
</template>
