<!--
  홈: 맨 위에 모델 칸(임베딩 · Jev · LLM)과 시스템 칸(DB · 스키마 · 저장 공간 · 작업 실행기)을 나란히 두고,
  그 아래 모듈마다 현황과 데이터셋(HomeSection), 맨 아래 최근 가져오기(모듈의 RecentCard).
  두 칸은 놓인 칸의 폭이 62.5rem보다 좁으면 위아래로 쌓는다(@container).
  상단 바의 버튼은 모듈이 내놓은 바로가기다. 예: 새 데이터셋 가져오기
-->
<script setup lang="ts">
import { House } from '@lucide/vue'
import { RouterLink } from 'vue-router'

import TopBar from '@/system/layout/TopBar.vue'
import { useModules } from '@/system/module'
import ModelCard from '@/system/pages/ModelCard.vue'
import SystemCard from '@/system/pages/SystemCard.vue'
import { Button } from '@/system/ui/button'

const modules = useModules()
const commands = modules.flatMap((module) => module.commands)
</script>

<template>
  <TopBar :crumbs="[{ label: '홈', icon: House }]">
    <Button v-for="command in commands" :key="command.to" size="sm" as-child>
      <RouterLink :to="command.to"><component :is="command.icon" />{{ command.label }}</RouterLink>
    </Button>
  </TopBar>

  <div class="page space-y-7">
    <div class="@container">
      <div class="grid items-stretch gap-4 @min-[62.5rem]:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <ModelCard class="min-w-0" />
        <SystemCard class="min-w-0" />
      </div>
    </div>
    <component :is="module.HomeSection" v-for="module in modules" :key="module.id" />
    <component :is="module.RecentCard" v-for="module in modules" :key="`recent-${module.id}`" class="min-w-0" />
  </div>
</template>
